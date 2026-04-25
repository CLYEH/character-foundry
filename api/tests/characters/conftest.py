"""Fixtures for the T-016 character + creation_session suites.

Mirrors tests/tasks/conftest.py: real Postgres, fakeredis for the
seq-bootstrap path, fresh sessions per test (the lru-cached engine
otherwise dies on event-loop turnover).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

JWT_SECRET = "test-jwt-secret-dont-use-in-prod"

# Order: child → parent. `teams` is migration-seeded.
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
def _migrate_once(alembic_config: Any, database_url: str) -> Iterator[None]:
    os.environ["JWT_SECRET"] = JWT_SECRET
    os.environ.setdefault("STORAGE_SIGNED_URL_SECRET", "test-storage-secret")
    command.upgrade(alembic_config, "head")
    yield


@pytest.fixture(autouse=True)
def _reset_session_cache() -> Iterator[None]:
    """See tests/tasks/conftest.py — pytest-asyncio gives each test
    its own loop, and the lru-cached engine binds to the first loop
    it sees. Drop the cache between tests so each gets a fresh one."""
    from app.db.session import async_session_factory, get_engine

    get_engine.cache_clear()
    async_session_factory.cache_clear()
    yield
    get_engine.cache_clear()
    async_session_factory.cache_clear()


@pytest.fixture
def clean_tables(database_url: str) -> None:
    asyncio.run(_delete_all(database_url))


@pytest.fixture
def fake_redis_server() -> fakeredis.FakeServer:
    """Underlying in-memory fakeredis server. Shared between async +
    sync clients so tests can assert state via a fresh sync client
    without running into "Queue bound to a different event loop" errors
    that show up when TestClient and `asyncio.run` mint separate loops."""
    return fakeredis.FakeServer()


@pytest.fixture
def fake_redis(fake_redis_server: fakeredis.FakeServer) -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)


@pytest.fixture
def fake_redis_sync(fake_redis_server: fakeredis.FakeServer) -> fakeredis.FakeStrictRedis:
    """Sync companion bound to the same FakeServer — use this in tests
    that need to read keys back without an event loop."""
    return fakeredis.FakeStrictRedis(server=fake_redis_server, decode_responses=True)


# ---------------------------------------------------------------------------
# DB session bound to TEST_DATABASE_URL (bypasses lru_cached factory).
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session(database_url: str) -> AsyncIterator[Any]:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# User seeding helpers
# ---------------------------------------------------------------------------


async def _insert_user(
    database_url: str, *, email: str, name: str, password_hash: str
) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            team_id = (
                await conn.execute(text("SELECT id FROM teams WHERE name='default'"))
            ).scalar_one()
            row = (
                await conn.execute(
                    text(
                        "INSERT INTO users (team_id, name, email, password_hash) "
                        "VALUES (:t, :n, :e, :h) RETURNING id"
                    ),
                    {"t": team_id, "n": name, "e": email, "h": password_hash},
                )
            ).scalar_one()
            return uuid.UUID(str(row))
    finally:
        await engine.dispose()


async def _team_id(database_url: str) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(text("SELECT id FROM teams WHERE name='default'"))
            ).scalar_one()
            return uuid.UUID(str(row))
    finally:
        await engine.dispose()


@pytest.fixture
def default_team_id(database_url: str) -> uuid.UUID:
    return asyncio.run(_team_id(database_url))


@pytest.fixture
def seeded_user(database_url: str, clean_tables: None) -> dict[str, Any]:
    from app.auth.passwords import hash_password

    email = "alice@example.com"
    password = "correct-horse-battery-staple"
    user_id = asyncio.run(
        _insert_user(
            database_url,
            email=email,
            name="Alice",
            password_hash=hash_password(password),
        )
    )
    return {"id": user_id, "email": email, "password": password, "name": "Alice"}


@pytest.fixture
def second_user(database_url: str, seeded_user: dict[str, Any]) -> dict[str, Any]:
    from app.auth.passwords import hash_password

    email = "bob@example.com"
    password = "also-not-guessable"
    user_id = asyncio.run(
        _insert_user(
            database_url,
            email=email,
            name="Bob",
            password_hash=hash_password(password),
        )
    )
    return {"id": user_id, "email": email, "password": password, "name": "Bob"}


def _access_token_for(user_id: uuid.UUID, team_id: uuid.UUID) -> str:
    from app.auth.jwt import sign_access_token

    token, _ = sign_access_token(user_id=user_id, team_id=team_id)
    return token


@pytest.fixture
def access_token(seeded_user: dict[str, Any], default_team_id: uuid.UUID) -> str:
    return _access_token_for(seeded_user["id"], default_team_id)


@pytest.fixture
def second_access_token(second_user: dict[str, Any], default_team_id: uuid.UUID) -> str:
    return _access_token_for(second_user["id"], default_team_id)


@pytest.fixture
def client(
    seeded_user: dict[str, Any],
    fake_redis: fakeredis.aioredis.FakeRedis,
    tmp_path: Path,
) -> Iterator[TestClient]:
    from app.api.deps import get_storage
    from app.core.redis_client import get_redis
    from app.db.session import async_session_factory, get_engine
    from app.main import app
    from app.storage.local import LocalFilesystemBackend

    get_engine.cache_clear()
    async_session_factory.cache_clear()

    async def _redis_override() -> Any:
        return fake_redis

    app.dependency_overrides[get_redis] = _redis_override
    app.dependency_overrides[get_storage] = lambda: LocalFilesystemBackend(tmp_path / "storage")
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for dep in (get_redis, get_storage):
            app.dependency_overrides.pop(dep, None)


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
