"""Fixtures for the T-013 task suite.

Integration-leaning by design — task_service does row-level locking
(`SELECT ... FOR UPDATE`) that fakes can't represent honestly, so we
hit a real Postgres via the same alembic-upgrade pattern the auth tests
use. Redis is faked (fakeredis 2.x covers pubsub), and the arq pool
is replaced by a tiny in-memory stand-in that records `enqueue_job` /
`abort_job` calls without speaking arq's wire protocol.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    command.upgrade(alembic_config, "head")
    yield


@pytest.fixture(autouse=True)
def _reset_session_cache() -> Iterator[None]:
    """Clear the lru-cached engine + session factory before every test.

    pytest-asyncio gives each test its own event loop. The lru-cached
    `get_engine()` / `async_session_factory()` in `app/db/session.py`
    bind their AsyncEngine to whichever loop ran first; subsequent
    tests then get `RuntimeError: Event loop is closed` when the
    generator's `async_session_factory()` tries to reuse a stale pool.
    Clearing here means each test starts fresh.
    """
    from app.db.session import async_session_factory, get_engine

    get_engine.cache_clear()
    async_session_factory.cache_clear()
    yield
    get_engine.cache_clear()
    async_session_factory.cache_clear()


@pytest.fixture
def clean_tables(database_url: str) -> None:
    asyncio.run(_delete_all(database_url))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeJob:
    """arq.Job-shaped record returned by FakeArqPool.queued_jobs()."""

    job_id: str


@dataclass
class FakeArqPool:
    """In-memory stand-in for arq's ArqRedis pool.

    Records every enqueue/abort so tests can assert what the service
    layer asked for. `queued_jobs()` returns whatever has been enqueued
    minus what's been aborted, in insertion order — close enough to the
    real pool's semantics for tests of `queue_position`.
    """

    enqueued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    aborted: list[str] = field(default_factory=list)
    _queue: list[FakeJob] = field(default_factory=list)

    async def enqueue_job(
        self,
        function_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> FakeJob:
        job_id = kwargs.get("_job_id") or str(uuid.uuid4())
        self.enqueued.append((function_name, args, kwargs))
        self._queue.append(FakeJob(job_id=job_id))
        return FakeJob(job_id=job_id)

    async def queued_jobs(self) -> list[FakeJob]:
        return [j for j in self._queue if j.job_id not in self.aborted]

    async def abort_job(self, job_id: str) -> None:
        self.aborted.append(job_id)


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def fake_arq_pool() -> FakeArqPool:
    return FakeArqPool()


# ---------------------------------------------------------------------------
# DB session factory bound to TEST_DATABASE_URL — bypasses the lru_cached
# `async_session_factory()` so tests using this fixture don't poison the
# process-wide cache for adjacent tests.
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
# Seeded user + JWT for route-level tests
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


@pytest.fixture
def access_token(seeded_user: dict[str, Any]) -> str:
    from app.auth.jwt import sign_access_token

    token, _ = sign_access_token(user_id=seeded_user["id"], team_id=uuid.UUID(int=0))
    return token


@pytest.fixture
def client(
    seeded_user: dict[str, Any],
    fake_redis: fakeredis.aioredis.FakeRedis,
    fake_arq_pool: FakeArqPool,
    tmp_path: Path,
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
    app.dependency_overrides[get_storage] = lambda: LocalFilesystemBackend(tmp_path / "storage")
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for dep in (get_redis, get_arq_pool, get_storage):
            app.dependency_overrides.pop(dep, None)
