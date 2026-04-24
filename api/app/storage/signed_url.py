"""JWT-backed signed URLs for the local storage backend.

S3 / MinIO use native presigned URLs. The local FS backend has no such
primitive, so we sign a short-lived JWT carrying `(key, user_id, exp)` and
serve files via `GET /storage/{key}?token=...`.

The signing secret is intentionally separate from `JWT_SECRET` (auth) so
neither domain can mint tokens for the other.
"""

from __future__ import annotations

import os
import time
from urllib.parse import quote

import jwt

_ALGORITHM = "HS256"
_SECRET_ENV = "STORAGE_SIGNED_URL_SECRET"


class SignedURLError(Exception):
    """Base for signed-URL verification failures."""


class SignedURLExpired(SignedURLError):
    """Token's `exp` is in the past."""


class SignedURLInvalid(SignedURLError):
    """Signature mismatch, malformed token, or key/payload mismatch."""


def _resolve_secret(secret: str | None) -> str:
    if secret is not None:
        return secret
    env_value = os.environ.get(_SECRET_ENV)
    if not env_value:
        raise RuntimeError(f"{_SECRET_ENV} is not set")
    return env_value


def sign_token(
    *,
    key: str,
    user_id: str | None,
    expires_in_seconds: int,
    secret: str | None = None,
    now: float | None = None,
) -> str:
    issued_at = now if now is not None else time.time()
    payload = {
        "key": key,
        "user_id": user_id,
        "exp": int(issued_at + expires_in_seconds),
    }
    return jwt.encode(payload, _resolve_secret(secret), algorithm=_ALGORITHM)


def verify_token(
    token: str,
    *,
    expected_key: str,
    secret: str | None = None,
) -> dict[str, object]:
    try:
        payload = jwt.decode(
            token,
            _resolve_secret(secret),
            algorithms=[_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise SignedURLExpired("signed URL has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise SignedURLInvalid("signed URL is invalid") from exc

    if payload.get("key") != expected_key:
        raise SignedURLInvalid("token key does not match request key")
    return dict(payload)


def build_signed_url(*, key: str, token: str) -> str:
    """Compose the URL the client will hit. `key` is path-escaped but `/` kept."""
    encoded_key = quote(key, safe="/")
    return f"/storage/{encoded_key}?token={token}"
