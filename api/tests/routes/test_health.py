from __future__ import annotations

from fastapi.testclient import TestClient

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
