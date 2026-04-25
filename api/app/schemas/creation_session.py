"""Pydantic DTOs for CreationSession (api-shape §6.8).

`CreationSessionDetailResponse` lives in `app.schemas.checkpoint` to
avoid an import cycle: that response embeds `CheckpointDTO`, and
`CheckpointDTO` itself references the session id but not the session
DTO. Splitting the response there keeps both modules importable in
isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

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
