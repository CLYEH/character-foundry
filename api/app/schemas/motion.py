"""Pydantic DTOs for the Motion resource (T-033, api-shape §6.5).

`video_url` and `thumbnail_url` are signed URLs minted at read time —
the storage `video_key` on the model is never exposed (storage-layout §5).
Same convention as Checkpoint / Base / Alias DTOs.

`motion_type` and `MotionParentType` are reused from `app.schemas.prompt`
so the discriminator literal stays identical between the prompt-preview
surface (T-035) and the create / read surfaces here. A future preset
addition flips both endpoints in one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.prompt import MotionParentType, MotionType

# Mirrors the `motions.name` DB CHECK constraint (length 1-50; charset
# `^[一-鿿a-zA-Z0-9_-]+$`). Whitespace is stripped at parse time so
# trailing newlines from the wire don't trip the regex check downstream.
# The character-class regex still happens in service code so we can
# raise the structured `VALIDATION_INVALID_CHARS` envelope instead of a
# Pydantic 422 (same approach as character.py:NameStr).
MotionNameStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=50, strip_whitespace=True),
]


class MotionParentRef(BaseModel):
    """Polymorphic parent ref carried on every MotionDTO (api-shape §6.5).

    A motion is bound to exactly one of (Base, Alias). The pair lives in
    the response so callers don't need to dereference `parent_id`
    against the right collection to know whether they're looking at a
    base- or alias-attached motion.
    """

    type: MotionParentType
    id: uuid.UUID


_MOTION_DESCRIPTION_MAX_LENGTH = 2000


class CreateMotionRequest(BaseModel):
    """Body for `POST /v1/bases/{id}/motions` and `POST /v1/aliases/{id}/motions`.

    `description` is required when `motion_type='custom'` and ignored
    when it's a preset (the worker reads a static template from
    `app.prompt.motion_templates.PRESET_MOTION_PROMPTS`). The cross-
    field check happens in the service layer so it can raise the
    structured `VALIDATION_MOTION_DESCRIPTION_REQUIRED` envelope.

    `max_length=2000` caps wire payload size so the reconciler isn't
    fed a runaway prompt (Codex T-033 nit). The DB column is `TEXT`
    with no cap; this lives at the boundary so internal callers /
    backfills can store longer strings if a future ticket needs them.
    """

    motion_type: MotionType
    name: MotionNameStr
    description: (
        Annotated[str, StringConstraints(max_length=_MOTION_DESCRIPTION_MAX_LENGTH)] | None
    ) = None


class CreateMotionResponse(BaseModel):
    """202 envelope. The motion row doesn't exist yet — it's written by
    the worker on success — but the id is reserved synchronously so SSE
    callers and the future `GET /v1/motions/{id}` agree on it (same
    pattern as `CreateCheckpointResponse`)."""

    task_id: uuid.UUID
    motion_id: uuid.UUID


class MotionDTO(BaseModel):
    """List-card / detail shape (api-shape §6.5)."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    parent: MotionParentRef
    motion_type: MotionType
    name: str
    description: str | None = None
    video_url: str | None = None
    thumbnail_url: str | None = None
    duration_ms: int | None = None
    created_at: datetime


class MotionResponse(BaseModel):
    motion: MotionDTO


class MotionGenerationDTO(BaseModel):
    """Compact GenerationLog projection embedded in `MotionDetailDTO`.

    Not the full audit row — `cost_units`, `parameters`, and other
    internals stay backend-side. The fields here are what a "view this
    motion's generation info" affordance needs: which model produced
    it, how long it took, and when it finished. Mirrors api-shape §6.5
    "{ ...GenerationLog subset... }" without committing to the full
    set so future tickets can extend.
    """

    model_config = ConfigDict(from_attributes=False)

    model_name: str
    model_version: str | None = None
    duration_ms: int | None = None
    completed_at: datetime | None = None


class MotionDetailDTO(MotionDTO):
    """`GET /v1/motions/{id}` shape (api-shape §5.4 → §6.5).

    Adds the `generation` subset on top of the list-card MotionDTO.
    `generation` is None when the motion's `generation_log_id` is null
    (e.g. a row inserted by a worker before generation logging was
    wired up — the soft FK is nullable per `motions.generation_log_id`).
    """

    generation: MotionGenerationDTO | None = None


class MotionDetailResponse(BaseModel):
    motion: MotionDetailDTO


class MotionListResponse(BaseModel):
    """`GET /v1/(bases|aliases)/{id}/motions` envelope (api-shape §5.4).

    No `next_cursor` — Phase 1 caps motions per parent at single digits
    (5 preset + ~handful of custom) so unpaginated is fine.
    """

    items: list[MotionDTO]


class PatchMotionRequest(BaseModel):
    """Body for `PATCH /v1/motions/{id}` — rename only (custom motions).

    Preset motions are name-locked at the service layer and surface as
    422 `VALIDATION_PRESET_RENAME_FORBIDDEN`; the wire schema stays
    permissive so OpenAPI clients don't need to multiplex.
    """

    name: MotionNameStr
