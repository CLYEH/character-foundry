"""Authentik OAuth access-token verification (T-054 dual-stack second path).

Bearer tokens whose `iss` claim matches `AUTHENTIK_ISSUER_URL` go through
here; the legacy HS256 internal-JWT path lives in `app.auth.jwt` and is
selected by `app.api.deps.get_current_user`.

Verification is stateless — Authentik signs RS256 JWTs and we fetch the
public keys from its JWKS endpoint, cache them in-process for one hour, and
verify locally. Going through Authentik's `/introspect/` endpoint instead
would add a network round-trip per request with no security benefit (the
token already carries the same claims) — see ticket Notes for the trade-off.

After signature / audience / issuer checks pass, we additionally enforce:

  • `client_id` (token's `azp` or `client_id` claim) is in
    `app.auth.mcp_clients.ALLOWED_CLIENTS`. Unknown → 403.
  • For M2M clients whose allowlist entry caps scopes, the token's `scope`
    claim is a subset of that cap. Exceeded → 403 (protects against an
    Authentik misconfiguration that widens issuance).

The JWKS cache is single-instance per process. Tests swap it via
`set_jwks_cache_for_test()` to a pre-loaded `JWKSCache` instance and assert
on `cache.fetch_count` to exercise the "second request doesn't refetch"
acceptance.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx
import jwt
from jwt import PyJWK

from app.auth.mcp_clients import get_allowed_scopes, is_allowed_client
from app.core.errors import (
    auth_client_not_allowed,
    auth_expired,
    auth_invalid_token,
    auth_scope_exceeds_allowlist,
)

_JWKS_TTL_SECONDS: Final[int] = 3600  # 1h, per ticket §"JWKS cache TTL" rationale
_JWKS_FETCH_TIMEOUT_SECONDS: Final[float] = 5.0
_ISSUER_ENV: Final[str] = "AUTHENTIK_ISSUER_URL"
_AUDIENCE_ENV: Final[str] = "AUTHENTIK_AUDIENCE"
_JWKS_URI_ENV: Final[str] = "AUTHENTIK_JWKS_URL"


# ---------------------------------------------------------------------------
# Public claims object — what `verify_oauth_token` returns. Kept distinct from
# the raw JWT payload dict so callers can't accidentally index by claim name
# and miss the post-verify validations that happened here (allowlist, scope
# cap, normalised client_id).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthClaims:
    sub: str
    client_id: str
    scopes: frozenset[str]
    email: str | None
    # `is_m2m` is a best-effort signal — Authentik marks client_credentials
    # tokens with `sub == client_id` and no email claim. Downstream callers
    # (e.g. `get_current_user`) use it to decide whether to resolve a User
    # row or treat the request as headless.
    is_m2m: bool


# ---------------------------------------------------------------------------
# JWKS cache. Single-instance per process. The asyncio lock prevents two
# concurrent cold-start requests from both firing an HTTP fetch — one waits
# and reads the populated cache. fetch_count is the test-only knob that
# proves the "second request hits cache" acceptance criterion.
# ---------------------------------------------------------------------------


@dataclass
class _JWKSEntry:
    keys: dict[str, PyJWK]
    expires_at: float


class JWKSCache:
    def __init__(self, jwks_uri: str, ttl_seconds: int = _JWKS_TTL_SECONDS):
        self._jwks_uri = jwks_uri
        self._ttl = ttl_seconds
        self._entry: _JWKSEntry | None = None
        self._lock = asyncio.Lock()
        self._fetch_count = 0

    @property
    def fetch_count(self) -> int:
        return self._fetch_count

    @property
    def jwks_uri(self) -> str:
        return self._jwks_uri

    async def get_key(self, kid: str) -> PyJWK | None:
        # Cheap pre-check outside the lock so warm-cache reads don't serialize.
        if self._is_fresh():
            assert self._entry is not None
            return self._entry.keys.get(kid)
        async with self._lock:
            if not self._is_fresh():
                await self._refresh()
            assert self._entry is not None
            return self._entry.keys.get(kid)

    def _is_fresh(self) -> bool:
        return self._entry is not None and time.time() < self._entry.expires_at

    def seed_keys_for_test(
        self,
        keys: dict[str, PyJWK],
        ttl_seconds: int = _JWKS_TTL_SECONDS,
    ) -> None:
        """Test-only seam: install a fully-populated entry without going
        through HTTP. Resets `fetch_count` to zero so subsequent assertions
        on cache-miss behaviour start from a known baseline. Production
        callers MUST use `get_key` instead.
        """
        self._entry = _JWKSEntry(keys=dict(keys), expires_at=time.time() + ttl_seconds)
        self._fetch_count = 0

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=_JWKS_FETCH_TIMEOUT_SECONDS) as client:
            resp = await client.get(self._jwks_uri)
            resp.raise_for_status()
            data = resp.json()
        keys: dict[str, PyJWK] = {}
        for key_data in data.get("keys", []):
            kid = key_data.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = PyJWK(key_data)
            except jwt.InvalidKeyError:
                # Skip malformed entries rather than failing the whole cache —
                # Authentik occasionally publishes keys with algorithms we
                # don't recognise and we shouldn't lock out the rest.
                continue
        self._fetch_count += 1
        self._entry = _JWKSEntry(keys=keys, expires_at=time.time() + self._ttl)


# ---------------------------------------------------------------------------
# Process-wide singleton + test seams. Tests should call
# `set_jwks_cache_for_test(...)` in a fixture (and reset with
# `reset_jwks_cache_for_test()` on teardown) rather than mutating the
# module variable directly.
# ---------------------------------------------------------------------------


_default_cache: JWKSCache | None = None


def get_jwks_cache() -> JWKSCache:
    global _default_cache
    if _default_cache is None:
        jwks_uri = os.environ.get(_JWKS_URI_ENV)
        if not jwks_uri:
            issuer = os.environ.get(_ISSUER_ENV)
            if not issuer:
                raise RuntimeError(
                    f"{_JWKS_URI_ENV} or {_ISSUER_ENV} must be set to verify "
                    "Authentik-issued OAuth tokens"
                )
            # Authentik convention: `<issuer>jwks/`. Operators can override
            # via `AUTHENTIK_JWKS_URL` when the deployment topology differs
            # (e.g. nginx sub-path rewrite).
            jwks_uri = issuer.rstrip("/") + "/jwks/"
        _default_cache = JWKSCache(jwks_uri)
    return _default_cache


def set_jwks_cache_for_test(cache: JWKSCache) -> None:
    global _default_cache
    _default_cache = cache


def reset_jwks_cache_for_test() -> None:
    global _default_cache
    _default_cache = None


# ---------------------------------------------------------------------------
# Verifier entry-point. Raises `AgentErrorException` (caught by the global
# handler in `app.main` → AgentError JSON). Returning `OAuthClaims` means
# every successful path goes through the same struct, so `get_current_user`
# doesn't have to re-parse the raw payload.
# ---------------------------------------------------------------------------


async def verify_oauth_token(token: str, *, cache: JWKSCache | None = None) -> OAuthClaims:
    """Verify an Authentik-issued access token end-to-end.

    Order of checks (each layer rejects with a distinct AgentError code so
    audit logs can distinguish symptoms):

      1. JWT header parseable + carries `kid` → else `AUTH_INVALID_TOKEN`.
      2. JWKS lookup yields a key for that `kid` → else `AUTH_INVALID_TOKEN`.
      3. RS256 signature + `aud` + `iss` + `exp` all pass → else either
         `AUTH_EXPIRED` (exp in past) or `AUTH_INVALID_TOKEN` (anything else).
      4. `client_id` claim present and in allowlist → else
         `AUTH_CLIENT_NOT_ALLOWED`.
      5. For capped clients, scope claim ⊆ cap → else
         `AUTH_SCOPE_EXCEEDS_ALLOWLIST`.
    """
    issuer = os.environ.get(_ISSUER_ENV)
    audience = os.environ.get(_AUDIENCE_ENV)
    if not issuer or not audience:
        # Misconfiguration — surface as invalid_token so callers don't get
        # a misleading "expired" or "client not allowed". Operators see
        # the 401 and check env in tandem with the audit log.
        raise auth_invalid_token()

    jwks_cache = cache if cache is not None else get_jwks_cache()

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise auth_invalid_token() from exc
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise auth_invalid_token()

    signing_key = await jwks_cache.get_key(kid)
    if signing_key is None:
        raise auth_invalid_token()

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
        )
    except jwt.ExpiredSignatureError as exc:
        raise auth_expired() from exc
    except jwt.InvalidTokenError as exc:
        raise auth_invalid_token() from exc

    # `azp` (authorized party) is the OIDC-standard claim for the originating
    # client; Authentik emits both `azp` and `client_id` for client_credentials
    # tokens and `azp` only for auth-code tokens. Prefer `azp` so we treat
    # both grant types uniformly.
    raw_client_id = payload.get("azp") or payload.get("client_id")
    if not isinstance(raw_client_id, str) or not raw_client_id:
        raise auth_invalid_token()
    client_id: str = raw_client_id

    if not is_allowed_client(client_id):
        raise auth_client_not_allowed()

    scope_claim = payload.get("scope", "")
    if not isinstance(scope_claim, str):
        raise auth_invalid_token()
    token_scopes = frozenset(scope_claim.split()) if scope_claim else frozenset()

    cap = get_allowed_scopes(client_id)
    if cap is not None and not token_scopes <= cap:
        raise auth_scope_exceeds_allowlist()

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise auth_invalid_token()

    email = payload.get("email")
    if email is not None and not isinstance(email, str):
        email = None

    is_m2m = email is None and sub == client_id

    return OAuthClaims(
        sub=sub,
        client_id=client_id,
        scopes=token_scopes,
        email=email,
        is_m2m=is_m2m,
    )


# ---------------------------------------------------------------------------
# Re-export for the dispatch layer in `app.api.deps`. Importing `is_authentik_token`
# there keeps the issuer-prefix decision in one place (this file) rather than
# letting `deps.py` peek at env vars itself.
# ---------------------------------------------------------------------------


def is_authentik_token(unverified_payload: dict[str, Any]) -> bool:
    """Cheap issuer-claim check used to route a bearer token to the right
    verifier. Reads `iss` from the *unverified* payload — the verify step
    re-checks it cryptographically, so this is a routing hint only, not a
    trust decision."""
    iss = unverified_payload.get("iss")
    expected = os.environ.get(_ISSUER_ENV)
    return bool(expected) and iss == expected
