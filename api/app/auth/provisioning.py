"""OAuth first-login auto-provisioning (T-071).

When a delegated OAuth token verifies against Authentik but no backend `users`
row exists for the claim email, `_resolve_oauth` (in `app.api.deps`) calls
`auto_provision_oauth_user` to create one — instead of 401-ing and forcing
the operator to run the `provision-operator` CLI by hand. The CLI keeps
working for break-glass / pre-provisioning; auto-provision covers the
normal first-login path.

Defense-in-depth guardrail: only emails whose domain is in
`OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS` are auto-provisioned. Authentik's own
Google Workspace `hd=` restriction (see `planning/devops/authentik-stack.md`
§5.2) is the upstream gate; this backend allowlist exists so a future
Authentik misconfig (or a second OAuth Source added without `hd=`) can't
silently let arbitrary verified Google accounts grow rows in our DB. Unset
or empty → fail-closed (every miss stays 401).
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import hash_password
from app.db.session import async_session_factory
from app.models.team import Team
from app.models.user import User

_logger = logging.getLogger(__name__)

_ALLOWED_DOMAINS_ENV: Final[str] = "OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS"
# Mirrors `provision-operator` CLI (api/app/cli.py): operators use the
# default Phase-1 team (DECISIONS §6 B5 — single team).
_DEFAULT_TEAM_NAME: Final[str] = "default"

# Synthetic email domain for M2M service-account users (T-092). `example.com`
# (RFC 2606 reserved) rather than `.local` — the latter is rejected by
# pydantic's `EmailStr`, which would 422 any owner DTO that serialises a
# service account's email (see the same note in api/app/cli.py E2E_USERS).
_M2M_SERVICE_EMAIL_DOMAIN: Final[str] = "example.com"


def _allowed_domains() -> frozenset[str]:
    raw = os.environ.get(_ALLOWED_DOMAINS_ENV, "")
    return frozenset(chunk.strip().lower() for chunk in raw.split(",") if chunk.strip())


def is_email_allowed_for_auto_provision(email: str) -> bool:
    """True when `email`'s domain is in the auto-provision allowlist.

    Domain match is case-insensitive on the part after the last `@`. Local
    part is not consulted — this is a coarse domain gate, not a per-user
    allowlist. Real per-user control belongs in the `provision-operator`
    CLI (or, eventually, an admin UI).
    """
    domains = _allowed_domains()
    if not domains:
        return False
    _, sep, domain = email.rpartition("@")
    if not sep or not domain:
        return False
    return domain.lower() in domains


async def auto_provision_oauth_user(*, email: str, name: str | None) -> User:
    """Insert a backend `users` row for a freshly-authenticated OAuth caller.

    Uses its own short-lived `AsyncSession` so the request transaction stays
    clean — auth deps run before any business-logic write, but isolating the
    insert makes that invariant explicit rather than incidental.

    Mirrors the `provision-operator` CLI shape:
      • team = `default` (Phase 1 single-team, DECISIONS §6 B5)
      • password_hash = hash of a never-recorded random token — the legacy
        JWT-login path is effectively dead for this user, which is correct
        (they're OAuth-only). A real password break-glass row still requires
        `create-user`.
      • display name = OIDC `name` claim if present, else email local part.

    Concurrent first-logins for the same email race on the `users.email`
    unique constraint; the IntegrityError branch re-selects the winning row
    instead of bubbling a 500 to one of the two callers.
    """
    factory = async_session_factory()
    async with factory() as db:
        team = (
            await db.execute(select(Team).where(Team.name == _DEFAULT_TEAM_NAME))
        ).scalar_one_or_none()
        if team is None:
            # Migration drift — every env we run in has the `default` team
            # seeded by migration 002. Loud failure beats silent 401 here.
            raise RuntimeError(f"team {_DEFAULT_TEAM_NAME!r} not found — run alembic migrations")
        display_name = (name or "").strip() or email.split("@", 1)[0]
        # `users.name` is VARCHAR(100); upstream OIDC name claims can exceed
        # that. Trim rather than 500 on the insert.
        display_name = display_name[:100]
        user = User(
            team_id=team.id,
            name=display_name,
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = (await db.execute(select(User).where(User.email == email))).scalar_one()
            _logger.info(
                "oauth_auto_provision_race_resolved",
                extra={"user_id": str(existing.id)},
            )
            return existing
        await db.refresh(user)
        return user


def m2m_service_account_email(client_id: str) -> str:
    """Stable synthetic email keying an M2M client to its service-account User.

    `agent+{client_id}@example.com` — the `+client_id` segment makes the row
    deterministic per client (Auth0 uses `<client_id>@clients` for the same
    purpose). Lowercased so the `LOWER(email)` lookup in
    `resolve_m2m_service_user_id` is a stable hit.
    """
    return f"agent+{client_id.lower()}@{_M2M_SERVICE_EMAIL_DOMAIN}"


async def auto_provision_m2m_service_user(*, client_id: str) -> User:
    """Insert (or re-fetch) the backend service-account `User` for an M2M client.

    Called by `resolve_m2m_service_user_id` the first time a sanctioned M2M
    client_credentials token reaches `/mcp/*`. Mirrors `auto_provision_oauth_user`:
      • team = `default` (Phase 1 single-team, DECISIONS §6 B5).
      • password_hash = hash of a never-recorded random token — there is no
        human and no password login for a service account; this just satisfies
        the NOT NULL column and guarantees `/v1/auth/login` can never match.
      • email = `m2m_service_account_email(client_id)` (the stable key).
      • name = `Service Agent ({client_id})`, trimmed to the VARCHAR(100) column.

    The caller is gated on `is_m2m_service_account_client(client_id)` BEFORE
    reaching here, so this never provisions an unsanctioned client. Concurrent
    first-calls race on the `users.email` unique constraint; the IntegrityError
    branch re-selects the winning row instead of 500-ing one of the callers.
    """
    email = m2m_service_account_email(client_id)
    factory = async_session_factory()
    async with factory() as db:
        team = (
            await db.execute(select(Team).where(Team.name == _DEFAULT_TEAM_NAME))
        ).scalar_one_or_none()
        if team is None:
            raise RuntimeError(f"team {_DEFAULT_TEAM_NAME!r} not found — run alembic migrations")
        user = User(
            team_id=team.id,
            name=f"Service Agent ({client_id})"[:100],
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = (await db.execute(select(User).where(User.email == email))).scalar_one()
            _logger.info(
                "m2m_service_account_provision_race_resolved",
                extra={"user_id": str(existing.id), "client_id": client_id},
            )
            return existing
        await db.refresh(user)
        _logger.info(
            "m2m_service_account_provisioned",
            extra={"user_id": str(user.id), "client_id": client_id},
        )
        return user
