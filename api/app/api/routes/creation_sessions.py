"""`/v1/creation-sessions/*` — read-side surface of the session/checkpoint flow.

T-016 only ships the GET. Mutations (`/checkpoints`, `/select-base`,
`/abandon`, `/fork`, `/reference-images`) land in T-017 / T-018.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.models.user import User
from app.schemas.creation_session import (
    CreationSessionDetailResponse,
    CreationSessionDTO,
)
from app.services import creation_session_service

router = APIRouter(prefix="/v1/creation-sessions", tags=["creation_sessions"])


@router.get("/{session_id}", response_model=CreationSessionDetailResponse)
async def get_creation_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CreationSessionDetailResponse:
    result = await creation_session_service.get_session_for_read(
        db, user=user, session_id=session_id
    )
    session_dto = CreationSessionDTO(
        id=result.session.id,
        character_id=result.session.character_id,
        input_mode=result.session.input_mode,  # type: ignore[arg-type]
        status=result.session.status,  # type: ignore[arg-type]
        checkpoint_count=result.checkpoint_count,
        created_at=result.session.created_at,
        completed_at=result.session.completed_at,
    )
    # Sprint 2 returns an empty checkpoints list (no rows yet — T-017
    # creates them). Frontend treats `[]` as "still loading the first
    # checkpoint" and renders the menu / reference upload flow.
    return CreationSessionDetailResponse(session=session_dto, checkpoints=[])
