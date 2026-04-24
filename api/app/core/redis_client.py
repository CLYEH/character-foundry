"""Async Redis client — single per-process instance + FastAPI dependency.

Reads `REDIS_URL` lazily so tests that override the `get_redis` dependency
never touch a real Redis. `decode_responses=True` because every caller in
this codebase deals in JSON strings; raw bytes would force repeated decodes.
"""

from __future__ import annotations

import os
from functools import lru_cache

from redis.asyncio import Redis, from_url


def _redis_url() -> str:
    url = os.environ.get("REDIS_URL")
    if not url:
        raise RuntimeError("REDIS_URL is not set")
    return url


@lru_cache(maxsize=1)
def _cached_redis() -> Redis:
    return from_url(_redis_url(), decode_responses=True)


async def get_redis() -> Redis:
    """FastAPI dependency. Returns a process-wide async Redis client.

    Override via `app.dependency_overrides[get_redis]` in tests — this avoids
    needing a real Redis for unit tests of routes that only touch keys.
    """
    return _cached_redis()


def reset_redis_cache() -> None:
    """Drop the cached client. Used by tests that need a fresh connection."""
    _cached_redis.cache_clear()
