"""DB-backed fixtures for the T-035 alias / motion preview surface.

The hermetic conftest in `tests/prompt_preview/conftest.py` covers
create_base mode (no DB lookup on the happy path); these tests need
real character / base / alias / mask rows because the alias and motion
flows resolve parents + ownership through the repos. Sub-directory
layout means the `client` fixture defined here overrides the hermetic
sibling for tests in this folder only — pytest's nearest-conftest wins.

Pattern mirrors `tests/checkpoints/conftest.py` for the user / token /
storage scaffolding; T-035 layers on character / base / alias / mask
seed helpers because each preview test needs a different parent shape.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.api.deps import (
    get_prompt_reconciler_dep,
    get_storage,
)
from app.core.redis_client import get_redis
from app.main import app
from app.prompt.reconciler import PromptReconciler
from app.storage.local import LocalFilesystemBackend
from tests.prompt_reconciler.conftest import FakeReconcilerClient

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


def _default_responder(_system: str, _user: str) -> dict[str, Any]:
    return {
        "reconciled_note_en": "an updated outfit, suit and tie",
        "removed_segments": [],
    }


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


# Reconciler fake exposed to tests so they can assert call counts / inspect
# user prompts the LLM would have seen.
@pytest.fixture
def fake_reconciler_client() -> FakeReconcilerClient:
    return FakeReconcilerClient(_default_responder)


# ---------------------------------------------------------------------------
# User / character / alias / mask seed helpers.
#
# Direct INSERT-via-AUTOCOMMIT (mirroring tests/checkpoints/conftest.py) so
# fixtures don't depend on the route surfaces under test. Each fixture
# returns a plain dict so tests can read ids without dragging ORM rows
# across event loops.
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


async def _insert_creation_session_and_checkpoint(
    database_url: str,
    *,
    character_id: uuid.UUID,
    initiator_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a completed creation_session + one checkpoint so a Base row can be
    inserted with `from_checkpoint_id` satisfying the FK."""
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
                    {
                        "s": session_id,
                        "k": f"checkpoints/{session_id}/output/seq-1.png",
                    },
                )
            ).scalar_one()
            return uuid.UUID(str(session_id)), uuid.UUID(str(checkpoint_id))
    finally:
        await engine.dispose()


async def _insert_base(
    database_url: str,
    *,
    character_id: uuid.UUID,
    from_checkpoint_id: uuid.UUID,
    image_key: str,
) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            base_id = (
                await conn.execute(
                    text(
                        "INSERT INTO bases "
                        "(character_id, from_checkpoint_id, image_key) "
                        "VALUES (:c, :ck, :k) RETURNING id"
                    ),
                    {"c": character_id, "ck": from_checkpoint_id, "k": image_key},
                )
            ).scalar_one()
            # Pin base_id back onto characters so DTO builders + ownership
            # paths see a complete character.
            await conn.execute(
                text("UPDATE characters SET base_id = :b WHERE id = :c"),
                {"b": base_id, "c": character_id},
            )
            return uuid.UUID(str(base_id))
    finally:
        await engine.dispose()


async def _insert_alias(
    database_url: str,
    *,
    character_id: uuid.UUID,
    name: str,
    image_key: str,
) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            alias_id = (
                await conn.execute(
                    text(
                        "INSERT INTO aliases "
                        "(character_id, name, prompt, input_mode, image_key) "
                        "VALUES (:c, :n, 'seed alias prompt', 'image2image', :k) "
                        "RETURNING id"
                    ),
                    {"c": character_id, "n": name, "k": image_key},
                )
            ).scalar_one()
            return uuid.UUID(str(alias_id))
    finally:
        await engine.dispose()


async def _insert_mask(
    database_url: str,
    *,
    character_id: uuid.UUID,
    uploaded_by: uuid.UUID,
) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            mask_id_raw = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO masks "
                    "(id, character_id, uploaded_by_user_id, "
                    " storage_key, mime_type, size_bytes) "
                    "VALUES (:i, :c, :u, :k, 'image/png', 1024)"
                ),
                {
                    "i": mask_id_raw,
                    "c": character_id,
                    "u": uploaded_by,
                    "k": f"creation-sessions/{character_id}/masks/{mask_id_raw}.png",
                },
            )
            return mask_id_raw
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Pytest fixtures
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
def seeded_character(
    database_url: str,
    seeded_user: dict[str, Any],
) -> dict[str, Any]:
    """Seed a character with a Base set up — alias / motion preview both
    require the parent character to have an immutable Base."""
    character_id = asyncio.run(
        _insert_character(
            database_url,
            owner_id=seeded_user["id"],
            team_id=seeded_user["team_id"],
            name="Alice-char",
            slug="alice-char",
        )
    )
    session_id, checkpoint_id = asyncio.run(
        _insert_creation_session_and_checkpoint(
            database_url,
            character_id=character_id,
            initiator_id=seeded_user["id"],
        )
    )
    base_image_key = f"checkpoints/{session_id}/output/seq-1.png"
    base_id = asyncio.run(
        _insert_base(
            database_url,
            character_id=character_id,
            from_checkpoint_id=checkpoint_id,
            image_key=base_image_key,
        )
    )
    return {
        "id": character_id,
        "owner_id": seeded_user["id"],
        "base_id": base_id,
        "base_image_key": base_image_key,
        "creation_session_id": session_id,
        "checkpoint_id": checkpoint_id,
    }


@pytest.fixture
def seeded_alias(
    database_url: str,
    seeded_character: dict[str, Any],
) -> dict[str, Any]:
    alias_id = asyncio.run(
        _insert_alias(
            database_url,
            character_id=seeded_character["id"],
            name="suit-alias",
            image_key=f"aliases/{uuid.uuid4()}.png",
        )
    )
    return {"id": alias_id, "character_id": seeded_character["id"]}


@pytest.fixture
def seeded_mask(
    database_url: str,
    seeded_character: dict[str, Any],
    seeded_user: dict[str, Any],
) -> uuid.UUID:
    return asyncio.run(
        _insert_mask(
            database_url,
            character_id=seeded_character["id"],
            uploaded_by=seeded_user["id"],
        )
    )


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
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def client(
    seeded_user: dict[str, Any],
    fake_redis_server: fakeredis.FakeServer,
    fake_reconciler_client: FakeReconcilerClient,
    storage_root: Path,
) -> Iterator[TestClient]:
    """DB-backed TestClient. Overrides the hermetic sibling client by
    being declared in this sub-conftest (pytest's nearest-fixture rule).

    Reconciler is wired against fakeredis + the test's `fake_reconciler_client`
    so tests can drive any LLM response and inspect call counts the same
    way the hermetic suite does. DB / auth / storage are real."""
    from app.db.session import async_session_factory, get_engine

    get_engine.cache_clear()
    async_session_factory.cache_clear()

    async def _redis_override() -> Any:
        return fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)

    async def _reconciler_override() -> PromptReconciler:
        redis = fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        return PromptReconciler(redis=redis, client=fake_reconciler_client)

    app.dependency_overrides[get_redis] = _redis_override
    app.dependency_overrides[get_prompt_reconciler_dep] = _reconciler_override
    app.dependency_overrides[get_storage] = lambda: LocalFilesystemBackend(storage_root)
    # We DO NOT override get_current_user — auth_headers + a real JWT
    # exercise the auth path end-to-end so non-owner / wrong-token cases
    # surface real 401 / 403 responses.
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for dep in (get_redis, get_prompt_reconciler_dep, get_storage):
            app.dependency_overrides.pop(dep, None)
