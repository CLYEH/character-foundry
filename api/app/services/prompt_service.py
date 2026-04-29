"""Service layer for `POST /v1/prompt/preview` (T-035).

Holds the per-mode dispatch logic so the route handler stays a thin
coordinator. Each mode resolves the inputs it needs (parent character,
mask presence, parent base/alias for motion), calls the reconciler when
appropriate, and assembles the per-mode response surface.

The route owns auth + DI; this module owns business validation
(ownership, parent resolution, mask existence) and the reconciler call.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    auth_insufficient_permission,
    not_found_character,
    not_found_checkpoint,
)
from app.models.user import User
from app.prompt.constraints import ReconcileMode, get_constraints_for_mode
from app.prompt.errors import (
    not_found_alias,
    not_found_mask,
    validation_empty_input,
    validation_mask_required,
    validation_motion_custom_requires_description,
    validation_motion_parent_mismatch,
)
from app.prompt.motion_templates import PRESET_MOTION_PROMPTS
from app.prompt.reconciler import PromptReconciler, ReconcileInput, ReconcileOutput
from app.repositories import (
    alias_repo,
    base_repo,
    character_repo,
    checkpoint_repo,
    mask_repo,
)
from app.schemas.prompt import (
    CreateAliasPreviewRequest,
    CreateBasePreviewRequest,
    CreateMotionPreviewRequest,
    DerivedFromInfo,
    MaskInput,
    MotionParentInfo,
    PromptPreviewResponse,
)
from app.storage.backend import StorageBackend

# Signed URLs for parent / base images live for one hour — same TTL the
# reference-image upload route mints. Long enough to render the modal,
# short enough that a leaked URL is useless within the day.
_SIGNED_URL_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# create_base
# ---------------------------------------------------------------------------


async def preview_create_base(
    *,
    body: CreateBasePreviewRequest,
    db: AsyncSession,
    reconciler: PromptReconciler,
) -> PromptPreviewResponse:
    """Sprint-2 surface (T-019), extended with `base_checkpoint_id` for
    remix previews.

    `base_checkpoint_id` is treated as a remix signal: presence flips
    `has_reference_image=True` because the worker would dispatch to
    image2image with the parent checkpoint as conditioning. The
    `reference_image_ids` list keeps the original meaning (user-supplied
    references); both routes funnel into the same flag.
    """
    has_reference = bool(body.reference_image_ids) or body.base_checkpoint_id is not None
    has_menu = bool(body.menu_selections)
    has_note = bool((body.freeform_note or "").strip())
    if not (has_menu or has_note or has_reference):
        raise validation_empty_input()

    if body.base_checkpoint_id is not None:
        # Validate the checkpoint exists so a typo'd remix doesn't
        # render a confidently-wrong "with reference image" preview.
        # No ownership check here — preview is read-only and mirroring
        # the worker's later check would require pulling the session +
        # team; the worker hard-validates at generate time.
        checkpoint = await checkpoint_repo.get(db, body.base_checkpoint_id)
        if checkpoint is None:
            raise not_found_checkpoint()

    mode = ReconcileMode.CREATE_BASE_WITH_REF if has_reference else ReconcileMode.CREATE_BASE
    output = await reconciler.preview(
        ReconcileInput(
            mode=mode,
            menu_selections=body.menu_selections,
            freeform_note=body.freeform_note,
            has_reference_image=has_reference,
            has_inpaint_mask=False,
        )
    )
    return _to_response(output)


# ---------------------------------------------------------------------------
# create_alias
# ---------------------------------------------------------------------------


async def preview_create_alias(
    *,
    body: CreateAliasPreviewRequest,
    db: AsyncSession,
    user: User,
    storage: StorageBackend,
    reconciler: PromptReconciler,
) -> PromptPreviewResponse:
    """Sprint-3 alias surface.

    Validates that the caller owns the parent character (403 otherwise),
    that any referenced mask exists (404 otherwise), and that the body
    has at least one non-trivial signal beyond `input_mode` itself.
    """
    character = await character_repo.get_active(db, body.character_id)
    if character is None:
        raise not_found_character()
    if character.owner_id != user.id:
        # Same envelope a non-owner write would get. T-031 will reuse
        # this exact code on the alias-create route.
        raise auth_insufficient_permission()

    mask: MaskInput | None = body.mask
    if mask is not None:
        # `mask: {}` lands here as `None` if the wire schema allowed it
        # via Pydantic, but we re-check for a missing mask_id defensively
        # so a future schema relaxation can't bypass the rule.
        if mask.mask_id is None:  # pragma: no cover — Pydantic enforces
            raise validation_mask_required()
        mask_row = await mask_repo.get(db, mask.mask_id)
        if mask_row is None:
            raise not_found_mask()
        # Mask must belong to the same character — otherwise we'd be
        # leaking other characters' mask uploads via id-enumeration.
        # Same code as missing so callers can't probe ownership.
        if mask_row.character_id != character.id:
            raise not_found_mask()

    has_note = bool((body.freeform_note or "").strip())
    has_refs = bool(body.reference_image_ids)
    has_mask = mask is not None
    if not (has_note or has_refs or has_mask):
        raise validation_empty_input()

    base = await base_repo.get_by_character_id(db, character.id)
    # Alias mode requires Base to be locked; if it isn't, the alias
    # surface itself is unreachable from the UI. Surface as 409 via
    # the existing CONFLICT_BASE_NOT_SET? We don't have that code yet
    # — T-031 will add it. For preview, treat missing base as
    # `not_found_character` shaped: the alias surface isn't visible
    # without a Base, so the call is invalid the same way a wrong id
    # would be.
    if base is None:
        raise not_found_character()

    output = await reconciler.preview(
        ReconcileInput(
            mode=ReconcileMode.CREATE_ALIAS,
            menu_selections=None,
            freeform_note=body.freeform_note,
            has_reference_image=has_refs,
            has_inpaint_mask=has_mask,
        )
    )
    return _to_response(
        output,
        derived_from=DerivedFromInfo(
            base_id=base.id,
            base_image_url=storage.get_signed_url(
                base.image_key,
                expires_in_seconds=_SIGNED_URL_TTL_SECONDS,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# create_motion
# ---------------------------------------------------------------------------


async def preview_create_motion(
    *,
    body: CreateMotionPreviewRequest,
    db: AsyncSession,
    user: User,
    storage: StorageBackend,
    reconciler: PromptReconciler,
) -> PromptPreviewResponse:
    """Sprint-3 motion surface.

    Preset motions short-circuit the reconciler (the prompt is a static
    template) — `motion_template_used` echoes the preset id verbatim.
    Custom motions go through `CREATE_MOTION` mode the same way alias
    custom freeform notes do, and `motion_template_used` reports
    `'custom_reconciled'` so the modal can show the reconciled prompt
    block.
    """
    parent_image_key, parent_character_id = await _resolve_motion_parent(
        body.parent_type, body.parent_id, db
    )
    parent_character = await character_repo.get_active(db, parent_character_id)
    if parent_character is None:
        raise not_found_character()
    if parent_character.owner_id != user.id:
        raise auth_insufficient_permission()

    if body.motion_type == "custom":
        if not (body.description or "").strip():
            raise validation_motion_custom_requires_description()
        output = await reconciler.preview(
            ReconcileInput(
                mode=ReconcileMode.CREATE_MOTION,
                menu_selections=None,
                freeform_note=body.description,
                has_reference_image=True,  # the parent image is conditioning
                has_inpaint_mask=False,
            )
        )
        template_used = "custom_reconciled"
    else:
        # Preset path: skip the LLM entirely and compose
        # constraints + preset template directly. Uses the same
        # `_compose_output` shape the reconciler emits so the response
        # envelope stays uniform.
        constraints = get_constraints_for_mode(ReconcileMode.CREATE_MOTION)
        preset_prompt = PRESET_MOTION_PROMPTS[body.motion_type]
        output = ReconcileOutput(
            final_prompt=", ".join(constraints) + ". " + preset_prompt + ".",
            reconciled_note_en=preset_prompt,
            menu_fragments_en=(),
            applied_constraints=constraints,
            removed_segments=(),
            llm_latency_ms=0,
            cached=False,
        )
        template_used = body.motion_type

    return _to_response(
        output,
        parent=MotionParentInfo(
            type=body.parent_type,
            id=body.parent_id,
            image_url=storage.get_signed_url(
                parent_image_key,
                expires_in_seconds=_SIGNED_URL_TTL_SECONDS,
            ),
        ),
        motion_template_used=template_used,
    )


async def _resolve_motion_parent(
    parent_type: str,
    parent_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[str, uuid.UUID]:
    """Return (image_storage_key, owning_character_id) for the motion's parent.

    `parent_type` is trusted in the response surface but cross-checked
    against the row at the DB layer: an alias id sent with
    `parent_type='base'` is a `VALIDATION_MOTION_PARENT_MISMATCH`, not a
    silent 404 — the row exists, just under the wrong kind.
    """
    if parent_type == "base":
        base = await base_repo.get(db, parent_id)
        if base is None:
            # Cross-check against alias before declaring "not found":
            # if the id matches an alias the caller meant 'alias', not
            # 'base' — surface as a structured mismatch.
            sibling_alias = await alias_repo.get_active(db, parent_id)
            if sibling_alias is not None:
                raise validation_motion_parent_mismatch()
            raise not_found_character()
        return base.image_key, base.character_id

    if parent_type == "alias":
        alias = await alias_repo.get_active(db, parent_id)
        if alias is None:
            sibling_base = await base_repo.get(db, parent_id)
            if sibling_base is not None:
                raise validation_motion_parent_mismatch()
            raise not_found_alias()
        return alias.image_key, alias.character_id

    # Unreachable — Pydantic narrows parent_type to the literal union.
    raise validation_motion_parent_mismatch()  # pragma: no cover


# ---------------------------------------------------------------------------
# Shared response builder
# ---------------------------------------------------------------------------


def _to_response(
    output: ReconcileOutput,
    *,
    derived_from: DerivedFromInfo | None = None,
    parent: MotionParentInfo | None = None,
    motion_template_used: str | None = None,
) -> PromptPreviewResponse:
    return PromptPreviewResponse(
        platform_constraints=", ".join(output.applied_constraints),
        reconciled_note_en=output.reconciled_note_en,
        menu_fragments=list(output.menu_fragments_en),
        final_prompt=output.final_prompt,
        derived_from=derived_from,
        parent=parent,
        motion_template_used=motion_template_used,  # type: ignore[arg-type]
    )
