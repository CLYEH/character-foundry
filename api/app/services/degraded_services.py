"""Aggregate `degraded:{service}` Redis keys for `/v1/meta.degraded_services`.

AI-client circuit breakers write to these keys when tripping open
(see planning/backend/ai-integration.md §3.5); the meta endpoint reads them.
SCAN (not KEYS) because Phase 2 prod Redis will hold many keys and KEYS is a
blocking O(N) operation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.core.constants import DEGRADED_KEY_PREFIX

_logger = logging.getLogger(__name__)

_SCAN_COUNT_HINT = 100


async def get_degraded_services(redis: Redis) -> list[dict[str, Any]]:
    """Return the current degraded service list, or `[]` if all healthy.

    Each entry is `{service, reason, retry_at?, message?}`. A malformed value
    at an individual key is skipped (with a warning log) rather than failing
    the whole response — one bad writer shouldn't tank the banner for every
    user.
    """
    services: list[dict[str, Any]] = []
    async for key in redis.scan_iter(
        match=f"{DEGRADED_KEY_PREFIX}*",
        count=_SCAN_COUNT_HINT,
    ):
        service_name = key.removeprefix(DEGRADED_KEY_PREFIX) if isinstance(key, str) else ""
        if not service_name:
            continue
        raw = await redis.get(key)
        if raw is None:
            # Race: key expired between scan and get. Skip.
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            _logger.warning("degraded key %s has non-JSON value; skipping", key)
            continue
        if not isinstance(payload, dict):
            _logger.warning("degraded key %s has non-object value; skipping", key)
            continue
        entry: dict[str, Any] = {"service": service_name}
        # Only forward fields defined by the /v1/meta schema. Extra keys are
        # dropped so a misbehaving writer can't smuggle arbitrary payload out
        # through the public API.
        for field in ("reason", "retry_at", "message"):
            if field in payload:
                entry[field] = payload[field]
        services.append(entry)

    services.sort(key=lambda s: s["service"])
    return services
