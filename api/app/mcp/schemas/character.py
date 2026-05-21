"""Input/output schemas for the `character.*` MCP tools (T-084).

Most outputs reuse the existing `app.schemas.*` response envelopes
(`CharacterDetailResponse`, `CharacterListResponse`, `CharacterResponse`,
`CheckpointResponse`, `CreationSessionDetailResponse`, `ForkCheckpointResponse`)
so the MCP wire shape can't drift from the REST endpoints these tools wrap —
see `app/mcp/tools/character.py`. This module holds the MCP-specific INPUT
schemas plus the few output shapes that have no REST envelope (the 204
delete / abandon endpoints, and the packaged `character.create` result).
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.base import BaseDTO
from app.schemas.character import CharacterDetailDTO, InputMode
from app.schemas.checkpoint import CheckpointAspectRatio

# ---------------------------------------------------------------------------
# Packaged tool — character.create
# ---------------------------------------------------------------------------


class CharacterCreateInput(BaseModel):
    """Input for the packaged `character.create` tool.

    Drives the whole Base-creation flow (create character + session →
    optionally upload references → run checkpoint → select base) as one call.
    `reference_images` are base64-encoded image bytes (MCP/JSON-RPC can't
    carry raw multipart) and are only consumed in `reference` mode.
    """

    name: str = Field(..., min_length=1, max_length=50, description="Character display name.")
    input_mode: InputMode = Field(
        ...,
        description="`template` (menu_selections + freeform_note) or `reference` (reference_images).",
    )
    menu_selections: dict[str, Any] | None = Field(
        default=None,
        description="Template-mode structured selections (e.g. style/gender/era). Ignored in reference mode.",
    )
    freeform_note: str | None = Field(
        default=None,
        description="Optional free-text guidance (any language; reconciled to English server-side).",
    )
    reference_images: list[str] | None = Field(
        default=None,
        description=(
            "Reference-mode only: base64-encoded image bytes (PNG/JPEG/WebP, ≤10MB each). "
            "Reference mode requires at least one."
        ),
    )
    aspect_ratio: CheckpointAspectRatio = Field(
        default="2:3",
        description="Output aspect ratio. Defaults to 2:3 portrait.",
    )
    checkpoint_count: int = Field(
        default=1,
        ge=1,
        le=10,
        description=(
            "How many checkpoints to generate before locking the Base (the last one is "
            "selected). Agents typically want 1; >1 generates variants first."
        ),
    )


class CharacterCreateResult(BaseModel):
    """`character.create` output — the finished character with its locked Base."""

    character: CharacterDetailDTO
    base: BaseDTO


# ---------------------------------------------------------------------------
# CRUD 1:1 wrap inputs
# ---------------------------------------------------------------------------


class CharacterListInput(BaseModel):
    """Input for `character.list` (wraps `GET /v1/characters`)."""

    owner_id: str | None = Field(
        default=None,
        description="`me` (caller), an explicit user UUID, or omitted for the whole team.",
    )
    q: str | None = Field(default=None, description="Case-insensitive substring match on name.")
    limit: int = Field(default=20, ge=1, le=100, description="Max characters to return (1–100).")
    cursor: str | None = Field(
        default=None, description="Opaque pagination cursor from a prior call."
    )


class CharacterGetInput(BaseModel):
    """Input for `character.get` (wraps `GET /v1/characters/{id}`)."""

    character_id: uuid.UUID = Field(..., description="The character id to fetch.")


class CharacterRenameInput(BaseModel):
    """Input for `character.rename` (wraps `PATCH /v1/characters/{id}`)."""

    character_id: uuid.UUID = Field(..., description="The character id to rename.")
    name: str = Field(..., min_length=1, max_length=50, description="New display name.")


class CharacterDeleteInput(BaseModel):
    """Input for `character.delete` (wraps `DELETE /v1/characters/{id}`, soft delete)."""

    character_id: uuid.UUID = Field(..., description="The character id to soft-delete.")


class CharacterDeleteResult(BaseModel):
    """`character.delete` output — REST returns 204; MCP needs a structured ack."""

    character_id: uuid.UUID
    status: str = Field(default="deleted", description="Always `deleted` on success.")


class CharacterRestoreInput(BaseModel):
    """Input for `character.restore` (wraps `POST /v1/characters/{id}/restore`)."""

    character_id: uuid.UUID = Field(..., description="The soft-deleted character id to restore.")


class CharacterForkInput(BaseModel):
    """Input for `character.fork` (wraps `POST /v1/checkpoints/{id}/fork`)."""

    checkpoint_id: uuid.UUID = Field(
        ..., description="The checkpoint to fork into a new character."
    )
    new_character_name: str = Field(
        ..., min_length=1, max_length=50, description="Name for the new forked character."
    )


class CharacterGetSessionInput(BaseModel):
    """Input for `character.get_session` (wraps `GET /v1/creation-sessions/{id}`)."""

    session_id: uuid.UUID = Field(..., description="The creation-session id to inspect.")


class CharacterAbandonSessionInput(BaseModel):
    """Input for `character.abandon_session` (wraps `POST /v1/creation-sessions/{id}/abandon`)."""

    session_id: uuid.UUID = Field(
        ..., description="The in-progress creation-session id to abandon."
    )


class SessionAbandonResult(BaseModel):
    """`character.abandon_session` output — REST returns 204; MCP returns the status."""

    session_id: uuid.UUID
    status: str = Field(description="The session status after the call (e.g. `abandoned`).")


class CharacterGetCheckpointInput(BaseModel):
    """Input for `character.get_checkpoint` (wraps `GET /v1/checkpoints/{id}`)."""

    checkpoint_id: uuid.UUID = Field(..., description="The checkpoint id to fetch.")
