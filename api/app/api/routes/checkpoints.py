"""`/v1/checkpoints/*` — single-checkpoint read (T-017) + fork (T-018).

GET returns the Checkpoint DTO when the worker has committed the row.
POST /fork opens a new Character + CreationSession seeded from the
checkpoint, copying the image bytes into the new session's storage
namespace per storage-layout §4.5.

Frontend doesn't poll GET — UI follows the create_checkpoint task's
SSE stream and renders from the `result` field — but it remains the
canonical reference for agent callers.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_storage
from app.api.routes.characters import build_character_list_dto
from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_CHARACTER_WRITE, require_scope
from app.models.user import User
from app.schemas.base import ForkCheckpointRequest, ForkCheckpointResponse
from app.schemas.checkpoint import CheckpointResponse
from app.schemas.checkpoint_builder import build_checkpoint_dto
from app.schemas.creation_session import CreationSessionDTO
from app.services import checkpoint_service, fork_service
from app.storage.backend import StorageBackend

router = APIRouter(prefix="/v1/checkpoints", tags=["checkpoints"])


@router.get("/{checkpoint_id}", response_model=CheckpointResponse)
async def get_checkpoint(
    checkpoint_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_READ)),
) -> CheckpointResponse:
    checkpoint = await checkpoint_service.get_checkpoint_for_read(
        db, user=user, checkpoint_id=checkpoint_id
    )
    return CheckpointResponse(checkpoint=build_checkpoint_dto(checkpoint, storage))


@router.post(
    "/{checkpoint_id}/fork",
    response_model=ForkCheckpointResponse,
    status_code=201,
)
async def fork_checkpoint(
    checkpoint_id: uuid.UUID,
    body: ForkCheckpointRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_WRITE)),
) -> ForkCheckpointResponse:
    """Open a new Character + CreationSession seeded from this
    checkpoint. Image bytes are copied into the new session's storage
    namespace so a future cleanup of the source session leaves the
    forked character intact (storage-layout §4.5)."""
    forked = await fork_service.fork_from_checkpoint(
        db,
        storage,
        user=user,
        checkpoint_id=checkpoint_id,
        new_character_name=body.new_character_name,
    )
    character_dto = await build_character_list_dto(db, forked.character, storage=storage)
    session_dto = CreationSessionDTO(
        id=forked.creation_session.id,
        character_id=forked.creation_session.character_id,
        input_mode=forked.creation_session.input_mode,  # type: ignore[arg-type]
        status=forked.creation_session.status,  # type: ignore[arg-type]
        # Forked session always starts with one checkpoint (the copy
        # we just inserted at sequence=1).
        checkpoint_count=1,
        created_at=forked.creation_session.created_at,
        completed_at=forked.creation_session.completed_at,
    )
    return ForkCheckpointResponse(character=character_dto, creation_session=session_dto)
