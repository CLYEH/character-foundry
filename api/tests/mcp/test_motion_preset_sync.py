"""Guard: the `motion.generate` wire schema's preset list == the /v1/meta source.

`MotionGenerateInput.motion_type` reuses `app.schemas.prompt.MotionType` so an
agent reading `tools/list` sees every selectable preset embedded in the schema
(T-086 §preset 清單來源 — "hardcode into schema" over runtime fetch). This test
pins that the preset values the schema exposes are EXACTLY the presets `/v1/meta`
advertises via `app.core.constants.PRESET_MOTIONS` (plus `custom`). If a future
ticket adds a 6th preset to `PRESET_MOTIONS` (or the `MotionType` Literal) but
forgets the other, this fails — preventing the MCP schema and the meta endpoint
from disagreeing on what an agent may select.
"""

from __future__ import annotations

from typing import get_args

from app.core.constants import PRESET_MOTIONS
from app.schemas.prompt import MotionType


def test_motion_type_literal_matches_meta_presets() -> None:
    schema_values = set(get_args(MotionType))
    meta_presets = {m["type"] for m in PRESET_MOTIONS}

    # The schema enumerates exactly the meta presets plus the `custom` sentinel.
    assert schema_values == meta_presets | {"custom"}, (
        "motion.generate's motion_type enum drifted from /v1/meta's PRESET_MOTIONS. "
        f"schema={sorted(schema_values)} meta_presets={sorted(meta_presets)}"
    )
