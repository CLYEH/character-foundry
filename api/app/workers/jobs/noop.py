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


async def run_noop(ctx: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Trivial worker handler.

    Reads the task row, honors a pre-pickup cancel, marks running →
    completed, and publishes the corresponding SSE events. Real handlers
    will call AI clients between the running/completed transitions.
    """
    session_factory = ctx["db_session_factory"]
    redis = ctx["redis"]
    task_uuid = uuid.UUID(task_id)

    async with session_factory() as db:
        task = await task_repo.get(db, task_uuid)
        if task is None:
            _logger.warning("run_noop: task %s not found, skipping", task_id)
            return {"task_id": task_id, "ok": False, "reason": "missing"}

        # Cooperative cancel BEFORE we flip to running. Cancel-while-queued
        # already sets status=cancelled, so we just respect that and exit.
        if task.cancel_requested or task.status == "cancelled":
            _logger.info("run_noop: task %s cancelled before pickup", task_id)
            await db.commit()
            return {"task_id": task_id, "ok": False, "reason": "cancelled"}

        await task_repo.mark_running(db, task_uuid)
        await db.commit()

    await publish_task_event(
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

    await publish_task_event(
        redis,
        task_uuid,
        {
            "status": "completed",
            "result": {"noop": True},
            "task_id": str(task_uuid),
        },
    )
    return {"task_id": task_id, "ok": True}
