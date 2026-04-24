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

# Fields forwarded from Redis payload to `/v1/meta.degraded_services`. Each
# must be a string (or absent) so the Pydantic `DegradedServiceEntry` model
# in `/v1/meta` doesn't 500 on a bad writer — same "one bad writer doesn't
# tank the whole response" contract as the outer try/except.
_ALLOWED_STRING_FIELDS = ("reason", "retry_at", "message")


async def get_degraded_services(redis: Redis) -> list[dict[str, Any]]:
    """Return the current degraded service list, or `[]` if all healthy.

    Each entry is `{service, reason, retry_at?, message?}`. A malformed value
    at an individual key is skipped (with a warning log) rather than failing
    the whole response — one bad writer shouldn't tank the banner for every
    user. A Redis outage is similarly non-fatal: log + return `[]` so
    `/v1/meta` still serves its static metadata (models, preset motions,
    version) during infra incidents.
    """
    services: list[dict[str, Any]] = []
    # Redis SCAN explicitly does NOT guarantee unique keys across a single
    # iteration (https://redis.io/commands/scan/). Without this dedupe set a
    # repeated key would surface as duplicate `degraded_services` entries and
    # the Frontend banner would render the same outage twice.
    seen: set[str] = set()
    try:
        async for key in redis.scan_iter(
            match=f"{DEGRADED_KEY_PREFIX}*",
            count=_SCAN_COUNT_HINT,
        ):
            service_name = key.removeprefix(DEGRADED_KEY_PREFIX) if isinstance(key, str) else ""
            if not service_name:
                continue
            if service_name in seen:
                continue
            seen.add(service_name)
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
            # Only forward fields defined by the /v1/meta schema. Extra keys
            # are dropped so a misbehaving writer can't smuggle arbitrary
            # payload out through the public API. Each value must be a
            # string — a nested dict / number / list would pass the outer
            # isinstance check and then 500 the endpoint at Pydantic
            # validation time.
            for field in _ALLOWED_STRING_FIELDS:
                value = payload.get(field)
                if value is None:
                    continue
                if not isinstance(value, str):
                    _logger.warning(
                        "degraded key %s field %s has non-string value (%s); skipping field",
                        key,
                        field,
                        type(value).__name__,
                    )
                    continue
                entry[field] = value
            services.append(entry)
    except Exception:  # noqa: BLE001 — Redis outage must not 500 /v1/meta
        _logger.exception(
            "degraded_services: redis scan failed; returning empty list. "
            "Static metadata in /v1/meta still served."
        )
        return []

    services.sort(key=lambda s: s["service"])
    return services
