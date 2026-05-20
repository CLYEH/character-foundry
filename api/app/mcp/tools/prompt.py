"""`prompt.preview` MCP tool (T-088) — 1:1 wrap of `POST /v1/prompt/preview`.

Per `planning/agent-interface/endpoint-mcp-mapping.md` §2.6: non-mutating
preview of the reconciled final prompt before an agent commits to a
generation. Scope `character:read`.

The wrapped endpoint takes a mode-discriminated body (`create_base` /
`create_alias` / `create_motion`); the handler accepts the same union as a
single `request` argument and dispatches to `prompt_service` exactly like the
route does. Dependencies the route gets via FastAPI DI (db / redis /
reconciler / storage / user) are assembled here because tools run outside the
request scope — same short-lived-session pattern as `app/mcp/tools/task.py`.
"""

from __future__ import annotations

from app.api.deps import get_storage
from app.auth.scopes import SCOPE_CHARACTER_READ
from app.core.errors import auth_invalid_token
from app.core.redis_client import get_redis
from app.db.session import async_session_factory
from app.mcp.auth import require_mcp_scopes, require_user_context, translate_agent_errors
from app.mcp.registry import MCPTool, register
from app.mcp.schemas.prompt import PromptPreviewInput
from app.models.user import User
from app.prompt.reconciler import get_prompt_reconciler
from app.schemas.prompt import (
    CreateAliasPreviewRequest,
    CreateBasePreviewRequest,
    CreateMotionPreviewRequest,
    PromptPreviewRequest,
    PromptPreviewResponse,
)
from app.services import prompt_service


async def prompt_preview(request: PromptPreviewRequest) -> PromptPreviewResponse:
    """Preview the reconciled final prompt for a create_base / create_alias /
    create_motion request — without enqueuing any generation.

    Mirrors `POST /v1/prompt/preview`: the same validation (ownership, parent
    resolution, mask existence) and the same per-mode response surface.
    """
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    redis = await get_redis()
    storage = get_storage()
    reconciler = get_prompt_reconciler(redis)
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await db.get(User, user_id)
            if user is None:
                # Token resolved but the user row is gone (race with deletion).
                # Mirror /v1/*'s get_current_user, which raises invalid-token
                # rather than leaking account-existence state.
                raise auth_invalid_token()
            if isinstance(request, CreateBasePreviewRequest):
                return await prompt_service.preview_create_base(
                    body=request, db=db, user=user, reconciler=reconciler
                )
            if isinstance(request, CreateAliasPreviewRequest):
                return await prompt_service.preview_create_alias(
                    body=request, db=db, user=user, storage=storage, reconciler=reconciler
                )
            if isinstance(request, CreateMotionPreviewRequest):
                return await prompt_service.preview_create_motion(
                    body=request, db=db, user=user, storage=storage, reconciler=reconciler
                )
            # Unreachable — the discriminated union narrows to one of the three
            # above. Kept as a guardrail if a fourth mode is added.
            raise NotImplementedError(  # pragma: no cover
                f"unhandled prompt-preview mode: {request!r}"
            )


PROMPT_PREVIEW = register(
    MCPTool(
        name="prompt.preview",
        description=(
            "Preview the reconciled final prompt for a character / alias / motion "
            "request before committing. Non-mutating: runs the same prompt "
            "reconciliation the generation would, so an agent can inspect the "
            "English prompt + applied platform constraints first."
        ),
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["POST /v1/prompt/preview"],
        input_schema=PromptPreviewInput,
        output_schema=PromptPreviewResponse,
        handler=prompt_preview,
    )
)
