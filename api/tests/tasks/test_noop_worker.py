"""Tests for the `run_noop` worker handler and the cleanup cron.

`run_noop` is exercised directly with a synthetic ctx — no live arq
process needed. Cleanup is exercised by inserting tasks with old/new
`completed_at` and asserting the right rows survive.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.task import Task
from app.repositories import task_repo
from app.services import task_service
from app.workers.jobs.cleanup import cleanup_terminal_tasks
from app.workers.jobs.noop import run_noop
from tests.tasks.conftest import FakeArqPool


def _factory_for(database_url: str) -> Any:
    engine = create_async_engine(database_url, future=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.mark.asyncio
async def test_run_noop_advances_task_to_completed(
    database_url: str,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    engine, factory = _factory_for(database_url)
    try:
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_alias",
                input_payload={},
            )
        result = await run_noop(
            {"db_session_factory": factory, "redis": fake_redis},
            str(created.task.id),
        )
        assert result == {"task_id": str(created.task.id), "ok": True}

        async with factory() as db:
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            assert row.status == "completed"
            assert row.started_at is not None
            assert row.completed_at is not None
            assert row.result == {"noop": True}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_noop_skips_pre_cancelled_task(
    database_url: str,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    """If cancel_requested is set before pickup, run_noop must NOT flip
    status to running — the cancel route already wrote status='cancelled'."""
    engine, factory = _factory_for(database_url)
    try:
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_alias",
                input_payload={},
            )
            # Mark cancelled in DB — same shape cancel endpoint produces.
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            row.status = "cancelled"
            row.cancel_requested = True
            row.cancel_requested_at = datetime.now(UTC)
            row.completed_at = datetime.now(UTC)
            await db.commit()

        result = await run_noop(
            {"db_session_factory": factory, "redis": fake_redis},
            str(created.task.id),
        )
        assert result["ok"] is False
        assert result["reason"] == "cancelled"

        async with factory() as db:
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            assert row.status == "cancelled"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_terminal_tasks_purges_only_old_terminal_rows(
    database_url: str,
    seeded_user: dict[str, Any],
) -> None:
    """Insert one row of each (status × age) combination and assert
    cleanup deletes exactly the terminal-AND-old ones.
    """
    engine, factory = _factory_for(database_url)
    try:
        old = datetime.now(UTC) - timedelta(hours=25)
        recent = datetime.now(UTC) - timedelta(hours=1)

        survives_ids: set[uuid.UUID] = set()
        purged_ids: set[uuid.UUID] = set()

        async with factory() as db:
            # Old completed → purge
            t1 = Task(
                id=uuid.uuid4(),
                user_id=seeded_user["id"],
                task_type="create_alias",
                status="completed",
                input_payload={},
                completed_at=old,
            )
            # Old failed → purge
            t2 = Task(
                id=uuid.uuid4(),
                user_id=seeded_user["id"],
                task_type="create_alias",
                status="failed",
                input_payload={},
                completed_at=old,
            )
            # Old cancelled → purge
            t3 = Task(
                id=uuid.uuid4(),
                user_id=seeded_user["id"],
                task_type="create_alias",
                status="cancelled",
                input_payload={},
                completed_at=old,
            )
            # Recent completed → survives
            t4 = Task(
                id=uuid.uuid4(),
                user_id=seeded_user["id"],
                task_type="create_alias",
                status="completed",
                input_payload={},
                completed_at=recent,
            )
            # Queued (no completed_at) → survives regardless of age
            t5 = Task(
                id=uuid.uuid4(),
                user_id=seeded_user["id"],
                task_type="create_alias",
                status="queued",
                input_payload={},
            )
            db.add_all([t1, t2, t3, t4, t5])
            await db.commit()
            purged_ids = {t1.id, t2.id, t3.id}
            survives_ids = {t4.id, t5.id}

        result = await cleanup_terminal_tasks({"db_session_factory": factory})
        assert result["deleted"] == 3

        async with factory() as db:
            rows = (await db.execute(select(Task))).scalars().all()
            ids = {r.id for r in rows}
            assert ids == survives_ids
            assert not (ids & purged_ids)
    finally:
        await engine.dispose()
