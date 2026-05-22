"""`/v1/bases/{id}/motions` + `/v1/aliases/{id}/motions` (T-033 + T-034).

POST endpoints (T-033): one per parent kind. Both share the same body
shape (`CreateMotionRequest`) and 202 envelope (`CreateMotionResponse`)
— the only difference is which `parent_type` the service is told the
id belongs to.

Read / mutate-by-id endpoints (T-034):

  - GET    /v1/bases/{id}/motions
  - GET    /v1/aliases/{id}/motions
  - GET    /v1/motions/{id}
  - PATCH  /v1/motions/{id}      (custom only; preset → 422)
  - DELETE /v1/motions/{id}      (soft delete)

Auth split mirrors `aliases.py`: list / detail are team-wide reads,
PATCH / DELETE are owner-only. NOT_FOUND_MOTION is the opacity envelope
for cross-team / soft-deleted / unknown ids.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_storage
from app.auth.scopes import (
    SCOPE_CHARACTER_READ,
    SCOPE_CHARACTER_WRITE,
    require_scope,
)
from app.core.redis_client import get_arq_pool
from app.models.user import User
from app.schemas.motion import (
    CreateMotionRequest,
    CreateMotionResponse,
    MotionDetailResponse,
    MotionListResponse,
    MotionResponse,
    PatchMotionRequest,
)
from app.schemas.motion_builder import build_motion_detail_dto, build_motion_dto
from app.services import motion_service
from app.storage.backend import StorageBackend

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
    _: None = Depends(require_scope(SCOPE_CHARACTER_WRITE)),
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
    _: None = Depends(require_scope(SCOPE_CHARACTER_WRITE)),
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


# ---------------------------------------------------------------------------
# T-034: list / detail / patch / delete
# ---------------------------------------------------------------------------


@router.get(
    "/v1/bases/{base_id}/motions",
    response_model=MotionListResponse,
)
async def list_base_motions(
    base_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_READ)),
) -> MotionListResponse:
    """List active motions under a Base, preset-first then created_at ASC."""
    motions = await motion_service.list_motions_for_parent(
        db, user=user, parent_type="base", parent_id=base_id
    )
    return MotionListResponse(items=[build_motion_dto(m, storage) for m in motions])


@router.get(
    "/v1/aliases/{alias_id}/motions",
    response_model=MotionListResponse,
)
async def list_alias_motions(
    alias_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_READ)),
) -> MotionListResponse:
    """List active motions under an Alias, preset-first then created_at ASC."""
    motions = await motion_service.list_motions_for_parent(
        db, user=user, parent_type="alias", parent_id=alias_id
    )
    return MotionListResponse(items=[build_motion_dto(m, storage) for m in motions])


@router.get(
    "/v1/motions/{motion_id}",
    response_model=MotionDetailResponse,
)
async def get_motion(
    motion_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_READ)),
) -> MotionDetailResponse:
    """Detail surface — same fields as the list card plus a `generation`
    subset (model name, duration, completed_at). Team-wide read."""
    detail = await motion_service.get_motion_detail(db, user=user, motion_id=motion_id)
    return MotionDetailResponse(
        motion=build_motion_detail_dto(
            detail.motion,
            storage,
            generation_log=detail.generation_log,
        )
    )


@router.patch(
    "/v1/motions/{motion_id}",
    response_model=MotionResponse,
)
async def patch_motion(
    motion_id: uuid.UUID,
    body: PatchMotionRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_WRITE)),
) -> MotionResponse:
    """Rename a custom motion. Preset → 422, duplicate → 409."""
    motion = await motion_service.update_motion_name(
        db, user=user, motion_id=motion_id, new_name=body.name
    )
    return MotionResponse(motion=build_motion_dto(motion, storage))


@router.delete(
    "/v1/motions/{motion_id}",
    status_code=204,
)
async def delete_motion(
    motion_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    _: None = Depends(require_scope(SCOPE_CHARACTER_WRITE)),
) -> Response:
    """Soft-delete. Returns 204 with no body (mirrors alias delete)."""
    await motion_service.soft_delete_motion(db, user=user, motion_id=motion_id)
    return Response(status_code=204)
