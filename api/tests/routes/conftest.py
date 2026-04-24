"""Fixtures for route-level tests that don't need real DB / Redis / storage.

`/health` and `/v1/meta` only exercise dependency-injection seams, so tests
override `db_session`, `get_redis`, and `get_storage` with fakes. That keeps
these tests hermetic (no `TEST_DATABASE_URL`, no live Redis) while still
covering the real wiring from route → dep → response.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import db_session, get_storage
from app.core.redis_client import get_redis
from app.main import app


class FakeDBSession:
    """Minimal AsyncSession stand-in — just enough to serve `SELECT 1`."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        if self.should_fail:
            raise RuntimeError("db down")

        class _Result:
            def scalar_one(self) -> int:  # pragma: no cover - unused here
                return 1

        return _Result()


class FakeRedis:
    """Async Redis fake covering scan_iter / get / set / ping.

    `scan_iter` supports the `match` glob used by `get_degraded_services`.
    Ping raises when `down=True` so the health check can observe a failure.
    """

    def __init__(self, *, down: bool = False) -> None:
        self._store: dict[str, str] = {}
        self.down = down

    async def ping(self) -> bool:
        if self.down:
            raise RuntimeError("redis down")
        return True

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._store.pop(k, None)

    async def scan_iter(
        self,
        match: str | None = None,
        count: int | None = None,  # noqa: ARG002 — signature parity
    ) -> AsyncIterator[str]:
        pattern = re.compile(fnmatch.translate(match)) if match else None
        for key in list(self._store.keys()):
            if pattern is None or pattern.match(key):
                yield key


class FakeStorage:
    """StorageBackend-shaped fake covering the put+exists round-trip."""

    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self._written: set[str] = set()

    def put(self, key: str, _content: bytes, _content_type: str) -> None:
        if self.down:
            raise RuntimeError("storage down")
        self._written.add(key)

    def exists(self, key: str) -> bool:
        if self.down:
            raise RuntimeError("storage down")
        return key in self._written


@pytest.fixture
def fake_db() -> FakeDBSession:
    return FakeDBSession()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def client(
    fake_db: FakeDBSession,
    fake_redis: FakeRedis,
    fake_storage: FakeStorage,
) -> Iterator[TestClient]:
    async def _db_override() -> AsyncIterator[FakeDBSession]:
        yield fake_db

    async def _redis_override() -> FakeRedis:
        return fake_redis

    def _storage_override() -> FakeStorage:
        return fake_storage

    app.dependency_overrides[db_session] = _db_override
    app.dependency_overrides[get_redis] = _redis_override
    app.dependency_overrides[get_storage] = _storage_override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for dep in (db_session, get_redis, get_storage):
            app.dependency_overrides.pop(dep, None)
