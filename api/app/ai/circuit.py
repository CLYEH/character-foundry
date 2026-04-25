"""Per-model circuit breaker (T-014).

Spec (planning/backend/ai-integration.md §3.4):
- 5 consecutive failures inside a 60s window → OPEN
- OPEN duration 300s; all calls during OPEN raise MODEL_UNAVAILABLE
- After 300s, breaker auto-transitions back to half-open / closed (we use
  Redis TTL to drive this; a successful call clears the failure set,
  a fresh failure starts the counter again)

Redis layout:
  circuit:{model}:failures  → sorted set of failure timestamps (score = epoch)
  degraded:{model}          → JSON {reason, retry_at, message} when OPEN
                              (read by /v1/meta.degraded_services)

Why two keys: the failure set is internal accounting; `degraded:{model}`
is the public-read surface that `/v1/meta` already consumes (T-009 wired
`get_degraded_services()` to scan that prefix). Keeping them split means
external readers never see internal counter state.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.ai import config
from app.ai.errors import model_unavailable
from app.core.constants import DEGRADED_KEY_PREFIX

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CircuitState:
    is_open: bool
    retry_at: datetime | None
    failure_count: int


def _failures_key(model: str) -> str:
    return f"circuit:{model}:failures"


def _degraded_key(model: str) -> str:
    return f"{DEGRADED_KEY_PREFIX}{model}"


def _now() -> float:
    return time.time()


class CircuitBreaker:
    """Wraps a per-model Redis-backed breaker.

    Instances are cheap; the state lives in Redis so multiple API / worker
    processes share one view of each model's health. Construct one per call
    site or cache them on the client — both work.
    """

    def __init__(self, model: str, redis: Redis) -> None:
        self.model = model
        self.redis = redis
        self.failure_threshold = config.circuit_failure_threshold()
        self.failure_window_seconds = config.circuit_failure_window_seconds()
        self.open_duration_seconds = config.circuit_open_duration_seconds()

    async def get_state(self) -> CircuitState:
        raw = await self.redis.get(_degraded_key(self.model))
        retry_at: datetime | None = None
        is_open = False
        if raw is not None:
            try:
                payload = json.loads(raw)
                retry_at_str = payload.get("retry_at")
                if isinstance(retry_at_str, str):
                    retry_at = datetime.fromisoformat(retry_at_str.replace("Z", "+00:00"))
                # Even if we can't parse retry_at, the key existing means OPEN.
                is_open = True
            except (ValueError, TypeError):
                # Corrupt payload — treat as OPEN to fail closed; the TTL
                # will auto-clear the bogus key.
                is_open = True

        failure_count = await self._trim_and_count_failures()
        return CircuitState(is_open=is_open, retry_at=retry_at, failure_count=failure_count)

    async def raise_if_open(self) -> None:
        """Short-circuit: raise MODEL_UNAVAILABLE without calling the provider."""
        state = await self.get_state()
        if state.is_open:
            cause = (
                f"Circuit OPEN until {state.retry_at.isoformat()}"
                if state.retry_at
                else "Circuit OPEN (retry_at unknown)"
            )
            raise model_unavailable(self.model, cause=cause)

    async def record_success(self) -> None:
        """A call succeeded — drop accumulated failures so a single recent
        glitch can't accumulate toward OPEN once the system is healthy again.

        Codex P1 round-2: deliberately does NOT delete `degraded:{model}`.
        Concurrent calls can race: A starts while CLOSED, B…F start in
        parallel, B…F fail and trip the breaker OPEN, A completes
        successfully *after*. If success cleared the OPEN key, the circuit
        would close instantly on a stale in-flight result and subsequent
        calls would resume hammering an unhealthy provider before its 300s
        cool-down elapsed. Let the TTL be authoritative for OPEN→CLOSED
        recovery; failures past the 60s window are trimmed naturally on
        the next `record_failure()`.
        """
        await self.redis.delete(_failures_key(self.model))

    async def record_failure(self) -> bool:
        """Add a failure timestamp. Returns True if this trip opened the circuit.

        Always trims the sliding window first so the threshold compares
        against fresh failures only.
        """
        now = _now()
        # ZADD with a unique member — duplicate scores would be deduped by
        # member, but timestamps as strings would collide on busy retries.
        member = f"{now:.6f}-{uuid.uuid4().hex}"
        await self.redis.zadd(_failures_key(self.model), {member: now})
        await self.redis.expire(
            _failures_key(self.model),
            # Keep slightly longer than the window so trim has room to work.
            self.failure_window_seconds * 2,
        )
        count = await self._trim_and_count_failures()
        if count >= self.failure_threshold:
            await self._open(now)
            return True
        return False

    async def _trim_and_count_failures(self) -> int:
        cutoff = _now() - self.failure_window_seconds
        await self.redis.zremrangebyscore(_failures_key(self.model), 0, cutoff)
        count = await self.redis.zcard(_failures_key(self.model))
        return int(count)

    async def _open(self, now: float) -> None:
        retry_at = datetime.fromtimestamp(now + self.open_duration_seconds, tz=UTC)
        payload = {
            "reason": "CIRCUIT_OPEN",
            "retry_at": retry_at.isoformat().replace("+00:00", "Z"),
            "message": (
                f"{self.model} 暫時不可用，預計於 {self.open_duration_seconds // 60} 分鐘後自動恢復"
            ),
        }
        await self.redis.set(
            _degraded_key(self.model),
            json.dumps(payload),
            ex=self.open_duration_seconds,
        )
        _logger.warning(
            "circuit OPEN: model=%s threshold=%d window=%ds open_for=%ds",
            self.model,
            self.failure_threshold,
            self.failure_window_seconds,
            self.open_duration_seconds,
        )
