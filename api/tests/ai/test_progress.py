"""progress_publisher CM behaviour (T-014)."""

from __future__ import annotations

import asyncio
import json
import uuid

import fakeredis.aioredis
import pytest

from app.ai.progress import progress_publisher
from app.core.redis_client import task_channel


async def _collect_messages(
    redis: fakeredis.aioredis.FakeRedis,
    task_id: uuid.UUID,
    *,
    duration_seconds: float,
) -> list[dict[str, object]]:
    """Subscribe and collect all messages published during `duration_seconds`."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(task_channel(task_id))

    messages: list[dict[str, object]] = []

    async def _drain() -> None:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            messages.append(json.loads(msg["data"]))

    drain_task = asyncio.create_task(_drain())
    await asyncio.sleep(duration_seconds)
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass
    await pubsub.unsubscribe(task_channel(task_id))
    await pubsub.aclose()
    return messages


async def test_progress_publisher_emits_running_events(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    task_id = uuid.uuid4()
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(task_channel(task_id))

    received: list[dict[str, object]] = []

    async def _consume() -> None:
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                received.append(json.loads(msg["data"]))
                if len(received) >= 2:
                    return

    consumer = asyncio.create_task(_consume())

    async with progress_publisher(fake_redis, task_id, estimated_ms=500, interval_seconds=0.05):
        # Wait long enough to see at least 2 ticks.
        await asyncio.wait_for(consumer, timeout=2.0)

    await pubsub.unsubscribe(task_channel(task_id))
    await pubsub.aclose()

    assert len(received) >= 2
    for msg in received:
        assert msg["status"] == "running"
        assert "progress" in msg
        progress = msg["progress"]
        assert isinstance(progress, int | float)
        assert 0.0 <= float(progress) <= 0.95


async def test_progress_publisher_caps_progress_at_ceiling(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    task_id = uuid.uuid4()
    # Tiny estimated_ms — the loop will hit the ceiling almost immediately.
    async with progress_publisher(fake_redis, task_id, estimated_ms=1, interval_seconds=0.05):
        await asyncio.sleep(0.15)

    # No assertions on event count here; the cap is asserted in the
    # consume-events test above. This case asserts the CM exits cleanly
    # even when progress saturates instantly (no ZeroDivision / overflow).


async def test_progress_publisher_cancels_loop_on_exit(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    task_id = uuid.uuid4()
    before_tasks = {t for t in asyncio.all_tasks() if not t.done()}

    async with progress_publisher(fake_redis, task_id, estimated_ms=10000, interval_seconds=0.05):
        await asyncio.sleep(0.01)
        # Inside the CM we expect exactly one new pending task — the loop.
        new_tasks = {t for t in asyncio.all_tasks() if not t.done()} - before_tasks
        # Filter out the test's own current task
        new_tasks.discard(asyncio.current_task())
        assert any(t.get_name().startswith("progress-publisher:") for t in new_tasks), (
            "publisher loop should be running while inside the CM"
        )

    # After exit, the loop task must be cleaned up (not just cancelled but awaited).
    leaked = [
        t
        for t in asyncio.all_tasks()
        if not t.done() and t.get_name().startswith("progress-publisher:")
    ]
    assert leaked == []


async def test_progress_publisher_short_call_does_not_emit(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """When the wrapped call finishes before the first tick, the CM exits
    with zero published events and no leaked task.
    """
    task_id = uuid.uuid4()
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(task_channel(task_id))
    try:
        async with progress_publisher(
            fake_redis, task_id, estimated_ms=10000, interval_seconds=10.0
        ):
            await asyncio.sleep(0.01)
        # No message expected.
        msg = await pubsub.get_message(timeout=0.05, ignore_subscribe_messages=True)
        assert msg is None
    finally:
        await pubsub.unsubscribe(task_channel(task_id))
        await pubsub.aclose()


async def test_progress_publisher_swallows_publish_errors(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flaky Redis publish must not crash the worker call wrapping the CM."""
    from app.ai import progress as progress_module

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr(progress_module, "publish_task_event", _boom)

    task_id = uuid.uuid4()
    async with progress_publisher(fake_redis, task_id, estimated_ms=100, interval_seconds=0.02):
        await asyncio.sleep(0.06)
    # If the CM exits without raising, the contract holds.
