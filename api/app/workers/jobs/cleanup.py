"""`cleanup_terminal_tasks` — hourly cron that purges stale terminal tasks.

UX retention rule (DECISIONS / planning/data/lifecycle.md): completed,
failed, and cancelled tasks live for 24h after `completed_at`. Past that
they're removed so /v1/tasks listings stay fresh and the table stays
small. We do this server-side rather than via Postgres TTL extension to
keep the dependency surface to vanilla pg15.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, text

from app.models.task import Task

_logger = logging.getLogger(__name__)


async def cleanup_terminal_tasks(ctx: dict[str, Any]) -> dict[str, int]:
    """Delete tasks where `status` is terminal and `completed_at` is older
    than 24 hours. Returns `{"deleted": <count>}` for the worker log.
    """
    session_factory = ctx["db_session_factory"]
    async with session_factory() as db:
        stmt = (
            delete(Task)
            .where(
                Task.status.in_(("completed", "failed", "cancelled")),
                Task.completed_at.is_not(None),
                Task.completed_at < text("NOW() - INTERVAL '24 hours'"),
            )
            .execution_options(synchronize_session=False)
        )
        result = await db.execute(stmt)
        await db.commit()

    deleted = result.rowcount or 0
    if deleted:
        _logger.info("cleanup_terminal_tasks: deleted %d task rows", deleted)
    return {"deleted": deleted}
