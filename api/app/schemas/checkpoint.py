"""Pydantic DTOs for the Checkpoint resource (api-shape §6.7).

`output_image_url` and `thumbnail_url` are signed URLs minted at read
time — the storage key on the model is never exposed (storage-layout.md
§5). Callers retry the parent endpoint when a signed URL expires
(api-shape §5.8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.creation_session import CreationSessionDTO

CheckpointMode = Literal["retry_same", "remix", "fresh"]


class CheckpointDTO(BaseModel):
    """List-card / detail shape (api-shape §6.7)."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    creation_session_id: uuid.UUID
    sequence: int
    prompt_summary: str
    output_image_url: str | None = None
    thumbnail_url: str | None = None
    selected_as_base: bool = False
    created_at: datetime


class CheckpointResponse(BaseModel):
    checkpoint: CheckpointDTO


class CreationSessionDetailResponse(BaseModel):
    """Returned by `GET /v1/creation-sessions/{id}` — bundles the
    session with its checkpoints so the UI's iteration view loads in
    one round-trip. Replaces the placeholder defined in
    `app.schemas.creation_session` once T-017 lands real DTOs."""

    session: CreationSessionDTO
    checkpoints: list[CheckpointDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request body for POST /v1/creation-sessions/{id}/checkpoints
# ---------------------------------------------------------------------------


class CreateCheckpointRequest(BaseModel):
    """Mode combinations enforced in the service layer (the matrix is
    short enough that a Pydantic validator wouldn't read clearer than
    a top-level branch).

    - `fresh`        → no `base_checkpoint_id`; menu / freeform / refs as
      provided. Reference images optional (input_mode=template) or
      mandatory (input_mode=reference).
    - `retry_same`   → `base_checkpoint_id` required. Re-runs with the
      same prompt + new seed. Menu / freeform / refs ignored on the wire
      (we re-derive from the source checkpoint server-side).
    - `remix`        → `base_checkpoint_id` required. The source
      checkpoint's output image becomes the conditioning input; menu /
      freeform / refs may be supplied to nudge the variation.
    """

    mode: CheckpointMode
    base_checkpoint_id: uuid.UUID | None = None
    menu_selections: dict[str, Any] | None = None
    freeform_note: str | None = None
    reference_image_ids: list[uuid.UUID] | None = None


class CreateCheckpointResponse(BaseModel):
    """202 envelope. The checkpoint row doesn't exist yet — it's written
    by the worker on success — but the id is reserved synchronously so
    SSE callers and the future `GET /v1/checkpoints/{id}` agree on it."""

    task_id: uuid.UUID
    checkpoint_id: uuid.UUID
