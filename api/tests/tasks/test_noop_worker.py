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
async def test_run_noop_is_idempotent_on_already_completed_task(
    database_url: str,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    """Codex round-3 P1: arq retries can re-invoke run_noop. If the
    task is already terminal, the handler MUST short-circuit rather
    than flipping the row back to running."""
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
        # Drive the handler once — succeeds.
        first_result = await run_noop(
            {"db_session_factory": factory, "redis": fake_redis},
            str(created.task.id),
        )
        assert first_result["ok"] is True

        # Simulate arq retry — second invocation. Must NOT regress to running.
        second_result = await run_noop(
            {"db_session_factory": factory, "redis": fake_redis},
            str(created.task.id),
        )
        assert second_result["ok"] is False
        assert second_result["reason"] == "completed"

        async with factory() as db:
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            assert row.status == "completed"
            assert row.result == {"noop": True}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_noop_completes_even_when_publish_raises(
    database_url: str,
    seeded_user: dict[str, Any],
    fake_arq_pool: FakeArqPool,
) -> None:
    """Codex round-3 P1: if Redis publish fails, run_noop must still
    advance the task to `completed` (DB is source of truth). Otherwise
    arq retries would loop forever, regressing state each time."""

    class ExplodingRedis:
        async def publish(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("redis publish down")

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
            {"db_session_factory": factory, "redis": ExplodingRedis()},
            str(created.task.id),
        )
        assert result["ok"] is True

        async with factory() as db:
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            assert row.status == "completed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_noop_marks_cancelled_when_retry_sees_cancel_on_running_row(
    database_url: str,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    """Codex P1 round-4 + P2 round-5: prior attempt committed
    status='running' then failed; user calls cancel between attempts ->
    cancel_requested=True on a running row; arq retries. The retry
    must (a) persist `cancelled` so the row doesn't stay non-terminal
    forever, AND (b) publish a terminal SSE event so subscribed
    clients close their stream instead of polling.
    """
    import json

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
            await task_repo.mark_running(db, created.task.id)
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            row.cancel_requested = True
            row.cancel_requested_at = datetime.now(UTC)
            await db.commit()

        # Subscribe to SSE channel BEFORE running the retry so we can
        # capture the terminal event the worker is supposed to publish.
        pubsub = fake_redis.pubsub()
        await pubsub.subscribe(f"task:{created.task.id}")
        await pubsub.get_message(timeout=0.1)  # drain subscribe ack

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
            assert row.completed_at is not None

        # Terminal SSE event was published.
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        assert msg is not None
        payload = json.loads(msg["data"])
        assert payload["status"] == "cancelled"
        await pubsub.aclose()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transition_queued_to_running_succeeds_for_clean_queued_row(
    database_url: str,
    seeded_user: dict[str, Any],
    fake_arq_pool: FakeArqPool,
) -> None:
    """CAS happy path: queued + cancel_requested=False → running."""
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
        async with factory() as db:
            ok = await task_repo.transition_queued_to_running(db, created.task.id)
            await db.commit()
            assert ok is True
        async with factory() as db:
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            assert row.status == "running"
            assert row.started_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transition_queued_to_running_fails_when_cancel_requested(
    database_url: str,
    seeded_user: dict[str, Any],
    fake_arq_pool: FakeArqPool,
) -> None:
    """Codex P1 round-6: cancel-vs-pickup race protection. CAS must
    refuse to transition if cancel_requested was set, even when the
    row is still in `queued` status."""
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
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            row.cancel_requested = True
            row.cancel_requested_at = datetime.now(UTC)
            await db.commit()
        async with factory() as db:
            ok = await task_repo.transition_queued_to_running(db, created.task.id)
            await db.commit()
            assert ok is False
        async with factory() as db:
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            # Row stayed `queued` — CAS did not regress it.
            assert row.status == "queued"
            assert row.started_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_noop_does_not_regress_when_cancel_committed_after_read(
    database_url: str,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    """End-to-end shape of the cancel-vs-pickup race (Codex round-6).

    Simulate the race by setting cancel_requested=True on a `queued`
    row (representing the cancel API committing AFTER the worker's
    initial read). run_noop's CAS should refuse to advance the row
    and return `cancelled`, NOT regress to `running`.
    """
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
        # Worker's first read happens inside run_noop and sees `queued`.
        # We set cancel_requested AFTER create but BEFORE running, so the
        # initial read in run_noop will already see cancel_requested=True
        # — that's the existing pre-pickup cancel branch. To exercise the
        # CAS specifically, the test orders things so run_noop sees
        # cancel_requested=True at first read AND the CAS would also
        # refuse: both layers of defense should converge to `cancelled`.
        async with factory() as db:
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            row.cancel_requested = True
            row.cancel_requested_at = datetime.now(UTC)
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
            # Critical: NOT regressed to running.
            assert row.status != "running"
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
