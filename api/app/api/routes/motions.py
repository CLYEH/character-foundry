"""`/v1/bases/{id}/motions` + `/v1/aliases/{id}/motions` (T-033).

Two POST endpoints, one per parent kind. Both share the same body
shape (`CreateMotionRequest`) and 202 envelope (`CreateMotionResponse`)
— the only difference is which `parent_type` the service is told the
id belongs to.

Read endpoints (GET / PATCH / DELETE) belong to T-034; this router
ships only the create surface so the worker pipeline can be exercised
end-to-end before T-034 lands.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.core.redis_client import get_arq_pool
from app.models.user import User
from app.schemas.motion import CreateMotionRequest, CreateMotionResponse
from app.services import motion_service

router = APIRouter(tags=["motions"])


@router.post(
    "/v1/bases/{base_id}/motions",
    response_model=CreateMotionResponse,
    status_code=202,
)
async def enqueue_base_motion(
    base_id: uuid.UUID,
    body: CreateMotionRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
) -> CreateMotionResponse:
    """Generate a motion bound to a Base.

    Returns 202 with the reserved task / motion ids. The motion row
    isn't written until the worker succeeds; the id flows into
    `task.input_payload` so SSE consumers see the same id the row
    eventually carries.
    """
    enqueued = await motion_service.enqueue_motion(
        db,
        arq_pool,
        user=user,
        parent_type="base",
        parent_id=base_id,
        motion_type=body.motion_type,
        name=body.name,
        description=body.description,
    )
    return CreateMotionResponse(task_id=enqueued.task_id, motion_id=enqueued.motion_id)


@router.post(
    "/v1/aliases/{alias_id}/motions",
    response_model=CreateMotionResponse,
    status_code=202,
)
async def enqueue_alias_motion(
    alias_id: uuid.UUID,
    body: CreateMotionRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    user: Annotated[User, Depends(get_current_user)],
) -> CreateMotionResponse:
    """Generate a motion bound to an Alias. Same shape as the Base
    endpoint with `parent_type='alias'` resolution."""
    enqueued = await motion_service.enqueue_motion(
        db,
        arq_pool,
        user=user,
        parent_type="alias",
        parent_id=alias_id,
        motion_type=body.motion_type,
        name=body.name,
        description=body.description,
    )
    return CreateMotionResponse(task_id=enqueued.task_id, motion_id=enqueued.motion_id)
