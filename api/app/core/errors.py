"""AgentError envelope for structured API error responses.

The API returns errors in the shape `{"error": {<AgentError fields>}}` so
both UI and agent callers get a stable, machine-readable surface. See
planning/backend/api-shape.md §4 for field semantics and category prefixes.
"""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_REQUEST_ID_CTX: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return _REQUEST_ID_CTX.get()


def set_request_id(value: str | None) -> None:
    _REQUEST_ID_CTX.set(value)


class AgentError(BaseModel):
    code: str
    message: str
    problem: str
    cause: str
    fix: str
    docs_url: str | None = None
    retryable: bool = False
    request_id: str | None = None


class AgentErrorException(Exception):
    """Raise inside a route to short-circuit with an AgentError JSON body."""

    def __init__(self, error: AgentError, status_code: int = 400) -> None:
        super().__init__(error.message)
        self.error = error
        self.status_code = status_code


def agent_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AgentErrorException)
    # Fill in request_id from contextvar if the caller didn't set one explicitly.
    payload = exc.error.model_dump()
    if payload.get("request_id") is None:
        payload["request_id"] = current_request_id()
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": payload},
    )


# ---------------------------------------------------------------------------
# AUTH_ error factories. Kept here (rather than inside auth/) so every domain
# that needs to 401 out imports the same place, and so the AgentError payload
# shape is consistent across callers.
# ---------------------------------------------------------------------------


def auth_invalid_credentials() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_INVALID_CREDENTIALS",
            message="帳號或密碼錯誤",
            problem="Email or password does not match any active user.",
            cause="Wrong email, wrong password, or the account does not exist.",
            fix="Double-check the credentials. If forgotten, contact an admin — "
            "Phase 1 has no self-serve password reset.",
            retryable=False,
        ),
        status_code=401,
    )


def auth_missing_token() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_MISSING_TOKEN",
            message="請先登入",
            problem="Request is missing the Authorization bearer token.",
            cause="No `Authorization: Bearer <jwt>` header was sent.",
            fix="Attach `Authorization: Bearer <access_token>` to the request.",
            retryable=False,
        ),
        status_code=401,
    )


def auth_expired() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_EXPIRED",
            message="登入已過期，請重新登入",
            problem="Access token `exp` is in the past.",
            cause="The 15-minute access-token TTL elapsed.",
            fix="Call POST /v1/auth/refresh with the refresh token to mint a new access token.",
            retryable=True,
        ),
        status_code=401,
    )


def auth_invalid_token() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_INVALID_TOKEN",
            message="無效的存取權杖",
            problem="Access token signature is invalid, malformed, or uses an unexpected algorithm.",
            cause="Token was tampered with, signed by a different secret, or never minted by this server.",
            fix="Re-authenticate via POST /v1/auth/login.",
            retryable=False,
        ),
        status_code=401,
    )


def auth_refresh_expired() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_REFRESH_EXPIRED",
            message="登入過期，請重新登入",
            problem="Refresh token is past its `expires_at`.",
            cause="The 30-day refresh-token TTL elapsed without use.",
            fix="Call POST /v1/auth/login to start a new session.",
            retryable=False,
        ),
        status_code=401,
    )


def auth_refresh_revoked() -> AgentErrorException:
    return AgentErrorException(
        AgentError(
            code="AUTH_REFRESH_REVOKED",
            message="登入已登出，請重新登入",
            problem="Refresh token has been revoked.",
            cause="POST /v1/auth/logout was called for this token, or an admin revoked it.",
            fix="Call POST /v1/auth/login to start a new session.",
            retryable=False,
        ),
        status_code=401,
    )
