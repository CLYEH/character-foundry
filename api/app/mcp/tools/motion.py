"""`motion.*` MCP tools (T-086) — Wave B's third packaged tool + CRUD wraps.

Six tools per `planning/agent-interface/endpoint-mcp-mapping.md` §2.4 / §3:

  • `motion.generate`       packaged, polymorphic — generate a motion for a Base
                            OR an Alias end-to-end (enqueue i2v → poll the task
                            → return the finished motion with its video URL)
  • `motion.list_for_base`  → GET    /v1/bases/{id}/motions
  • `motion.list_for_alias` → GET    /v1/aliases/{id}/motions
  • `motion.get`            → GET    /v1/motions/{id}
  • `motion.rename`         → PATCH  /v1/motions/{id}   (custom only; preset → 422)
  • `motion.delete`         → DELETE /v1/motions/{id}   (soft delete)

Invocation model mirrors `app/mcp/tools/alias.py` (T-085) and
`app/mcp/tools/character.py` (T-084): each tool resolves the caller from the MCP
auth contextvar (`require_mcp_scopes` → `require_user_context`), opens
short-lived `AsyncSession`s (tools run inside the JSON-RPC dispatch loop, not a
FastAPI request scope), and calls the SAME `motion_service` layer the REST
routes use. Service-layer `AgentErrorException`s are translated to MCP
`ToolError`s carrying the identical AgentError envelope, and DTO assembly reuses
`build_motion_dto` / `build_motion_detail_dto` so the MCP wire shape can't drift
from `/v1/*`.

`motion.generate` follows the packaged-tool pattern: progress notifications per
phase (`queueing` / `running_i2v` / `finalizing`), a service-driven async task it
polls to completion, and a phase-tagged error envelope `{error, phase}` on
failure. Like `alias.add` (and unlike `character.create`) there is no half-built
session to abandon — a motion writes no row until the worker succeeds, so a
failed run leaves nothing behind.

Polymorphism: ONE tool, two target kinds. The agent's mental unit is "give a
visual a motion" — `target_type` is a parameter, not a tool distinction (per
T-083 §3). i2v is the longest task in Phase 1 (30–120s); the progress
notifications emitted here are the test object T-087 builds Last-Event-ID
resumability on top of (T-086 implements progress, not reconnect).

RAI / safety: a Veo RAI-filter miss surfaces as the worker's recorded
`MODEL_CONTENT_FILTERED` AgentError (T-051), reconstructed faithfully from
`task.error` here — so the agent sees a machine-readable `code` (not a generic
`MODEL_INVALID_REQUEST`) and can choose to retry / rephrase, exactly as the REST
surface does.
"""

from __future__ import annotations

import json
import logging
import uuid

from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_storage
from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ
from app.core.errors import (
    AgentError,
    AgentErrorException,
    auth_invalid_token,
)
from app.core.redis_client import get_arq_pool
from app.db.session import async_session_factory
from app.mcp.auth import require_mcp_scopes, require_user_context, translate_agent_errors
from app.mcp.registry import MCPTool, register
from app.mcp.schemas.motion import (
    MotionDeleteInput,
    MotionDeleteResult,
    MotionGenerateInput,
    MotionGenerateResult,
    MotionGetInput,
    MotionListForAliasInput,
    MotionListForBaseInput,
    MotionRenameInput,
)
from app.models.user import User
from app.schemas.motion import (
    MotionDetailResponse,
    MotionListResponse,
    MotionNameStr,
    MotionResponse,
)
from app.schemas.motion_builder import build_motion_detail_dto, build_motion_dto
from app.schemas.prompt import MotionParentType, MotionType
from app.services import motion_service

_logger = logging.getLogger(__name__)

# Phase label for `motion.generate`'s synchronous submit step. `queueing`
# covers enqueue + the validation `enqueue_motion` runs (parent resolution,
# name/description checks, dedup) — the only failures the tool itself surfaces
# now that it returns a handle instead of polling (T-087). The i2v run + any
# RAI-filter miss are observed by the agent via `task.get`.
_PHASE_QUEUEING = "queueing"


async def _load_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Load the calling user's row, or raise like /v1/*'s get_current_user.

    The user row can vanish between token resolution and this read (race with
    deletion). Mirror the REST surface: surface AUTH_INVALID_TOKEN rather than
    leaking account-existence state or letting None flow into a service call.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise auth_invalid_token()
    return user


def _phase_tool_error(phase: str, error: AgentError) -> ToolError:
    """Packaged-tool error envelope: the standard AgentError plus a sibling
    `phase` so the agent knows which step failed (ticket §async)."""
    return ToolError(json.dumps({"error": error.model_dump(mode="json"), "phase": phase}))


def _agent_error_from_unexpected(exc: BaseException) -> AgentError:
    """Wrap a non-AgentError infra failure (storage / DB error, …) into the
    standard envelope so a packaged-tool phase still surfaces a structured
    error (mirrors the worker's `_agent_error_from_exception`)."""
    return AgentError(
        code="INTERNAL_UNEXPECTED_ERROR",
        message="系統發生未預期錯誤",
        problem=f"Unhandled {type(exc).__name__} in motion.generate: {exc}",
        cause="Infra/runtime failure inside a packaged-tool phase (e.g. storage or DB).",
        fix="Retry; if persistent, inspect the api logs.",
        retryable=True,
    )


# ---------------------------------------------------------------------------
# CRUD 1:1 wraps
# ---------------------------------------------------------------------------


async def motion_list_for_base(base_id: uuid.UUID) -> MotionListResponse:
    """List a Base's active motions (`GET /v1/bases/{id}/motions`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            motions = await motion_service.list_motions_for_parent(
                db, user=user, parent_type="base", parent_id=base_id
            )
            return MotionListResponse(items=[build_motion_dto(m, storage) for m in motions])


async def motion_list_for_alias(alias_id: uuid.UUID) -> MotionListResponse:
    """List an Alias's active motions (`GET /v1/aliases/{id}/motions`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            motions = await motion_service.list_motions_for_parent(
                db, user=user, parent_type="alias", parent_id=alias_id
            )
            return MotionListResponse(items=[build_motion_dto(m, storage) for m in motions])


async def motion_get(motion_id: uuid.UUID) -> MotionDetailResponse:
    """Fetch one motion's detail (`GET /v1/motions/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            detail = await motion_service.get_motion_detail(db, user=user, motion_id=motion_id)
            return MotionDetailResponse(
                motion=build_motion_detail_dto(
                    detail.motion, storage, generation_log=detail.generation_log
                )
            )


async def motion_rename(motion_id: uuid.UUID, name: MotionNameStr) -> MotionResponse:
    """Rename a custom motion (`PATCH /v1/motions/{id}`).

    Preset motions are name-locked — the service raises
    `VALIDATION_PRESET_RENAME_FORBIDDEN`, surfaced here as a ToolError with the
    same envelope the REST 422 carries.
    """
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    storage = get_storage()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            motion = await motion_service.update_motion_name(
                db, user=user, motion_id=motion_id, new_name=name
            )
            return MotionResponse(motion=build_motion_dto(motion, storage))


async def motion_delete(motion_id: uuid.UUID) -> MotionDeleteResult:
    """Soft-delete a motion (`DELETE /v1/motions/{id}`)."""
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE)
    user_id = require_user_context(auth)
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            user = await _load_user(db, user_id)
            await motion_service.soft_delete_motion(db, user=user, motion_id=motion_id)
            return MotionDeleteResult(motion_id=motion_id)


# ---------------------------------------------------------------------------
# Packaged tool — motion.generate
# ---------------------------------------------------------------------------


async def motion_generate(
    target_type: MotionParentType,
    target_id: uuid.UUID,
    motion_type: MotionType,
    name: MotionNameStr,
    description: str | None = None,
) -> MotionGenerateResult:
    """Submit an i2v motion-generation job for a Base or Alias; return a handle.

    Non-blocking (T-087): enqueues the i2v task and returns
    `{task_id, motion_id, status}` immediately. The agent then polls
    `task.get(task_id)` until terminal — a Veo RAI-filter miss surfaces there as
    `status="failed"` with `error.code` = `MODEL_CONTENT_FILTERED` (T-051),
    machine-readable so the agent can retry / rephrase — and fetches the finished
    motion via `motion.get(motion_id)` on `completed`. The i2v work runs in the
    arq worker, independent of this MCP connection, so a dropped connection never
    loses it; the agent re-queries with the ids it already holds.

    Synchronous enqueue validation (parent not-found / 403, name/description
    validation, preset-slot or duplicate-name conflict) surfaces as a
    phase-tagged ToolError (`queueing`); no task is created in that case.

    `description` is required for `custom` motions and ignored for presets
    (the service normalises it to None and reads a static template).
    """
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ)
    user_id = require_user_context(auth)

    factory = async_session_factory()
    try:
        arq_pool = await get_arq_pool()
        async with factory() as db:
            user = await _load_user(db, user_id)
            enqueued = await motion_service.enqueue_motion(
                db,
                arq_pool,
                user=user,
                parent_type=target_type,
                parent_id=target_id,
                motion_type=motion_type,
                name=name,
                description=description,
            )
    except AgentErrorException as exc:
        raise _phase_tool_error(_PHASE_QUEUEING, exc.error) from exc
    except Exception as exc:  # noqa: BLE001 — infra failure → structured tool error
        raise _phase_tool_error(_PHASE_QUEUEING, _agent_error_from_unexpected(exc)) from exc

    return MotionGenerateResult(task_id=enqueued.task_id, motion_id=enqueued.motion_id)


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

MOTION_GENERATE = register(
    MCPTool(
        name="motion.generate",
        description=(
            "Submit an i2v motion-generation job for a Base or an Alias and return a "
            "handle immediately (non-blocking). Polymorphic — set target_type to "
            "base/alias. Use a preset motion_type (preset_wave/nod/gesture/happy/idle) "
            "or 'custom' with a description. Returns {task_id, motion_id}: poll "
            "task.get(task_id) until completed (a content-filter block surfaces there "
            "as failed + error.code=MODEL_CONTENT_FILTERED), then motion.get(motion_id) "
            "for the video. The job survives connection drops — re-query with task_id."
        ),
        # task:read stays required (and GET /v1/tasks stays bundled): the tool's
        # workflow is submit-then-poll, so a token that can submit must also be
        # able to track the task it created — fail fast at submit otherwise.
        scopes=[SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ],
        bundles=[
            "POST /v1/bases/{base_id}/motions",
            "POST /v1/aliases/{alias_id}/motions",
            "GET /v1/tasks/{task_id}",
        ],
        input_schema=MotionGenerateInput,
        output_schema=MotionGenerateResult,
        handler=motion_generate,
    )
)

MOTION_LIST_FOR_BASE = register(
    MCPTool(
        name="motion.list_for_base",
        description="List a Base's active motions.",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/bases/{base_id}/motions"],
        input_schema=MotionListForBaseInput,
        output_schema=MotionListResponse,
        handler=motion_list_for_base,
    )
)

MOTION_LIST_FOR_ALIAS = register(
    MCPTool(
        name="motion.list_for_alias",
        description="List an Alias's active motions.",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/aliases/{alias_id}/motions"],
        input_schema=MotionListForAliasInput,
        output_schema=MotionListResponse,
        handler=motion_list_for_alias,
    )
)

MOTION_GET = register(
    MCPTool(
        name="motion.get",
        description="Fetch one motion's full detail (incl. video URL + generation info).",
        scopes=[SCOPE_CHARACTER_READ],
        bundles=["GET /v1/motions/{motion_id}"],
        input_schema=MotionGetInput,
        output_schema=MotionDetailResponse,
        handler=motion_get,
    )
)

MOTION_RENAME = register(
    MCPTool(
        name="motion.rename",
        description="Rename a custom motion (preset motions are name-locked).",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["PATCH /v1/motions/{motion_id}"],
        input_schema=MotionRenameInput,
        output_schema=MotionResponse,
        handler=motion_rename,
    )
)

MOTION_DELETE = register(
    MCPTool(
        name="motion.delete",
        description="Soft-delete a motion.",
        scopes=[SCOPE_CHARACTER_WRITE],
        bundles=["DELETE /v1/motions/{motion_id}"],
        input_schema=MotionDeleteInput,
        output_schema=MotionDeleteResult,
        handler=motion_delete,
    )
)
