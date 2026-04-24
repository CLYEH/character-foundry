from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from app.api.deps import db_session
from app.main import app
from tests.routes.conftest import FakeDBSession, FakeRedis, FakeStorage


def test_health_all_ok_returns_200(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "db": "ok", "redis": "ok", "storage": "ok"}


def test_health_redis_down_returns_503(client: TestClient, fake_redis: FakeRedis) -> None:
    fake_redis.down = True

    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["redis"] == "fail"
    assert body["db"] == "ok"
    assert body["storage"] == "ok"


def test_health_db_down_returns_503(client: TestClient, fake_db: FakeDBSession) -> None:
    fake_db.should_fail = True

    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["db"] == "fail"


def test_health_storage_down_returns_503(client: TestClient, fake_storage: FakeStorage) -> None:
    fake_storage.down = True

    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["storage"] == "fail"


def test_health_does_not_require_auth(client: TestClient) -> None:
    # Called without Authorization header — must still succeed.
    resp = client.get("/health")
    assert resp.status_code in (200, 503)


def test_health_dep_resolution_failure_returns_503_all_fail() -> None:
    """A misconfigured dep (e.g. missing DATABASE_URL) raises during FastAPI
    dependency resolution, *before* the handler runs. Without the safety-net
    route wrapper this would be a 500 and drop the per-component body that
    monitoring depends on. Simulate by overriding `db_session` with a
    dependency that raises at init, then assert the documented 503 shape.
    """

    async def _broken_db() -> AsyncIterator[None]:
        raise RuntimeError("DATABASE_URL is not set")
        yield  # pragma: no cover — unreachable; satisfies generator protocol

    app.dependency_overrides[db_session] = _broken_db
    try:
        with TestClient(app) as c:
            resp = c.get("/health")
            assert resp.status_code == 503
            body = resp.json()
            assert body == {
                "status": "degraded",
                "db": "fail",
                "redis": "fail",
                "storage": "fail",
            }
    finally:
        app.dependency_overrides.pop(db_session, None)
