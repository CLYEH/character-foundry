"""Platform constants surfaced via `/v1/meta`.

Ship in a module (not a DB table) so agents can bundle them at build time and
so the list is reviewable in PRs. Phase 1 preset motion list is fixed at 5 per
product/functional-scope.md F-20; adding a 6th requires a platform version
bump + UI changes.
"""

from __future__ import annotations

from typing import TypedDict


class PresetMotion(TypedDict):
    type: str
    display_name_zh: str
    display_name_en: str
    default_duration_ms: int


API_VERSION = "v1"

# 5 preset motions — product/functional-scope.md §F-20 + backend/ai-integration.md §4.3.
# Duration ms derived from PRESET_DURATIONS seconds in ai-integration.md.
PRESET_MOTIONS: list[PresetMotion] = [
    {
        "type": "preset_wave",
        "display_name_zh": "招手歡迎",
        "display_name_en": "Wave Hello",
        "default_duration_ms": 3500,
    },
    {
        "type": "preset_nod",
        "display_name_zh": "點頭說明",
        "display_name_en": "Nod",
        "default_duration_ms": 3000,
    },
    {
        "type": "preset_gesture",
        "display_name_zh": "手勢指引",
        "display_name_en": "Gesture",
        "default_duration_ms": 4000,
    },
    {
        "type": "preset_happy",
        "display_name_zh": "開心回應",
        "display_name_en": "Happy Response",
        "default_duration_ms": 3000,
    },
    {
        "type": "preset_idle",
        "display_name_zh": "靜置待機",
        "display_name_en": "Idle",
        "default_duration_ms": 5000,
    },
]

# Model identifiers surfaced in `/v1/meta.models`. Distinct from the env-driven
# `GPT_IMAGE_2_MODEL` / `VEO_MODEL` tuning knobs — those are for swapping
# provider SKUs; these are the stable names clients key on.
MODELS = {
    "image": "gpt-image-2",
    "video": "veo-3.1",
    "reconciler": "gpt-5-mini",
}

# Redis key prefix read by `/v1/meta.degraded_services`. Written to by AI
# client circuit breakers (see ai-integration.md §3.5).
DEGRADED_KEY_PREFIX = "degraded:"

# Storage marker file exercised by `/health`. Written lazily on first probe —
# the check only needs the backend to answer without raising.
STORAGE_HEALTH_PROBE_KEY = ".health-probe"
