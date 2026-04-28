"""`POST /v1/prompt/preview` — synchronous reconciler preview (T-019).

Thin wrapper over `PromptReconciler.preview()`:

- No task, no DB writes — just compose the prompt and return.
- Calls `preview()` (NOT `reconcile()`) so the cache stays clean. A
  user clicking 進階檢視 with a one-off "what if" input shouldn't seed
  the same key the worker would later read; if the user proceeds to
  generate with the same input, that worker-side `reconcile()` call
  populates the cache properly.
- Validation: at least one of menu_selections / freeform_note /
  reference_image_ids / mask must be present; otherwise 400.
- Wire mode `create_base` + non-empty `reference_image_ids` maps to the
  reconciler's `CREATE_BASE_WITH_REF` so the right constraint set
  + LLM signal flows through.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, get_prompt_reconciler_dep
from app.models.user import User
from app.prompt.constraints import ReconcileMode
from app.prompt.errors import validation_empty_input
from app.prompt.reconciler import PromptReconciler, ReconcileInput
from app.schemas.prompt import (
    PromptPreviewRequest,
    PromptPreviewResponse,
    WirePromptMode,
)

router = APIRouter(prefix="/v1/prompt", tags=["prompt"])


def _resolve_mode(wire_mode: WirePromptMode, *, has_reference: bool) -> ReconcileMode:
    if wire_mode == "create_base":
        return ReconcileMode.CREATE_BASE_WITH_REF if has_reference else ReconcileMode.CREATE_BASE
    if wire_mode == "create_alias":
        return ReconcileMode.CREATE_ALIAS
    return ReconcileMode.CREATE_MOTION


@router.post("/preview", response_model=PromptPreviewResponse)
async def preview_prompt(
    body: PromptPreviewRequest,
    reconciler: Annotated[PromptReconciler, Depends(get_prompt_reconciler_dep)],
    _user: Annotated[User, Depends(get_current_user)],
) -> PromptPreviewResponse:
    has_reference = bool(body.reference_image_ids)
    has_mask = body.mask is not None
    has_menu = bool(body.menu_selections)
    has_note = bool((body.freeform_note or "").strip())
    if not (has_menu or has_note or has_reference or has_mask):
        raise validation_empty_input()

    output = await reconciler.preview(
        ReconcileInput(
            mode=_resolve_mode(body.mode, has_reference=has_reference),
            menu_selections=body.menu_selections,
            freeform_note=body.freeform_note,
            has_reference_image=has_reference,
            has_inpaint_mask=has_mask,
        )
    )

    return PromptPreviewResponse(
        platform_constraints=", ".join(output.applied_constraints),
        reconciled_note_en=output.reconciled_note_en,
        menu_fragments=list(output.menu_fragments_en),
        final_prompt=output.final_prompt,
    )
