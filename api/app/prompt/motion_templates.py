"""Preset motion prompt templates (T-035 / T-033 shared surface).

The five preset motions don't go through the reconciler — there's no
user note to translate or constraint to resolve. Instead, the worker
reads a static English prompt keyed by `motion_type` and feeds that
plus the motion-mode platform constraints to Veo 3.1.

T-035 introduces these templates so `/v1/prompt/preview` can render
faithful preview output for preset selections; T-033 (Wave B) reuses
the same dict in its worker job.

Phase 1 templates are intentionally short — they describe the action
and let the platform constraints (motion_creation block) hold the
"transparent bg / camera stationary / smooth motion" guarantees.
"""

from __future__ import annotations

from typing import Final, Literal

# Preset-only narrowing of `MotionType`. Keeping the literal here (not
# in `app/schemas/prompt.py`) means the dict's key type and the
# narrowing the dispatcher needs travel together — a typo-introduced
# new preset would surface as a Literal mismatch at the dispatch site,
# not a silent KeyError when the lookup runs.
PresetMotionType = Literal[
    "preset_wave",
    "preset_nod",
    "preset_gesture",
    "preset_happy",
    "preset_idle",
]

PRESET_MOTION_PROMPTS: Final[dict[PresetMotionType, str]] = {
    "preset_wave": (
        "the character raises their hand and waves hello to the camera, warm and welcoming"
    ),
    "preset_nod": "the character nods their head once in acknowledgement, calm and attentive",
    "preset_gesture": (
        "the character makes a clear pointing gesture forward to highlight something off-camera"
    ),
    "preset_happy": (
        "the character smiles and reacts with subtle joyful expression, shoulders relaxing"
    ),
    "preset_idle": (
        "the character stands still in a relaxed neutral idle pose, "
        "with subtle natural breathing motion"
    ),
}
