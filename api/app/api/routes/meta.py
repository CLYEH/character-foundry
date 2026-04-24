"""`GET /v1/meta` — platform metadata for the Frontend / agents (T-009).

No auth required. Surfaces model identifiers, preset motion list, platform
constraint version, API version, and the currently degraded services.
Frontend polls this every 60s to drive the DegradedBanner.
See planning/backend/api-shape.md §5.9.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.constants import API_VERSION, MODELS, PRESET_MOTIONS
from app.core.platform_constraints import load_platform_constraints
from app.core.redis_client import get_redis
from app.services.degraded_services import get_degraded_services

router = APIRouter(prefix="/v1", tags=["meta"])


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
