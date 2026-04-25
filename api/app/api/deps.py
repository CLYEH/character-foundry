"""Shared FastAPI dependencies."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import JWTExpired, JWTInvalid, verify_access_token
from app.core.errors import (
    auth_expired,
    auth_invalid_token,
    auth_missing_token,
)
from app.db import get_db
from app.models.user import User
from app.storage.backend import StorageBackend
from app.storage.local import LocalFilesystemBackend


def get_storage() -> StorageBackend:
    """Resolve the storage backend for the current request.

    Phase 1 always returns `LocalFilesystemBackend` rooted at `STORAGE_ROOT`
    (default `/storage` inside the container). Tests override this via
    `app.dependency_overrides`.
    """
    root = os.environ.get("STORAGE_ROOT", "/storage")
    return LocalFilesystemBackend(Path(root))


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in get_db():
        yield session


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise auth_missing_token()
    # Expected: "Bearer <token>" — case-insensitive scheme.
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise auth_invalid_token()
    return parts[1].strip()


def _user_id_from_authorization(authorization: str | None) -> uuid.UUID:
    """Extract + verify a JWT and return its `sub` UUID. No DB.

    Shared between `get_current_user` (yield-dep DB session) and
    `get_current_user_no_pin` (short-lived session) so the token-side
    failure modes stay identical between the two surfaces.
    """
    token = _extract_bearer(authorization)
    try:
        payload = verify_access_token(token)
    except JWTExpired as exc:
        raise auth_expired() from exc
    except JWTInvalid as exc:
        raise auth_invalid_token() from exc

    try:
        return uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise auth_invalid_token() from exc


async def get_current_user(
    db: Annotated[AsyncSession, Depends(db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    user_id = _user_id_from_authorization(authorization)
    user = await db.get(User, user_id)
    if user is None:
        # Token is cryptographically valid but the user is gone — treat as
        # invalid rather than revealing account existence state.
        raise auth_invalid_token()
    return user


async def get_current_user_no_pin(
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Same auth contract as `get_current_user` but does NOT depend on
    `db_session` — opens + closes its own short-lived AsyncSession.

    Used by long-lived endpoints (SSE stream) where the FastAPI
    yield-based dep would hold a DB connection until the response
    finishes — i.e. for an open stream, until the client disconnects
    (Codex P1 round-4 review).
    """
    from app.db.session import async_session_factory

    user_id = _user_id_from_authorization(authorization)
    factory = async_session_factory()
    async with factory() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise auth_invalid_token()
        return user
