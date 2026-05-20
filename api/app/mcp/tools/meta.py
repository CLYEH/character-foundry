"""`meta.get` MCP tool (T-088) — 1:1 wrap of `GET /v1/meta`.

Per `planning/agent-interface/endpoint-mcp-mapping.md` §2.9: agent-readable
platform metadata (model identifiers, preset motions, platform-constraints
version, API version, degraded services). **Public** — no scope required, per
`api-shape.md` §5.9: capability/health info every client (including
unauthenticated ones) must be able to read.

`degraded_services` is additionally surfaced on the MCP `tools/list` response
`_meta` (see `app/mcp/app.py`) so agents can read degraded state without an
explicit `meta.get` call. Both surfaces go through the SAME aggregator —
`app.services.degraded_services.aggregate_degraded_services` — which is also
the Redis source `/v1/meta` reads, so the three can't drift.
"""

from __future__ import annotations

from app.core.constants import API_VERSION, MODELS, PRESET_MOTIONS
from app.core.platform_constraints import load_platform_constraints
from app.mcp.registry import MCPTool, register
from app.mcp.schemas.meta import (
    DegradedServiceEntry,
    MetaGetInput,
    MetaPayload,
    PresetMotionEntry,
)
from app.services import degraded_services


async def meta_get() -> MetaPayload:
    """Return the full platform metadata payload (same as `GET /v1/meta`).

    Public: no token or scope required. `degraded_services` reflects the
    current Redis-aggregated circuit-breaker state.
    """
    constraints = load_platform_constraints()
    degraded = await degraded_services.aggregate_degraded_services()
    return MetaPayload(
        models=dict(MODELS),
        preset_motions=[PresetMotionEntry(**m) for m in PRESET_MOTIONS],
        platform_constraints_version=constraints.version,
        api_version=API_VERSION,
        degraded_services=[DegradedServiceEntry(**entry) for entry in degraded],
    )


META_GET = register(
    MCPTool(
        name="meta.get",
        description=(
            "Return platform metadata: model identifiers, the 5 preset motions, "
            "platform-constraints version, API version, and currently degraded "
            "services. Public — no scope required. Agents call this to discover "
            "capabilities and self-defer when a service is degraded."
        ),
        # Public capability info — no scope. Empty `scopes` with a `bundles`
        # entry passes CI guardrail 2 because `GET /v1/meta` declares no
        # require_scope, so the bundle-union is also empty.
        scopes=[],
        bundles=["GET /v1/meta"],
        input_schema=MetaGetInput,
        output_schema=MetaPayload,
        handler=meta_get,
    )
)
