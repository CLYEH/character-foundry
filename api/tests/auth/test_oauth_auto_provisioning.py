"""OAuth first-login auto-provisioning tests (T-071).

`_resolve_oauth` used to 401 when an Authentik-verified token's email had no
matching `users` row — the only fix was to manually run the
`provision-operator` CLI for each new operator. T-071 makes the first-login
provisioning automatic, gated on a backend domain allowlist.

Three acceptance flows from the ticket:

  1. Authentik-verified token, email passes guardrail, no `users` row →
     row is created automatically and the request succeeds (200).
  2. Authentik-verified token, email FAILS guardrail, no `users` row →
     still 401 with `AUTH_INVALID_TOKEN`, no row created.
  3. Authentik-verified token whose email already has a `users` row →
     behaviour unchanged (no duplicate insert, request succeeds 200).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.oauth import JWKSCache
from app.auth.provisioning import (
    _ALLOWED_DOMAINS_ENV,
    is_email_allowed_for_auto_provision,
)

# ---------------------------------------------------------------------------
# Pure-function unit tests on the guardrail. Cheap to run, easy to triangulate
# misconfiguration without spinning up the full TestClient.
# ---------------------------------------------------------------------------


def test_guardrail_unset_env_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ALLOWED_DOMAINS_ENV, raising=False)
    assert not is_email_allowed_for_auto_provision("alice@anything.example")


def test_guardrail_empty_env_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "   ,  ")
    assert not is_email_allowed_for_auto_provision("alice@anything.example")


def test_guardrail_matches_listed_domain_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "Character-Foundry.com, acme.example")
    assert is_email_allowed_for_auto_provision("operator@character-foundry.com")
    assert is_email_allowed_for_auto_provision("OPERATOR@CHARACTER-FOUNDRY.COM")
    assert is_email_allowed_for_auto_provision("agent@acme.example")


def test_guardrail_rejects_unlisted_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "character-foundry.com")
    assert not is_email_allowed_for_auto_provision("operator@evil.example")


def test_guardrail_rejects_substring_domain_not_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`evilcharacter-foundry.com` must NOT pass when allowlist is
    `character-foundry.com`. Domain match is whole-domain equality on the
    part after `@`, not a substring or suffix search.
    """
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "character-foundry.com")
    assert not is_email_allowed_for_auto_provision("operator@evilcharacter-foundry.com")


def test_guardrail_rejects_malformed_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "character-foundry.com")
    # No `@` at all → can't extract a domain → reject (don't pretend the
    # whole string is the domain).
    assert not is_email_allowed_for_auto_provision("not-an-email")
    # Trailing `@` with empty domain → reject.
    assert not is_email_allowed_for_auto_provision("alice@")


# ---------------------------------------------------------------------------
# Integration tests against the live TestClient. Reuses the fixtures /
# token factory from test_dual_stack.py — pytest auto-discovers the
# conftest in the same directory and the test_dual_stack module is
# importable for its fixtures.
# ---------------------------------------------------------------------------


def _count_users(database_url: str, email: str) -> int:
    """Direct SQL count to verify provisioning side effect outside the
    request-scoped session (which won't have committed when we ask)."""

    async def _q() -> int:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT COUNT(*) FROM users WHERE email = :e"),
                        {"e": email},
                    )
                ).one()
                return int(row[0])
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def _fetch_user_row(database_url: str, email: str) -> dict[str, Any]:
    async def _q() -> dict[str, Any]:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT id, name, email, team_id FROM users WHERE email = :e"),
                        {"e": email},
                    )
                ).one()
                return {
                    "id": str(row.id),  # type: ignore[attr-defined]
                    "name": row.name,  # type: ignore[attr-defined]
                    "email": row.email,  # type: ignore[attr-defined]
                    "team_id": str(row.team_id),  # type: ignore[attr-defined]
                }
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def test_oauth_first_login_auto_provisions_user_when_domain_allowed(
    client: TestClient,
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """AC #1: Authentik-verified token for a brand-new operator whose email
    domain is on the allowlist must auto-create the `users` row on first
    login and return 200 (not 401)."""
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "character-foundry.com")
    new_operator_email = "newoperator@character-foundry.com"
    assert _count_users(database_url, new_operator_email) == 0

    token = make_oauth_token(
        scopes=["character:read"],
        client_id="claude-code",
        email=new_operator_email,
        extra={"name": "New Operator"},
    )
    resp = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == new_operator_email
    assert body["user"]["name"] == "New Operator"

    # Side-effect verification — row exists exactly once.
    assert _count_users(database_url, new_operator_email) == 1
    row = _fetch_user_row(database_url, new_operator_email)
    assert row["name"] == "New Operator"


def test_oauth_first_login_falls_back_to_email_local_part_when_name_claim_absent(
    client: TestClient,
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """OIDC `name` is delivered with the `profile` scope; if the token's
    issuer mapping omits it, the display name defaults to the email local
    part rather than 500-ing on the NOT NULL column."""
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "character-foundry.com")
    email = "noname@character-foundry.com"
    token = make_oauth_token(
        scopes=["character:read"],
        client_id="claude-code",
        email=email,
        # No `name` claim — make_oauth_token only sets it via `extra`.
    )
    resp = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert _fetch_user_row(database_url, email)["name"] == "noname"


def test_oauth_first_login_rejected_when_domain_not_allowlisted(
    client: TestClient,
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """AC #2: Authentik-verified token whose email domain is NOT on the
    allowlist must stay 401, leaving no `users` row behind. This is the
    primary defense-in-depth case — if Authentik's `hd=` gate is ever
    misconfigured or a second OAuth Source is added without it, the backend
    still won't grow rows for arbitrary verified Google accounts.
    """
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "character-foundry.com")
    rogue_email = "attacker@evil.example"

    token = make_oauth_token(
        scopes=["character:read"],
        client_id="claude-code",
        email=rogue_email,
    )
    resp = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "AUTH_INVALID_TOKEN"
    # No row created — guardrail short-circuits before provisioning.
    assert _count_users(database_url, rogue_email) == 0


def test_oauth_first_login_rejected_when_env_unset(
    client: TestClient,
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """Unset `OAUTH_AUTO_PROVISION_ALLOWED_DOMAINS` is fail-closed: every
    first-login attempt stays 401 (i.e. behaviour before T-071, requiring
    operators be pre-provisioned via the CLI). This protects deployments
    where the operator forgot to set the env var."""
    monkeypatch.delenv(_ALLOWED_DOMAINS_ENV, raising=False)
    email = "noone@anywhere.example"
    token = make_oauth_token(
        scopes=["character:read"],
        client_id="claude-code",
        email=email,
    )
    resp = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401, resp.text
    assert _count_users(database_url, email) == 0


def test_oauth_existing_user_row_is_not_re_provisioned(
    client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """AC #3: When the email already has a `users` row, the auto-provision
    path is never taken — no duplicate insert, name not overwritten with
    a fresh OIDC `name` claim. Confirms T-071 is additive to the existing
    lookup, not a replacement."""
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "example.com")
    before = _fetch_user_row(database_url, seeded_user["email"])

    token = make_oauth_token(
        scopes=["character:read"],
        client_id="claude-code",
        email=seeded_user["email"],
        extra={"name": "Renamed Operator"},  # Would clobber if provisioning ran.
    )
    resp = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    after = _fetch_user_row(database_url, seeded_user["email"])
    assert after == before, "auto-provision wrongly mutated the existing row"
    assert _count_users(database_url, seeded_user["email"]) == 1


def test_oauth_long_name_claim_is_truncated_to_column_width(
    client: TestClient,
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """`users.name` is VARCHAR(100); an OIDC `name` longer than that must be
    truncated rather than 500 on the insert."""
    monkeypatch.setenv(_ALLOWED_DOMAINS_ENV, "character-foundry.com")
    email = "longname@character-foundry.com"
    long_name = "X" * 200
    token = make_oauth_token(
        scopes=["character:read"],
        client_id="claude-code",
        email=email,
        extra={"name": long_name},
    )
    resp = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert len(_fetch_user_row(database_url, email)["name"]) == 100
