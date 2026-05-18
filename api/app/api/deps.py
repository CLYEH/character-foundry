"""Shared FastAPI dependencies."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

import jwt as pyjwt
from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import JWTExpired, JWTInvalid, verify_access_token
from app.auth.oauth import OAuthClaims, is_authentik_token, verify_oauth_token
from app.auth.provisioning import (
    auto_provision_oauth_user,
    is_email_allowed_for_auto_provision,
)
from app.auth.scopes import CANONICAL_SCOPES
from app.core.errors import (
    auth_expired,
    auth_invalid_token,
    auth_m2m_wrong_surface,
    auth_missing_token,
)
from app.core.redis_client import get_redis
from app.db import get_db
from app.models.user import User
from app.prompt.reconciler import PromptReconciler, get_prompt_reconciler
from app.storage.backend import StorageBackend
from app.storage.local import LocalFilesystemBackend

_logger = logging.getLogger(__name__)


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


def _peek_unverified_payload(token: str) -> dict[str, Any]:
    """Decode the JWT body WITHOUT verifying signature/exp/aud/iss.

    Used only to read the `iss` claim so the right verifier can be picked
    (legacy HS256 vs Authentik RS256). Every subsequent step re-verifies the
    claim cryptographically — this peek is a routing decision, not a trust
    decision. The `verify_signature=False` flag is the correct, intentional
    choice at this layer; rejecting on unverified-iss would force us to pick
    one verifier blindly before we know which keypair the token was signed
    with.
    """
    try:
        # Routing-only peek; signature is re-verified one call later in
        # _resolve_oauth / _resolve_jwt against the correct key (see docstring).
        # nosemgrep: python.jwt.security.unverified-jwt-decode.unverified-jwt-decode
        return pyjwt.decode(token, options={"verify_signature": False})
    except pyjwt.InvalidTokenError as exc:
        raise auth_invalid_token() from exc


async def _resolve_oauth(
    request: Request,
    db: AsyncSession,
    token: str,
) -> uuid.UUID:
    """Authentik OAuth path. Verifies token, writes scopes to request.state,
    resolves the matching User by email (delegated tokens only). M2M tokens
    raise `AUTH_M2M_WRONG_SURFACE` — they reach the MCP server in T-3.5b,
    never `/v1/*`.
    """
    claims: OAuthClaims = await verify_oauth_token(token)

    if claims.is_m2m:
        # Sanctioned token, wrong endpoint surface. Don't pollute
        # request.state before raising — the request never reaches
        # downstream deps that would read it.
        raise auth_m2m_wrong_surface()

    if not claims.email:
        # Delegated token with no email claim is a misconfiguration on the
        # Authentik provider mapping side; we can't resolve a User without
        # it. Generic invalid_token (don't leak which claim was missing).
        raise auth_invalid_token()

    request.state.token_scopes = claims.scopes
    request.state.oauth_client_id = claims.client_id
    request.state.is_m2m = claims.is_m2m

    # Canonicalize email at the auth-dep boundary (Codex round-1 P2). `users.
    # email` is a plain unique String column (not citext), so case drift
    # between successive Google logins (e.g. `Alice@x.com` vs `alice@x.com`)
    # would otherwise miss the existing row and call auto_provision again,
    # splitting one operator across two rows. Lowercasing once here makes
    # both the lookup and the auto-provision insert agree on a single
    # canonical form. RFC 5321 says local-part is technically case-sensitive,
    # but real-world identity providers (Google, Microsoft, etc.) treat it
    # as case-insensitive; matching that behaviour is the safer default.
    email = claims.email.lower()

    # Case-insensitive lookup (Codex round-2 P2) — without it, an existing
    # mixed-case row created via the `provision-operator` / `create-user`
    # CLI (e.g. `--email Leo@Omniguider.com`) wouldn't match the lowercased
    # OAuth claim, the miss-branch would run auto_provision, and the insert
    # would succeed with the lowercase variant alongside the mixed-case
    # original — silently splitting one operator across two rows. Comparing
    # `LOWER(email)` against the already-lowercased token form matches
    # whichever casing the existing row was stored with. Phase 1 has O(10)
    # users so the seq-scan cost is irrelevant; a functional index on
    # `lower(email)` is the right scale-out lever and lives in a schema
    # migration outside T-071 scope.
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalar_one_or_none()
    if user is None:
        # Authentik knows the email but we don't have a CF User row yet.
        # T-071: auto-provision the row on first login, gated on a
        # backend domain allowlist (defense in depth — Authentik's own
        # `hd=` gate is upstream, see `planning/devops/authentik-stack.md`
        # §5.2 / §5.7.2). Unknown domains stay 401 so we don't leak
        # existence of arbitrary verified Google identities into our DB.
        domain = email.rpartition("@")[2]
        if not is_email_allowed_for_auto_provision(email):
            _logger.warning(
                "oauth_auto_provision_denied",
                extra={
                    "email_domain": domain,
                    "client_id": claims.client_id,
                },
            )
            raise auth_invalid_token()
        user = await auto_provision_oauth_user(email=email, name=claims.name)
        _logger.info(
            "oauth_user_auto_provisioned",
            extra={
                "user_id": str(user.id),
                "email_domain": domain,
                "client_id": claims.client_id,
            },
        )
    return user.id


def _resolve_jwt(
    request: Request,
    token: str,
) -> uuid.UUID:
    """Legacy HS256 JWT path. Verifies the token and writes the full canonical
    scope set into `request.state.token_scopes` — pre-OAuth sessions weren't
    scope-aware, and treating them as "full access" preserves dual-stack
    regression behavior while we migrate (per `planning/auth/open-questions.md`
    Q4 simplified dual-stack)."""
    try:
        payload = verify_access_token(token)
    except JWTExpired as exc:
        raise auth_expired() from exc
    except JWTInvalid as exc:
        raise auth_invalid_token() from exc

    # Legacy JWTs get the full canonical scope set (per Q4 simplified
    # dual-stack — pre-OAuth sessions are grandfathered through any
    # require_scope gate). This is **only** safe because `/v1/*` is the
    # human-user surface; when `/mcp/*` ships in T-3.5b it MUST guard
    # against `oauth_client_id is None` so legacy JWTs can't reach
    # M2M-only tools. The `oauth_client_id` / `is_m2m` writes below are
    # the contract surface for that future check.
    request.state.token_scopes = CANONICAL_SCOPES
    request.state.oauth_client_id = None
    request.state.is_m2m = False

    try:
        return uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise auth_invalid_token() from exc


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Dual-stack: dispatch by `iss` claim, verify, populate `request.state`,
    resolve User. Both paths leave `token_scopes` / `oauth_client_id` /
    `is_m2m` on `request.state` so `app.auth.scopes.require_scope` and any
    future audit middleware can read them uniformly.
    """
    token = _extract_bearer(authorization)
    unverified = _peek_unverified_payload(token)

    if is_authentik_token(unverified):
        user_id = await _resolve_oauth(request, db, token)
    else:
        user_id = _resolve_jwt(request, token)

    user = await db.get(User, user_id)
    if user is None:
        # Token verified but the user is gone — treat as invalid rather than
        # revealing account existence state.
        raise auth_invalid_token()
    return user


async def get_prompt_reconciler_dep(
    redis: Annotated[Redis, Depends(get_redis)],
) -> PromptReconciler:
    """DI seam for `POST /v1/prompt/preview` so tests can swap in a
    `FakeReconcilerClient` without touching `AI_STUB_MODE`. Production
    composition stays in `app.prompt.reconciler.get_prompt_reconciler`.
    """
    return get_prompt_reconciler(redis)


async def get_current_user_no_pin(
    request: Request,
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

    token = _extract_bearer(authorization)
    unverified = _peek_unverified_payload(token)

    factory = async_session_factory()
    async with factory() as db:
        if is_authentik_token(unverified):
            user_id = await _resolve_oauth(request, db, token)
        else:
            user_id = _resolve_jwt(request, token)
        user = await db.get(User, user_id)
        if user is None:
            raise auth_invalid_token()
        return user
