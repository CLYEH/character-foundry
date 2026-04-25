"""Circuit breaker behaviour (T-014)."""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest

from app.ai import circuit as circuit_module
from app.ai.circuit import CircuitBreaker
from app.core.errors import AgentErrorException


async def test_breaker_opens_after_threshold_failures(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    breaker = CircuitBreaker("gpt-image-2", fake_redis)
    breaker.failure_threshold = 5
    breaker.failure_window_seconds = 60

    for _ in range(4):
        opened = await breaker.record_failure()
        assert opened is False

    opened = await breaker.record_failure()
    assert opened is True

    raw = await fake_redis.get("degraded:gpt-image-2")
    assert raw is not None
    payload = json.loads(raw)
    assert payload["reason"] == "CIRCUIT_OPEN"
    assert "retry_at" in payload


async def test_raise_if_open_short_circuits_call(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    await fake_redis.set(
        "degraded:gpt-image-2",
        json.dumps(
            {
                "reason": "CIRCUIT_OPEN",
                "retry_at": "2099-01-01T00:00:00Z",
                "message": "down",
            }
        ),
    )
    breaker = CircuitBreaker("gpt-image-2", fake_redis)

    with pytest.raises(AgentErrorException) as info:
        await breaker.raise_if_open()
    assert info.value.error.code == "MODEL_UNAVAILABLE"


async def test_raise_if_open_passes_when_closed(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    breaker = CircuitBreaker("gpt-image-2", fake_redis)
    # Should not raise — circuit closed, no degraded key set.
    await breaker.raise_if_open()


async def test_record_success_clears_state(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    breaker = CircuitBreaker("gpt-image-2", fake_redis)
    breaker.failure_threshold = 5

    for _ in range(5):
        await breaker.record_failure()
    assert await fake_redis.get("degraded:gpt-image-2") is not None

    await breaker.record_success()

    assert await fake_redis.get("degraded:gpt-image-2") is None
    state = await breaker.get_state()
    assert state.is_open is False
    assert state.failure_count == 0


async def test_failures_outside_window_do_not_count(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = CircuitBreaker("gpt-image-2", fake_redis)
    breaker.failure_threshold = 5
    breaker.failure_window_seconds = 60

    # Time advances per record_failure: the first 4 happen close together,
    # then a long gap pushes them outside the window so the 5th alone counts.
    state = {"value": 1000.0}

    def _fake_now() -> float:
        return state["value"]

    monkeypatch.setattr(circuit_module, "_now", _fake_now)

    for delta in (0.0, 1.0, 2.0, 3.0):
        state["value"] = 1000.0 + delta
        opened = await breaker.record_failure()
        assert opened is False

    # Jump 500s — well past the 60s window. Earlier failures are trimmed.
    state["value"] = 1500.0
    opened = await breaker.record_failure()
    assert opened is False
    snapshot = await breaker.get_state()
    assert snapshot.is_open is False
    assert snapshot.failure_count == 1


async def test_breaker_state_reads_retry_at_from_redis(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    await fake_redis.set(
        "degraded:gpt-image-2",
        json.dumps(
            {
                "reason": "CIRCUIT_OPEN",
                "retry_at": "2030-01-01T12:00:00Z",
                "message": "down",
            }
        ),
    )
    breaker = CircuitBreaker("gpt-image-2", fake_redis)
    state = await breaker.get_state()
    assert state.is_open is True
    assert state.retry_at is not None
    assert state.retry_at.year == 2030
