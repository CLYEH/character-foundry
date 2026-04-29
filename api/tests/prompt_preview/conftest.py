"""Hermetic fixtures for `POST /v1/prompt/preview` (T-019 + T-035).

The endpoint is auth-gated and now (T-035) takes DB + storage deps for
alias / motion modes. T-019-style create_base tests don't need real
Postgres — the only DB lookup on that path is for `base_checkpoint_id`,
which the create_base happy / cache / OpenAPI cases don't supply — so
this conftest stays hermetic by overriding `db_session` + `get_storage`
with fakes alongside the existing user / redis / reconciler fakes.

Alias / motion tests (the new T-035 surface) need real character /
alias / motion / mask rows to exercise ownership + parent resolution;
they live in `test_prompt_preview_alias_motion.py` and bring a
DB-backed `client` fixture that *replaces* the hermetic one in this
file. Pytest fixture resolution treats the closest definition as
authoritative, so the override is local to that module.

Cross-loop redis: `fakeredis.aioredis.FakeRedis` instances bind their
internal asyncio.Queue to the loop that first reads/writes. TestClient
runs the FastAPI app in a portal worker loop; pytest-asyncio's
`asyncio_mode = "auto"` runs the test body on a different loop. Sharing
one `aioredis.FakeRedis` across both raises "bound to a different event
loop". So we share a `FakeServer` (the in-memory data store) and mint a
fresh async client per FastAPI request, plus a sync client for tests
that want to inspect keys directly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    db_session,
    get_current_user,
    get_prompt_reconciler_dep,
    get_storage,
)
from app.core.redis_client import get_redis
from app.main import app
from app.models.user import User
from app.prompt.reconciler import PromptReconciler
from tests.prompt_reconciler.conftest import FakeReconcilerClient


def _default_responder(_system: str, _user: str) -> dict[str, Any]:
    return {
        "reconciled_note_en": "an elegant figure in classical attire",
        "removed_segments": [],
    }


class _FakeDBSession:
    """Minimal AsyncSession stand-in for hermetic create_base tests.

    Returns None for every `db.get(...)` and a no-op `Result` for
    `db.execute(...)` — enough to keep the route from raising when
    `db_session` is wired but no actual lookup is performed. Tests that
    need real lookups bring their own DB-backed `client` fixture and
    don't see this fake.
    """

    async def get(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        class _Result:
            def scalar_one_or_none(self) -> None:
                return None

            def scalars(self) -> _Result:
                return self

            def all(self) -> list[Any]:
                return []

        return _Result()


class _FakeStorage:
    """Storage fake — only `get_signed_url` is exercised in this suite,
    and only by alias / motion paths that bring their own DB-backed
    fixtures. The hermetic surface never actually calls it; this is a
    safety net so a path mistake surfaces as an obvious AttributeError
    rather than swallowing the failure."""

    def get_signed_url(self, key: str, *, expires_in_seconds: int) -> str:  # noqa: ARG002
        return f"https://signed.test/{key}"


@pytest.fixture
def fake_server() -> fakeredis.FakeServer:
    """In-memory redis data store shared between async (per-request) and
    sync (test-introspection) clients."""
    return fakeredis.FakeServer()


@pytest.fixture
def sync_redis(fake_server: fakeredis.FakeServer) -> fakeredis.FakeStrictRedis:
    """Sync handle on the same server — for tests that scan keys from
    the outer test loop without touching async Redis (which would bind
    its queue to that loop and conflict with TestClient's worker loop).
    """
    return fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)


@pytest.fixture
def fake_reconciler_client() -> FakeReconcilerClient:
    return FakeReconcilerClient(_default_responder)


@pytest.fixture
def fake_user() -> User:
    return User(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="Tester",
        email="tester@example.com",
        password_hash="not-used",
    )


@pytest.fixture
def client(
    fake_server: fakeredis.FakeServer,
    fake_user: User,
    fake_reconciler_client: FakeReconcilerClient,
) -> Iterator[TestClient]:
    async def _redis_override() -> fakeredis.aioredis.FakeRedis:
        # Per-request client → binds to the request's worker loop. Server
        # state is shared across requests (and with the test's sync client),
        # so cache-hit semantics still hold.
        return fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)

    async def _user_override() -> User:
        return fake_user

    async def _reconciler_override() -> PromptReconciler:
        redis = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
        return PromptReconciler(redis=redis, client=fake_reconciler_client)

    async def _db_override() -> AsyncIterator[_FakeDBSession]:
        yield _FakeDBSession()

    def _storage_override() -> _FakeStorage:
        return _FakeStorage()

    app.dependency_overrides[get_redis] = _redis_override
    app.dependency_overrides[get_current_user] = _user_override
    app.dependency_overrides[get_prompt_reconciler_dep] = _reconciler_override
    app.dependency_overrides[db_session] = _db_override
    app.dependency_overrides[get_storage] = _storage_override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for dep in (
            get_redis,
            get_current_user,
            get_prompt_reconciler_dep,
            db_session,
            get_storage,
        ):
            app.dependency_overrides.pop(dep, None)
