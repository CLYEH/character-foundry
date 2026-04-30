"""DB-backed fixtures for the T-031 alias-create suite.

Pattern mirrors `tests/prompt_preview/alias_motion/conftest.py` because
the route tests need real character + base + reference + mask rows;
the worker tests bypass the TestClient and drive `run_create_alias`
with a synthetic ctx (same pattern as `test_create_checkpoint_worker`).

The Base seed flow is identical to the prompt-preview suite — character
→ creation_session (status='completed') → checkpoint → base — because
the alias-create endpoint enforces the same "Base must exist" guard
the preview endpoint does.
"""

from __future__ import annotations

import asyncio
import io
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.tasks.conftest import FakeArqPool

JWT_SECRET = "test-jwt-secret-dont-use-in-prod"

# Order: child → parent. `teams` is migration-seeded.
_TABLES_TO_CLEAN = (
    "refresh_tokens",
    "tasks",
    "generation_logs",
    "motions",
    "aliases",
    "masks",
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


# ---------------------------------------------------------------------------
# Seed helpers — direct SQL via AUTOCOMMIT, mirrors prompt_preview/alias_motion
# ---------------------------------------------------------------------------


async def _insert_user(
    database_url: str, *, email: str, name: str, password_hash: str
) -> tuple[uuid.UUID, uuid.UUID]:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            team_id = (
                await conn.execute(text("SELECT id FROM teams WHERE name='default'"))
            ).scalar_one()
            user_id = (
                await conn.execute(
                    text(
                        "INSERT INTO users (team_id, name, email, password_hash) "
                        "VALUES (:t, :n, :e, :h) RETURNING id"
                    ),
                    {"t": team_id, "n": name, "e": email, "h": password_hash},
                )
            ).scalar_one()
            return uuid.UUID(str(user_id)), uuid.UUID(str(team_id))
    finally:
        await engine.dispose()


async def _insert_character(
    database_url: str,
    *,
    owner_id: uuid.UUID,
    team_id: uuid.UUID,
    name: str,
    slug: str,
) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            row_id = (
                await conn.execute(
                    text(
                        "INSERT INTO characters (team_id, owner_id, name, slug) "
                        "VALUES (:t, :o, :n, :s) RETURNING id"
                    ),
                    {"t": team_id, "o": owner_id, "n": name, "s": slug},
                )
            ).scalar_one()
            return uuid.UUID(str(row_id))
    finally:
        await engine.dispose()


async def _seed_base(
    database_url: str,
    *,
    character_id: uuid.UUID,
    initiator_id: uuid.UUID,
    base_image_key: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            session_id = (
                await conn.execute(
                    text(
                        "INSERT INTO creation_sessions "
                        "(character_id, initiator_id, input_mode, status) "
                        "VALUES (:c, :u, 'template', 'completed') RETURNING id"
                    ),
                    {"c": character_id, "u": initiator_id},
                )
            ).scalar_one()
            checkpoint_id = (
                await conn.execute(
                    text(
                        "INSERT INTO checkpoints "
                        "(creation_session_id, sequence, prompt, "
                        " output_image_key, selected_as_base) "
                        "VALUES (:s, 1, 'seed prompt', :k, true) "
                        "RETURNING id"
                    ),
                    {"s": session_id, "k": base_image_key},
                )
            ).scalar_one()
            base_id = (
                await conn.execute(
                    text(
                        "INSERT INTO bases "
                        "(character_id, from_checkpoint_id, image_key) "
                        "VALUES (:c, :ck, :k) RETURNING id"
                    ),
                    {
                        "c": character_id,
                        "ck": checkpoint_id,
                        "k": base_image_key,
                    },
                )
            ).scalar_one()
            await conn.execute(
                text("UPDATE characters SET base_id = :b WHERE id = :c"),
                {"b": base_id, "c": character_id},
            )
            return uuid.UUID(str(base_id)), uuid.UUID(str(checkpoint_id))
    finally:
        await engine.dispose()


def _png_bytes(*, size: int = 32, color: tuple[int, int, int, int] = (255, 0, 0, 255)) -> bytes:
    """Build a tiny valid PNG. Used both in the upload route tests and as
    the seeded Base image bytes that the alias worker reads back."""
    im = Image.new("RGBA", (size, size), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pytest fixtures — users / character / base / storage / TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_user(database_url: str, clean_tables: None) -> dict[str, Any]:
    from app.auth.passwords import hash_password

    user_id, team_id = asyncio.run(
        _insert_user(
            database_url,
            email="alice@example.com",
            name="Alice",
            password_hash=hash_password("correct-horse-battery-staple"),
        )
    )
    return {"id": user_id, "team_id": team_id, "email": "alice@example.com"}


@pytest.fixture
def second_user(database_url: str, seeded_user: dict[str, Any]) -> dict[str, Any]:
    from app.auth.passwords import hash_password

    user_id, team_id = asyncio.run(
        _insert_user(
            database_url,
            email="bob@example.com",
            name="Bob",
            password_hash=hash_password("also-not-guessable"),
        )
    )
    return {"id": user_id, "team_id": team_id, "email": "bob@example.com"}


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_base_to_storage(storage_root: Path, key: str, payload: bytes) -> None:
    """Materialize the base image bytes the worker will read back."""
    full = storage_root / key
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(payload)


@pytest.fixture
def seeded_character_with_base(
    database_url: str,
    seeded_user: dict[str, Any],
    storage_root: Path,
) -> dict[str, Any]:
    """Character + Base + actual base PNG on disk.

    The worker reads `base_image_key` via the storage backend, so the
    test harness must materialize a real PNG at the seeded key —
    skipping that produces a STORAGE_NOT_FOUND in the worker even
    though the DB rows are valid.
    """
    character_id = asyncio.run(
        _insert_character(
            database_url,
            owner_id=seeded_user["id"],
            team_id=seeded_user["team_id"],
            name="Alice-char",
            slug="alice-char",
        )
    )
    # Use a deterministic key tied to the character id so cleanup is
    # straightforward and tests can assert against it.
    base_key = f"checkpoints/seed-{character_id}/base.png"
    base_id, checkpoint_id = asyncio.run(
        _seed_base(
            database_url,
            character_id=character_id,
            initiator_id=seeded_user["id"],
            base_image_key=base_key,
        )
    )
    base_payload = _png_bytes(size=64, color=(0, 128, 255, 255))
    _write_base_to_storage(storage_root, base_key, base_payload)
    return {
        "id": character_id,
        "owner_id": seeded_user["id"],
        "base_id": base_id,
        "checkpoint_id": checkpoint_id,
        "base_image_key": base_key,
        "base_bytes": base_payload,
    }


@pytest.fixture
def character_without_base(
    database_url: str,
    seeded_user: dict[str, Any],
) -> dict[str, Any]:
    """Character with no Base — used to verify CONFLICT_BASE_NOT_SET."""
    character_id = asyncio.run(
        _insert_character(
            database_url,
            owner_id=seeded_user["id"],
            team_id=seeded_user["team_id"],
            name="No-base-char",
            slug="no-base-char",
        )
    )
    return {"id": character_id, "owner_id": seeded_user["id"]}


def _access_token_for(user_id: uuid.UUID, team_id: uuid.UUID) -> str:
    from app.auth.jwt import sign_access_token

    token, _ = sign_access_token(user_id=user_id, team_id=team_id)
    return token


@pytest.fixture
def access_token(seeded_user: dict[str, Any]) -> str:
    return _access_token_for(seeded_user["id"], seeded_user["team_id"])


@pytest.fixture
def second_access_token(second_user: dict[str, Any]) -> str:
    return _access_token_for(second_user["id"], second_user["team_id"])


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(
    seeded_user: dict[str, Any],
    fake_redis: fakeredis.aioredis.FakeRedis,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> Iterator[TestClient]:
    """DB-backed TestClient. Storage is the on-disk LocalFilesystemBackend
    rooted at `storage_root` so the route + worker share the same view
    of uploaded files."""
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
