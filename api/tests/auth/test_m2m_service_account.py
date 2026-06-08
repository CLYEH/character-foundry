"""M2M service-account identity (T-092).

A sanctioned M2M (client_credentials) client resolves on `/mcp/*` to a
provisioned backend service-account `User`, so a headless agent can run
user-scoped tools (character.create, ...) and OWN what it creates — the
industry-standard machine-principal model. Unsanctioned M2M clients stay
`user_id=None` (read-only). `is_m2m` stays True either way, so `/v1/*` keeps
rejecting the token via `auth_m2m_wrong_surface` — the service identity is
confined to `/mcp/*`.

Three layers:
  1. Pure: the sanctioned-set invariant + the helper functions.
  2. Routing (mocked): `resolve_mcp_token` populates `user_id` for sanctioned
     M2M clients and leaves it None otherwise.
  3. Provisioning (real DB): the service-account row is created once, reused,
     and never leaks onto the `/v1/*` REST surface.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.mcp_clients import (
    M2M_SERVICE_ACCOUNT_CLIENTS,
    get_allowed_scopes,
    is_allowed_client,
    is_m2m_service_account_client,
)
from app.auth.oauth import JWKSCache, OAuthClaims
from app.auth.provisioning import m2m_service_account_email

# ---------------------------------------------------------------------------
# 1. Pure — sanctioned-set invariant + helpers
# ---------------------------------------------------------------------------


def test_sanctioned_set_entries_are_capped_m2m_clients() -> None:
    """Every entry must be a known, scope-capped (M2M) client in ALLOWED_CLIENTS.

    Guards against listing a delegated client (`scopes=None`) or a typo'd
    client_id — either would mean granting resource ownership to something that
    isn't actually an authenticated M2M principal.
    """
    assert M2M_SERVICE_ACCOUNT_CLIENTS, "expected at least cf-test-agent"
    for client_id in M2M_SERVICE_ACCOUNT_CLIENTS:
        assert is_allowed_client(client_id), f"{client_id} not in ALLOWED_CLIENTS"
        # A capped (non-None) scope set is what marks an M2M client; delegated
        # clients carry `scopes=None` (consent-time scope).
        assert get_allowed_scopes(client_id) is not None, f"{client_id} is delegated, not M2M"


def test_is_m2m_service_account_client_membership() -> None:
    assert is_m2m_service_account_client("cf-test-agent")
    # Delegated clients and unknown ids are NOT service accounts.
    assert not is_m2m_service_account_client("claude-code")
    assert not is_m2m_service_account_client("character-foundry-spa")
    assert not is_m2m_service_account_client("never-registered")


def test_service_account_email_is_stable_and_lowercased() -> None:
    assert m2m_service_account_email("cf-test-agent") == "agent+cf-test-agent@example.com"
    # Case-insensitive so the LOWER(email) lookup is a stable hit regardless of
    # how Authentik cases the client_id in the token.
    assert m2m_service_account_email("CF-Test-Agent") == "agent+cf-test-agent@example.com"


# ---------------------------------------------------------------------------
# 2. Routing — resolve_mcp_token (DB + verifier mocked, like test_skeleton)
# ---------------------------------------------------------------------------


def _fake_session_factory() -> Any:
    """A no-op async_session_factory replacement: the resolver is mocked out, so
    the yielded session is a sentinel that's never actually queried."""

    class _FakeSession:
        pass

    def _factory() -> Any:
        @contextlib.asynccontextmanager
        async def _ctx() -> Any:
            yield _FakeSession()

        return _ctx

    return _factory


def _unsigned_token() -> str:
    """A parseable JWT whose signature is irrelevant — `verify_oauth_token` is
    monkeypatched, so only the top-level unverified decode in `resolve_mcp_token`
    runs against this string."""
    return pyjwt.encode(
        {"iss": "any", "sub": "any", "exp": int(time.time()) + 60},
        "irrelevant-secret",
        algorithm="HS256",
    )


async def test_sanctioned_m2m_client_resolves_service_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cf-test-agent M2M token resolves to a service-account user_id, with
    `is_m2m=True` preserved."""
    from app.mcp.auth import MCPAuthContext, resolve_mcp_token

    service_user_id = uuid.uuid4()
    m2m_claims = OAuthClaims(
        sub="ak-cf-test-agent-client_credentials",
        client_id="cf-test-agent",
        scopes=frozenset({"character:write", "task:read"}),
        email=None,  # M2M tokens carry no email
        name=None,
        is_m2m=True,
    )

    async def _fake_verify(_token: str) -> OAuthClaims:
        return m2m_claims

    async def _fake_resolve(client_id: str, _db: Any) -> uuid.UUID:
        assert client_id == "cf-test-agent"
        return service_user_id

    monkeypatch.setattr("app.mcp.auth.is_authentik_token", lambda _: True)
    monkeypatch.setattr("app.mcp.auth.verify_oauth_token", _fake_verify)
    monkeypatch.setattr("app.mcp.auth.resolve_m2m_service_user_id", _fake_resolve)
    monkeypatch.setattr("app.mcp.auth.async_session_factory", _fake_session_factory())

    result = await resolve_mcp_token(_unsigned_token())

    assert isinstance(result, MCPAuthContext), result
    assert result.user_id == service_user_id
    assert result.is_m2m is True, (
        "service identity must NOT downgrade is_m2m (keeps /v1/* rejecting)"
    )
    assert result.client_id == "cf-test-agent"
    assert result.scopes == frozenset({"character:write", "task:read"})


async def test_m2m_scopes_come_from_token_claim_no_cap_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2M scopes are read straight from the token's `scope` claim — there is NO
    fallback to the client's allowlist cap.

    T-093 made Authentik emit the granted app scopes into the access-token JWT
    (via the ScopeMapping expressions), so the empty-claim case the old
    cap-fallback existed for no longer happens for a correctly-configured client.
    The fallback was REMOVED because it would silently widen a future narrow-cap
    client's empty-claim misconfig up to its full cap (S3.5-6 security 🟡). The
    cap still bounds the token as a ceiling in `verify_oauth_token`
    (`token_scopes <= cap`), so a real claim can never exceed the client's
    authorization; this pins that an empty claim now yields an EMPTY grant, not
    the cap. (A real, populated claim is honored verbatim — covered by
    test_sanctioned_m2m_client_resolves_service_user_id.)
    """
    from app.mcp.auth import MCPAuthContext, resolve_mcp_token

    empty_scope_claims = OAuthClaims(
        sub="ak-cf-test-agent-client_credentials",
        client_id="cf-test-agent",
        scopes=frozenset(),  # an empty / misconfigured scope claim
        email=None,
        name=None,
        is_m2m=True,
    )

    async def _fake_verify(_token: str) -> OAuthClaims:
        return empty_scope_claims

    async def _fake_resolve(_client_id: str, _db: Any) -> uuid.UUID:
        return uuid.uuid4()

    monkeypatch.setattr("app.mcp.auth.is_authentik_token", lambda _: True)
    monkeypatch.setattr("app.mcp.auth.verify_oauth_token", _fake_verify)
    monkeypatch.setattr("app.mcp.auth.resolve_m2m_service_user_id", _fake_resolve)
    monkeypatch.setattr("app.mcp.auth.async_session_factory", _fake_session_factory())

    result = await resolve_mcp_token(_unsigned_token())

    assert isinstance(result, MCPAuthContext), result
    # No cap-fallback: an empty scope claim resolves to an empty grant.
    assert result.scopes == frozenset()
    assert result.is_m2m is True


async def test_unsanctioned_m2m_client_stays_userless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An M2M client NOT in the allowlist keeps `user_id=None` — the resolver is
    never invoked, preserving the T-084/85/86 read-only-M2M default."""
    from app.mcp.auth import MCPAuthContext, resolve_mcp_token

    m2m_claims = OAuthClaims(
        sub="some-future-bot",
        client_id="some-future-bot",  # not in M2M_SERVICE_ACCOUNT_CLIENTS
        scopes=frozenset({"character:read"}),
        email=None,
        name=None,
        is_m2m=True,
    )

    async def _fake_verify(_token: str) -> OAuthClaims:
        return m2m_claims

    def _boom_resolve(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("resolve_m2m_service_user_id must NOT run for an unsanctioned client")

    monkeypatch.setattr("app.mcp.auth.is_authentik_token", lambda _: True)
    monkeypatch.setattr("app.mcp.auth.verify_oauth_token", _fake_verify)
    monkeypatch.setattr("app.mcp.auth.resolve_m2m_service_user_id", _boom_resolve)

    result = await resolve_mcp_token(_unsigned_token())

    assert isinstance(result, MCPAuthContext), result
    assert result.user_id is None
    assert result.is_m2m is True


# ---------------------------------------------------------------------------
# 3. Provisioning — real DB
# ---------------------------------------------------------------------------


def _count_users(database_url: str, email: str) -> int:
    async def _q() -> int:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT COUNT(*) FROM users WHERE email = :e"), {"e": email}
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
                        text(
                            "SELECT name, email, team_id, password_hash FROM users WHERE email = :e"
                        ),
                        {"e": email},
                    )
                ).one()
                return {
                    "name": row.name,  # type: ignore[attr-defined]
                    "email": row.email,  # type: ignore[attr-defined]
                    "team_id": str(row.team_id),  # type: ignore[attr-defined]
                    "password_hash": row.password_hash,  # type: ignore[attr-defined]
                }
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def _run_resolve(client_id: str) -> uuid.UUID:
    """Open a real short-lived session and resolve (provisioning on first call).

    Clears the engine / sessionmaker lru_caches first so the factory binds to the
    current test DATABASE_URL even if a prior `client`-fixture test warmed it."""

    async def _go() -> uuid.UUID:
        from app.auth.user_resolution import resolve_m2m_service_user_id
        from app.db.session import async_session_factory, get_engine

        get_engine.cache_clear()
        async_session_factory.cache_clear()
        factory = async_session_factory()
        async with factory() as db:
            return await resolve_m2m_service_user_id(client_id, db)

    return asyncio.run(_go())


def test_provision_creates_single_service_user_and_is_idempotent(
    database_url: str,
    clean_auth_tables: None,
) -> None:
    """First resolve provisions the row; a second resolve reuses it (no
    duplicate). Row lands in the default team with the service-agent name."""
    email = m2m_service_account_email("cf-test-agent")
    assert _count_users(database_url, email) == 0

    first = _run_resolve("cf-test-agent")
    second = _run_resolve("cf-test-agent")

    assert first == second, "second resolve must reuse the provisioned row, not create a new one"
    assert _count_users(database_url, email) == 1

    row = _fetch_user_row(database_url, email)
    assert row["name"] == "Service Agent (cf-test-agent)"
    # Service account carries a real (random) password hash — not an empty
    # string or a guessable sentinel — so `/v1/auth/login` can never
    # authenticate as it (nobody holds the cleartext). The repo hashes with
    # argon2id (app.auth.passwords); assert the stored value is a real argon2
    # hash rather than pinning the exact parameters.
    assert row["password_hash"].startswith("$argon2"), row["password_hash"]


def test_provision_is_race_safe_on_integrity_error(
    database_url: str,
    clean_auth_tables: None,
) -> None:
    """Two provisioner calls exercise the IntegrityError re-select branch.

    `auto_provision_m2m_service_user` does a lookup-free insert, so calling it
    twice forces the second commit to collide on the `users.email` unique
    constraint — the exact state a concurrent first-call race produces (both
    callers miss the lookup, both try to insert). The branch must re-select the
    winning row and return it, not bubble a 500. Calling the provisioner
    directly (rather than `resolve_m2m_service_user_id`, which lookups first and
    would short-circuit) is what makes the race branch deterministically
    reachable.
    """

    async def _provision_twice() -> tuple[uuid.UUID, uuid.UUID]:
        from app.auth.provisioning import auto_provision_m2m_service_user
        from app.db.session import async_session_factory, get_engine

        get_engine.cache_clear()
        async_session_factory.cache_clear()
        first = await auto_provision_m2m_service_user(client_id="cf-test-agent")
        second = await auto_provision_m2m_service_user(client_id="cf-test-agent")
        return first.id, second.id

    id1, id2 = asyncio.run(_provision_twice())

    assert id1 == id2, "the colliding second insert must re-select the winning row"
    assert _count_users(database_url, m2m_service_account_email("cf-test-agent")) == 1


def test_m2m_service_account_token_rejected_on_v1_surface(
    client: TestClient,
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
    database_url: str,
) -> None:
    """A cf-test-agent M2M token on `/v1/*` is still rejected with
    `AUTH_M2M_WRONG_SURFACE` (is_m2m stays True), and the REST path provisions
    NO service-account row — the service identity is `/mcp/*`-only."""
    token = make_oauth_token(
        scopes=["character:read"],
        client_id="cf-test-agent",
        email=None,  # M2M
    )
    resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "AUTH_M2M_WRONG_SURFACE"
    # Rejected before any provisioning could run on the REST surface.
    assert _count_users(database_url, m2m_service_account_email("cf-test-agent")) == 0
