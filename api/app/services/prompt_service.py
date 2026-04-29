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
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    not_found_character,
    not_found_checkpoint,
)
from app.core.permissions import assert_can_modify_character
from app.models.user import User
from app.prompt.constraints import ReconcileMode, get_constraints_for_mode
from app.prompt.errors import (
    conflict_base_not_set,
    not_found_alias,
    not_found_mask,
    validation_empty_input,
    validation_motion_custom_requires_description,
    validation_motion_parent_mismatch,
)
from app.prompt.motion_templates import PRESET_MOTION_PROMPTS, PresetMotionType
from app.prompt.reconciler import PromptReconciler, ReconcileInput, ReconcileOutput
from app.repositories import (
    alias_repo,
    base_repo,
    character_repo,
    checkpoint_repo,
    creation_session_repo,
    mask_repo,
)
from app.schemas.prompt import (
    CreateAliasPreviewRequest,
    CreateBasePreviewRequest,
    CreateMotionPreviewRequest,
    DerivedFromInfo,
    MotionParentInfo,
    MotionParentType,
    MotionTemplateUsed,
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
    user: User,
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
        # Validate the checkpoint exists AND belongs to a session the
        # caller initiated. Mirrors `_resolve_base_checkpoint` in
        # `checkpoint_service` which the worker uses at generate time —
        # otherwise preview would 200 on cross-user checkpoint ids that
        # the worker would later reject, and an authenticated caller
        # could confirm checkpoint existence platform-wide via
        # 200-vs-404 oracle. Cross-session collapses to
        # NOT_FOUND_CHECKPOINT, same opacity as a typo'd id (Codex P2
        # on commit 0b04ff4).
        checkpoint = await checkpoint_repo.get(db, body.base_checkpoint_id)
        if checkpoint is None:
            raise not_found_checkpoint()
        session = await creation_session_repo.get(db, checkpoint.creation_session_id)
        if session is None or session.initiator_id != user.id:
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

    Validates that the caller owns the parent character (cross-team
    collapses to 404, same-team-non-owner 403 — same pattern as every
    other write-style read in the codebase, per
    `assert_can_modify_character`), that any referenced mask exists
    (404 otherwise), and that the body has at least one non-trivial
    signal beyond `input_mode` itself.
    """
    character = await character_repo.get_active(db, body.character_id)
    if character is None:
        raise not_found_character()
    # Cross-team collapses to 404 here so the response can't reveal
    # whether a character exists in another team. Same-team-non-owner
    # gets 403 — matches alias-create write semantics in T-031.
    assert_can_modify_character(character, user)

    # Cheap structural checks first — surface VALIDATION_EMPTY_INPUT
    # before paying for any DB lookups (mask, Base) on a body that's
    # already known to be insufficient.
    has_note = bool((body.freeform_note or "").strip())
    has_refs = bool(body.reference_image_ids)
    has_mask = body.mask is not None
    if not (has_note or has_refs or has_mask):
        raise validation_empty_input()

    if body.mask is not None:
        mask_row = await mask_repo.get(db, body.mask.mask_id)
        # Cross-character mask access collapses to NOT_FOUND_MASK so the
        # response can't probe other characters' mask uploads. Mirrors
        # NOT_FOUND_REFERENCE_IMAGE (api/app/core/errors.py).
        if mask_row is None or mask_row.character_id != character.id:
            raise not_found_mask()

    base = await base_repo.get_by_character_id(db, character.id)
    if base is None:
        # Distinct from NOT_FOUND_CHARACTER so the modal can render
        # "請先確立基礎形象" instead of generic "character not found".
        # T-031's alias-create write path will raise the same code.
        raise conflict_base_not_set()

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
        body.parent_type, body.parent_id, user=user, db=db
    )
    parent_character = await character_repo.get_active(db, parent_character_id)
    if parent_character is None:
        # The parent row pointed at a character that no longer exists —
        # treat as a stale id, same envelope as a typo.
        raise not_found_character()
    # Cross-team collapses to 404, same-team-non-owner 403.
    assert_can_modify_character(parent_character, user)

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
        template_used: MotionTemplateUsed = "custom_reconciled"
    else:
        # Preset path: skip the LLM entirely and compose
        # constraints + preset template directly. Uses the same
        # `ReconcileOutput` shape the reconciler emits so the response
        # envelope stays uniform.
        preset_type: PresetMotionType = body.motion_type  # narrowed by literal
        constraints = get_constraints_for_mode(ReconcileMode.CREATE_MOTION)
        preset_prompt = PRESET_MOTION_PROMPTS[preset_type]
        output = ReconcileOutput(
            final_prompt=", ".join(constraints) + ". " + preset_prompt + ".",
            reconciled_note_en=preset_prompt,
            menu_fragments_en=(),
            applied_constraints=constraints,
            removed_segments=(),
            llm_latency_ms=0,
            cached=False,
        )
        template_used = preset_type

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
    parent_type: MotionParentType,
    parent_id: uuid.UUID,
    *,
    user: User,
    db: AsyncSession,
) -> tuple[str, uuid.UUID]:
    """Return (image_storage_key, owning_character_id) for the motion's parent.

    `parent_type` is trusted in the response surface but cross-checked
    against the row at the DB layer: an alias id sent with
    `parent_type='base'` is a `VALIDATION_MOTION_PARENT_MISMATCH`, not a
    silent 404 — the row exists, just under the wrong kind.

    Mismatch is only surfaced when the caller has WRITE access on the
    sibling's character (i.e. is the owner). Same-team-non-owner and
    cross-team callers collapse to 404 — anything else leaks
    parent-kind/existence information for resources the caller can't
    modify, which Codex flagged on PR #42 (commit 4e26141, P2): the
    legitimate "right parent_type but not owner" path returns 403 via
    `assert_can_modify_character`, so the wrong-parent_type path must
    not return a more informative 400 to the same caller.
    """
    if parent_type == "base":
        base = await base_repo.get(db, parent_id)
        if base is not None:
            return base.image_key, base.character_id
        sibling_alias = await alias_repo.get_active(db, parent_id)
        if sibling_alias is not None and await _caller_can_modify_character(
            sibling_alias.character_id, user=user, db=db
        ):
            raise validation_motion_parent_mismatch()
        raise not_found_character()

    if parent_type == "alias":
        alias = await alias_repo.get_active(db, parent_id)
        if alias is not None:
            return alias.image_key, alias.character_id
        sibling_base = await base_repo.get(db, parent_id)
        if sibling_base is not None and await _caller_can_modify_character(
            sibling_base.character_id, user=user, db=db
        ):
            raise validation_motion_parent_mismatch()
        raise not_found_alias()

    assert_never(parent_type)


async def _caller_can_modify_character(
    character_id: uuid.UUID,
    *,
    user: User,
    db: AsyncSession,
) -> bool:
    """Return True iff `character_id` resolves to a non-deleted character
    the caller can modify (same team + owner).

    Used by `_resolve_motion_parent` to gate the parent-mismatch
    envelope so it only fires for callers who have write access on the
    sibling row. Cross-team and same-team-non-owner callers get the
    same 404 they'd get for a missing parent — keeps the error surface
    consistent with the legitimate parent-resolution path that goes
    through `assert_can_modify_character` in `preview_create_motion`.
    """
    character = await character_repo.get_active(db, character_id)
    if character is None:
        return False
    try:
        assert_can_modify_character(character, user)
    except Exception:  # noqa: BLE001 — collapsing the AgentError envelope to bool
        return False
    return True


# ---------------------------------------------------------------------------
# Shared response builder
# ---------------------------------------------------------------------------


def _to_response(
    output: ReconcileOutput,
    *,
    derived_from: DerivedFromInfo | None = None,
    parent: MotionParentInfo | None = None,
    motion_template_used: MotionTemplateUsed | None = None,
) -> PromptPreviewResponse:
    return PromptPreviewResponse(
        platform_constraints=", ".join(output.applied_constraints),
        reconciled_note_en=output.reconciled_note_en,
        menu_fragments=list(output.menu_fragments_en),
        final_prompt=output.final_prompt,
        derived_from=derived_from,
        parent=parent,
        motion_template_used=motion_template_used,
    )
