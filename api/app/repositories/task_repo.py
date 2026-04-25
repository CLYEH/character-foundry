"""Pure DB ops for the `tasks` table.

Kept separate from `services.task_service` so the service layer can compose
DB + Redis + arq side effects without this file ever importing them. Worker
job handlers also import this module directly to advance task state.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task


async def get(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    return await db.get(Task, task_id)


async def get_owned(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Task | None:
    """Fetch a task scoped to an owner. Returns None for both
    "doesn't exist" and "exists but belongs to a different user" so the
    cancel/lookup paths can collapse to a single 404."""
    task = await db.get(Task, task_id)
    if task is None or task.user_id != user_id:
        return None
    return task


async def get_owned_for_update(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Task | None:
    """Same as `get_owned` but takes a row-level lock for the duration of
    the surrounding transaction. Used by cancel where we need to read +
    transition state in one TX to avoid racing with the worker.

    `user_id` is part of the WHERE clause (not just a post-fetch check)
    so cross-user requests don't briefly hold a `FOR UPDATE` lock on the
    real owner's row before returning 404 (Codex P1 review).
    """
    stmt = select(Task).where(Task.id == task_id, Task.user_id == user_id).with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_for_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    status: str | None = None,
    limit: int = 50,
) -> Sequence[Task]:
    stmt = select(Task).where(Task.user_id == user_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    stmt = stmt.order_by(Task.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


async def insert(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_type: str,
    input_payload: dict[str, Any],
    estimated_duration_ms: int | None,
) -> Task:
    task = Task(
        user_id=user_id,
        task_type=task_type,
        status="queued",
        input_payload=input_payload,
        estimated_duration_ms=estimated_duration_ms,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


async def mark_running(db: AsyncSession, task_id: uuid.UUID) -> None:
    task = await db.get(Task, task_id)
    if task is None:
        return
    task.status = "running"
    task.started_at = datetime.now(UTC)


async def transition_queued_to_running(db: AsyncSession, task_id: uuid.UUID) -> bool:
    """Atomic CAS: `queued AND NOT cancel_requested` → `running`.

    Worker handlers must use this (instead of `mark_running`) for the
    pre-running transition so a cancel that committed between the
    handler's initial read and its update can't be overwritten —
    Codex P1 round-6 race. The plain `mark_running` issues an
    unconditional UPDATE that ignores the current DB state and would
    regress a freshly-cancelled row back to `running`.

    Returns True iff the row transitioned. False means cancel won the
    race, the row is already past `queued`, or the task is gone — the
    caller should re-read state and decide what to do (skip / mark
    cancelled / fail).
    """
    stmt = (
        update(Task)
        .where(
            Task.id == task_id,
            Task.status == "queued",
            Task.cancel_requested.is_(False),
        )
        .values(status="running", started_at=func.now())
        .returning(Task.id)
        .execution_options(synchronize_session=False)
    )
    # `RETURNING id` lets us check transition success via the public
    # typed `Result.scalar_one_or_none()` API instead of poking
    # `rowcount` (which lives on `CursorResult` and trips mypy on the
    # broader `Result[...]` return type of AsyncSession.execute).
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def mark_completed(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    task = await db.get(Task, task_id)
    if task is None:
        return
    task.status = "completed"
    task.entity_type = entity_type
    task.entity_id = entity_id
    task.result = result
    task.completed_at = datetime.now(UTC)
    task.progress = 1.0


async def mark_failed(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    error: dict[str, Any],
) -> None:
    task = await db.get(Task, task_id)
    if task is None:
        return
    task.status = "failed"
    task.error = error
    task.completed_at = datetime.now(UTC)


async def mark_cancelled(db: AsyncSession, task_id: uuid.UUID) -> None:
    task = await db.get(Task, task_id)
    if task is None:
        return
    task.status = "cancelled"
    if task.completed_at is None:
        task.completed_at = datetime.now(UTC)


async def fetch_recent_durations_ms(
    db: AsyncSession,
    *,
    task_type: str,
    limit: int = 50,
) -> list[int]:
    """Return durations (ms) of the most recent successful tasks of this
    type. Used by the duration estimator. We use the tasks table itself
    (not GenerationLog) so this stays useful before T-014 lands.
    """
    stmt = (
        select(Task.started_at, Task.completed_at)
        .where(
            Task.task_type == task_type,
            Task.status == "completed",
            Task.started_at.is_not(None),
            Task.completed_at.is_not(None),
        )
        .order_by(Task.completed_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    durations: list[int] = []
    for started_at, completed_at in result.all():
        if started_at is None or completed_at is None:
            continue
        delta_ms = int((completed_at - started_at).total_seconds() * 1000)
        if delta_ms >= 0:
            durations.append(delta_ms)
    return durations
