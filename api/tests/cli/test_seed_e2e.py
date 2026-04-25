"""Tests for `python -m app.cli seed-e2e` (T-012)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command


@pytest.fixture(scope="module", autouse=True)
def _migrate_once(alembic_config, database_url: str) -> Iterator[None]:
    command.upgrade(alembic_config, "head")
    yield


async def _purge_users(database_url: str) -> None:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("DELETE FROM refresh_tokens"))
            await conn.execute(text("DELETE FROM users"))
    finally:
        await engine.dispose()


async def _user_count(database_url: str) -> int:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text("SELECT COUNT(*) FROM users WHERE email LIKE 'test+%@internal.local'")
                )
            ).scalar_one()
    finally:
        await engine.dispose()


@pytest.fixture
def clean_users(database_url: str) -> None:
    asyncio.run(_purge_users(database_url))


def _reset_engine_cache() -> None:
    # `get_engine` / `async_session_factory` are lru_cached. Clear so the CLI
    # picks up the test DATABASE_URL set by the alembic_config fixture.
    from app.db.session import async_session_factory, get_engine

    get_engine.cache_clear()
    async_session_factory.cache_clear()


def test_seed_e2e_creates_users_and_is_idempotent(clean_users: None, database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    _reset_engine_cache()

    from app.cli import E2E_USERS, main

    assert main(["seed-e2e"]) == 0
    assert asyncio.run(_user_count(database_url)) == len(E2E_USERS)

    # Re-running must not error and must not duplicate.
    assert main(["seed-e2e"]) == 0
    assert asyncio.run(_user_count(database_url)) == len(E2E_USERS)


def test_seed_e2e_users_can_login(clean_users: None, database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    _reset_engine_cache()

    from app.auth.passwords import verify_password
    from app.cli import E2E_PASSWORD, E2E_USERS, main

    assert main(["seed-e2e"]) == 0

    async def _password_hashes() -> list[str]:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT password_hash FROM users "
                            "WHERE email LIKE 'test+%@internal.local' "
                            "ORDER BY email"
                        )
                    )
                ).all()
                return [row[0] for row in rows]
        finally:
            await engine.dispose()

    hashes = asyncio.run(_password_hashes())
    assert len(hashes) == len(E2E_USERS)
    for h in hashes:
        assert verify_password(E2E_PASSWORD, h)
