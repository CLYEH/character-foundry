from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

JWT_SECRET = "test-jwt-secret-dont-use-in-prod"


# Tables we clear between tests. Order matters: child → parent so that even if
# a test briefly populated something deeper than users (not expected, but
# cheap), we leave a clean slate for the next test. `teams` is untouched —
# migration 002 seeds the "default" team and callers rely on it.
_TABLES_TO_CLEAN = (
    "refresh_tokens",
    "tasks",
    "motions",
    "aliases",
    "bases",
    "checkpoints",
    "creation_sessions",
    "characters",
    "users",
)


async def _delete_all(database_url: str) -> None:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for table in _TABLES_TO_CLEAN:
                await conn.execute(text(f"DELETE FROM {table}"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _migrate_once(alembic_config, database_url: str) -> Iterator[None]:
    """Upgrade to head once per auth-test module.

    Uses `alembic_config` purely for its side-effect of setting
    `DATABASE_URL` so the app's engine picks it up.
    """
    os.environ["JWT_SECRET"] = JWT_SECRET
    command.upgrade(alembic_config, "head")
    yield


@pytest.fixture
def clean_auth_tables(database_url: str) -> None:
    asyncio.run(_delete_all(database_url))


@pytest.fixture
def client(clean_auth_tables: None) -> Iterator[TestClient]:
    # Import lazily so env vars (JWT_SECRET, DATABASE_URL) are set first.
    from app.db.session import async_session_factory, get_engine
    from app.main import app

    # Engine/factory are lru_cached; clear them so this test process picks up
    # whatever DATABASE_URL the current run is using, even if a prior test run
    # in the same process warmed the cache against a different URL.
    get_engine.cache_clear()
    async_session_factory.cache_clear()

    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded_user(database_url: str, clean_auth_tables: None) -> dict[str, str]:
    """Insert one user into the default team and return its credentials."""
    from app.auth.passwords import hash_password

    email = "alice@example.com"
    password = "correct-horse-battery-staple"
    name = "Alice"
    password_hash = hash_password(password)

    async def _seed() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                team_id = (
                    await conn.execute(text("SELECT id FROM teams WHERE name='default'"))
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO users (team_id, name, email, password_hash) "
                        "VALUES (:t, :n, :e, :h)"
                    ),
                    {"t": team_id, "n": name, "e": email, "h": password_hash},
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    return {"email": email, "password": password, "name": name}
