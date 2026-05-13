"""Dual-stack auth middleware tests (T-054).

Covers all eight acceptance criteria from the ticket:

  1. Existing JWT-bearing request still hits a protected endpoint (regression).
  2. Authentik OAuth token (delegated, email matches a seeded User) reaches
     the same endpoint and returns 200.
  3. `require_scope("character:write")` lets sufficient-scope tokens through,
     403s on insufficient, 401s on missing.
  4. OAuth token with a `client_id` not in `ALLOWED_CLIENTS` → 403 with code
     `AUTH_CLIENT_NOT_ALLOWED`.
  5. M2M token whose `scope` claim exceeds its allowlist cap → 403 with code
     `AUTH_SCOPE_EXCEEDS_ALLOWLIST`.
  6. JWKS cache: two consecutive OAuth requests trigger only one HTTP fetch.
  7. Every error body conforms to the AgentError envelope shape.
  8. (covered transitively) Every assertion above goes through the same
     `get_current_user` so the rest of `/v1/*` keeps working.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from typing import Any

import httpx
import jwt as pyjwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.auth.jwt import sign_access_token
from app.auth.mcp_clients import ALLOWED_CLIENTS, ClientPolicy
from app.auth.oauth import (
    JWKSCache,
    reset_jwks_cache_for_test,
    set_jwks_cache_for_test,
)
from app.auth.scopes import require_scope

# ---------------------------------------------------------------------------
# Constants used by the synthetic OAuth tokens. The issuer / audience strings
# are arbitrary but must match what `app.auth.oauth` reads from the env
# vars — `_oauth_env` (below) sets them, and `verify_oauth_token` re-checks
# them against the JWT claims.
# ---------------------------------------------------------------------------

_TEST_ISSUER = "https://auth.test.example/application/o/character-foundry-test/"
_TEST_AUDIENCE = "character-foundry-test"
_TEST_JWKS_URI = "https://auth.test.example/application/o/character-foundry-test/jwks/"
_TEST_KID = "cf-test-rsa-key-1"


# ---------------------------------------------------------------------------
# RSA keypair fixture (module-scoped — generating 2048-bit RSA per test
# triples the test wall time). The public half is wrapped into a JWKS doc
# both for the in-process cache (most tests) and the HTTP fetch path (the
# JWKS-cache-TTL acceptance test).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _rsa_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def _private_key_pem(_rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> bytes:
    priv, _pub = _rsa_keypair
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def _jwks_document(_rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> dict[str, Any]:
    """A minimal JWKS doc describing our single test signing key."""
    _priv, pub = _rsa_keypair
    numbers = pub.public_numbers()

    def _b64url_uint(n: int) -> str:
        import base64

        byte_len = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(byte_len, "big")).rstrip(b"=").decode("ascii")

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": _TEST_KID,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }


# ---------------------------------------------------------------------------
# Env-var + JWKS-cache fixtures. `_oauth_env` writes the env vars
# `app.auth.oauth.verify_oauth_token` reads; `_preload_jwks_cache` swaps in
# a JWKSCache that already contains our public key so most tests skip HTTP.
# The JWKS-fetch acceptance test (`test_jwks_cache_second_request_no_refetch`)
# opts out of the preload and uses respx instead.
# ---------------------------------------------------------------------------


@pytest.fixture
def _oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHENTIK_ISSUER_URL", _TEST_ISSUER)
    monkeypatch.setenv("AUTHENTIK_AUDIENCE", _TEST_AUDIENCE)
    monkeypatch.setenv("AUTHENTIK_JWKS_URL", _TEST_JWKS_URI)


@pytest.fixture
def _preload_jwks_cache(
    _oauth_env: None,
    _jwks_document: dict[str, Any],
) -> Iterator[JWKSCache]:
    """Install a JWKSCache pre-populated from `_jwks_document` so tests that
    don't care about HTTP fetch behaviour avoid wiring respx for every case.
    """
    cache = JWKSCache(_TEST_JWKS_URI)
    # Seed via the dedicated test seam — keeps the test out of the cache's
    # private attributes so an internal refactor doesn't silently break us.
    keys = {key_data["kid"]: pyjwt.PyJWK(key_data) for key_data in _jwks_document["keys"]}
    cache.seed_keys_for_test(keys)
    set_jwks_cache_for_test(cache)
    try:
        yield cache
    finally:
        reset_jwks_cache_for_test()


# ---------------------------------------------------------------------------
# Token factories. `make_oauth_token` mints an RS256 token matching whatever
# `_oauth_env` configured; `make_jwt_token` reuses the legacy HS256 path
# (it's what `app.auth.jwt.sign_access_token` already produces in
# production).
# ---------------------------------------------------------------------------


@pytest.fixture
def make_oauth_token(_private_key_pem: bytes) -> Any:
    def _make(
        *,
        scopes: list[str] | None = None,
        client_id: str = "claude-code",
        email: str | None = "alice@example.com",
        sub: str | None = None,
        issuer: str = _TEST_ISSUER,
        audience: str = _TEST_AUDIENCE,
        expires_in: int = 3600,
        kid: str = _TEST_KID,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + expires_in,
            "azp": client_id,
            "sub": sub if sub is not None else (email or client_id),
            "scope": " ".join(scopes) if scopes else "",
        }
        if email is not None:
            payload["email"] = email
        if extra:
            payload.update(extra)
        return pyjwt.encode(payload, _private_key_pem, algorithm="RS256", headers={"kid": kid})

    return _make


@pytest.fixture
def make_jwt_token(seeded_user: dict[str, str], database_url: str) -> Any:
    """Mint a legacy HS256 token bound to the seeded user. We resolve the
    User's id from the DB so the token's `sub` matches what
    `get_current_user` will look up.
    """

    async def _resolve_user_id() -> str:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT id, team_id FROM users WHERE email = :e"),
                        {"e": seeded_user["email"]},
                    )
                ).one()
                return str(row.id), str(row.team_id)  # type: ignore[return-value]
        finally:
            await engine.dispose()

    user_id, team_id = asyncio.run(_resolve_user_id())

    def _make() -> str:
        token, _ttl = sign_access_token(user_id=user_id, team_id=team_id)
        return token

    return _make


# ---------------------------------------------------------------------------
# Test-only routes that exercise `require_scope`. Registered once on first
# import of this module; FastAPI's routing table happily carries them for
# the rest of the test process (they're prefixed with `/_test_t054/` so they
# can't collide with real endpoints).
# ---------------------------------------------------------------------------


def _ensure_test_routes_registered() -> None:
    """Add the require_scope demonstration routes to the running FastAPI app
    once. Subsequent calls are no-ops. We can't do this at module import
    time because that would trigger `app.main` import before the conftest
    sets `JWT_SECRET` / `AUTHENTIK_*` env vars.

    Refuses to register outside a pytest run as a belt-and-braces guard —
    even though only the `scoped_client` fixture should call this helper,
    mutating the real FastAPI app object from a production import path is a
    smell worth blocking structurally.
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError(
            "_ensure_test_routes_registered called outside a pytest run. "
            "This helper mutates the production FastAPI app object and must "
            "never execute in deployed code."
        )

    from app.main import app

    sentinel = "_t054_test_routes_registered"
    if getattr(app.state, sentinel, False):
        return

    router = APIRouter(prefix="/_test_t054")

    @router.get("/needs-write")
    async def needs_write(
        _: None = Depends(require_scope("character:write")),
    ) -> dict[str, bool]:
        return {"ok": True}

    @router.get("/needs-two-scopes")
    async def needs_two(
        _: None = Depends(
            require_scope("character:write", "task:cancel"),
        ),
    ) -> dict[str, bool]:
        return {"ok": True}

    app.include_router(router)
    setattr(app.state, sentinel, True)


@pytest.fixture
def scoped_client(client: TestClient) -> TestClient:
    _ensure_test_routes_registered()
    return client


# ---------------------------------------------------------------------------
# Helper assertions
# ---------------------------------------------------------------------------


def _assert_agent_error(body: dict[str, Any], code: str) -> None:
    """Every AgentError response must carry the canonical envelope shape."""
    err = body["error"]
    assert err["code"] == code, f"expected code={code}, got {err.get('code')}"
    for required in ("message", "problem", "cause", "fix"):
        assert isinstance(err.get(required), str) and err[required], (
            f"AgentError field {required!r} missing or empty in {err!r}"
        )
    assert "retryable" in err and isinstance(err["retryable"], bool)


# ---------------------------------------------------------------------------
# Acceptance #1 — JWT regression. Existing HS256 tokens still hit a real
# endpoint.
# ---------------------------------------------------------------------------


def test_jwt_path_existing_endpoint_still_returns_200(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    make_jwt_token: Any,
) -> None:
    token = make_jwt_token()
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == seeded_user["email"]


# ---------------------------------------------------------------------------
# Acceptance #2 — OAuth delegated token reaches the same endpoint when the
# email maps to a CF user row.
# ---------------------------------------------------------------------------


def test_oauth_delegated_token_existing_endpoint_returns_200(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    token = make_oauth_token(
        scopes=["character:read", "character:write"],
        client_id="claude-code",
        email=seeded_user["email"],
    )
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == seeded_user["email"]


# ---------------------------------------------------------------------------
# Acceptance #3 — require_scope decorator behaviour.
# ---------------------------------------------------------------------------


def test_require_scope_grants_when_scope_present_oauth(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    token = make_oauth_token(
        scopes=["character:write"],
        client_id="claude-code",
        email=seeded_user["email"],
    )
    resp = scoped_client.get(
        "/_test_t054/needs-write",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


def test_require_scope_403_when_scope_missing_oauth(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    token = make_oauth_token(
        scopes=["character:read"],  # write missing
        client_id="claude-code",
        email=seeded_user["email"],
    )
    resp = scoped_client.get(
        "/_test_t054/needs-write",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    _assert_agent_error(resp.json(), "AUTH_INSUFFICIENT_SCOPE")


def test_require_scope_401_when_token_absent(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
) -> None:
    resp = scoped_client.get("/_test_t054/needs-write")
    assert resp.status_code == 401, resp.text
    _assert_agent_error(resp.json(), "AUTH_MISSING_TOKEN")


def test_require_scope_grants_when_two_scopes_both_present(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    token = make_oauth_token(
        scopes=["character:write", "task:cancel"],
        client_id="claude-code",
        email=seeded_user["email"],
    )
    resp = scoped_client.get(
        "/_test_t054/needs-two-scopes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


def test_require_scope_403_when_only_one_of_two_scopes_present(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    token = make_oauth_token(
        scopes=["character:write"],  # task:cancel missing
        client_id="claude-code",
        email=seeded_user["email"],
    )
    resp = scoped_client.get(
        "/_test_t054/needs-two-scopes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    _assert_agent_error(resp.json(), "AUTH_INSUFFICIENT_SCOPE")


def test_require_scope_rejects_unknown_scope_at_construction() -> None:
    """Typo'd scope literals shouldn't silently lock out an endpoint at
    runtime — `require_scope` raises ValueError when called with a string
    that isn't in CANONICAL_SCOPES."""
    with pytest.raises(ValueError, match="non-canonical scope"):
        require_scope("character:write", "made:up")


# ---------------------------------------------------------------------------
# Acceptance #4 — Unknown client_id is rejected with the dedicated code.
# Tokens from a real Authentik instance whose `azp` claim is some new client
# the operator forgot to allowlist should not reach business logic.
# ---------------------------------------------------------------------------


def test_oauth_unknown_client_id_rejected(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    token = make_oauth_token(
        scopes=["character:read"],
        client_id="not-allowlisted-agent",
        email=seeded_user["email"],
    )
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    _assert_agent_error(resp.json(), "AUTH_CLIENT_NOT_ALLOWED")


# ---------------------------------------------------------------------------
# Acceptance #5 — M2M scope exceeds allowlist. We monkeypatch a narrow-cap
# entry into ALLOWED_CLIENTS for the duration of the test, then mint an M2M
# token whose scope claim widens beyond it. The verifier must reject before
# any user resolution.
# ---------------------------------------------------------------------------


def test_oauth_m2m_with_valid_scope_on_v1_returns_wrong_surface(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    """A valid M2M token (passes signature, allowlist, scope-cap) hitting
    `/v1/*` must report the dedicated wrong-surface code so on-call debugging
    sees a specific signal instead of the generic AUTH_INVALID_TOKEN whose
    fix message suggests `/v1/auth/login`."""
    token = make_oauth_token(
        scopes=sorted(ALLOWED_CLIENTS["cf-test-agent"]["scopes"] or []),
        client_id="cf-test-agent",
        email=None,
        sub="cf-test-agent",
    )
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    _assert_agent_error(resp.json(), "AUTH_M2M_WRONG_SURFACE")


def test_oauth_token_with_divergent_azp_and_client_id_rejected(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    """OIDC tokens MUST agree on azp == client_id when both are present.
    Divergence is a confusion signal — either a misconfigured provider
    mapping or an attempt to claim two identities — and the verifier
    rejects rather than picking one."""
    token = make_oauth_token(
        scopes=["character:read"],
        client_id="claude-code",
        email=seeded_user["email"],
        extra={"client_id": "cf-test-agent"},  # divergent from azp
    )
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401, resp.text
    _assert_agent_error(resp.json(), "AUTH_INVALID_TOKEN")


def test_oauth_m2m_scope_exceeds_allowlist_rejected(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capped: ClientPolicy = {"scopes": ["character:read"]}
    # Inject a narrow-cap M2M entry. monkeypatch.setitem restores the dict
    # on teardown. Acceptable because (a) this is the only test that needs
    # a narrow-capped client, (b) pytest currently runs serially. If
    # `pytest-xdist` is ever enabled this test must move to a proper DI
    # seam (e.g. `get_allowed_clients()` factory) since other workers may
    # read ALLOWED_CLIENTS concurrently.
    monkeypatch.setitem(ALLOWED_CLIENTS, "narrow-m2m-agent", capped)

    # M2M shape: no email, sub == client_id, scope widened beyond the cap.
    token = make_oauth_token(
        scopes=["character:read", "character:write"],
        client_id="narrow-m2m-agent",
        email=None,
        sub="narrow-m2m-agent",
    )
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    _assert_agent_error(resp.json(), "AUTH_SCOPE_EXCEEDS_ALLOWLIST")


# ---------------------------------------------------------------------------
# Acceptance #6 — JWKS cache: second OAuth request must not hit the network.
# Opt out of the preload fixture and let the real JWKSCache fetch — respx
# intercepts the call. Two consecutive requests should yield exactly one
# upstream HTTP roundtrip.
# ---------------------------------------------------------------------------


def test_jwks_cache_second_request_no_refetch(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _oauth_env: None,
    _jwks_document: dict[str, Any],
    make_oauth_token: Any,
) -> None:
    reset_jwks_cache_for_test()
    try:
        with respx.mock(assert_all_called=False) as router:
            jwks_route = router.get(_TEST_JWKS_URI).mock(
                return_value=httpx.Response(200, json=_jwks_document)
            )

            for _ in range(2):
                token = make_oauth_token(
                    scopes=["character:read"],
                    client_id="claude-code",
                    email=seeded_user["email"],
                )
                resp = scoped_client.get(
                    "/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 200, resp.text

            assert jwks_route.call_count == 1, (
                f"expected exactly 1 JWKS fetch across 2 requests, got {jwks_route.call_count}"
            )
    finally:
        reset_jwks_cache_for_test()


# ---------------------------------------------------------------------------
# Acceptance #7 — error body shape. Re-uses the helper above on a couple
# more 401/403 cases to triangulate that the envelope is consistent across
# every dual-stack failure mode.
# ---------------------------------------------------------------------------


def test_oauth_token_with_small_iat_clock_skew_is_accepted(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    _private_key_pem: bytes,
) -> None:
    """Codex round-6 P2: NTP-bounded clock drift between Authentik and the
    API worker (Authentik's clock slightly ahead → token `iat` in our
    future) must NOT 401. The decode `leeway` admits up to 30s of skew on
    both `iat` and `exp` boundaries.
    """
    now = int(time.time())
    skewed_iat = now + 5  # Authentik clock 5s ahead — well within leeway
    payload = {
        "iss": _TEST_ISSUER,
        "aud": _TEST_AUDIENCE,
        "iat": skewed_iat,
        "exp": skewed_iat + 3600,
        "azp": "claude-code",
        "sub": seeded_user["email"],
        "email": seeded_user["email"],
        "scope": "character:read",
    }
    token = pyjwt.encode(payload, _private_key_pem, algorithm="RS256", headers={"kid": _TEST_KID})
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


def test_oauth_token_missing_exp_claim_is_rejected(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    _private_key_pem: bytes,
) -> None:
    """Codex round-5 P1: a token without `exp` would otherwise be eternally
    valid because PyJWT only validates `exp` when it's present. The decode
    must require the claim and reject the token at the AUTH_INVALID_TOKEN
    layer.

    We bypass `make_oauth_token` (which always sets exp) and hand-craft the
    payload to omit it.
    """
    now = int(time.time())
    payload = {
        "iss": _TEST_ISSUER,
        "aud": _TEST_AUDIENCE,
        "iat": now,
        # `exp` deliberately omitted.
        "azp": "claude-code",
        "sub": seeded_user["email"],
        "email": seeded_user["email"],
        "scope": "character:read",
    }
    token = pyjwt.encode(payload, _private_key_pem, algorithm="RS256", headers={"kid": _TEST_KID})
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401, resp.text
    _assert_agent_error(resp.json(), "AUTH_INVALID_TOKEN")


def test_expired_oauth_token_returns_auth_expired_envelope(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    token = make_oauth_token(
        scopes=["character:read"],
        client_id="claude-code",
        email=seeded_user["email"],
        expires_in=-60,  # already expired
    )
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401, resp.text
    _assert_agent_error(resp.json(), "AUTH_EXPIRED")


def test_missing_authorization_header_returns_missing_token(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
) -> None:
    resp = scoped_client.get("/v1/auth/me")
    assert resp.status_code == 401
    _assert_agent_error(resp.json(), "AUTH_MISSING_TOKEN")


def test_malformed_bearer_header_returns_invalid_token(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
) -> None:
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": "NotBearer abcdef"},
    )
    assert resp.status_code == 401
    _assert_agent_error(resp.json(), "AUTH_INVALID_TOKEN")


# ---------------------------------------------------------------------------
# Bonus — unknown-iss audit. A token whose `iss` claim is neither our JWT
# nor our Authentik issuer goes through the JWT verifier and fails signature
# check (legacy fallthrough). The 401 surfaces as AUTH_INVALID_TOKEN.
# ---------------------------------------------------------------------------


def test_token_with_unknown_issuer_falls_through_to_invalid_token(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
) -> None:
    # Hand-craft a JWT with foreign iss and an HS256 signature using a
    # different secret. JWT-path verify will reject on signature; OAuth-path
    # routing only triggers on `iss == AUTHENTIK_ISSUER_URL`.
    bogus = pyjwt.encode(
        {"iss": "https://attacker.example/", "sub": "x", "exp": int(time.time()) + 600},
        "totally-different-secret",
        algorithm="HS256",
    )
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {bogus}"},
    )
    assert resp.status_code == 401, resp.text
    _assert_agent_error(resp.json(), "AUTH_INVALID_TOKEN")


# ---------------------------------------------------------------------------
# Internal sanity — make sure the env vars are wired such that the JWKS
# cache singleton can be constructed without monkeypatching. Catches
# accidental drift where someone removes the env var read in oauth.py.
# ---------------------------------------------------------------------------


def test_jwks_uri_scheme_validation_rejects_non_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`AUTHENTIK_JWKS_URL` must be http(s). A `file://` or `gopher://`
    misconfiguration would otherwise let httpx execute arbitrary fetcher
    behaviour — a cheap SSRF guard sitting between misconfig and httpx."""
    from app.auth.oauth import _validate_jwks_uri

    for hostile in ("file:///etc/passwd", "gopher://attacker/", "ftp://x/"):
        with pytest.raises(RuntimeError, match="scheme"):
            _validate_jwks_uri(hostile)

    # Sanity: real schemes must pass.
    _validate_jwks_uri("https://auth.example/jwks/")
    _validate_jwks_uri("http://authentik-server:9000/jwks/")


def test_test_seam_refuses_outside_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-braces: `set_jwks_cache_for_test` and friends must refuse to
    run when `PYTEST_CURRENT_TEST` isn't set — otherwise a misuse in a
    production import path could pin attacker-controlled signing keys for
    one TTL window."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(RuntimeError, match="outside a pytest run"):
        reset_jwks_cache_for_test()


def test_jwks_cache_refreshes_on_unknown_kid(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _oauth_env: None,
    _jwks_document: dict[str, Any],
    make_oauth_token: Any,
) -> None:
    """Codex P1: when a token presents a `kid` the fresh cache doesn't know
    (Authentik just rotated signing keys), the cache must refresh once before
    rejecting the token. Without this, valid new-kid tokens 401 for the full
    1h TTL after every key rotation.
    """
    reset_jwks_cache_for_test()
    try:
        # Seed the cache with the keys we know, then construct a token whose
        # kid is intentionally unknown — refresh-on-miss must fetch the same
        # JWKS doc and notice the kid is still missing (so we still 401), but
        # critically must have *attempted* the refresh.
        cache = JWKSCache(_TEST_JWKS_URI)
        cache.seed_keys_for_test(
            {key_data["kid"]: pyjwt.PyJWK(key_data) for key_data in _jwks_document["keys"]}
        )
        set_jwks_cache_for_test(cache)

        before = cache.fetch_count

        token = make_oauth_token(
            scopes=["character:read"],
            client_id="claude-code",
            email=seeded_user["email"],
            kid="rotated-key-not-in-cache",
        )

        with respx.mock(assert_all_called=False) as router:
            # Authentik post-rotation still doesn't have this kid in our test
            # double; return the old JWKS so refresh succeeds but `kid` lookup
            # still misses.
            router.get(_TEST_JWKS_URI).mock(return_value=httpx.Response(200, json=_jwks_document))

            resp = scoped_client.get(
                "/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401, resp.text
            _assert_agent_error(resp.json(), "AUTH_INVALID_TOKEN")

            after = cache.fetch_count
            assert after == before + 1, (
                f"Expected exactly one refresh-on-kid-miss; fetch_count went {before} → {after}"
            )
    finally:
        reset_jwks_cache_for_test()


def test_jwks_cache_kid_miss_refresh_is_token_bucket_rate_limited(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _oauth_env: None,
    _jwks_document: dict[str, Any],
    make_oauth_token: Any,
) -> None:
    """Codex round-3 P2: an attacker probing with random kids must not
    induce one JWKS fetch per request, but a single bad token must NOT block
    legit rotation pickup either. The cache uses a token bucket: up to N
    refreshes per window, then throttled.

    With N = `_JWKS_MISS_REFRESH_MAX_PER_WINDOW` (=3 today), `N + 2` distinct
    unknown-kid requests in quick succession should yield exactly N
    refreshes — bounded outbound traffic, still room for rotation pickup
    after a stray probe.
    """
    from app.auth.oauth import _JWKS_MISS_REFRESH_MAX_PER_WINDOW

    reset_jwks_cache_for_test()
    try:
        cache = JWKSCache(_TEST_JWKS_URI)
        cache.seed_keys_for_test(
            {key_data["kid"]: pyjwt.PyJWK(key_data) for key_data in _jwks_document["keys"]}
        )
        set_jwks_cache_for_test(cache)
        before = cache.fetch_count

        probes = [f"attacker-probe-{i}" for i in range(_JWKS_MISS_REFRESH_MAX_PER_WINDOW + 2)]
        with respx.mock(assert_all_called=False) as router:
            router.get(_TEST_JWKS_URI).mock(return_value=httpx.Response(200, json=_jwks_document))
            for kid in probes:
                token = make_oauth_token(
                    scopes=["character:read"],
                    client_id="claude-code",
                    email=seeded_user["email"],
                    kid=kid,
                )
                resp = scoped_client.get(
                    "/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 401, resp.text

        after = cache.fetch_count
        assert after - before == _JWKS_MISS_REFRESH_MAX_PER_WINDOW, (
            f"Expected token-bucket to cap refresh-on-miss at "
            f"{_JWKS_MISS_REFRESH_MAX_PER_WINDOW} within the window; "
            f"fetch_count delta was {after - before}"
        )
    finally:
        reset_jwks_cache_for_test()


def test_jwks_refresh_handles_broader_pyjwk_parse_errors(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _oauth_env: None,
    _jwks_document: dict[str, Any],
    make_oauth_token: Any,
) -> None:
    """Codex round-3 P1: PyJWK() can raise PyJWKError / PyJWKSetError —
    siblings of InvalidKeyError under the PyJWTError umbrella, NOT
    subclasses. The narrow `except InvalidKeyError` let them escape as
    500. Now we catch `PyJWTError`; a JWKS doc with one valid key plus a
    malformed entry must stay on the controlled path and the valid token
    must still verify.
    """
    reset_jwks_cache_for_test()
    try:
        bogus_jwks = {
            "keys": [
                *_jwks_document["keys"],
                {
                    # Missing the `n` / `e` RSA parameters — PyJWK raises
                    # somewhere in the PyJWTError hierarchy.
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": "malformed-entry",
                },
            ]
        }
        with respx.mock(assert_all_called=False) as router:
            router.get(_TEST_JWKS_URI).mock(return_value=httpx.Response(200, json=bogus_jwks))

            token = make_oauth_token(
                scopes=["character:read"],
                client_id="claude-code",
                email=seeded_user["email"],
            )
            resp = scoped_client.get(
                "/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            # The valid kid still resolves → 200. The point is we did NOT
            # bubble a 500 because PyJWK couldn't parse the second entry.
            assert resp.status_code == 200, resp.text
    finally:
        reset_jwks_cache_for_test()


def test_oauth_jwks_endpoint_unreachable_returns_provider_unavailable(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _oauth_env: None,
    make_oauth_token: Any,
) -> None:
    """Codex P2: when the JWKS endpoint is unreachable (transport error /
    non-JSON / 5xx), surface AUTH_OAUTH_PROVIDER_UNAVAILABLE (503, retryable)
    instead of letting the httpx error bubble as a 500. A brief Authentik
    outage must not take auth-protected endpoints down.
    """
    reset_jwks_cache_for_test()
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get(_TEST_JWKS_URI).mock(
                return_value=httpx.Response(500, text="Internal Server Error")
            )
            token = make_oauth_token(
                scopes=["character:read"],
                client_id="claude-code",
                email=seeded_user["email"],
            )
            resp = scoped_client.get(
                "/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 503, resp.text
            body = resp.json()
            _assert_agent_error(body, "AUTH_OAUTH_PROVIDER_UNAVAILABLE")
            assert body["error"]["retryable"] is True
    finally:
        reset_jwks_cache_for_test()


def test_oauth_multi_issuer_csv_env_accepts_each_app(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    _jwks_document: dict[str, Any],
    make_oauth_token: Any,
) -> None:
    """Codex round-4 P1: AUTHENTIK_ISSUER_URL / AUTHENTIK_AUDIENCE must
    accept comma-separated lists so tokens from any registered Authentik
    application verify. Configure 3 issuers / 3 audiences; mint a token
    issued by the *second* entry and verify it passes."""
    issuer_a = "https://auth.test.example/application/o/spa/"
    issuer_b = "https://auth.test.example/application/o/claude-code/"
    issuer_c = "https://auth.test.example/application/o/cursor/"

    reset_jwks_cache_for_test()
    try:
        monkeypatch.setenv("AUTHENTIK_ISSUER_URL", ",".join([issuer_a, issuer_b, issuer_c]))
        monkeypatch.setenv(
            "AUTHENTIK_AUDIENCE",
            ",".join(["character-foundry-spa", "claude-code", "cursor"]),
        )
        monkeypatch.setenv("AUTHENTIK_JWKS_URL", _TEST_JWKS_URI)

        cache = JWKSCache(_TEST_JWKS_URI)
        cache.seed_keys_for_test(
            {key_data["kid"]: pyjwt.PyJWK(key_data) for key_data in _jwks_document["keys"]}
        )
        set_jwks_cache_for_test(cache)

        # Token from the second registered application — must verify.
        token = make_oauth_token(
            scopes=["character:read"],
            client_id="claude-code",
            email=seeded_user["email"],
            issuer=issuer_b,
            audience="claude-code",
        )
        resp = scoped_client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text

        # And one with an issuer NOT in the list must still 401.
        rogue = make_oauth_token(
            scopes=["character:read"],
            client_id="claude-code",
            email=seeded_user["email"],
            issuer="https://attacker.example/o/fake/",
            audience="claude-code",
        )
        # Rogue iss won't match `is_authentik_token` → falls through to
        # legacy JWT path → HS256 verify fails → AUTH_INVALID_TOKEN.
        resp = scoped_client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {rogue}"},
        )
        assert resp.status_code == 401, resp.text
    finally:
        reset_jwks_cache_for_test()


def test_oauth_spa_client_is_accepted_on_v1(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _preload_jwks_cache: JWKSCache,
    make_oauth_token: Any,
) -> None:
    """Codex round-4 P1: `character-foundry-spa` must be allowlisted so
    T-056's SPA OAuth login doesn't 403 with AUTH_CLIENT_NOT_ALLOWED. The
    original T-053 design scoped the allowlist to /mcp/* only; T-054
    promoted it to universal client recognition once /v1/* started running
    the OAuth path through the same verifier."""
    token = make_oauth_token(
        scopes=["character:read"],
        client_id="character-foundry-spa",
        email=seeded_user["email"],
    )
    resp = scoped_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == seeded_user["email"]


def test_oauth_jwks_payload_wrong_shape_returns_provider_unavailable(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    _oauth_env: None,
    make_oauth_token: Any,
) -> None:
    """Codex round-3 P2: a JWKS endpoint that returns valid JSON with the
    wrong top-level shape (array, null, scalar) must surface as
    AUTH_OAUTH_PROVIDER_UNAVAILABLE, not bubble as an AttributeError 500.
    Misconfigured reverse-proxies sometimes return `[]` or wrapping objects;
    the verifier shouldn't take auth down because of it.
    """
    reset_jwks_cache_for_test()
    try:
        with respx.mock(assert_all_called=False) as router:
            # Top-level array — typical "we accidentally returned a list of
            # other things" misconfiguration.
            router.get(_TEST_JWKS_URI).mock(return_value=httpx.Response(200, json=[]))
            token = make_oauth_token(
                scopes=["character:read"],
                client_id="claude-code",
                email=seeded_user["email"],
            )
            resp = scoped_client.get(
                "/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 503, resp.text
            _assert_agent_error(resp.json(), "AUTH_OAUTH_PROVIDER_UNAVAILABLE")
    finally:
        reset_jwks_cache_for_test()


def test_oauth_misconfigured_jwks_uri_returns_provider_unavailable(
    scoped_client: TestClient,
    seeded_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    make_oauth_token: Any,
    _private_key_pem: bytes,
) -> None:
    """Codex P2: deploy-time misconfig (e.g. `AUTHENTIK_JWKS_URL` with a
    disallowed scheme) must surface as a controlled provider-unavailable
    error, not propagate the RuntimeError from `_validate_jwks_uri` as 500.
    """
    reset_jwks_cache_for_test()
    try:
        monkeypatch.setenv("AUTHENTIK_ISSUER_URL", _TEST_ISSUER)
        monkeypatch.setenv("AUTHENTIK_AUDIENCE", _TEST_AUDIENCE)
        monkeypatch.setenv("AUTHENTIK_JWKS_URL", "file:///etc/passwd")  # rejected by scheme check

        token = make_oauth_token(
            scopes=["character:read"],
            client_id="claude-code",
            email=seeded_user["email"],
        )
        resp = scoped_client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503, resp.text
        _assert_agent_error(resp.json(), "AUTH_OAUTH_PROVIDER_UNAVAILABLE")
    finally:
        reset_jwks_cache_for_test()


def test_get_jwks_cache_reads_env_var(_oauth_env: None) -> None:
    from app.auth.oauth import get_jwks_cache

    reset_jwks_cache_for_test()
    try:
        cache = get_jwks_cache()
        assert isinstance(cache, JWKSCache)
        assert cache.jwks_uri == _TEST_JWKS_URI
    finally:
        reset_jwks_cache_for_test()
