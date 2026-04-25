"""Pydantic DTOs for CreationSession (api-shape §6.8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

InputMode = Literal["template", "reference"]
SessionStatus = Literal["in_progress", "completed", "abandoned"]


class CreationSessionDTO(BaseModel):
    id: uuid.UUID
    character_id: uuid.UUID | None
    input_mode: InputMode
    status: SessionStatus
    checkpoint_count: int = 0
    created_at: datetime
    completed_at: datetime | None = None


class CreationSessionDetailResponse(BaseModel):
    """Returned by `GET /v1/creation-sessions/{id}` — bundles the
    session with its checkpoints so callers don't need a second
    round-trip. Checkpoints are typed as `dict[str, Any]` for now;
    T-017 will swap in the real `CheckpointDTO` schema."""

    session: CreationSessionDTO
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
