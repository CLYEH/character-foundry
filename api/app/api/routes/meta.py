"""`GET /v1/meta` — platform metadata for the Frontend / agents (T-009).

No auth required. Surfaces model identifiers, preset motion list, platform
constraint version, API version, and the currently degraded services.
Frontend polls this every 60s to drive the DegradedBanner.
See planning/backend/api-shape.md §5.9.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from redis.asyncio import Redis
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from app.core.constants import API_VERSION, MODELS, PRESET_MOTIONS
from app.core.platform_constraints import load_platform_constraints
from app.core.redis_client import get_redis
from app.services.degraded_services import get_degraded_services

_logger = logging.getLogger(__name__)


class _MetaSafeRoute(APIRoute):
    """Convert dep-resolution failures into a 200 with empty degraded_services.

    `/v1/meta` depends on `get_redis` via DI. A misconfigured `REDIS_URL`
    would raise before `meta()` runs and return 500, losing the static
    metadata (models, preset_motions, versions) that the Frontend needs
    most during infra incidents. `get_degraded_services` already tolerates
    *operational* Redis outages (logs + returns []); this wrapper closes
    the remaining gap at the DI layer. Same shape as `_HealthSafeRoute`.
    """

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, StarletteResponse]]:
        original = super().get_route_handler()

        async def _safe(request: Request) -> StarletteResponse:
            try:
                return await original(request)
            except Exception:  # noqa: BLE001 — /v1/meta must keep serving static meta
                _logger.exception(
                    "meta: dep resolution failed; serving static meta with empty degraded"
                )
                return _static_meta_response(degraded_fallback=True)

        return _safe


def _static_meta_payload() -> dict[str, Any]:
    constraints = load_platform_constraints()
    return {
        "models": dict(MODELS),
        "preset_motions": list(PRESET_MOTIONS),
        "platform_constraints_version": constraints.version,
        "api_version": API_VERSION,
        "degraded_services": [],
    }


def _static_meta_response(*, degraded_fallback: Literal[True]) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(status_code=200, content=_static_meta_payload())


router = APIRouter(prefix="/v1", tags=["meta"], route_class=_MetaSafeRoute)


class PresetMotionResponse(BaseModel):
    type: str
    display_name_zh: str
    display_name_en: str
    default_duration_ms: int


class DegradedServiceEntry(BaseModel):
    service: str
    reason: str | None = None
    retry_at: str | None = None
    message: str | None = None


class MetaResponse(BaseModel):
    models: dict[str, str]
    preset_motions: list[PresetMotionResponse]
    platform_constraints_version: str
    api_version: str
    degraded_services: list[DegradedServiceEntry]


@router.get("/meta", response_model=MetaResponse)
async def meta(
    redis: Annotated[Redis, Depends(get_redis)],
) -> MetaResponse:
    constraints = load_platform_constraints()
    degraded: list[dict[str, Any]] = await get_degraded_services(redis)

    return MetaResponse(
        models=dict(MODELS),
        preset_motions=[PresetMotionResponse(**m) for m in PRESET_MOTIONS],
        platform_constraints_version=constraints.version,
        api_version=API_VERSION,
        degraded_services=[DegradedServiceEntry(**entry) for entry in degraded],
    )
