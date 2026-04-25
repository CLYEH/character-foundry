"""Async Redis client + arq pool + pubsub helpers.

Reads `REDIS_URL` lazily so tests that override the `get_redis` dependency
never touch a real Redis. `decode_responses=True` because every caller in
this codebase deals in JSON strings; raw bytes would force repeated decodes.

The arq pool is a sibling of the plain `Redis` client (different connection
pool, different protocol surface). We keep both so route handlers and
services can publish events and enqueue jobs without juggling two URLs.
"""

from __future__ import annotations

import json
import os
import uuid
from functools import lru_cache
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from redis.asyncio import Redis, from_url


def _redis_url() -> str:
    url = os.environ.get("REDIS_URL")
    if not url:
        raise RuntimeError("REDIS_URL is not set")
    return url


@lru_cache(maxsize=1)
def _cached_redis() -> Redis:
    # redis-py's `from_url` returns Any in current stubs; cast keeps the
    # downstream typing surface clean.
    client: Redis = from_url(_redis_url(), decode_responses=True)  # type: ignore[no-untyped-call]
    return client


async def get_redis() -> Redis:
    """FastAPI dependency. Returns a process-wide async Redis client.

    Override via `app.dependency_overrides[get_redis]` in tests — this avoids
    needing a real Redis for unit tests of routes that only touch keys.
    """
    return _cached_redis()


def reset_redis_cache() -> None:
    """Drop the cached client. Used by tests that need a fresh connection."""
    _cached_redis.cache_clear()


# ---------------------------------------------------------------------------
# arq pool (separate from the plain Redis client; arq speaks its own job
# protocol on top of Redis lists). We cache one pool per process and lazily
# create it on first use.
# ---------------------------------------------------------------------------


def arq_redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(_redis_url())


_arq_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    """FastAPI dependency. Returns a process-wide arq pool.

    Tests override via `app.dependency_overrides[get_arq_pool]`.
    """
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(arq_redis_settings())
    return _arq_pool


async def reset_arq_pool() -> None:
    """Close + drop the cached arq pool. Tests call this between cases."""
    global _arq_pool
    if _arq_pool is not None:
        try:
            await _arq_pool.close()
        finally:
            _arq_pool = None


# ---------------------------------------------------------------------------
# Pubsub helpers — single source of truth for channel names so worker /
# router / cancel paths can't drift apart.
# ---------------------------------------------------------------------------


def task_channel(task_id: uuid.UUID | str) -> str:
    return f"task:{task_id}"


def task_cancel_channel(task_id: uuid.UUID | str) -> str:
    return f"task:{task_id}:cancel"


async def publish_task_event(
    redis: Redis,
    task_id: uuid.UUID | str,
    payload: dict[str, Any],
) -> None:
    """Publish a task SSE event. Body is JSON-encoded with `default=str` so
    UUIDs and datetimes serialize without callers having to pre-stringify.
    """
    await redis.publish(task_channel(task_id), json.dumps(payload, default=str))


async def publish_task_cancel(redis: Redis, task_id: uuid.UUID | str) -> None:
    """Signal a running worker to abort at the next cooperative checkpoint."""
    await redis.publish(task_cancel_channel(task_id), "1")
