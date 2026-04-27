"""`GET /v1/checkpoints/{id}` (T-017).

Returns the Checkpoint DTO when the worker has committed the row.
Frontend doesn't poll this — UI follows the create_checkpoint task's
SSE stream and renders from the `result` field — but the endpoint is
the canonical reference for agent callers and the upcoming
`POST /checkpoints/{id}/fork` (T-018).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_storage
from app.models.user import User
from app.schemas.checkpoint import CheckpointResponse
from app.schemas.checkpoint_builder import build_checkpoint_dto
from app.services import checkpoint_service
from app.storage.backend import StorageBackend

router = APIRouter(prefix="/v1/checkpoints", tags=["checkpoints"])


@router.get("/{checkpoint_id}", response_model=CheckpointResponse)
async def get_checkpoint(
    checkpoint_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> CheckpointResponse:
    checkpoint = await checkpoint_service.get_checkpoint_for_read(
        db, user=user, checkpoint_id=checkpoint_id
    )
    return CheckpointResponse(checkpoint=build_checkpoint_dto(checkpoint, storage))
