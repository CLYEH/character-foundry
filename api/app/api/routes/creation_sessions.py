"""`/v1/creation-sessions/*` — session read + checkpoint enqueue + Base selection / abandon (T-018).

T-016 introduced the GET; T-017 added the checkpoint POST + embedded
`CheckpointDTO`s in the GET response; T-018 adds select-base + abandon
to close the loop. Reference uploads live on a separate router
(`api/app/api/routes/reference_images.py`) but share the same path prefix.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_storage
from app.api.routes.characters import build_character_list_dto
from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_CHARACTER_WRITE, require_scope
from app.core.redis_client import get_arq_pool, get_redis
from app.models.user import User
from app.schemas.base import SelectBaseRequest, SelectBaseResponse
from app.schemas.checkpoint import (
    CreateCheckpointRequest,
    CreateCheckpointResponse,
    CreationSessionDetailResponse,
)
from app.schemas.checkpoint_builder import build_base_dto, build_checkpoint_dto
from app.schemas.creation_session import CreationSessionDTO
from app.services import base_service, checkpoint_service, creation_session_service
from app.storage.backend import StorageBackend

router = APIRouter(prefix="/v1/creation-sessions", tags=["creation_sessions"])


@router.get("/{session_id}", response_model=CreationSessionDetailResponse)
async def get_creation_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_READ)),
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
    _: None = Depends(require_scope(SCOPE_CHARACTER_WRITE)),
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
        aspect_ratio=body.aspect_ratio,
    )
    return CreateCheckpointResponse(
        task_id=enqueued.task_id,
        checkpoint_id=enqueued.checkpoint_id,
    )


@router.post(
    "/{session_id}/select-base",
    response_model=SelectBaseResponse,
)
async def select_base(
    session_id: uuid.UUID,
    body: SelectBaseRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_WRITE)),
) -> SelectBaseResponse:
    """Promote a checkpoint into the Character's immutable Base.

    Returns 200 with the updated character + new Base. Subsequent
    select-base calls on the same session 409 with CONFLICT_BASE_LOCKED
    (Phase 1 Base is immutable per DECISIONS §5).
    """
    selected = await base_service.select_base(
        db,
        user=user,
        session_id=session_id,
        checkpoint_id=body.checkpoint_id,
    )
    character_dto = await build_character_list_dto(db, selected.character, storage=storage)
    base_dto = build_base_dto(selected.base, storage)
    return SelectBaseResponse(character=character_dto, base=base_dto)


@router.post("/{session_id}/abandon", status_code=204)
async def abandon_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_WRITE)),
) -> Response:
    """Mark the session abandoned. Idempotent on already-abandoned;
    409 CONFLICT_BASE_LOCKED if the Base has already been selected
    (the user must instead delete the character to start over)."""
    await base_service.abandon_session(db, user=user, session_id=session_id)
    return Response(status_code=204)
