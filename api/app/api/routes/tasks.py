"""`/v1/tasks/*` — async task lifecycle endpoints (T-013).

Surface:
- GET  /v1/tasks/{id}            — Task DTO (with queue_position if queued)
- GET  /v1/tasks/{id}/stream     — SSE: initial state + Redis fan-out
- POST /v1/tasks/{id}/cancel     — 4 cancel outcomes (api-shape §5.5)
- GET  /v1/tasks                 — list caller's tasks (?status, ?limit)

Notes:
- SSE handler subscribes to Redis BEFORE reading initial state so a
  worker event published between the read and subscribe can't be lost.
- SSE handler does NOT use `Depends(db_session)` — yield-based deps
  hold the DB connection until the response finishes, which for an
  open SSE stream is "until the client disconnects". We open + close
  short-lived sessions for each DB read instead.
- Cancel state transitions go through `task_service.cancel_task` which
  takes a row-level lock for the duration of the TX.
- We use `text/event-stream` + `X-Accel-Buffering: no` so nginx (T-005
  reverse proxy) doesn't buffer the stream into chunks.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_current_user_no_pin
from app.auth.scopes import (
    SCOPE_TASK_CANCEL,
    SCOPE_TASK_READ,
    require_scope,
    require_scope_no_pin,
)
from app.core.errors import not_found_task
from app.core.redis_client import (
    get_arq_pool,
    get_redis,
    task_channel,
)
from app.db.session import async_session_factory
from app.models.task import Task
from app.models.user import User
from app.repositories import task_repo
from app.schemas.task import (
    CancelTaskResponse,
    TaskDTO,
    TaskListResponse,
    TaskResponse,
)
from app.services import task_service

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])

_SSE_HEARTBEAT_SECONDS = 15.0
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _build_dto(
    task: Task,
    *,
    queue_position: int | None = None,
) -> TaskDTO:
    """Synchronous DTO builder.

    Queue position must be resolved by the caller — for single-task
    endpoints we call `task_service.queue_position`; for the list
    endpoint we call `task_service.queue_positions_bulk` once and
    thread the result in (Codex P2 round 3: avoid O(N×queue) scans).
    """
    return TaskDTO.from_model(task, queue_position=queue_position)


async def _resolve_queue_position(arq_pool: ArqRedis, task: Task) -> int | None:
    if task.status != "queued":
        return None
    return await task_service.queue_position(arq_pool, task.id)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
    _: None = Depends(require_scope(SCOPE_TASK_READ)),
) -> TaskResponse:
    task = await task_repo.get_owned(db, task_id=task_id, user_id=user.id)
    if task is None:
        raise not_found_task()
    pos = await _resolve_queue_position(arq_pool, task)
    return TaskResponse(task=_build_dto(task, queue_position=pos))


@router.post("/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
    _: None = Depends(require_scope(SCOPE_TASK_CANCEL)),
) -> CancelTaskResponse:
    result = await task_service.cancel_task(
        db,
        redis,
        arq_pool,
        task_id=task_id,
        user_id=user.id,
    )
    pos = await _resolve_queue_position(arq_pool, result.task)
    return CancelTaskResponse(
        task=_build_dto(result.task, queue_position=pos),
        cancel_outcome=result.cancel_outcome,  # type: ignore[arg-type]
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    db: Annotated[AsyncSession, Depends(db_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    _: None = Depends(require_scope(SCOPE_TASK_READ)),
) -> TaskListResponse:
    tasks = await task_service.list_user_tasks(db, user_id=user.id, status=status, limit=limit)
    queued_ids = [t.id for t in tasks if t.status == "queued"]
    positions = await task_service.queue_positions_bulk(arq_pool, queued_ids)
    items = [_build_dto(t, queue_position=positions.get(t.id)) for t in tasks]
    return TaskListResponse(items=items)


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


def _sse_format(payload: str) -> str:
    return f"data: {payload}\n\n"


async def _read_initial_task(
    *,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Task | None:
    """Fetch the task in a short-lived DB session and return it.

    Pulled out so `_stream_task_events` can read the initial snapshot
    without holding a long-lived session: each call creates a session,
    reads the row, and closes the session before any awaitable that
    blocks on Redis. Returns None if the task is gone (cleanup, etc.).
    """
    factory = async_session_factory()
    async with factory() as db:
        return await task_repo.get_owned(db, task_id=task_id, user_id=user_id)


async def _stream_task_events(
    *,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    redis: Redis,
) -> AsyncIterator[str]:
    """Yield SSE-formatted strings for a single task.

    Order matters (Codex P1 fix): subscribe BEFORE reading initial state
    so a worker event published between the route's auth check and our
    subscribe is buffered on the channel rather than missed. We then
    fetch the snapshot from a short-lived DB session — keeping the
    session alive through the stream would pin a connection per
    listener (Codex P1 #2).
    """
    pubsub = redis.pubsub()
    channel = task_channel(task_id)
    await pubsub.subscribe(channel)
    try:
        task = await _read_initial_task(task_id=task_id, user_id=user_id)
        if task is None:
            # Task vanished between the route's existence check and our
            # initial fetch (cleanup cron, owner deletion). Close
            # without yielding — the route already 404'd the obvious
            # missing-task case; this branch is racey enough that a
            # silent close is the right surface.
            return

        initial_payload = {
            "status": task.status,
            "progress": task.progress,
            "cancel_requested": task.cancel_requested,
            "result": task.result,
            "error": task.error,
            "task_id": str(task.id),
        }
        yield _sse_format(json.dumps(initial_payload, default=str))

        if task.status in _TERMINAL_STATUSES:
            return

        while True:
            # `pubsub.get_message(timeout=...)` blocks up to `timeout`
            # seconds, returning None only on real timeout. We don't pass
            # `ignore_subscribe_messages=True` because redis-py reports
            # subscribe-acks back as None too — that would falsely trip
            # the heartbeat path the first iteration after subscribe.
            # Filter on `msg["type"]` instead so subscribe-acks are
            # dropped silently and only data events flow through.
            msg = await pubsub.get_message(timeout=_SSE_HEARTBEAT_SECONDS)
            if msg is None:
                # No event arrived inside the heartbeat window — keep the
                # connection warm so proxies (nginx etc.) don't reap it.
                # Comments are valid SSE noise per the EventSource spec.
                yield ": keepalive\n\n"
                continue
            if msg.get("type") not in ("message", "pmessage"):
                # subscribe / psubscribe / unsubscribe / etc. — control
                # frames, never user data; skip without a heartbeat.
                continue

            data = msg.get("data")
            if not isinstance(data, str):
                continue

            yield _sse_format(data)

            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                _logger.warning("SSE: dropped non-JSON payload on %s", channel)
                continue

            if parsed.get("status") in _TERMINAL_STATUSES:
                return
    finally:
        try:
            await pubsub.unsubscribe(channel)
        finally:
            # redis-py types `aclose` as untyped in some stub versions; the
            # method exists on PubSub at runtime in 5.0+.
            await pubsub.aclose()  # type: ignore[no-untyped-call]


@router.get("/{task_id}/stream")
async def stream_task(
    task_id: uuid.UUID,
    redis: Annotated[Redis, Depends(get_redis)],
    user: Annotated[User, Depends(get_current_user_no_pin)],
    _: None = Depends(require_scope_no_pin(SCOPE_TASK_READ)),
) -> StreamingResponse:
    """Open an SSE stream for a task.

    Auth uses `get_current_user_no_pin` (Codex P1 round-4): the
    standard `get_current_user` chains through `Depends(db_session)`,
    which would still pin a DB connection for the entire stream
    lifetime even though THIS function doesn't take `db` directly.
    The no-pin variant opens its own short-lived session for the
    user lookup and closes it before this handler runs.

    The existence + ownership check below also uses a short-lived
    session, closed before we hand the response off to
    StreamingResponse — so no DB connection is held during the
    actual streaming.
    """
    factory = async_session_factory()
    async with factory() as db:
        task = await task_repo.get_owned(db, task_id=task_id, user_id=user.id)
        if task is None:
            raise not_found_task()

    return StreamingResponse(
        _stream_task_events(task_id=task_id, user_id=user.id, redis=redis),
        media_type="text/event-stream",
        headers={
            # Tell nginx to flush each chunk as it arrives.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
