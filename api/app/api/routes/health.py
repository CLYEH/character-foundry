"""`GET /health` — liveness probe for DevOps monitoring (T-009).

No `/v1` prefix — this is an infra endpoint, not part of the versioned API
surface. Returns 200 when every critical dependency is reachable, 503 when
any check fails. See planning/backend/api-shape.md §5.9.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_storage
from app.core.constants import STORAGE_HEALTH_PROBE_KEY
from app.core.redis_client import get_redis
from app.storage.backend import StorageBackend

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


CheckStatus = Literal["ok", "fail"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: CheckStatus
    redis: CheckStatus
    storage: CheckStatus


async def _check_db(db: AsyncSession) -> CheckStatus:
    try:
        await db.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001 — health probe intentionally swallows
        _logger.exception("health: db check failed")
        return "fail"


async def _check_redis(redis: Redis) -> CheckStatus:
    try:
        # redis-py types ping() as `Awaitable[bool] | bool` for the shared
        # sync/async code path; at runtime the async client always returns an
        # awaitable.
        await redis.ping()  # type: ignore[misc]
        return "ok"
    except Exception:  # noqa: BLE001
        _logger.exception("health: redis check failed")
        return "fail"


def _check_storage(storage: StorageBackend) -> CheckStatus:
    # Write-then-read round-trip. `exists()` alone would pass on a
    # read-only / permission-broken backend where every user upload would
    # fail; `put` + `exists` exercises the full write path cheaply so a
    # masked failure mode can't sneak through as `ok`.
    try:
        storage.put(STORAGE_HEALTH_PROBE_KEY, b"ok", "text/plain")
        if not storage.exists(STORAGE_HEALTH_PROBE_KEY):
            return "fail"
        return "ok"
    except Exception:  # noqa: BLE001
        _logger.exception("health: storage check failed")
        return "fail"


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
async def health(
    response: Response,
    db: Annotated[AsyncSession, Depends(db_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> HealthResponse:
    db_status = await _check_db(db)
    redis_status = await _check_redis(redis)
    storage_status = _check_storage(storage)

    all_ok = db_status == "ok" and redis_status == "ok" and storage_status == "ok"
    overall: Literal["ok", "degraded"] = "ok" if all_ok else "degraded"
    # 503 by mutation instead of raising so the body still carries the
    # per-component breakdown (monitoring needs to know *what* is failing,
    # not just that something is).
    if not all_ok:
        response.status_code = 503

    return HealthResponse(
        status=overall,
        db=db_status,
        redis=redis_status,
        storage=storage_status,
    )
