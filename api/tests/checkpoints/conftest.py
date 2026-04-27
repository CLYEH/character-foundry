"""Fixtures for the T-017 checkpoint suite — DB + fakeredis + storage.

Mirrors `tests/creation_sessions/conftest.py` because the route-level
tests run through the same FastAPI surface. The worker tests bypass the
TestClient and drive `run_create_checkpoint` with a synthetic ctx (same
pattern as `test_noop_worker.py`).
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
from tests.tasks.conftest import FakeArqPool

JWT_SECRET = "test-jwt-secret-dont-use-in-prod"

_TABLES_TO_CLEAN = (
    "refresh_tokens",
    "tasks",
    # generation_logs has FK to users(RESTRICT) — clean before users.
    # T-017's worker writes a row per successful checkpoint, so this
    # suite (and any adjacent one running in the same session) needs
    # the partition cleaned ahead of the user delete.
    "generation_logs",
    "motions",
    "aliases",
    "bases",
    "reference_images",
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
    os.environ.setdefault("AI_STUB_MODE", "true")
    command.upgrade(alembic_config, "head")
    yield


@pytest.fixture(autouse=True)
def _reset_session_cache() -> Iterator[None]:
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
    return fakeredis.FakeServer()


@pytest.fixture
def fake_redis(fake_redis_server: fakeredis.FakeServer) -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)


@pytest.fixture
def fake_arq_pool() -> FakeArqPool:
    return FakeArqPool()


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
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def client(
    seeded_user: dict[str, Any],
    fake_redis: fakeredis.aioredis.FakeRedis,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> Iterator[TestClient]:
    from app.api.deps import get_storage
    from app.core.redis_client import get_arq_pool, get_redis
    from app.db.session import async_session_factory, get_engine
    from app.main import app
    from app.storage.local import LocalFilesystemBackend

    get_engine.cache_clear()
    async_session_factory.cache_clear()

    async def _redis_override() -> Any:
        return fake_redis

    async def _arq_override() -> Any:
        return fake_arq_pool

    app.dependency_overrides[get_redis] = _redis_override
    app.dependency_overrides[get_arq_pool] = _arq_override
    app.dependency_overrides[get_storage] = lambda: LocalFilesystemBackend(storage_root)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for dep in (get_redis, get_arq_pool, get_storage):
            app.dependency_overrides.pop(dep, None)


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
