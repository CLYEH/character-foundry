"""Fixtures for the T-014 AI client suite.

`fakeredis.aioredis.FakeRedis` actually implements ZADD/ZREM/SCAN/EXPIRE
which the circuit breaker needs — the lighter `FakeRedis` in
`tests/routes/conftest.py` only covers GET/SET/SCAN. We pin a fresh
instance per test so failure-set state can't leak between cases.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)
