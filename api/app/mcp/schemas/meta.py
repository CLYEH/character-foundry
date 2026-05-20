"""Input / output schemas for the `meta.get` MCP tool (T-088).

Mirrors the `GET /v1/meta` response (api-shape §5.9) but defined in the MCP
layer so the tool doesn't import the HTTP route module. `degraded_services`
here is the same Redis-aggregated state `/v1/meta` serves and the
`tools/list` `_meta` extension surfaces — all three go through
`app.services.degraded_services.aggregate_degraded_services`.
"""

from __future__ import annotations

from pydantic import BaseModel


class MetaGetInput(BaseModel):
    """Input for `meta.get` — no parameters."""


class PresetMotionEntry(BaseModel):
    type: str
    display_name_zh: str
    display_name_en: str
    default_duration_ms: int


class DegradedServiceEntry(BaseModel):
    service: str
    reason: str | None = None
    retry_at: str | None = None
    message: str | None = None


class MetaPayload(BaseModel):
    """Full `/v1/meta` payload returned by `meta.get`."""

    models: dict[str, str]
    preset_motions: list[PresetMotionEntry]
    platform_constraints_version: str
    api_version: str
    degraded_services: list[DegradedServiceEntry]
