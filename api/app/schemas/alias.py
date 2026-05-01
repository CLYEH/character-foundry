"""Pydantic DTOs for the Alias resource (T-031).

Mirrors planning/backend/api-shape.md §5.3 + §6.4. Wire surface for
`POST /v1/characters/{id}/aliases` is intentionally permissive on the
optional fields (Pydantic doesn't enforce the matrix per `input_mode`);
the service layer applies the per-mode rules so the AgentError envelope
stays structured.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.character import NameStr
from app.schemas.prompt import AliasInputMode, MaskInput


class AliasDTO(BaseModel):
    """List-card / detail shape (api-shape §6.4)."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    character_id: uuid.UUID
    name: str
    input_mode: Literal["image2image", "inpaint", "text2image", "mixed"]
    image_url: str | None = None
    thumbnail_url: str | None = None
    motion_count: int = 0
    created_at: datetime


class CreateAliasRequest(BaseModel):
    """Body for `POST /v1/characters/{character_id}/aliases`.

    The wire surface accepts any combination of `freeform_note /
    reference_image_ids / mask`; the service applies the input_mode
    matrix (T-031 ticket §Scope):
    - `inpaint`: mask required
    - `image`:   reference_image_ids required (>=1)
    - `text`:    freeform_note required
    - `mixed`:   at least one of (note / refs / mask)
    """

    name: NameStr
    input_mode: AliasInputMode
    freeform_note: str | None = None
    reference_image_ids: list[uuid.UUID] | None = None
    mask: MaskInput | None = None


class CreateAliasResponse(BaseModel):
    """202 envelope. Alias row is not written until the worker commits;
    the id is reserved synchronously so SSE callers + future
    `GET /v1/aliases/{id}` agree."""

    task_id: uuid.UUID
    alias_id: uuid.UUID


class AliasResponse(BaseModel):
    alias: AliasDTO


class AliasListResponse(BaseModel):
    """`GET /v1/characters/{id}/aliases` envelope (api-shape §5.3).

    No `next_cursor` — the list is unpaginated in Phase 1 (per T-032
    §Scope: a character is not expected to accumulate hundreds of
    aliases).
    """

    items: list[AliasDTO]


class PatchAliasRequest(BaseModel):
    """Body for `PATCH /v1/aliases/{id}` — rename only."""

    name: NameStr


class MaskUploadResponse(BaseModel):
    """201 envelope for `POST /v1/characters/{id}/aliases/masks`.

    Mirrors `ReferenceImageUploadResponse`: caller takes the returned
    `mask_id` and embeds it in the alias-create / preview body's
    `{ mask: { mask_id } }` shape.
    """

    mask_id: uuid.UUID
    url: str
