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

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_storage
from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ
from app.core.errors import (
    AgentError,
    AgentErrorException,
    auth_invalid_token,
    not_found_task,
)
from app.core.redis_client import get_arq_pool
from app.db.session import async_session_factory
from app.mcp.auth import require_mcp_scopes, require_user_context, translate_agent_errors
from app.mcp.progress import report_progress
from app.mcp.registry import MCPTool, register
from app.mcp.schemas.motion import (
    MotionDeleteInput,
    MotionDeleteResult,
    MotionGenerateInput,
    MotionGetInput,
    MotionListForAliasInput,
    MotionListForBaseInput,
    MotionRenameInput,
)
from app.models.user import User
from app.repositories import task_repo
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

# motion.generate polls the i2v task to completion. Interval keeps the loop
# responsive without hammering Postgres; the timeout stays under the nginx
# `/mcp` `proxy_read_timeout` (T-082, ≥180s) so the tool gives up cleanly
# rather than having the connection cut from under it. i2v is the longest
# Phase 1 task (30–120s observed) so the budget is the largest of the
# packaged tools — but still inside the proxy window.
_POLL_INTERVAL_S = 1.0
_POLL_TIMEOUT_S = 170.0

# Phase labels for the packaged `motion.generate` tool — sent as the progress
# `message` and the failure envelope's `phase`. `queueing` covers enqueue +
# the synchronous validation `enqueue_motion` runs (parent resolution,
# name/description checks, dedup); `running_i2v` covers the Veo call (where an
# RAI filter miss surfaces); `finalizing` covers the post-completion detail
# DTO assembly.
_PHASE_QUEUEING = "queueing"
_PHASE_RUNNING = "running_i2v"
_PHASE_FINALIZING = "finalizing"


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


def _agent_error_from_task(task: Any) -> AgentError:
    """Reconstruct the AgentError a failed i2v task stored in `task.error`.

    The faithful passthrough is what gives the agent a machine-readable
    `reason` (e.g. `MODEL_CONTENT_FILTERED` for a Veo RAI miss, T-051) instead
    of a generic envelope.
    """
    err = task.error
    if isinstance(err, dict):
        try:
            return AgentError(**err)
        except (TypeError, ValueError):
            pass
    return AgentError(
        code="INTERNAL_UNEXPECTED_ERROR",
        message="系統發生未預期錯誤",
        problem=f"Motion-generation task {task.id} failed without a structured error payload.",
        cause="Worker recorded a non-AgentError failure.",
        fix="Retry; if persistent, inspect the worker log.",
        retryable=True,
    )


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


async def _wait_for_motion_task(
    factory: Any,
    ctx: Context[Any, Any, Any] | None,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
) -> None:
    """Poll the i2v task to a terminal state, emitting `running_i2v` progress.

    Returns on `completed`. Raises `AgentErrorException` on `failed` (with the
    worker's recorded error — e.g. the RAI-filter `MODEL_CONTENT_FILTERED`), on
    `cancelled`, on a missing task row, or on timeout. Each poll opens a
    short-lived session so it observes the worker's committed writes (the
    worker runs in a separate process / event loop). The interval doubles as
    the heartbeat that keeps the agent's progress bar moving during the
    30–120s Veo call.
    """
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while True:
        async with factory() as db:
            task = await task_repo.get_owned(db, task_id=task_id, user_id=user_id)
        if task is None:
            raise not_found_task()
        prog = float(task.progress) if isinstance(task.progress, int | float) else 0.0
        await report_progress(
            ctx, progress=max(0.3, min(0.9, prog)), total=1.0, message=_PHASE_RUNNING
        )
        if task.status == "completed":
            return
        if task.status == "failed":
            raise AgentErrorException(_agent_error_from_task(task))
        if task.status == "cancelled":
            raise AgentErrorException(
                AgentError(
                    code="TASK_CANCELLED",
                    message="任務已取消",
                    problem=f"Motion-generation task {task_id} was cancelled before completion.",
                    cause="A concurrent task.cancel ran during motion.generate.",
                    fix="Retry motion.generate.",
                    retryable=True,
                )
            )
        if time.monotonic() >= deadline:
            raise AgentErrorException(
                AgentError(
                    code="MCP_TOOL_TIMEOUT",
                    message="生成逾時",
                    problem=(
                        f"motion.generate polled task {task_id} for "
                        f"{int(_POLL_TIMEOUT_S)}s without completion."
                    ),
                    cause="The i2v task is taking longer than the tool's wait budget.",
                    fix=f"The task may still finish — poll task.get with task_id={task_id}, or retry.",
                    retryable=True,
                )
            )
        await asyncio.sleep(_POLL_INTERVAL_S)


async def motion_generate(
    target_type: MotionParentType,
    target_id: uuid.UUID,
    motion_type: MotionType,
    name: MotionNameStr,
    description: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> MotionDetailResponse:
    """Generate a motion for a Base or Alias end-to-end and return it.

    Bundles motion enqueue + internal i2v task polling into one call across
    both target kinds. Emits a `notifications/progress` per phase (`queueing` →
    `running_i2v` → `finalizing`). On any failure a phase-tagged AgentError is
    raised; no motion row is written until the worker succeeds, so there is no
    half-built state to clean up.

    `description` is required for `custom` motions and ignored for presets
    (the service normalises it to None and reads a static template).
    """
    auth = require_mcp_scopes(SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ)
    user_id = require_user_context(auth)

    storage = get_storage()
    factory = async_session_factory()

    # ----- Phase 1: enqueue. `enqueue_motion` raises synchronous AgentErrors
    # (parent not-found / 403, name/description validation, preset-slot or
    # duplicate-name conflict) that all belong to the `queueing` phase.
    await report_progress(ctx, progress=0.1, total=1.0, message=_PHASE_QUEUEING)
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

    # ----- Phase 2: poll the i2v task to completion (Veo runs here; an RAI
    # miss surfaces as the worker's MODEL_CONTENT_FILTERED).
    try:
        await _wait_for_motion_task(factory, ctx, user_id=user_id, task_id=enqueued.task_id)
    except AgentErrorException as exc:
        raise _phase_tool_error(_PHASE_RUNNING, exc.error) from exc
    except Exception as exc:  # noqa: BLE001 — infra failure → structured tool error
        raise _phase_tool_error(_PHASE_RUNNING, _agent_error_from_unexpected(exc)) from exc

    # ----- Phase 3: assemble the finished motion's detail DTO (incl. video URL
    # + the generation subset).
    await report_progress(ctx, progress=0.95, total=1.0, message=_PHASE_FINALIZING)
    try:
        async with factory() as db:
            user = await _load_user(db, user_id)
            detail = await motion_service.get_motion_detail(
                db, user=user, motion_id=enqueued.motion_id
            )
            motion_dto = build_motion_detail_dto(
                detail.motion, storage, generation_log=detail.generation_log
            )
    except AgentErrorException as exc:
        raise _phase_tool_error(_PHASE_FINALIZING, exc.error) from exc
    except Exception as exc:  # noqa: BLE001 — infra failure → structured tool error
        raise _phase_tool_error(_PHASE_FINALIZING, _agent_error_from_unexpected(exc)) from exc

    await report_progress(ctx, progress=1.0, total=1.0, message="done")
    return MotionDetailResponse(motion=motion_dto)


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

MOTION_GENERATE = register(
    MCPTool(
        name="motion.generate",
        description=(
            "Generate a motion video for a Base or an Alias end-to-end: enqueue the "
            "i2v job, run it, and return the finished motion (with its video URL). "
            "Polymorphic — set target_type to base/alias. Use a preset motion_type "
            "(preset_wave/nod/gesture/happy/idle) or 'custom' with a description. "
            "Blocks until done (~30-120s), emitting progress per phase."
        ),
        scopes=[SCOPE_CHARACTER_WRITE, SCOPE_TASK_READ],
        bundles=[
            "POST /v1/bases/{base_id}/motions",
            "POST /v1/aliases/{alias_id}/motions",
            "GET /v1/tasks/{task_id}",
        ],
        input_schema=MotionGenerateInput,
        output_schema=MotionDetailResponse,
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
