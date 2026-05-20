"""`task.*` MCP tools (T-088) — 1:1 wraps of `/v1/tasks/*`.

Three 1:1 wraps per `planning/agent-interface/endpoint-mcp-mapping.md` §2.5:

  • `task.get`    → `GET  /v1/tasks/{task_id}`      (scope `task:read`)
  • `task.list`   → `GET  /v1/tasks`                (scope `task:read`)
  • `task.cancel` → `POST /v1/tasks/{task_id}/cancel` (scope `task:cancel`)

`task.get` is ALSO the internal poll target the Wave B packaged tools
(T-084 / T-085 / T-086) call directly against the REST endpoint — that
internal use is NOT a separate registration; this module owns the 1:1
MCP tool only (ticket §"task.get 的特殊雙身分").

Invocation model: same-process, like the rest of `/mcp`. Each tool resolves
the caller from the MCP auth contextvar (`require_mcp_scopes` →
`require_user_context`), opens a short-lived `AsyncSession` (mirroring
`app/mcp/auth.py`, NOT a FastAPI `Depends(db_session)` — tools run inside the
JSON-RPC dispatch loop with no request scope), and calls the same
`task_service` / `task_repo` layer the REST routes use. Service-layer
`AgentErrorException`s (`not_found_task`, `conflict_task_already_terminal`,
...) are translated to MCP `ToolError`s carrying the identical AgentError
envelope via `translate_agent_errors()`.

Outputs reuse the `/v1/tasks/*` response envelopes so the MCP wire shape can't
drift from REST.
"""

from __future__ import annotations

import uuid

from app.auth.scopes import SCOPE_TASK_CANCEL, SCOPE_TASK_READ
from app.core.errors import not_found_task
from app.core.redis_client import get_arq_pool, get_redis
from app.db.session import async_session_factory
from app.mcp.auth import require_mcp_scopes, require_user_context, translate_agent_errors
from app.mcp.registry import MCPTool, register
from app.mcp.schemas.task import TaskCancelInput, TaskGetInput, TaskListInput
from app.repositories import task_repo
from app.schemas.task import (
    CancelTaskResponse,
    TaskDTO,
    TaskListResponse,
    TaskResponse,
    TaskStatus,
)
from app.services import task_service


async def task_get(task_id: uuid.UUID) -> TaskResponse:
    """Fetch one task by id, scoped to the calling user.

    Returns the same `{task: TaskDTO}` envelope as `GET /v1/tasks/{id}`,
    including `queue_position` while the task is queued. Unknown ids and
    other users' tasks both surface as `NOT_FOUND_TASK` (no ownership leak).
    """
    auth = require_mcp_scopes(SCOPE_TASK_READ)
    user_id = require_user_context(auth)
    arq_pool = await get_arq_pool()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            task = await task_repo.get_owned(db, task_id=task_id, user_id=user_id)
            if task is None:
                raise not_found_task()
            pos = (
                await task_service.queue_position(arq_pool, task.id)
                if task.status == "queued"
                else None
            )
            return TaskResponse(task=TaskDTO.from_model(task, queue_position=pos))


async def task_list(
    status: TaskStatus | None = None,
    limit: int = 50,
) -> TaskListResponse:
    """List the calling user's tasks, newest first.

    Optional `status` filters by lifecycle state; `limit` (1–200, default 50)
    caps the page. Queue positions are resolved in one bulk arq scan.
    """
    auth = require_mcp_scopes(SCOPE_TASK_READ)
    user_id = require_user_context(auth)
    arq_pool = await get_arq_pool()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            tasks = await task_service.list_user_tasks(
                db, user_id=user_id, status=status, limit=limit
            )
            queued_ids = [t.id for t in tasks if t.status == "queued"]
            positions = await task_service.queue_positions_bulk(arq_pool, queued_ids)
            items = [TaskDTO.from_model(t, queue_position=positions.get(t.id)) for t in tasks]
            return TaskListResponse(items=items)


async def task_cancel(task_id: uuid.UUID) -> CancelTaskResponse:
    """Request cancellation of a task, scoped to the calling user.

    Returns the task plus a `cancel_outcome` (`cancelled_immediately` /
    `cancel_pending` / `too_late_completed` / `too_late_failed`), matching
    `POST /v1/tasks/{id}/cancel`. A task that's already terminal-with-prior-
    cancel surfaces `CONFLICT_TASK_ALREADY_TERMINAL`.
    """
    auth = require_mcp_scopes(SCOPE_TASK_CANCEL)
    user_id = require_user_context(auth)
    redis = await get_redis()
    arq_pool = await get_arq_pool()
    with translate_agent_errors():
        factory = async_session_factory()
        async with factory() as db:
            result = await task_service.cancel_task(
                db,
                redis,
                arq_pool,
                task_id=task_id,
                user_id=user_id,
            )
            pos = (
                await task_service.queue_position(arq_pool, result.task.id)
                if result.task.status == "queued"
                else None
            )
            return CancelTaskResponse(
                task=TaskDTO.from_model(result.task, queue_position=pos),
                cancel_outcome=result.cancel_outcome,  # type: ignore[arg-type]
            )


TASK_GET = register(
    MCPTool(
        name="task.get",
        description=(
            "Fetch one async task by id (scoped to the calling user). Returns the "
            "task state + queue_position. Use to poll a task started by "
            "character.create / alias.add / motion.generate."
        ),
        scopes=[SCOPE_TASK_READ],
        bundles=["GET /v1/tasks/{task_id}"],
        input_schema=TaskGetInput,
        output_schema=TaskResponse,
        handler=task_get,
    )
)

TASK_LIST = register(
    MCPTool(
        name="task.list",
        description=(
            "List the calling user's async tasks (newest first), optionally "
            "filtered by status. For inspection / debugging."
        ),
        scopes=[SCOPE_TASK_READ],
        bundles=["GET /v1/tasks"],
        input_schema=TaskListInput,
        output_schema=TaskListResponse,
        handler=task_list,
    )
)

TASK_CANCEL = register(
    MCPTool(
        name="task.cancel",
        description=(
            "Request cancellation of an async task (scoped to the calling user). "
            "Returns a cancel_outcome describing whether the task was stopped, "
            "is pending cancellation, or already finished."
        ),
        scopes=[SCOPE_TASK_CANCEL],
        bundles=["POST /v1/tasks/{task_id}/cancel"],
        input_schema=TaskCancelInput,
        output_schema=CancelTaskResponse,
        handler=task_cancel,
    )
)
