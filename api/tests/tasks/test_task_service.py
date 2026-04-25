"""Unit tests for `task_service.create_task` and `cancel_task`.

The 4 cancel outcomes (planning/backend/api-shape.md §5.5) are the
load-bearing surface: queued → cancelled_immediately, running →
cancel_pending, terminal-without-prior → too_late_*, terminal-with-
prior → 409. We also assert the side-effect contract: queued cancels
produce an arq abort_job + a "cancelled" pubsub event; running cancels
produce a cancel-channel publish + a status-running event flagged
cancel_requested=True.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.errors import AgentErrorException
from app.repositories import task_repo
from app.services import task_service
from tests.tasks.conftest import FakeArqPool


@pytest.mark.asyncio
async def test_create_task_inserts_row_and_enqueues_arq_job(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_arq_pool: FakeArqPool,
) -> None:
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_checkpoint",
        input_payload={"foo": "bar"},
    )

    assert created.task.status == "queued"
    assert created.task.task_type == "create_checkpoint"
    assert created.task.user_id == seeded_user["id"]
    assert created.task.estimated_duration_ms == 15_000
    assert created.job_function == "run_create_checkpoint"

    # arq pool received the enqueue with task uuid as job id.
    assert len(fake_arq_pool.enqueued) == 1
    fn, _args, kwargs = fake_arq_pool.enqueued[0]
    assert fn == "run_create_checkpoint"
    assert kwargs["task_id"] == str(created.task.id)
    assert kwargs["_job_id"] == str(created.task.id)


@pytest.mark.asyncio
async def test_cancel_queued_task_is_immediate(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={},
    )

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(f"task:{created.task.id}")
    # drain subscription confirmation
    await pubsub.get_message(timeout=0.1)

    result = await task_service.cancel_task(
        db_session,
        fake_redis,
        fake_arq_pool,  # type: ignore[arg-type]
        task_id=created.task.id,
        user_id=seeded_user["id"],
    )

    assert result.cancel_outcome == "cancelled_immediately"
    assert result.task.status == "cancelled"
    assert result.task.cancel_requested is True
    assert result.task.cancel_requested_at is not None
    assert result.task.completed_at is not None
    assert str(created.task.id) in fake_arq_pool.aborted

    # Pubsub event published.
    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    assert msg is not None
    payload = json.loads(msg["data"])
    assert payload["status"] == "cancelled"
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_cancel_running_task_is_pending(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_motion",
        input_payload={},
    )
    # Manually advance to running.
    await task_repo.mark_running(db_session, created.task.id)
    await db_session.commit()

    cancel_pubsub = fake_redis.pubsub()
    await cancel_pubsub.subscribe(f"task:{created.task.id}:cancel")
    await cancel_pubsub.get_message(timeout=0.1)  # drain subscribe ack

    result = await task_service.cancel_task(
        db_session,
        fake_redis,
        fake_arq_pool,  # type: ignore[arg-type]
        task_id=created.task.id,
        user_id=seeded_user["id"],
    )

    assert result.cancel_outcome == "cancel_pending"
    assert result.task.status == "running"
    assert result.task.cancel_requested is True
    # arq abort_job should NOT be called for the running case — cancel
    # is cooperative; the worker must detect cancel_requested itself.
    assert str(created.task.id) not in fake_arq_pool.aborted

    # Cancel signal was published on the cancel channel.
    msg = await cancel_pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    assert msg is not None
    assert msg["data"] == "1"
    await cancel_pubsub.aclose()


@pytest.mark.asyncio
async def test_cancel_completed_task_returns_too_late(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="copy_character",
        input_payload={},
    )
    await task_repo.mark_running(db_session, created.task.id)
    await task_repo.mark_completed(db_session, created.task.id, result={"ok": True})
    await db_session.commit()

    result = await task_service.cancel_task(
        db_session,
        fake_redis,
        fake_arq_pool,  # type: ignore[arg-type]
        task_id=created.task.id,
        user_id=seeded_user["id"],
    )
    assert result.cancel_outcome == "too_late_completed"
    assert result.task.status == "completed"


@pytest.mark.asyncio
async def test_cancel_failed_task_returns_too_late(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_motion",
        input_payload={},
    )
    await task_repo.mark_running(db_session, created.task.id)
    await task_repo.mark_failed(
        db_session,
        created.task.id,
        error={"code": "MODEL_TIMEOUT", "message": "tag"},
    )
    await db_session.commit()

    result = await task_service.cancel_task(
        db_session,
        fake_redis,
        fake_arq_pool,  # type: ignore[arg-type]
        task_id=created.task.id,
        user_id=seeded_user["id"],
    )
    assert result.cancel_outcome == "too_late_failed"
    assert result.task.status == "failed"


@pytest.mark.asyncio
async def test_double_cancel_already_cancelled_returns_409(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={},
    )
    await task_service.cancel_task(
        db_session,
        fake_redis,
        fake_arq_pool,  # type: ignore[arg-type]
        task_id=created.task.id,
        user_id=seeded_user["id"],
    )

    with pytest.raises(AgentErrorException) as exc_info:
        await task_service.cancel_task(
            db_session,
            fake_redis,
            fake_arq_pool,  # type: ignore[arg-type]
            task_id=created.task.id,
            user_id=seeded_user["id"],
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.error.code == "CONFLICT_TASK_ALREADY_TERMINAL"


@pytest.mark.asyncio
async def test_cancel_unknown_task_returns_404(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    with pytest.raises(AgentErrorException) as exc_info:
        await task_service.cancel_task(
            db_session,
            fake_redis,
            fake_arq_pool,  # type: ignore[arg-type]
            task_id=uuid.uuid4(),
            user_id=seeded_user["id"],
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.error.code == "NOT_FOUND_TASK"


@pytest.mark.asyncio
async def test_cancel_other_users_task_returns_404(
    db_session: Any,
    seeded_user: dict[str, Any],
    second_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    """Owner scoping: Bob can't cancel Alice's task. We surface 404
    (not 403) so Bob can't probe whether a given task id exists."""
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={},
    )

    with pytest.raises(AgentErrorException) as exc_info:
        await task_service.cancel_task(
            db_session,
            fake_redis,
            fake_arq_pool,  # type: ignore[arg-type]
            task_id=created.task.id,
            user_id=second_user["id"],
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_terminal_with_prior_cancel_returns_409(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    """Race: user cancelled while running, worker still finished. Second
    cancel call should 409, not return another `too_late_*` outcome."""
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={},
    )
    await task_repo.mark_running(db_session, created.task.id)
    await db_session.commit()

    # First cancel while running → cancel_pending
    first = await task_service.cancel_task(
        db_session,
        fake_redis,
        fake_arq_pool,  # type: ignore[arg-type]
        task_id=created.task.id,
        user_id=seeded_user["id"],
    )
    assert first.cancel_outcome == "cancel_pending"

    # Worker finishes anyway.
    await task_repo.mark_completed(db_session, created.task.id, result={"ok": True})
    await db_session.commit()

    # Second cancel — task is terminal AND cancel_requested already True → 409.
    with pytest.raises(AgentErrorException) as exc_info:
        await task_service.cancel_task(
            db_session,
            fake_redis,
            fake_arq_pool,  # type: ignore[arg-type]
            task_id=created.task.id,
            user_id=seeded_user["id"],
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_queue_position_returns_index_for_queued_task(
    fake_arq_pool: FakeArqPool,
) -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await fake_arq_pool.enqueue_job("run_x", _job_id=str(a))
    await fake_arq_pool.enqueue_job("run_x", _job_id=str(b))
    await fake_arq_pool.enqueue_job("run_x", _job_id=str(c))

    assert await task_service.queue_position(fake_arq_pool, a) == 1  # type: ignore[arg-type]
    assert await task_service.queue_position(fake_arq_pool, b) == 2  # type: ignore[arg-type]
    assert await task_service.queue_position(fake_arq_pool, c) == 3  # type: ignore[arg-type]
    # Unknown job → None.
    assert (
        await task_service.queue_position(fake_arq_pool, uuid.uuid4())  # type: ignore[arg-type]
        is None
    )


@pytest.mark.asyncio
async def test_list_user_tasks_filters_by_status(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_arq_pool: FakeArqPool,
) -> None:
    a = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={"label": "a"},
    )
    b = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={"label": "b"},
    )
    # Mark `b` completed.
    await task_repo.mark_running(db_session, b.task.id)
    await task_repo.mark_completed(db_session, b.task.id, result={"x": 1})
    await db_session.commit()

    queued = await task_service.list_user_tasks(
        db_session, user_id=seeded_user["id"], status="queued"
    )
    completed = await task_service.list_user_tasks(
        db_session, user_id=seeded_user["id"], status="completed"
    )
    assert {t.id for t in queued} == {a.task.id}
    assert {t.id for t in completed} == {b.task.id}


@pytest.mark.asyncio
async def test_cancel_isolation_does_not_alter_unrelated_task(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    """Sanity check: cancelling task A must not flip task B's flags."""
    a = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={"x": 1},
    )
    b = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={"x": 2},
    )

    await task_service.cancel_task(
        db_session,
        fake_redis,
        fake_arq_pool,  # type: ignore[arg-type]
        task_id=a.task.id,
        user_id=seeded_user["id"],
    )

    refreshed_b = await task_repo.get(db_session, b.task.id)
    assert refreshed_b is not None
    assert refreshed_b.status == "queued"
    assert refreshed_b.cancel_requested is False
    assert refreshed_b.completed_at is None
    # Don't actually use this, just keep mypy happy about unused var.
    _ = datetime.now(UTC)
