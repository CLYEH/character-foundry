"""Auth business logic: login, refresh, logout.

Refresh tokens are opaque UUIDs; only their sha256 hex is persisted. Lookup is
hash-first (never `SELECT * WHERE token = ...`) so a read-only DB leak can't
produce usable session tokens.

See DECISIONS §6 B4 for TTL policy, api-shape §2 for endpoint contracts.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import sign_access_token
from app.auth.passwords import PLACEHOLDER_HASH, verify_password
from app.core.errors import (
    auth_invalid_credentials,
    auth_invalid_token,
    auth_refresh_expired,
    auth_refresh_revoked,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User

_REFRESH_TTL_ENV = "JWT_REFRESH_TTL_SECONDS"
_DEFAULT_REFRESH_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def refresh_ttl_seconds() -> int:
    raw = os.environ.get(_REFRESH_TTL_ENV)
    if raw is None:
        return _DEFAULT_REFRESH_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{_REFRESH_TTL_ENV} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{_REFRESH_TTL_ENV} must be positive, got {value}")
    return value


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoginResult:
    user: User
    access_token: str
    refresh_token: str
    expires_in: int


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def login(db: AsyncSession, *, email: str, password: str) -> LoginResult:
    user = await _get_user_by_email(db, email)
    # Run verify_password even when user is None so timing is uniform between
    # "unknown email" and "wrong password".
    password_hash = user.password_hash if user is not None else PLACEHOLDER_HASH
    password_ok = verify_password(password, password_hash)
    if user is None or not password_ok:
        raise auth_invalid_credentials()

    access_token, expires_in = sign_access_token(user_id=user.id, team_id=user.team_id)

    raw_refresh = uuid.uuid4().hex
    token_hash = hash_refresh_token(raw_refresh)
    now = datetime.now(UTC)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=now + timedelta(seconds=refresh_ttl_seconds()),
        )
    )
    user.last_login_at = now
    await db.commit()

    return LoginResult(
        user=user,
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=expires_in,
    )


@dataclass(frozen=True)
class RefreshResult:
    access_token: str
    expires_in: int


async def _lookup_refresh_token(db: AsyncSession, *, raw_token: str) -> RefreshToken:
    token_hash = hash_refresh_token(raw_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    row = result.scalar_one_or_none()
    if row is None:
        raise auth_invalid_token()
    return row


async def refresh(db: AsyncSession, *, raw_token: str) -> RefreshResult:
    row = await _lookup_refresh_token(db, raw_token=raw_token)
    if row.revoked_at is not None:
        raise auth_refresh_revoked()
    if row.expires_at <= datetime.now(UTC):
        raise auth_refresh_expired()

    user = await db.get(User, row.user_id)
    if user is None:
        # Refresh token refers to a deleted user — treat as invalid rather
        # than leaking that the account existed.
        raise auth_invalid_token()

    access_token, expires_in = sign_access_token(user_id=user.id, team_id=user.team_id)
    return RefreshResult(access_token=access_token, expires_in=expires_in)


async def logout(db: AsyncSession, *, raw_token: str) -> None:
    """Idempotent: already-revoked / unknown tokens return silently.

    Rationale: a logout endpoint that 401s on unknown/stale tokens gives
    attackers a probe for token validity and surfaces stale-state noise to
    legitimate clients. Success-on-unknown keeps the UX simple: "after you
    call logout, that token cannot be refreshed" is true either way.
    """
    token_hash = hash_refresh_token(raw_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    row = result.scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = datetime.now(UTC)
    await db.commit()
