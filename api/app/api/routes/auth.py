"""`/v1/auth/*` — JWT login / refresh / logout / me (T-006).

See planning/backend/api-shape.md §2 for endpoint contracts.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.auth import service
from app.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    UserPublic,
)
from app.models.user import User

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _user_public(user: User) -> UserPublic:
    return UserPublic(
        id=user.id,
        name=user.name,
        email=user.email,
        team_id=user.team_id,
        created_at=user.created_at,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
) -> LoginResponse:
    result = await service.login(db, email=body.email, password=body.password)
    return LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        user=_user_public(result.user),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
) -> RefreshResponse:
    result = await service.refresh(db, raw_token=body.refresh_token)
    return RefreshResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> LogoutResponse:
    """Revoke the supplied refresh token for the authenticated user.

    The token is matched against (token_hash, user.id) so a caller cannot
    revoke another account's session even if they know the raw refresh token.
    """
    await service.logout(db, raw_token=body.refresh_token, user_id=user.id)
    return LogoutResponse(ok=True)


@router.get("/me", response_model=MeResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    return MeResponse(user=_user_public(user))
