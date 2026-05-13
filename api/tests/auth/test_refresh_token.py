"""T-055: refresh_tokens.token_source coverage.

Hits four surfaces the dual-stack roll-out depends on:
  * migration adds the column, the postgres enum, and backfills 'jwt';
  * `RefreshToken` round-trips both enum members;
  * `create_refresh_token()` defaults to JWT (so existing call sites stay
    silently correct after the helper extraction in T-055);
  * explicit OAUTH callers (T-3.5b refresh path) get the right value.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.service import create_refresh_token, hash_refresh_token
from app.models.refresh_token import RefreshTokenSource


def _login(client: TestClient, seeded: dict[str, str]) -> dict[str, str]:
    r = client.post(
        "/v1/auth/login", json={"email": seeded["email"], "password": seeded["password"]}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_create_refresh_token_defaults_to_jwt() -> None:
    row, raw = create_refresh_token(user_id=uuid.uuid4())
    assert row.token_source is RefreshTokenSource.JWT
    assert RefreshTokenSource.JWT.value == "jwt"
    # Raw token is returned exactly once; the row only carries its sha256.
    assert row.token_hash == hash_refresh_token(raw)
    assert row.token_hash != raw


def test_create_refresh_token_accepts_explicit_oauth_source() -> None:
    row, _ = create_refresh_token(user_id=uuid.uuid4(), token_source=RefreshTokenSource.OAUTH)
    assert row.token_source is RefreshTokenSource.OAUTH
    assert RefreshTokenSource.OAUTH.value == "oauth"


def test_login_persists_token_source_jwt(
    client: TestClient, seeded_user: dict[str, str], database_url: str
) -> None:
    """Existing JWT login path must continue stamping 'jwt' (Phase 1 invariant)."""
    _login(client, seeded_user)

    async def _read_sources() -> list[str]:
        engine = create_async_engine(database_url, future=True)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(text("SELECT token_source FROM refresh_tokens"))
                return [r[0] for r in rows.fetchall()]
        finally:
            await engine.dispose()

    sources = asyncio.run(_read_sources())
    assert sources == ["jwt"]


def test_refresh_token_oauth_row_roundtrips(seeded_user: dict[str, str], database_url: str) -> None:
    """An OAUTH row inserted at the DB layer comes back as 'oauth'."""
    raw = uuid.uuid4().hex
    token_hash = hash_refresh_token(raw)

    async def _roundtrip() -> str:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                user_id = (
                    await conn.execute(
                        text("SELECT id FROM users WHERE email = :e"),
                        {"e": seeded_user["email"]},
                    )
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO refresh_tokens "
                        "(user_id, token_hash, expires_at, token_source) "
                        "VALUES (:u, :h, NOW() + INTERVAL '1 hour', 'oauth')"
                    ),
                    {"u": user_id, "h": token_hash},
                )
                row = await conn.execute(
                    text("SELECT token_source FROM refresh_tokens WHERE token_hash = :h"),
                    {"h": token_hash},
                )
                return row.scalar_one()
        finally:
            await engine.dispose()

    assert asyncio.run(_roundtrip()) == "oauth"


def test_refresh_token_column_metadata(database_url: str) -> None:
    """Migration outcome: column type is the named enum + NOT NULL + no default."""

    async def _fetch() -> tuple[str, str, str | None]:
        engine = create_async_engine(database_url, future=True)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            """
                            SELECT udt_name, is_nullable, column_default
                            FROM information_schema.columns
                            WHERE table_name = 'refresh_tokens'
                              AND column_name = 'token_source'
                            """
                        )
                    )
                ).first()
                assert row is not None, "token_source column missing"
                return row[0], row[1], row[2]
        finally:
            await engine.dispose()

    udt_name, is_nullable, column_default = asyncio.run(_fetch())
    assert udt_name == "refresh_token_source"
    assert is_nullable == "NO"
    # No SQL-level default — the source is decided by the call site
    # (create_refresh_token's python default), not silently by the DB.
    assert column_default is None


def test_refresh_token_enum_values(database_url: str) -> None:
    """The enum carries exactly the two Phase 1 / Phase 3.5 sources."""

    async def _fetch() -> set[str]:
        engine = create_async_engine(database_url, future=True)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(
                    text(
                        """
                        SELECT e.enumlabel
                        FROM pg_type t
                        JOIN pg_enum e ON e.enumtypid = t.oid
                        WHERE t.typname = 'refresh_token_source'
                        """
                    )
                )
                return {r[0] for r in rows.fetchall()}
        finally:
            await engine.dispose()

    assert asyncio.run(_fetch()) == {"jwt", "oauth"}


@pytest.mark.parametrize("invalid_value", ["service_account", "JWT", "", "oauth2"])
def test_refresh_token_enum_rejects_unknown_value(
    seeded_user: dict[str, str],
    database_url: str,
    invalid_value: str,
) -> None:
    """DB enforces the enum constraint — non-canonical strings get rejected."""

    async def _attempt() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                user_id = (
                    await conn.execute(
                        text("SELECT id FROM users WHERE email = :e"),
                        {"e": seeded_user["email"]},
                    )
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO refresh_tokens "
                        "(user_id, token_hash, expires_at, token_source) "
                        "VALUES (:u, :h, NOW() + INTERVAL '1 hour', :s)"
                    ),
                    {
                        "u": user_id,
                        "h": hash_refresh_token(uuid.uuid4().hex),
                        "s": invalid_value,
                    },
                )
        finally:
            await engine.dispose()

    with pytest.raises(Exception) as exc:
        asyncio.run(_attempt())
    msg = str(exc.value).lower()
    assert "invalid input value for enum" in msg or "refresh_token_source" in msg
