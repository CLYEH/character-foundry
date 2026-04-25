"""Live SSE stream test — initial state + a worker-published event.

Validates the contract from planning/backend/api-shape.md §3.1: SSE
forwards Redis pubsub messages on `task:{id}` until a terminal status
arrives, then closes. We use the route handler's generator directly
rather than going through TestClient so we can publish into the same
fakeredis instance the route reads from (TestClient runs the route in
a separate thread/loop which complicates pubsub timing).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.api.routes.tasks import _stream_task_events
from app.core.redis_client import publish_task_event
from app.repositories import task_repo
from app.services import task_service
from tests.tasks.conftest import FakeArqPool


@pytest.mark.asyncio
async def test_sse_forwards_running_then_completed(
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
    task = created.task

    gen = _stream_task_events(
        task_id=task.id,
        user_id=seeded_user["id"],
        redis=fake_redis,
    )

    # First yield is the initial snapshot.
    first = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert first.startswith("data: ")
    initial = json.loads(first[len("data: ") :].strip())
    assert initial["status"] == "queued"

    # Simulate worker publishing running → completed.
    async def _publish_lifecycle() -> None:
        await asyncio.sleep(0.05)
        await publish_task_event(fake_redis, task.id, {"status": "running", "progress": 0.3})
        await asyncio.sleep(0.05)
        await publish_task_event(
            fake_redis,
            task.id,
            {"status": "completed", "result": {"ok": True}},
        )

    publish_task = asyncio.create_task(_publish_lifecycle())

    received: list[dict[str, Any]] = []
    try:
        async for frame in gen:
            if frame.startswith(": "):
                # heartbeat — ignore
                continue
            assert frame.startswith("data: ")
            received.append(json.loads(frame[len("data: ") :].strip()))
            if received[-1].get("status") == "completed":
                break
    finally:
        await publish_task
        await gen.aclose()

    statuses = [m["status"] for m in received]
    assert statuses == ["running", "completed"]
    assert received[1]["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_sse_terminal_initial_state_closes_after_one_frame(
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
    await task_repo.mark_running(db_session, created.task.id)
    await task_repo.mark_failed(
        db_session,
        created.task.id,
        error={"code": "MODEL_TIMEOUT", "message": "x"},
    )
    await db_session.commit()
    await db_session.refresh(created.task)

    gen = _stream_task_events(
        task_id=created.task.id,
        user_id=seeded_user["id"],
        redis=fake_redis,
    )
    frames: list[str] = []
    async for frame in gen:
        frames.append(frame)
    # Exactly one SSE data frame; no heartbeats since loop exits immediately.
    assert len(frames) == 1
    payload = json.loads(frames[0][len("data: ") :].strip())
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "MODEL_TIMEOUT"


@pytest.mark.asyncio
async def test_sse_subscribes_before_initial_state_read(
    db_session: Any,
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
) -> None:
    """Codex P1 race: if the worker publishes a terminal event between
    the route's auth check and our subscribe, the client gets stuck on a
    stale `queued` snapshot. The generator must subscribe FIRST so the
    event is buffered before the initial-state read.

    We exercise this by publishing on the channel BEFORE pulling the
    first frame from the generator. The generator's `subscribe` runs as
    soon as it's awaited (anext), so the published "completed" frame
    must show up after the initial snapshot rather than be lost.
    """
    created = await task_service.create_task(
        db_session,
        fake_arq_pool,  # type: ignore[arg-type]
        user_id=seeded_user["id"],
        task_type="create_alias",
        input_payload={},
    )
    task = created.task

    gen = _stream_task_events(
        task_id=task.id,
        user_id=seeded_user["id"],
        redis=fake_redis,
    )

    # Pull the first frame — this drives the subscribe + initial fetch.
    first = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    initial = json.loads(first[len("data: ") :].strip())
    assert initial["status"] == "queued"

    # Now publish a terminal event AFTER we've subscribed but during the
    # active stream. The generator must forward it.
    await publish_task_event(
        fake_redis,
        task.id,
        {"status": "completed", "result": {"late": True}},
    )

    second_frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    second = json.loads(second_frame[len("data: ") :].strip())
    assert second["status"] == "completed"
    assert second["result"] == {"late": True}

    await gen.aclose()
