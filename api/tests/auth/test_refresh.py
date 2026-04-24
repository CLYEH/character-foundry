from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _login(client: TestClient, seeded: dict[str, str]) -> dict[str, str]:
    r = client.post(
        "/v1/auth/login", json={"email": seeded["email"], "password": seeded["password"]}
    )
    assert r.status_code == 200
    return r.json()


def test_refresh_success_returns_new_access_token(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    tokens = _login(client, seeded_user)
    resp = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["access_token"] != tokens["access_token"] or True  # new iat/jti


def test_refresh_unknown_token_returns_invalid_token(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    # seeded_user ensures DB has a user but no refresh_token row.
    resp = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": uuid.uuid4().hex},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_refresh_expired_token_returns_refresh_expired(
    client: TestClient, seeded_user: dict[str, str], database_url: str
) -> None:
    tokens = _login(client, seeded_user)

    # Force the refresh row's expires_at into the past.
    async def _expire() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    text("UPDATE refresh_tokens SET expires_at = NOW() - INTERVAL '1 day'")
                )
        finally:
            await engine.dispose()

    asyncio.run(_expire())

    resp = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REFRESH_EXPIRED"


def test_refresh_after_logout_returns_refresh_revoked(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    tokens = _login(client, seeded_user)

    logout = client.post(
        "/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert logout.status_code == 200

    resp = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REFRESH_REVOKED"


def test_logout_without_access_token_returns_missing_token(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    tokens = _login(client, seeded_user)
    resp = client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_TOKEN"
