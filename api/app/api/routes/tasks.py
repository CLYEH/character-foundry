"""`/v1/tasks/*` — async task lifecycle endpoints (T-013).

Surface:
- GET  /v1/tasks/{id}            — Task DTO (with queue_position if queued)
- GET  /v1/tasks/{id}/stream     — SSE: initial state + Redis fan-out
- POST /v1/tasks/{id}/cancel     — 4 cancel outcomes (api-shape §5.5)
- GET  /v1/tasks                 — list caller's tasks (?status, ?limit)

Notes:
- SSE handler subscribes to Redis BEFORE reading initial state so a
  worker event published between the read and subscribe can't be lost.
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

from app.api.deps import db_session, get_current_user
from app.core.errors import not_found_task
from app.core.redis_client import (
    get_arq_pool,
    get_redis,
    task_channel,
)
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


async def _build_dto(
    task: Task,
    arq_pool: ArqRedis,
) -> TaskDTO:
    """Build a TaskDTO, resolving queue_position from arq when status='queued'."""
    position: int | None = None
    if task.status == "queued":
        position = await task_service.queue_position(arq_pool, task.id)
    return TaskDTO.from_model(task, queue_position=position)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
) -> TaskResponse:
    task = await task_repo.get_owned(db, task_id=task_id, user_id=user.id)
    if task is None:
        raise not_found_task()
    return TaskResponse(task=await _build_dto(task, arq_pool))


@router.post("/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
) -> CancelTaskResponse:
    result = await task_service.cancel_task(
        db,
        redis,
        arq_pool,
        task_id=task_id,
        user_id=user.id,
    )
    return CancelTaskResponse(
        task=await _build_dto(result.task, arq_pool),
        cancel_outcome=result.cancel_outcome,  # type: ignore[arg-type]
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    db: Annotated[AsyncSession, Depends(db_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TaskListResponse:
    tasks = await task_service.list_user_tasks(db, user_id=user.id, status=status, limit=limit)
    items = [await _build_dto(t, arq_pool) for t in tasks]
    return TaskListResponse(items=items)


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


def _sse_format(payload: str) -> str:
    return f"data: {payload}\n\n"


async def _stream_task_events(
    task: Task,
    redis: Redis,
) -> AsyncIterator[str]:
    """Yield SSE-formatted strings for a single task.

    Order matters: subscribe BEFORE reading initial state so we don't
    miss an event the worker publishes between the DB read and the
    subscription. After yielding the initial state we drain any messages
    that arrived during the subscribe-window.
    """
    pubsub = redis.pubsub()
    channel = task_channel(task.id)
    await pubsub.subscribe(channel)
    try:
        # Initial state snapshot from DB. If terminal, we close right
        # after — no point holding the connection open.
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
            # `pubsub.get_message(timeout=...)` blocks up to `timeout` seconds
            # for the next message, returning None on timeout. Don't wrap with
            # `asyncio.wait_for` — get_message returns None immediately when
            # no message is queued, which would spin the loop without ever
            # firing the wait_for timeout.
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=_SSE_HEARTBEAT_SECONDS,
            )
            if msg is None:
                # No event arrived inside the heartbeat window — keep the
                # connection warm so proxies (nginx etc.) don't reap it.
                # Comments are valid SSE noise per the EventSource spec.
                yield ": keepalive\n\n"
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
    db: Annotated[AsyncSession, Depends(db_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    task = await task_repo.get_owned(db, task_id=task_id, user_id=user.id)
    if task is None:
        raise not_found_task()

    return StreamingResponse(
        _stream_task_events(task, redis),
        media_type="text/event-stream",
        headers={
            # Tell nginx to flush each chunk as it arrives.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
