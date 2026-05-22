"""Input/output schemas for the `alias.*` MCP tools (T-085).

Outputs reuse the existing `app.schemas.alias` envelopes (`AliasResponse`,
`AliasListResponse`) so the MCP wire shape can't drift from the REST endpoints
these tools wrap — see `app/mcp/tools/alias.py`. This module holds the
MCP-specific INPUT schemas plus the one output shape that has no REST envelope
(the 204 delete).

`alias.add` packages the optional mask upload + alias create + task polling.
Per `endpoint-mcp-mapping.md` §6 Q-D7, Phase 1 has NO character-scoped
reference-image upload endpoint, so `image` / `mixed` modes consume existing
`reference_image_ids` from the Base's source creation session — the tool
rejects inline `reference_images` bytes with guidance toward `reference_image_ids`.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.prompt import AliasInputMode

# ---------------------------------------------------------------------------
# Packaged tool — alias.add
# ---------------------------------------------------------------------------


class AliasAddInput(BaseModel):
    """Input for the packaged `alias.add` tool.

    Drives the whole alias-creation flow (optional mask upload → create alias
    → poll generation task) as one call across all four input modes.
    """

    character_id: uuid.UUID = Field(..., description="The character to add the alias to.")
    name: str = Field(..., min_length=1, max_length=50, description="Alias display name.")
    input_mode: AliasInputMode = Field(
        ...,
        description=(
            "`text` (freeform_note), `image` (reference_image_ids), `inpaint` (mask), "
            "or `mixed` (any combination — at least one signal)."
        ),
    )
    freeform_note: str | None = Field(
        default=None,
        description="Free-text guidance (any language; reconciled to English server-side).",
    )
    reference_image_ids: list[uuid.UUID] | None = Field(
        default=None,
        description=(
            "Existing reference-image ids from the character's Base source creation session. "
            "REQUIRED for `image` mode, optional for `mixed`. Phase 1 cannot upload brand-new "
            "references at alias time — pass ids that were uploaded during Base creation."
        ),
    )
    mask_file: str | None = Field(
        default=None,
        description=(
            "Base64-encoded inpaint mask PNG (PNG only; transparent pixels mark the edit "
            "region). Uploaded internally and bound as `{ mask: { mask_id } }`. REQUIRED for "
            "`inpaint` mode, optional for `mixed`. Mutually exclusive with `mask_id`."
        ),
    )
    mask_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Reuse a previously-uploaded mask (e.g. a prior `alias.add` round) instead of "
            "re-sending the bytes. Mutually exclusive with `mask_file`."
        ),
    )
    reference_images: list[str] | None = Field(
        default=None,
        description=(
            "NOT SUPPORTED in Phase 1 — there is no character-scoped reference upload endpoint. "
            "Supplying this is rejected with guidance to use `reference_image_ids` instead. "
            "(Field kept so the rejection is explicit rather than silently ignored.)"
        ),
    )


# ---------------------------------------------------------------------------
# CRUD 1:1 wrap inputs
# ---------------------------------------------------------------------------


class AliasListInput(BaseModel):
    """Input for `alias.list` (wraps `GET /v1/characters/{id}/aliases`)."""

    character_id: uuid.UUID = Field(..., description="The character whose aliases to list.")


class AliasGetInput(BaseModel):
    """Input for `alias.get` (wraps `GET /v1/aliases/{id}`)."""

    alias_id: uuid.UUID = Field(..., description="The alias id to fetch.")


class AliasRenameInput(BaseModel):
    """Input for `alias.rename` (wraps `PATCH /v1/aliases/{id}`)."""

    alias_id: uuid.UUID = Field(..., description="The alias id to rename.")
    name: str = Field(..., min_length=1, max_length=50, description="New display name.")


class AliasDeleteInput(BaseModel):
    """Input for `alias.delete` (wraps `DELETE /v1/aliases/{id}`, soft delete)."""

    alias_id: uuid.UUID = Field(..., description="The alias id to soft-delete.")


class AliasDeleteResult(BaseModel):
    """`alias.delete` output — REST returns 204; MCP needs a structured ack."""

    alias_id: uuid.UUID
    status: str = Field(default="deleted", description="Always `deleted` on success.")
