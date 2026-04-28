"""Pydantic DTOs for the Base resource (api-shape §6.3).

`image_url` and `thumbnail_url` are signed URLs minted at read time —
the storage `image_key` on the model is never exposed (storage-layout
§5). Same convention as CheckpointDTO.

`generation` (subset of GenerationLog) is omitted from the Sprint 2 DTO
since the worker writes a `generation_log_id` reference but Sprint 2
does not yet surface log-derived fields. T-019 onward can extend.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.character import CharacterDTO
from app.schemas.creation_session import CreationSessionDTO


class BaseDTO(BaseModel):
    """Base detail shape (api-shape §6.3).

    `thumbnail_url` is derived from the same `image_key` via the
    `_thumb.png` suffix convention (storage-layout §4); both URLs are
    short-lived signed links re-minted at read time."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    character_id: uuid.UUID
    image_url: str | None = None
    thumbnail_url: str | None = None
    from_checkpoint_id: uuid.UUID
    created_at: datetime


class SelectBaseRequest(BaseModel):
    checkpoint_id: uuid.UUID


class ForkCheckpointRequest(BaseModel):
    """`new_character_name` follows the same NameStr constraints used
    by character create — enforced by the service layer rather than
    Pydantic so the structured AgentError surfaces the right code on
    bad input."""

    new_character_name: str


class SelectBaseResponse(BaseModel):
    """Returned by `POST /v1/creation-sessions/{id}/select-base`.

    Bundles the freshly-mutated character (now with `base_thumbnail_url`
    populated) and the new Base row so the frontend can flip its
    detail view in one round-trip."""

    character: CharacterDTO
    base: BaseDTO


class ForkCheckpointResponse(BaseModel):
    """Returned by `POST /v1/checkpoints/{id}/fork`. Same envelope shape
    as `CreateCharacterResponse` — the frontend dispatches the user to
    the new session URL using `creation_session.id`."""

    character: CharacterDTO
    creation_session: CreationSessionDTO
