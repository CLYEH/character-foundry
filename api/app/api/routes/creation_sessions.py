"""`/v1/creation-sessions/*` — session read + checkpoint enqueue.

T-016 introduced the GET; T-017 adds the checkpoint POST and embeds
`CheckpointDTO`s into the GET response. Reference uploads live on a
separate router (`api/app/api/routes/reference_images.py`) but share
the same path prefix.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_storage
from app.core.redis_client import get_arq_pool, get_redis
from app.models.user import User
from app.schemas.checkpoint import (
    CreateCheckpointRequest,
    CreateCheckpointResponse,
    CreationSessionDetailResponse,
)
from app.schemas.checkpoint_builder import build_checkpoint_dto
from app.schemas.creation_session import CreationSessionDTO
from app.services import checkpoint_service, creation_session_service
from app.storage.backend import StorageBackend

router = APIRouter(prefix="/v1/creation-sessions", tags=["creation_sessions"])


@router.get("/{session_id}", response_model=CreationSessionDetailResponse)
async def get_creation_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> CreationSessionDetailResponse:
    result = await creation_session_service.get_session_for_read(
        db, user=user, session_id=session_id
    )

    # Checkpoint images are initiator-only per
    # planning/data/storage-layout.md §5.1. The session itself is
    # team-visible (read-side), but the embedded checkpoint DTOs
    # carry signed image URLs — gate them so a same-team non-
    # initiator can see the session shell without getting back
    # someone else's image links (Codex P1 round-16 — same policy
    # `checkpoint_service.get_checkpoint_for_read` enforces, applied
    # to the embedded list here).
    is_initiator = result.session.initiator_id == user.id
    visible_checkpoints = result.checkpoints if is_initiator else ()

    session_dto = CreationSessionDTO(
        id=result.session.id,
        character_id=result.session.character_id,
        input_mode=result.session.input_mode,  # type: ignore[arg-type]
        status=result.session.status,  # type: ignore[arg-type]
        # Non-initiators see no count either — leaking "this session
        # has 7 checkpoints I can't show you" exposes generation
        # cadence to viewers who shouldn't see the artefacts.
        checkpoint_count=len(visible_checkpoints),
        created_at=result.session.created_at,
        completed_at=result.session.completed_at,
    )
    checkpoint_dtos = [build_checkpoint_dto(c, storage) for c in visible_checkpoints]
    return CreationSessionDetailResponse(session=session_dto, checkpoints=checkpoint_dtos)


@router.post(
    "/{session_id}/checkpoints",
    response_model=CreateCheckpointResponse,
    status_code=202,
)
async def enqueue_checkpoint(
    session_id: uuid.UUID,
    body: CreateCheckpointRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
) -> CreateCheckpointResponse:
    enqueued = await checkpoint_service.enqueue_checkpoint(
        db,
        redis,
        arq_pool,
        user=user,
        session_id=session_id,
        mode=body.mode,
        base_checkpoint_id=body.base_checkpoint_id,
        menu_selections=body.menu_selections,
        freeform_note=body.freeform_note,
        reference_image_ids=body.reference_image_ids,
    )
    return CreateCheckpointResponse(
        task_id=enqueued.task_id,
        checkpoint_id=enqueued.checkpoint_id,
    )
