"""Async task orchestration: create + queue, cancel, estimate duration.

This file owns the business rules around the `tasks` table — anything that
touches DB + Redis + arq together belongs here. Worker job handlers and
route handlers both call into this layer; lower-level callers (tests,
T-016, etc.) can use `task_repo` directly.

Cancel is the load-bearing piece: we transition state in a single
`SELECT ... FOR UPDATE` transaction (planning/backend/task-queue.md §7) so
worker pickup, worker completion, and user cancel can't race on the same
row. The 4 cancel outcomes follow planning/backend/api-shape.md §5.5.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from arq.connections import ArqRedis
from arq.jobs import Job
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    conflict_task_already_terminal,
    not_found_task,
)
from app.core.redis_client import (
    publish_task_cancel,
    publish_task_event,
)
from app.models.task import Task
from app.repositories import task_repo

_logger = logging.getLogger(__name__)


async def _abort_queued_arq_job(arq_pool: ArqRedis, job_id: str) -> None:
    """Best-effort: drop a queued job from arq's queue.

    arq 0.28's `ArqRedis` exposes the abort surface via `Job.abort()`,
    not on the pool itself. We hide that behind this helper so test
    fakes can monkey-patch `abort_job` on the pool object without the
    service needing to know which kind of pool it has. The cooperative
    `cancel_requested` check in worker handlers covers any leak from a
    failed abort here.
    """
    test_abort = getattr(arq_pool, "abort_job", None)
    if callable(test_abort):
        await test_abort(job_id)
        return
    job = Job(job_id, redis=arq_pool)
    # timeout=0.0 → fire the abort signal but don't block waiting for
    # the worker to confirm. We just want the queue entry removed.
    await job.abort(timeout=0.0)


# ---------------------------------------------------------------------------
# Duration estimation
# ---------------------------------------------------------------------------


# Hardcoded fallbacks (planning/backend/task-queue.md §4.2). Used until we
# have ≥5 historical samples for the task type.
DEFAULT_ESTIMATES_MS: dict[str, int] = {
    "create_checkpoint": 15_000,
    "create_alias": 20_000,
    "create_motion": 60_000,
    "export_zip": 10_000,
    "copy_character": 3_000,
}

# Minimum sample count before we trust the historical p50.
_HISTORICAL_MIN_SAMPLES = 5
_HISTORICAL_LIMIT = 50


async def estimate_duration_ms(
    db: AsyncSession,
    *,
    task_type: str,
    input_payload: dict[str, Any],
) -> int:
    """Return the estimated duration in ms for a fresh task.

    Strategy: median of the last 50 successful tasks of this type if we
    have ≥5 samples, else hardcoded default. `export_zip` adds 2s per
    motion so heavy exports report a longer estimate up-front.
    """
    durations = await task_repo.fetch_recent_durations_ms(
        db, task_type=task_type, limit=_HISTORICAL_LIMIT
    )
    if len(durations) >= _HISTORICAL_MIN_SAMPLES:
        return int(statistics.median(durations))

    if task_type == "export_zip":
        try:
            motion_count = int(input_payload.get("motion_count", 0))
        except (TypeError, ValueError):
            motion_count = 0
        return DEFAULT_ESTIMATES_MS["export_zip"] + 2_000 * max(motion_count, 0)
    return DEFAULT_ESTIMATES_MS.get(task_type, 30_000)


# ---------------------------------------------------------------------------
# Create + enqueue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreatedTask:
    task: Task
    job_function: str  # arq function name we enqueued (`run_<task_type>`)


async def create_task(
    db: AsyncSession,
    arq_pool: ArqRedis,
    *,
    user_id: uuid.UUID,
    task_type: str,
    input_payload: dict[str, Any],
) -> CreatedTask:
    """Insert a queued task row and enqueue the matching arq job.

    Internal helper — not bound to any HTTP route. T-014/T-017/T-018 will
    call this from their respective domain services.

    The arq job id is set to the task uuid so cancel can target it
    deterministically without a separate mapping.

    If `arq_pool.enqueue_job` raises (e.g. Redis is down), the task row
    is committed first then immediately marked `failed` with
    `QUEUE_UNAVAILABLE` so the caller sees a real error rather than a
    permanently-stuck `queued` orphan (Codex P2 review).
    """
    estimated = await estimate_duration_ms(db, task_type=task_type, input_payload=input_payload)
    task = await task_repo.insert(
        db,
        user_id=user_id,
        task_type=task_type,
        input_payload=input_payload,
        estimated_duration_ms=estimated,
    )
    await db.commit()
    await db.refresh(task)

    job_function = f"run_{task_type}"
    try:
        await arq_pool.enqueue_job(
            job_function,
            task_id=str(task.id),
            _job_id=str(task.id),
        )
    except Exception as exc:
        _logger.exception(
            "create_task: enqueue_job failed for task %s; marking row failed",
            task.id,
        )
        await task_repo.mark_failed(
            db,
            task.id,
            error={
                "code": "QUEUE_UNAVAILABLE",
                "message": "任務佇列暫時不可用，請稍後再試",
                "problem": "arq enqueue_job raised; the task row has been "
                "marked failed to avoid a stuck queued orphan.",
                "cause": "Redis or the arq worker pool is unreachable.",
                "fix": "Retry shortly. If the issue persists, check "
                "infra/redis status and the worker process.",
                "retryable": True,
            },
        )
        await db.commit()
        raise exc
    return CreatedTask(task=task, job_function=job_function)


# ---------------------------------------------------------------------------
# Cancel — single-TX state transition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CancelResult:
    task: Task
    cancel_outcome: str


async def cancel_task(
    db: AsyncSession,
    redis: Redis,
    arq_pool: ArqRedis,
    *,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
) -> CancelResult:
    """Atomically transition a task to cancel/cancel-pending.

    Outcomes (planning/backend/api-shape.md §5.5):
    - cancelled_immediately: queued → cancelled (no worker has touched it)
    - cancel_pending: running → cancel_requested=True (cooperative)
    - too_late_completed/too_late_failed: terminal-without-prior-cancel
      (the worker beat the cancel call)

    Raises CONFLICT_TASK_ALREADY_TERMINAL when the task is already
    cancelled, OR when it's terminal AND a previous cancel was already
    acknowledged (the user is double-cancelling). Raises NOT_FOUND_TASK
    for unknown / cross-user task ids.
    """
    task = await task_repo.get_owned_for_update(db, task_id=task_id, user_id=user_id)
    if task is None:
        raise not_found_task()

    now = datetime.now(UTC)
    outcome: str

    if task.status == "queued":
        task.status = "cancelled"
        task.cancel_requested = True
        task.cancel_requested_at = now
        task.completed_at = now
        outcome = "cancelled_immediately"
    elif task.status == "running":
        # If a prior cancel was already acknowledged we still report
        # cancel_pending — the worker may still be in-flight, and the
        # client just needs to keep listening.
        if not task.cancel_requested:
            task.cancel_requested = True
            task.cancel_requested_at = now
        outcome = "cancel_pending"
    elif task.status == "completed":
        if task.cancel_requested:
            raise conflict_task_already_terminal()
        outcome = "too_late_completed"
    elif task.status == "failed":
        if task.cancel_requested:
            raise conflict_task_already_terminal()
        outcome = "too_late_failed"
    else:  # 'cancelled'
        raise conflict_task_already_terminal()

    await db.commit()
    await db.refresh(task)

    # Side effects after commit must be best-effort (Codex P2 review):
    # the DB is the source of truth for cancel state, and propagating
    # a Redis publish error as a 500 would surface "failed cancel" to
    # the client even though the task is already cancelled in the DB.
    # SSE listeners reconcile via the next poll / refresh; the worker
    # also re-checks `cancel_requested` cooperatively.
    if outcome == "cancelled_immediately":
        try:
            await _abort_queued_arq_job(arq_pool, str(task_id))
        except Exception:  # noqa: BLE001 — best-effort; cooperative check covers this
            _logger.exception(
                "cancel: arq abort failed; cooperative worker check will catch this on pickup",
            )
        try:
            await publish_task_event(
                redis,
                task_id,
                {"status": "cancelled", "task_id": str(task_id)},
            )
        except Exception:  # noqa: BLE001 — best-effort; SSE clients reconcile via poll
            _logger.exception(
                "cancel: redis publish failed for cancelled_immediately on task %s",
                task_id,
            )
    elif outcome == "cancel_pending":
        try:
            await publish_task_cancel(redis, task_id)
        except Exception:  # noqa: BLE001 — best-effort
            _logger.exception("cancel: redis publish_task_cancel failed for task %s", task_id)
        try:
            await publish_task_event(
                redis,
                task_id,
                {
                    "status": "running",
                    "cancel_requested": True,
                    "task_id": str(task_id),
                },
            )
        except Exception:  # noqa: BLE001 — best-effort
            _logger.exception(
                "cancel: redis publish_task_event failed for cancel_pending on task %s",
                task_id,
            )

    return CancelResult(task=task, cancel_outcome=outcome)


# ---------------------------------------------------------------------------
# Read helpers (queue position via arq pool)
# ---------------------------------------------------------------------------


async def queue_position(arq_pool: ArqRedis, task_id: uuid.UUID) -> int | None:
    """Return 1-based queue position for a task, or None if it isn't
    queued (already running or unknown). Cheap because arq stores queued
    job ids in Redis sorted sets; it's still O(N) so callers should only
    invoke when status='queued'.
    """
    try:
        queued_jobs = await arq_pool.queued_jobs()
    except Exception:  # noqa: BLE001 — Redis hiccup shouldn't fail the GET
        _logger.exception("queue_position: arq queued_jobs() failed")
        return None
    target = str(task_id)
    for i, job in enumerate(queued_jobs, start=1):
        if job.job_id == target:
            return i
    return None


async def list_user_tasks(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    status: str | None = None,
    limit: int = 50,
) -> Sequence[Task]:
    return await task_repo.list_for_user(db, user_id=user_id, status=status, limit=limit)
