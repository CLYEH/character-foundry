"""Fixtures for the T-088 MCP tool suite (`task.*` / `prompt.preview` / `meta.get`).

The `task.*` and `prompt.preview` tools resolve the caller from the MCP auth
contextvar and call the same service / repo layer the REST routes use, opening
their own short-lived `AsyncSession` (they run inside the JSON-RPC dispatch
loop, not a FastAPI request scope). So these tests:

  • drive the handlers DIRECTLY (`await task_get(...)`) with the auth contextvar
    set via `auth_as(...)` — bypassing the streamable-HTTP transport, which is
    already smoke-tested for the registry/transport wiring in
    `tests/mcp/test_skeleton.py`. The `meta.get` + `tools/list` tests DO go
    through the real transport (they need no DB, and the `_meta` extension lives
    on the transport layer).
  • seed real Postgres rows (cancel's `SELECT ... FOR UPDATE` and ownership
    scoping can't be faked honestly — same rationale as `tests/tasks/conftest`).
  • monkeypatch each tool module's dependency accessors (`async_session_factory`
    / `get_redis` / `get_arq_pool` / `get_storage` / `get_prompt_reconciler`)
    to test doubles — these can't be swapped via `app.dependency_overrides`
    because the tools don't run under FastAPI DI.

The parent `tests/mcp/conftest.py` autouse fixtures (`_mcp_env`,
`_mcp_db_session_stub`) still apply; the db-session stub patches
`app.mcp.auth.async_session_factory` (transport-time token auth), which is
independent of the tool-module factories patched here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.auth.scopes import CANONICAL_SCOPES
from tests.tasks.conftest import FakeArqPool, FakeJob

JWT_SECRET = "test-jwt-secret-dont-use-in-prod"

# child → parent; `teams` is migration-seeded.
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


# ---------------------------------------------------------------------------
# Auth contextvar + ToolError helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def auth_as(
    *,
    user_id: uuid.UUID | None,
    scopes: frozenset[str] = CANONICAL_SCOPES,
    client_id: str | None = None,
    is_m2m: bool = False,
) -> Iterator[None]:
    """Install an `MCPAuthContext` on the MCP auth contextvar for one block.

    Mirrors what `MCPAuthContextMiddleware` does at the ASGI layer, so a
    handler called directly sees the same `require_mcp_scopes(...)` state it
    would over the wire.
    """
    from app.mcp.auth import MCPAuthContext, mcp_auth_state_var

    ctx = MCPAuthContext(
        user_id=user_id,
        client_id=client_id,
        scopes=frozenset(scopes),
        is_m2m=is_m2m,
    )
    token = mcp_auth_state_var.set(ctx)
    try:
        yield
    finally:
        mcp_auth_state_var.reset(token)


def tool_error_code(exc: ToolError) -> str:
    """Extract the AgentError `code` from a tool error's JSON payload.

    Tool errors carry a JSON-serialized AgentError envelope in `args[0]`
    (see `app/mcp/auth.py::_agent_error_payload`).
    """
    text_payload = str(exc.args[0])
    brace = text_payload.find("{")
    assert brace != -1, f"expected JSON payload in tool error, got {text_payload!r}"
    return json.loads(text_payload[brace:])["error"]["code"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# DB migrate / clean (mirrors tests/tasks/conftest.py)
# ---------------------------------------------------------------------------


async def _delete_all(database_url: str) -> None:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for table in _TABLES_TO_CLEAN:
                await conn.execute(text(f"DELETE FROM {table}"))
    finally:
        await engine.dispose()


# NOT autouse: the `meta.get` / `tools/list` transport tests in this package
# need no database, and an autouse module fixture that depends on `database_url`
# would skip them when TEST_DATABASE_URL is unset. The DB-backed fixtures
# (`clean_tables`, `bind_tool_db`) request this explicitly instead.
@pytest.fixture(scope="module")
def migrate_once(alembic_config: Any, database_url: str) -> Iterator[None]:
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
def clean_tables(migrate_once: None, database_url: str) -> None:
    asyncio.run(_delete_all(database_url))


# ---------------------------------------------------------------------------
# Test doubles + tool-module dependency binding
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def fake_arq_pool() -> FakeArqPool:
    return FakeArqPool()


@pytest.fixture
async def bind_tool_db(
    migrate_once: None,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Point the tool modules' `async_session_factory` at the test database.

    Async fixture so the engine binds to the test's event loop — the handlers
    are awaited in that same loop, so their sessions use a live pool. The
    tools reference `async_session_factory` as a module global, so patching
    the bound name redirects their session creation without touching the
    process-wide lru-cached factory.
    """
    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    def _factory() -> async_sessionmaker[AsyncSession]:
        return factory

    monkeypatch.setattr("app.mcp.tools.task.async_session_factory", _factory)
    monkeypatch.setattr("app.mcp.tools.prompt.async_session_factory", _factory)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def bind_task_deps(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.aioredis.FakeRedis,
    fake_arq_pool: FakeArqPool,
) -> None:
    """Redirect `task.*` tools' redis + arq accessors to test doubles."""

    async def _redis() -> Any:
        return fake_redis

    async def _arq() -> Any:
        return fake_arq_pool

    monkeypatch.setattr("app.mcp.tools.task.get_redis", _redis)
    monkeypatch.setattr("app.mcp.tools.task.get_arq_pool", _arq)


# ---------------------------------------------------------------------------
# Seed helpers (direct AUTOCOMMIT INSERT — independent of the routes/tools)
# ---------------------------------------------------------------------------


async def _insert_user(database_url: str, *, email: str, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    from app.auth.passwords import hash_password

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
                    {"t": team_id, "n": name, "e": email, "h": hash_password("pw-not-used")},
                )
            ).scalar_one()
            return uuid.UUID(str(user_id)), uuid.UUID(str(team_id))
    finally:
        await engine.dispose()


async def _insert_character(
    database_url: str, *, owner_id: uuid.UUID, team_id: uuid.UUID, name: str, slug: str
) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "INSERT INTO characters (team_id, owner_id, name, slug) "
                        "VALUES (:t, :o, :n, :s) RETURNING id"
                    ),
                    {"t": team_id, "o": owner_id, "n": name, "s": slug},
                )
            ).scalar_one()
            return uuid.UUID(str(row))
    finally:
        await engine.dispose()


async def _insert_session_checkpoint_base(
    database_url: str, *, character_id: uuid.UUID, initiator_id: uuid.UUID
) -> tuple[uuid.UUID, str, uuid.UUID, uuid.UUID]:
    """Seed completed session + checkpoint + Base.

    Returns (base_id, image_key, session_id, checkpoint_id) — the session /
    checkpoint ids feed the T-084 fork / get_session / get_checkpoint tools.
    """
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
                        "(creation_session_id, sequence, prompt, output_image_key, "
                        " selected_as_base) "
                        "VALUES (:s, 1, 'seed prompt', :k, true) RETURNING id"
                    ),
                    {"s": session_id, "k": f"checkpoints/{session_id}/output/seq-1.png"},
                )
            ).scalar_one()
            image_key = f"checkpoints/{session_id}/output/seq-1.png"
            base_id = (
                await conn.execute(
                    text(
                        "INSERT INTO bases (character_id, from_checkpoint_id, image_key) "
                        "VALUES (:c, :ck, :k) RETURNING id"
                    ),
                    {"c": character_id, "ck": checkpoint_id, "k": image_key},
                )
            ).scalar_one()
            await conn.execute(
                text("UPDATE characters SET base_id = :b WHERE id = :c"),
                {"b": base_id, "c": character_id},
            )
            return (
                uuid.UUID(str(base_id)),
                image_key,
                uuid.UUID(str(session_id)),
                uuid.UUID(str(checkpoint_id)),
            )
    finally:
        await engine.dispose()


async def _insert_alias(database_url: str, *, character_id: uuid.UUID, name: str) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            alias_id = (
                await conn.execute(
                    text(
                        "INSERT INTO aliases "
                        "(character_id, name, prompt, input_mode, image_key) "
                        "VALUES (:c, :n, 'seed alias prompt', 'image2image', :k) RETURNING id"
                    ),
                    {"c": character_id, "n": name, "k": f"aliases/{uuid.uuid4()}.png"},
                )
            ).scalar_one()
            return uuid.UUID(str(alias_id))
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Task seeding via the service layer (so estimates / state are realistic)
# ---------------------------------------------------------------------------


async def seed_task(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    status: str = "queued",
    cancel_requested: bool = False,
    arq_pool: FakeArqPool,
) -> uuid.UUID:
    """Create a task row and force it into `status`.

    Async (awaited inside the test's event loop) and driven through the
    `bind_tool_db` session factory so it shares the test's engine/loop — a
    `asyncio.run(...)` here would crash inside the already-running test loop.

    Built via `task_service.create_task` (real estimate + arq enqueue) then
    patched to the target status with a direct UPDATE — the cancel-outcome
    matrix needs running/completed/failed/cancelled rows.
    """
    from app.services import task_service

    async with factory() as db:
        created = await task_service.create_task(
            db,
            arq_pool,  # type: ignore[arg-type]
            user_id=user_id,
            task_type="create_checkpoint",
            input_payload={"foo": "bar"},
        )
        task_id = created.task.id
    if status != "queued" or cancel_requested:
        # `cancel_task` keys off status + cancel_requested, but the tasks table
        # has a CHECK requiring terminal rows to carry completed_at, so set the
        # timestamps too. Computed in Python (one bind param each) to avoid
        # asyncpg's "inconsistent types deduced" when a param is reused in both
        # an assignment and a comparison.
        now = datetime.now(UTC)
        started = now if status in ("running", "completed", "failed") else None
        completed = now if status in ("completed", "failed", "cancelled") else None
        async with factory() as db:
            await db.execute(
                text(
                    "UPDATE tasks SET status = :st, cancel_requested = :cr, "
                    "started_at = :started, completed_at = :completed WHERE id = :id"
                ),
                {
                    "st": status,
                    "cr": cancel_requested,
                    "started": started,
                    "completed": completed,
                    "id": task_id,
                },
            )
            await db.commit()
    return task_id


# ---------------------------------------------------------------------------
# Pytest fixtures: seeded entities
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_user(database_url: str, clean_tables: None) -> dict[str, Any]:
    user_id, team_id = asyncio.run(
        _insert_user(database_url, email="alice@example.com", name="Alice")
    )
    return {"id": user_id, "team_id": team_id, "email": "alice@example.com"}


@pytest.fixture
def second_user(database_url: str, seeded_user: dict[str, Any]) -> dict[str, Any]:
    user_id, team_id = asyncio.run(_insert_user(database_url, email="bob@example.com", name="Bob"))
    return {"id": user_id, "team_id": team_id, "email": "bob@example.com"}


@pytest.fixture
def seeded_character(database_url: str, seeded_user: dict[str, Any]) -> dict[str, Any]:
    character_id = asyncio.run(
        _insert_character(
            database_url,
            owner_id=seeded_user["id"],
            team_id=seeded_user["team_id"],
            name="Alice-char",
            slug="alice-char",
        )
    )
    base_id, base_image_key, session_id, checkpoint_id = asyncio.run(
        _insert_session_checkpoint_base(
            database_url, character_id=character_id, initiator_id=seeded_user["id"]
        )
    )
    return {
        "id": character_id,
        "owner_id": seeded_user["id"],
        "base_id": base_id,
        "base_image_key": base_image_key,
        "session_id": session_id,
        "checkpoint_id": checkpoint_id,
    }


@pytest.fixture
def seeded_alias(database_url: str, seeded_character: dict[str, Any]) -> dict[str, Any]:
    alias_id = asyncio.run(
        _insert_alias(database_url, character_id=seeded_character["id"], name="suit-alias")
    )
    return {"id": alias_id, "character_id": seeded_character["id"]}


# ---------------------------------------------------------------------------
# Prompt-preview: reconciler + storage doubles
# ---------------------------------------------------------------------------


@pytest.fixture
def bind_prompt_deps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Redirect `prompt.preview` tool's redis / storage / reconciler accessors.

    Uses the project's `FakeReconcilerClient` over fakeredis so the reconciler
    never calls a real LLM, and a real `LocalFilesystemBackend` for signed-URL
    minting on alias / motion modes.
    """
    from app.prompt.reconciler import PromptReconciler
    from app.storage.local import LocalFilesystemBackend
    from tests.prompt_reconciler.conftest import FakeReconcilerClient

    def _responder(_system: str, _user: str) -> dict[str, Any]:
        return {
            "reconciled_note_en": "an elegant figure in classical attire",
            "removed_segments": [],
        }

    server = fakeredis.FakeServer()

    async def _redis() -> Any:
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    def _reconciler(_redis: Any) -> PromptReconciler:
        client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        return PromptReconciler(redis=client, client=FakeReconcilerClient(_responder))

    def _storage() -> LocalFilesystemBackend:
        return LocalFilesystemBackend(tmp_path / "storage")

    monkeypatch.setattr("app.mcp.tools.prompt.get_redis", _redis)
    monkeypatch.setattr("app.mcp.tools.prompt.get_prompt_reconciler", _reconciler)
    monkeypatch.setattr("app.mcp.tools.prompt.get_storage", _storage)


# ---------------------------------------------------------------------------
# character.* tool fixtures (T-084)
# ---------------------------------------------------------------------------


async def _insert_in_progress_session(
    database_url: str, *, character_id: uuid.UUID, initiator_id: uuid.UUID
) -> uuid.UUID:
    """Seed a bare in_progress creation session (no checkpoints / base).

    Used by the `character.abandon_session` test — abandon requires an active
    session (a completed one with a locked Base 409s).
    """
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            session_id = (
                await conn.execute(
                    text(
                        "INSERT INTO creation_sessions "
                        "(character_id, initiator_id, input_mode, status) "
                        "VALUES (:c, :u, 'template', 'in_progress') RETURNING id"
                    ),
                    {"c": character_id, "u": initiator_id},
                )
            ).scalar_one()
            return uuid.UUID(str(session_id))
    finally:
        await engine.dispose()


@pytest.fixture
def in_progress_session(database_url: str, seeded_character: dict[str, Any]) -> dict[str, Any]:
    session_id = asyncio.run(
        _insert_in_progress_session(
            database_url,
            character_id=seeded_character["id"],
            initiator_id=seeded_character["owner_id"],
        )
    )
    return {"id": session_id, "character_id": seeded_character["id"]}


class InlineCheckpointArqPool:
    """Duck-typed arq pool that runs `run_create_checkpoint` inline on enqueue.

    `character.create` enqueues a checkpoint task then polls it to completion.
    With no real arq worker in the test process, this pool runs the worker
    synchronously when `task_service.create_task` enqueues it (the same `ctx`
    shape `tests/checkpoints/test_create_checkpoint_worker.py` uses), so the
    task is already terminal by the time the tool's poll loop reads it —
    deterministic, no sleeps. Pass a failing `ai_client` to drive the
    checkpoint-failure path (the worker catches the AgentError and marks the
    task `failed`).
    """

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        redis: Any,
        storage: Any,
        ai_client: Any,
    ) -> None:
        self._factory = factory
        self._redis = redis
        self._storage = storage
        self._ai_client = ai_client
        self.enqueued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, function_name: str, *args: Any, **kwargs: Any) -> FakeJob:
        self.enqueued.append((function_name, args, kwargs))
        if function_name == "run_create_checkpoint":
            from app.workers.jobs.create_checkpoint import run_create_checkpoint

            ctx: dict[str, Any] = {
                "db_session_factory": self._factory,
                "redis": self._redis,
                "storage": self._storage,
                "ai_client": self._ai_client,
            }
            await run_create_checkpoint(ctx, str(kwargs["task_id"]))
        return FakeJob(job_id=str(kwargs.get("_job_id") or uuid.uuid4()))


@pytest.fixture
def character_storage(tmp_path: Path) -> Any:
    from app.storage.local import LocalFilesystemBackend

    root = tmp_path / "char-storage"
    root.mkdir(parents=True, exist_ok=True)
    return LocalFilesystemBackend(root)


@pytest.fixture
async def bind_character_db(
    migrate_once: None,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Point `app.mcp.tools.character`'s session factory at the test database.

    Same rationale as `bind_tool_db` for the task / prompt tools — the handlers
    reference `async_session_factory` as a module global, so patching the bound
    name redirects their sessions without touching the lru-cached factory.
    """
    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    def _factory() -> async_sessionmaker[AsyncSession]:
        return factory

    monkeypatch.setattr("app.mcp.tools.character.async_session_factory", _factory)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def bind_character_storage(monkeypatch: pytest.MonkeyPatch, character_storage: Any) -> Any:
    """Redirect the character tools' `get_storage()` to a test filesystem backend."""
    monkeypatch.setattr("app.mcp.tools.character.get_storage", lambda: character_storage)
    return character_storage


@pytest.fixture
def make_character_create_deps(
    monkeypatch: pytest.MonkeyPatch,
    bind_character_db: async_sessionmaker[AsyncSession],
    character_storage: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> Any:
    """Return `install(ai_client) -> InlineCheckpointArqPool`.

    Binds the character tool's redis + storage accessors once; each test calls
    the returned function with the AI client it wants (a `StubAIClient` for the
    happy paths, a failing one for the checkpoint-failure case) and gets back
    the inline pool whose `enqueue_job` runs the checkpoint worker synchronously.
    """
    factory = bind_character_db

    async def _redis() -> Any:
        return fake_redis

    monkeypatch.setattr("app.mcp.tools.character.get_redis", _redis)
    monkeypatch.setattr("app.mcp.tools.character.get_storage", lambda: character_storage)

    def _install(ai_client: Any) -> InlineCheckpointArqPool:
        pool = InlineCheckpointArqPool(
            factory=factory, redis=fake_redis, storage=character_storage, ai_client=ai_client
        )

        async def _arq() -> Any:
            return pool

        monkeypatch.setattr("app.mcp.tools.character.get_arq_pool", _arq)
        return pool

    return _install


# Re-export so test modules can `from tests.mcp.tools.conftest import ...`.
__all__ = [
    "FakeArqPool",
    "InlineCheckpointArqPool",
    "auth_as",
    "seed_task",
    "tool_error_code",
]
