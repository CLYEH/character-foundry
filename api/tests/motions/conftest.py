"""Fixtures for the T-033 motion suite.

Mirrors `tests/select_base/conftest.py` (DB + fakeredis + storage +
fake arq pool). The route-level tests run through the same FastAPI
surface; the worker tests bypass the TestClient and drive
`run_create_motion` with a synthetic ctx.

Helper seeders for Base / Alias rows live here because T-031 hasn't
landed yet — we can't go through the real alias-create endpoint, so
the suite seeds rows directly via SQL. Once T-031 ships, the alias
helper can be replaced with a route call without changing the
asserts.
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


# ---------------------------------------------------------------------------
# Domain seeders
# ---------------------------------------------------------------------------


async def seed_base_for_character_async(
    database_url: str,
    *,
    character_id: str,
    creation_session_id: str,
    storage_root: Path,
    image_bytes: bytes = b"placeholder-base-bytes",
) -> tuple[str, str]:
    """Async core for `seed_base_for_character` — see that wrapper for
    docs. The async form is callable from inside `pytest-asyncio` tests
    where `asyncio.run` would refuse to nest into the running loop."""
    cid = uuid.uuid4()
    bid = uuid.uuid4()
    image_key = f"checkpoints/{creation_session_id}/{cid}.png"

    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "INSERT INTO checkpoints (id, creation_session_id, sequence, "
                    "prompt, output_image_key) "
                    "VALUES (:cid, :sid, :seq, 'a base prompt', :okey)"
                ),
                {"cid": str(cid), "sid": creation_session_id, "seq": 1, "okey": image_key},
            )
            await conn.execute(
                text(
                    "INSERT INTO bases (id, character_id, from_checkpoint_id, image_key) "
                    "VALUES (:bid, :char, :ckpt, :ikey)"
                ),
                {
                    "bid": str(bid),
                    "char": character_id,
                    "ckpt": str(cid),
                    "ikey": image_key,
                },
            )
            # Mirror what select-base would do so reads of
            # `characters.base_id` resolve.
            await conn.execute(
                text("UPDATE characters SET base_id = :bid WHERE id = :char"),
                {"bid": str(bid), "char": character_id},
            )
            await conn.execute(
                text(
                    "UPDATE creation_sessions SET status='completed', completed_at=NOW() "
                    "WHERE id = :sid"
                ),
                {"sid": creation_session_id},
            )
    finally:
        await engine.dispose()

    target = storage_root / image_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)

    return str(bid), image_key


def seed_base_for_character(
    database_url: str,
    *,
    character_id: str,
    creation_session_id: str,
    storage_root: Path,
    image_bytes: bytes = b"placeholder-base-bytes",
) -> tuple[str, str]:
    """Seed a checkpoint + Base row for `character_id` and return
    `(base_id_str, image_key)`. Writes a placeholder file at the
    storage key so the worker can read parent bytes during tests.

    Direct SQL because the select-base route requires a real
    creation_session_id. We bypass the route to keep the test setup
    minimal — the row schema is the contract the worker sees.

    Sync wrapper around `seed_base_for_character_async` for use in
    sync route tests; pytest-asyncio worker tests should call the
    async core directly to avoid the "nested event loop" runtime error.
    """
    import asyncio as _asyncio

    return _asyncio.run(
        seed_base_for_character_async(
            database_url,
            character_id=character_id,
            creation_session_id=creation_session_id,
            storage_root=storage_root,
            image_bytes=image_bytes,
        )
    )


async def seed_alias_for_character_async(
    database_url: str,
    *,
    character_id: str,
    storage_root: Path,
    name: str = "casual_alias",
    image_bytes: bytes = b"placeholder-alias-bytes",
) -> tuple[str, str]:
    """Async core for `seed_alias_for_character` — see wrapper for docs."""
    aid = uuid.uuid4()
    image_key = f"characters/{character_id}/aliases/{aid}.png"

    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "INSERT INTO aliases (id, character_id, name, prompt, input_mode, "
                    "image_key) VALUES (:aid, :char, :name, 'an alias prompt', "
                    "'image2image', :ikey)"
                ),
                {
                    "aid": str(aid),
                    "char": character_id,
                    "name": name,
                    "ikey": image_key,
                },
            )
    finally:
        await engine.dispose()

    target = storage_root / image_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)

    return str(aid), image_key


def seed_alias_for_character(
    database_url: str,
    *,
    character_id: str,
    storage_root: Path,
    name: str = "casual_alias",
    image_bytes: bytes = b"placeholder-alias-bytes",
) -> tuple[str, str]:
    """Seed an Alias row for `character_id` and return
    `(alias_id_str, image_key)`. Direct SQL because T-031 hasn't
    landed yet — the row shape is what the motion service / worker
    actually depends on.

    Sync wrapper; async tests should call
    `seed_alias_for_character_async` directly.
    """
    import asyncio as _asyncio

    return _asyncio.run(
        seed_alias_for_character_async(
            database_url,
            character_id=character_id,
            storage_root=storage_root,
            name=name,
            image_bytes=image_bytes,
        )
    )


def seed_motion_row(
    database_url: str,
    *,
    parent_type: str,
    parent_id: str,
    motion_type: str,
    name: str,
    description: str | None = None,
    video_key: str | None = None,
) -> str:
    """Insert a non-deleted motion row directly. Used to set up
    duplicate-name / duplicate-preset assertions without going through
    the worker."""
    import asyncio as _asyncio

    mid = uuid.uuid4()
    base_id = parent_id if parent_type == "base" else None
    alias_id = parent_id if parent_type == "alias" else None
    if video_key is None:
        if parent_type == "base":
            video_key = f"bases/{parent_id}/motions/{mid}.mp4"
        else:
            video_key = f"aliases/{parent_id}/motions/{mid}.mp4"

    async def _insert() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO motions (id, base_id, alias_id, motion_type, name, "
                        "description, video_key) "
                        "VALUES (:mid, :bid, :aid, :mt, :n, :d, :vk)"
                    ),
                    {
                        "mid": str(mid),
                        "bid": base_id,
                        "aid": alias_id,
                        "mt": motion_type,
                        "n": name,
                        "d": description,
                        "vk": video_key,
                    },
                )
        finally:
            await engine.dispose()

    _asyncio.run(_insert())
    return str(mid)


def create_character_via_api(
    client: TestClient, token: str, *, name: str = "MotionChar"
) -> dict[str, Any]:
    """Drive `POST /v1/characters` and return the response payload.
    Reused by route + worker tests to set up an initial character +
    creation session pair."""
    resp = client.post(
        "/v1/characters",
        json={"name": name, "input_mode": "template"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
