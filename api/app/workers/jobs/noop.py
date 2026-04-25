"""`run_noop` — smoke-test job for the arq queue path.

T-013 only delivers the queue scaffold; the real job functions
(`run_create_checkpoint`, `run_create_alias`, etc.) land in T-014/T-017/
T-018. `run_noop` exists so we can verify in CI / docker-compose that:

  1. an enqueued job is picked up by the worker within ~30s
  2. the worker can advance a task row through `running → completed`
  3. the cancel-request short-circuit fires before any "real work"

It's intentionally written as a template the later jobs can copy.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.core.redis_client import publish_task_event
from app.repositories import task_repo

_logger = logging.getLogger(__name__)


_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


async def _safe_publish(redis: Any, task_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Publish an SSE event without ever raising back into the worker.

    Codex P1 (round 3): if a post-commit publish raises, the worker
    propagates the exception, arq retries, and the next run can flip
    a terminal task back into `running`. Treat the publish as advisory —
    log and swallow — so DB state remains the source of truth and
    monotonic across retries.
    """
    try:
        await publish_task_event(redis, task_id, payload)
    except Exception:  # noqa: BLE001 — best-effort; SSE clients reconcile via poll
        _logger.exception(
            "run_noop: redis publish failed for task %s payload=%s",
            task_id,
            payload.get("status"),
        )


async def run_noop(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Trivial worker handler.

    Reads the task row, honors a pre-pickup cancel OR an already-
    terminal state (idempotent retry — Codex P1 round 3), marks
    running → completed, and publishes SSE events with best-effort
    semantics. Real handlers will call AI clients between the
    running/completed transitions, but should follow this same shape.
    """
    session_factory = ctx["db_session_factory"]
    redis = ctx["redis"]
    task_uuid = uuid.UUID(task_id)

    async with session_factory() as db:
        task = await task_repo.get(db, task_uuid)
        if task is None:
            _logger.warning("run_noop: task %s not found, skipping", task_id)
            return {"task_id": task_id, "ok": False, "reason": "missing"}

        # Idempotent retry guard: if a previous run already advanced the
        # task to a terminal state (or the cancel route flipped it to
        # cancelled before pickup), do nothing rather than regressing the
        # row back to `running`. Same branch covers cooperative cancel.
        published_terminal_after_retry_cancel = False
        if task.cancel_requested or task.status in _TERMINAL_STATUSES:
            # Codex P1 round-4: if cancel arrived between a failed first
            # attempt (which committed `running`) and this retry, the
            # row is currently `running` with cancel_requested=True. We
            # must persist `cancelled` here or the retry returns ok and
            # the row stays non-terminal forever.
            if task.cancel_requested and task.status == "running":
                await task_repo.mark_cancelled(db, task_uuid)
                published_terminal_after_retry_cancel = True
            _logger.info(
                "run_noop: task %s already terminal (status=%s, cancel=%s); skipping",
                task_id,
                task.status,
                task.cancel_requested,
            )
            await db.commit()

            # Codex P2 round-5: SSE clients are likely still subscribed
            # holding `cancel_pending` from the original cancel call.
            # Publish the terminal `cancelled` event so they close the
            # stream without falling back to REST polling. Best-effort.
            if published_terminal_after_retry_cancel:
                await _safe_publish(
                    redis,
                    task_uuid,
                    {"status": "cancelled", "task_id": str(task_uuid)},
                )

            return {
                "task_id": task_id,
                "ok": False,
                "reason": "cancelled" if task.cancel_requested else task.status,
            }

        await task_repo.mark_running(db, task_uuid)
        await db.commit()

    await _safe_publish(
        redis,
        task_uuid,
        {"status": "running", "task_id": str(task_uuid)},
    )

    async with session_factory() as db:
        await task_repo.mark_completed(
            db,
            task_uuid,
            result={"noop": True},
        )
        await db.commit()

    await _safe_publish(
        redis,
        task_uuid,
        {
            "status": "completed",
            "result": {"noop": True},
            "task_id": str(task_uuid),
        },
    )
    return {"task_id": task_id, "ok": True}
