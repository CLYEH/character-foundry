"""OAuth claim → backend `users.id` resolution, shared across surfaces.

Both `/v1/*` (via `app.api.deps._resolve_oauth`) and `/mcp/*` (via
`app.mcp.auth.resolve_mcp_token`) need the same logic to map a verified
delegated `OAuthClaims` to a `User.id`: lowercase-canonicalise the email,
look up by `LOWER(email)`, catch case-variant collisions, and
auto-provision on first login if the email domain is allowlisted.

Extracted from `_resolve_oauth` after T-080 PR #107 Codex round-2 P1
flagged that the MCP path was treating delegated tokens like M2M
(setting `user_id=None`), losing user identity for any tool that
later needs to scope data to the caller. The shared helper means
both surfaces stay in sync — if a future Codex round on `/v1/*`
tightens the lookup (e.g. functional unique index on `lower(email)`),
`/mcp/*` automatically benefits.

M2M handling stays caller-specific: `/v1/*` raises
`auth_m2m_wrong_surface` BEFORE this helper runs; `/mcp/*` only calls
this for delegated tokens (M2M short-circuits to `user_id=None`
upstream). So this function assumes `claims.is_m2m is False`.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oauth import OAuthClaims
from app.auth.provisioning import (
    auto_provision_m2m_service_user,
    auto_provision_oauth_user,
    is_email_allowed_for_auto_provision,
    m2m_service_account_email,
)
from app.core.errors import auth_invalid_token
from app.models.user import User

_logger = logging.getLogger(__name__)


async def resolve_oauth_user_id(claims: OAuthClaims, db: AsyncSession) -> uuid.UUID:
    """Resolve a verified delegated `OAuthClaims` to a backend `User.id`.

    Raises:
        AgentErrorException(`AUTH_INVALID_TOKEN`):
          • `claims.email` is None (delegated token with missing email — an
            Authentik provider mapping misconfig; generic code so the caller
            can't enumerate which claim was missing).
          • DB has multiple rows whose `LOWER(email)` matches — pre-existing
            case-variant duplicates from earlier `provision-operator` /
            `create-user` runs (logged loud; operator must dedupe by hand).
          • Email's domain is not in `OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS`
            and no row exists yet (defense-in-depth against an Authentik
            misconfig that omits its upstream `hd=` Google Workspace gate).

    Per `app.auth.provisioning`, an empty / unset allowlist domain env
    fails closed — first-login from any domain stays 401 until the
    operator explicitly widens it.
    """
    if not claims.email:
        raise auth_invalid_token()

    # See `_resolve_oauth` comments for canonicalisation rationale —
    # `users.email` is case-sensitive String unique, real IdPs treat
    # email as case-insensitive, so we lowercase here and lookup via
    # `LOWER(email)`.
    email = claims.email.lower()

    try:
        result = await db.execute(select(User).where(func.lower(User.email) == email))
        user = result.scalar_one_or_none()
    except MultipleResultsFound:
        _logger.error(
            "oauth_lookup_multiple_email_variants",
            extra={
                "email_lowercased": email,
                "client_id": claims.client_id,
            },
        )
        raise auth_invalid_token() from None

    if user is not None:
        return user.id

    # First-login auto-provision (T-071), gated on the email-domain
    # allowlist. The allowlist exists so a future Authentik misconfig
    # (or an added OAuth Source missing its own `hd=` gate) can't
    # silently grow rows for arbitrary verified Google identities.
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


async def resolve_m2m_service_user_id(client_id: str, db: AsyncSession) -> uuid.UUID:
    """Resolve a sanctioned M2M client to its backend service-account `User.id`.

    Looks the service account up by its synthetic email (`m2m_service_account_email`)
    and provisions it on first call (T-092). Unlike `resolve_oauth_user_id` there
    is no email-domain gate — the gate is `is_m2m_service_account_client(client_id)`,
    which the caller (`app.mcp.auth.resolve_mcp_token`) checks BEFORE invoking this.
    The `client_id` itself rode in on a cryptographically-verified token whose
    `client_id` is already in `ALLOWED_CLIENTS`, so reaching here means the client
    is both sanctioned and authenticated.

    The lookup uses the caller's short-lived session `db`; provisioning opens its
    own session (mirroring `auto_provision_oauth_user`) so the insert is isolated.
    """
    email = m2m_service_account_email(client_id)
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalar_one_or_none()
    if user is not None:
        return user.id
    provisioned = await auto_provision_m2m_service_user(client_id=client_id)
    return provisioned.id
