"""`POST /v1/prompt/preview` — synchronous reconciler preview (T-019 + T-035).

Thin coordinator: pull dependencies, dispatch to the per-mode service
function, return its `PromptPreviewResponse`.

Three wire modes (discriminated union by `mode`):
  - `create_base`   : original Sprint-2 surface, plus `base_checkpoint_id`
                      for remix previews (closes STATUS.md S2-5).
  - `create_alias`  : Sprint-3 alias mode (text/image/inpaint/mixed) with
                      mask reference (closes STATUS.md S3-1).
  - `create_motion` : Sprint-3 motion mode — preset prompts skip the
                      reconciler; custom prompts run it.

DB / storage / reconciler injection happens here so each service
function reads as a pure transformation; the cache-write semantics
stay the responsibility of the reconciler itself (preview never writes,
per T-019).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_prompt_reconciler_dep, get_storage
from app.models.user import User
from app.prompt.reconciler import PromptReconciler
from app.schemas.prompt import (
    CreateAliasPreviewRequest,
    CreateBasePreviewRequest,
    CreateMotionPreviewRequest,
    PromptPreviewRequest,
    PromptPreviewResponse,
)
from app.services import prompt_service
from app.storage.backend import StorageBackend

router = APIRouter(prefix="/v1/prompt", tags=["prompt"])


@router.post(
    "/preview",
    response_model=PromptPreviewResponse,
    # `derived_from` / `parent` / `motion_template_used` are mode-specific:
    # only one is populated per response. Stripping the unused-None keys
    # keeps the create_base wire surface unchanged from T-019 (existing
    # frontend modal asserts on the four-key shape) and shrinks the
    # alias / motion responses to just the relevant block.
    response_model_exclude_none=True,
)
async def preview_prompt(
    body: PromptPreviewRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    reconciler: Annotated[PromptReconciler, Depends(get_prompt_reconciler_dep)],
    user: Annotated[User, Depends(get_current_user)],
) -> PromptPreviewResponse:
    if isinstance(body, CreateBasePreviewRequest):
        return await prompt_service.preview_create_base(
            body=body,
            db=db,
            user=user,
            reconciler=reconciler,
        )
    if isinstance(body, CreateAliasPreviewRequest):
        return await prompt_service.preview_create_alias(
            body=body,
            db=db,
            user=user,
            storage=storage,
            reconciler=reconciler,
        )
    if isinstance(body, CreateMotionPreviewRequest):
        return await prompt_service.preview_create_motion(
            body=body,
            db=db,
            user=user,
            storage=storage,
            reconciler=reconciler,
        )
    # Unreachable — `Field(discriminator='mode')` narrows to one of the
    # three branches above. Kept as a final guardrail in case a fourth
    # mode is added without updating the dispatcher.
    raise NotImplementedError(f"unhandled prompt-preview mode: {body!r}")  # pragma: no cover
