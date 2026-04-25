"""Fixtures for the prompt reconciler suite (T-015)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import fakeredis.aioredis
import pytest


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


class FakeReconcilerClient:
    """Test double for `ReconcilerClient`.

    Pass a callable that maps `(system_prompt, user_prompt) -> dict`. Calls
    are recorded so tests can assert cache behaviour (e.g. that a second
    `reconcile()` with the same input never invokes the LLM).

    `identity` mirrors the real protocol's `client_identity` so cache-key
    isolation tests can fork two fakes that read different cache slots.
    """

    def __init__(
        self,
        responder: Callable[[str, str], dict[str, Any]],
        *,
        identity: str = "fake:test",
    ) -> None:
        self._responder = responder
        self.calls: list[tuple[str, str]] = []
        self._identity = identity

    @property
    def client_identity(self) -> str:
        return self._identity

    async def call(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        self.calls.append((system_prompt, user_prompt))
        return self._responder(system_prompt, user_prompt)
