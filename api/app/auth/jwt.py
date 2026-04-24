"""JWT access-token utilities.

Access tokens are stateless: server trusts any token that verifies and hasn't
expired. TTL is short (15 min) so the blast radius of leak is bounded. The
signing secret comes from `JWT_SECRET`; it is intentionally separate from
`STORAGE_SIGNED_URL_SECRET` (see `app.storage.signed_url`) so neither domain
can mint tokens for the other.

Refresh tokens do NOT go through here — they're opaque UUIDs validated against
`refresh_tokens` in Postgres. See `app.auth.service`.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import jwt

_ALGORITHM = "HS256"
_SECRET_ENV = "JWT_SECRET"
_ACCESS_TTL_ENV = "JWT_ACCESS_TTL_SECONDS"
_DEFAULT_ACCESS_TTL_SECONDS = 900  # 15 minutes, per DECISIONS §6 B4


class JWTError(Exception):
    """Base for JWT verification failures."""


class JWTExpired(JWTError):
    """Token's `exp` is in the past."""


class JWTInvalid(JWTError):
    """Signature mismatch, malformed token, or wrong algorithm."""


def _resolve_secret(secret: str | None) -> str:
    if secret is not None:
        return secret
    env_value = os.environ.get(_SECRET_ENV)
    if not env_value:
        raise RuntimeError(f"{_SECRET_ENV} is not set")
    return env_value


def access_ttl_seconds() -> int:
    raw = os.environ.get(_ACCESS_TTL_ENV)
    if raw is None:
        return _DEFAULT_ACCESS_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{_ACCESS_TTL_ENV} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{_ACCESS_TTL_ENV} must be positive, got {value}")
    return value


def sign_access_token(
    *,
    user_id: uuid.UUID | str,
    team_id: uuid.UUID | str,
    secret: str | None = None,
    now: float | None = None,
    ttl_seconds: int | None = None,
) -> tuple[str, int]:
    """Return (access_token, expires_in_seconds)."""
    issued_at = now if now is not None else time.time()
    ttl = ttl_seconds if ttl_seconds is not None else access_ttl_seconds()
    payload = {
        "sub": str(user_id),
        "team_id": str(team_id),
        "iat": int(issued_at),
        "exp": int(issued_at + ttl),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, _resolve_secret(secret), algorithm=_ALGORITHM)
    return token, ttl


def verify_access_token(token: str, *, secret: str | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _resolve_secret(secret),
            algorithms=[_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise JWTExpired("access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise JWTInvalid("access token is invalid") from exc

    if "sub" not in payload or "team_id" not in payload:
        raise JWTInvalid("access token missing required claims")
    return dict(payload)
