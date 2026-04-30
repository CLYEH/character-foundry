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


class CreateMotionRequest(BaseModel):
    """Body for `POST /v1/bases/{id}/motions` and `POST /v1/aliases/{id}/motions`.

    `description` is required when `motion_type='custom'` and ignored
    when it's a preset (the worker reads a static template from
    `app.prompt.motion_templates.PRESET_MOTION_PROMPTS`). The cross-
    field check happens in the service layer so it can raise the
    structured `VALIDATION_MOTION_DESCRIPTION_REQUIRED` envelope.
    """

    motion_type: MotionType
    name: MotionNameStr
    description: str | None = None


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
