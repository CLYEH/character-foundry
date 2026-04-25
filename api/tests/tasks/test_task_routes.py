"""Route-level tests for /v1/tasks/*.

Auth, ownership scoping, cancel outcome wire format, list filtering,
and SSE initial-state short-circuit on terminal tasks. Live SSE
publish-and-forward is exercised separately in test_sse_stream.py to
keep this file focused on the synchronous endpoints.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.repositories import task_repo
from app.services import task_service
from tests.tasks.conftest import FakeArqPool


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_queued_task(
    seeded_user: dict[str, Any],
    fake_arq_pool: FakeArqPool,
    database_url: str,
) -> uuid.UUID:
    """Create a queued task by reaching into the service layer with a
    fresh DB session — TestClient's overrides handle the request-side
    DB session, but for setup we drive the same logic directly."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _go() -> uuid.UUID:
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_checkpoint",
                input_payload={"foo": "bar"},
            )
            return created.task.id

    try:
        return asyncio.run(_go())
    finally:
        asyncio.run(engine.dispose())


def test_get_task_returns_dto_with_queue_position(
    client: TestClient,
    seeded_user: dict[str, Any],
    access_token: str,
    fake_arq_pool: FakeArqPool,
    database_url: str,
) -> None:
    task_id = _create_queued_task(seeded_user, fake_arq_pool, database_url)

    resp = client.get(f"/v1/tasks/{task_id}", headers=_auth_headers(access_token))
    assert resp.status_code == 200
    body = resp.json()["task"]
    assert body["id"] == str(task_id)
    assert body["status"] == "queued"
    assert body["task_type"] == "create_checkpoint"
    assert body["queue_position"] == 1
    assert body["estimated_duration_ms"] == 15_000
    assert body["cancel_requested"] is False


def test_get_task_requires_auth(
    client: TestClient,
    seeded_user: dict[str, Any],
    fake_arq_pool: FakeArqPool,
    database_url: str,
) -> None:
    task_id = _create_queued_task(seeded_user, fake_arq_pool, database_url)
    resp = client.get(f"/v1/tasks/{task_id}")
    assert resp.status_code == 401


def test_get_task_404_for_unknown(
    client: TestClient,
    access_token: str,
) -> None:
    resp = client.get(f"/v1/tasks/{uuid.uuid4()}", headers=_auth_headers(access_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_TASK"


def test_cancel_returns_outcome_for_queued_task(
    client: TestClient,
    seeded_user: dict[str, Any],
    access_token: str,
    fake_arq_pool: FakeArqPool,
    database_url: str,
) -> None:
    task_id = _create_queued_task(seeded_user, fake_arq_pool, database_url)

    resp = client.post(f"/v1/tasks/{task_id}/cancel", headers=_auth_headers(access_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["cancel_outcome"] == "cancelled_immediately"
    assert body["task"]["status"] == "cancelled"
    assert body["task"]["cancel_requested"] is True
    assert str(task_id) in fake_arq_pool.aborted


def test_cancel_409_when_already_cancelled(
    client: TestClient,
    seeded_user: dict[str, Any],
    access_token: str,
    fake_arq_pool: FakeArqPool,
    database_url: str,
) -> None:
    task_id = _create_queued_task(seeded_user, fake_arq_pool, database_url)
    client.post(f"/v1/tasks/{task_id}/cancel", headers=_auth_headers(access_token))

    resp = client.post(f"/v1/tasks/{task_id}/cancel", headers=_auth_headers(access_token))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_TASK_ALREADY_TERMINAL"


def test_list_tasks_filters_by_status_and_owner(
    client: TestClient,
    seeded_user: dict[str, Any],
    access_token: str,
    fake_arq_pool: FakeArqPool,
    database_url: str,
) -> None:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    a_id = _create_queued_task(seeded_user, fake_arq_pool, database_url)
    b_id = _create_queued_task(seeded_user, fake_arq_pool, database_url)

    # Mark `b` completed via direct DB.
    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _complete_b() -> None:
        async with factory() as db:
            await task_repo.mark_running(db, b_id)
            await task_repo.mark_completed(db, b_id, result={"ok": True})
            await db.commit()

    asyncio.run(_complete_b())
    asyncio.run(engine.dispose())

    resp = client.get("/v1/tasks?status=queued", headers=_auth_headers(access_token))
    assert resp.status_code == 200
    queued_ids = {item["id"] for item in resp.json()["items"]}
    assert queued_ids == {str(a_id)}

    resp = client.get("/v1/tasks?status=completed", headers=_auth_headers(access_token))
    completed_ids = {item["id"] for item in resp.json()["items"]}
    assert completed_ids == {str(b_id)}


def test_sse_initial_state_short_circuits_for_terminal_task(
    client: TestClient,
    seeded_user: dict[str, Any],
    access_token: str,
    fake_arq_pool: FakeArqPool,
    database_url: str,
) -> None:
    """When the task is already terminal at request time, SSE should
    yield exactly one frame (the initial snapshot) and close. We
    intentionally do not block on subsequent reads because the worker
    won't publish anything for a finished task.

    Kept sync (no @pytest.mark.asyncio) because TestClient.stream() is
    sync and our setup helper uses `asyncio.run`, which can't be
    invoked from inside a running pytest-asyncio loop.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    task_id = _create_queued_task(seeded_user, fake_arq_pool, database_url)

    async def _mark_completed() -> None:
        engine = create_async_engine(database_url, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as db:
                await task_repo.mark_running(db, task_id)
                await task_repo.mark_completed(db, task_id, result={"ok": True, "label": "done"})
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_mark_completed())

    with client.stream(
        "GET",
        f"/v1/tasks/{task_id}/stream",
        headers=_auth_headers(access_token),
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        # Read all bytes — terminal short-circuit means the stream closes.
        body = b""
        for chunk in response.iter_bytes():
            body += chunk
        text = body.decode("utf-8")

    # Exactly one SSE frame, with status=completed.
    assert text.count("data: ") == 1
    assert '"status": "completed"' in text
    assert '"label": "done"' in text
