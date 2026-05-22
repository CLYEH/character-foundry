"""Input/output schemas for the `motion.*` MCP tools (T-086).

Outputs reuse the existing `app.schemas.motion` envelopes (`MotionResponse`,
`MotionDetailResponse`, `MotionListResponse`) so the MCP wire shape can't drift
from the REST endpoints these tools wrap — see `app/mcp/tools/motion.py`. This
module holds the MCP-specific INPUT schemas plus the one output shape that has
no REST envelope (the 204 delete).

`motion.generate` is polymorphic: one tool, two target kinds (`base` / `alias`).
The agent's mental unit is "give a visual a motion" — the target is a parameter,
not a tool distinction (per T-083 §3 / `oauth-mcp-integration.md` §3.3), so we
deliberately do NOT split into `motion.generate_for_base` /
`motion.generate_for_alias`.

`motion_type` reuses `MotionType` from `app.schemas.prompt` (the same Literal the
REST `CreateMotionRequest` uses), so the preset list is embedded in the wire
schema — an agent reading `tools/list` sees every selectable preset without a
prior `meta.get` call — AND it cannot drift from the canonical enum. A guard test
(`tests/mcp/test_motion_preset_sync.py`) pins those preset values to
`PRESET_MOTIONS` in `app.core.constants` (the `/v1/meta` source).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.prompt import MotionParentType, MotionType

# ---------------------------------------------------------------------------
# Packaged tool — motion.generate
# ---------------------------------------------------------------------------


class MotionGenerateInput(BaseModel):
    """Input for the packaged, polymorphic `motion.generate` tool.

    Drives the whole motion-generation flow (enqueue i2v → poll the task to
    completion → return the finished motion) for either a Base or an Alias
    target. `description` is required only for `custom` motions (preset
    prompts are static platform templates and ignore any description).
    """

    target_type: MotionParentType = Field(
        ...,
        description="Whether `target_id` is a Base (`base`) or an Alias (`alias`).",
    )
    target_id: uuid.UUID = Field(
        ...,
        description="The Base id or Alias id to attach the motion to (per `target_type`).",
    )
    motion_type: MotionType = Field(
        ...,
        description=(
            "One of the 5 platform presets (`preset_wave` / `preset_nod` / "
            "`preset_gesture` / `preset_happy` / `preset_idle`) or `custom`. Presets "
            "use static prompts; `custom` requires `description`."
        ),
    )
    name: str = Field(..., min_length=1, max_length=50, description="Motion display name.")
    description: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Free-text motion description (any language; reconciled to English "
            "server-side). REQUIRED for `custom`; ignored for presets."
        ),
    )


# ---------------------------------------------------------------------------
# CRUD 1:1 wrap inputs
# ---------------------------------------------------------------------------


class MotionListForBaseInput(BaseModel):
    """Input for `motion.list_for_base` (wraps `GET /v1/bases/{id}/motions`)."""

    base_id: uuid.UUID = Field(..., description="The Base whose motions to list.")


class MotionListForAliasInput(BaseModel):
    """Input for `motion.list_for_alias` (wraps `GET /v1/aliases/{id}/motions`)."""

    alias_id: uuid.UUID = Field(..., description="The Alias whose motions to list.")


class MotionGetInput(BaseModel):
    """Input for `motion.get` (wraps `GET /v1/motions/{id}`)."""

    motion_id: uuid.UUID = Field(..., description="The motion id to fetch.")


class MotionRenameInput(BaseModel):
    """Input for `motion.rename` (wraps `PATCH /v1/motions/{id}`; custom only)."""

    motion_id: uuid.UUID = Field(..., description="The motion id to rename (custom only).")
    name: str = Field(..., min_length=1, max_length=50, description="New display name.")


class MotionDeleteInput(BaseModel):
    """Input for `motion.delete` (wraps `DELETE /v1/motions/{id}`, soft delete)."""

    motion_id: uuid.UUID = Field(..., description="The motion id to soft-delete.")


class MotionDeleteResult(BaseModel):
    """`motion.delete` output — REST returns 204; MCP needs a structured ack."""

    motion_id: uuid.UUID
    status: str = Field(default="deleted", description="Always `deleted` on success.")
