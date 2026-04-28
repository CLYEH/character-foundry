"""Fixtures for `POST /v1/prompt/preview` (T-019).

The endpoint is auth-gated but doesn't read the DB or storage, so this
suite stays hermetic by overriding `get_current_user` directly with a
synthetic `User`. Reconciler is wired against `fakeredis` + a
`FakeReconcilerClient` so tests can drive any LLM response — including
PROMPT_CONFLICT failures — without spinning up Postgres or a real LLM.

Cross-loop redis: `fakeredis.aioredis.FakeRedis` instances bind their
internal asyncio.Queue to the loop that first reads/writes. TestClient
runs the FastAPI app in a portal worker loop; pytest-asyncio's
`asyncio_mode = "auto"` runs the test body on a different loop. Sharing
one `aioredis.FakeRedis` across both raises "bound to a different event
loop". So we share a `FakeServer` (the in-memory data store) and mint a
fresh async client per FastAPI request, plus a sync client for tests
that want to inspect keys directly. Both attach to the same server, so
cache state is shared, but no asyncio primitive crosses loops.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_current_user,
    get_prompt_reconciler_dep,
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

    app.dependency_overrides[get_redis] = _redis_override
    app.dependency_overrides[get_current_user] = _user_override
    app.dependency_overrides[get_prompt_reconciler_dep] = _reconciler_override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for dep in (get_redis, get_current_user, get_prompt_reconciler_dep):
            app.dependency_overrides.pop(dep, None)
