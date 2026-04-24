from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def test_login_success_returns_tokens_and_user(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    resp = client.post(
        "/v1/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] > 0
    user = body["user"]
    assert user["email"] == seeded_user["email"]
    assert user["name"] == seeded_user["name"]
    assert user["team_id"]


def test_login_wrong_password_returns_auth_invalid_credentials(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    resp = client.post(
        "/v1/auth/login",
        json={"email": seeded_user["email"], "password": "nope-not-it"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_login_unknown_email_returns_auth_invalid_credentials(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    """Same code as wrong password — don't leak account-existence state."""
    resp = client.post(
        "/v1/auth/login",
        json={"email": "ghost@example.com", "password": "anything"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_login_stores_refresh_token_hash_not_raw(
    client: TestClient, seeded_user: dict[str, str], database_url: str
) -> None:
    """Raw refresh token must NOT appear in the DB — only its sha256."""
    resp = client.post(
        "/v1/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    raw = resp.json()["refresh_token"]

    async def _fetch() -> list[tuple[str, str]]:
        engine = create_async_engine(database_url, future=True)
        try:
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(text("SELECT token_hash FROM refresh_tokens"))
                ).fetchall()
                return [tuple(r) for r in rows]
        finally:
            await engine.dispose()

    rows = asyncio.run(_fetch())
    assert len(rows) == 1
    stored_hash = rows[0][0]
    assert stored_hash != raw
    assert len(stored_hash) == 64  # sha256 hex


def test_login_updates_last_login_at(
    client: TestClient, seeded_user: dict[str, str], database_url: str
) -> None:
    client.post(
        "/v1/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )

    async def _fetch() -> object:
        engine = create_async_engine(database_url, future=True)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT last_login_at FROM users WHERE email=:e"),
                    {"e": seeded_user["email"]},
                )
                return result.scalar_one()
        finally:
            await engine.dispose()

    last_login = asyncio.run(_fetch())
    assert last_login is not None
