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
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlparse

import httpx
import jwt
from jwt import PyJWK

from app.auth.mcp_clients import get_allowed_scopes, is_allowed_client
from app.core.errors import (
    auth_client_not_allowed,
    auth_expired,
    auth_invalid_token,
    auth_oauth_provider_unavailable,
    auth_scope_exceeds_allowlist,
)

_logger = logging.getLogger(__name__)

_ALLOWED_JWKS_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

_JWKS_TTL_SECONDS: Final[int] = 3600  # 1h, per ticket §"JWKS cache TTL" rationale
_JWKS_FETCH_TIMEOUT_SECONDS: Final[float] = 5.0
# Refresh-on-unknown-kid is gated by a small token bucket (Codex P1 → P2):
# allow up to `_JWKS_MISS_REFRESH_MAX_PER_WINDOW` JWKS fetches within
# `_JWKS_MISS_REFRESH_WINDOW_SECONDS`. A single bad-kid request used to
# suppress refresh for the entire window — Codex round-3 flagged that a
# subsequent legitimate Authentik rotation would then be invisible until
# the gate cleared. Token bucket fixes this without re-opening the spam
# vector: Authentik rotation only needs one refresh; an attacker spraying
# random kids consumes the budget but stops at 3 outbound fetches/min.
_JWKS_MISS_REFRESH_WINDOW_SECONDS: Final[float] = 60.0
_JWKS_MISS_REFRESH_MAX_PER_WINDOW: Final[int] = 3
_ISSUER_ENV: Final[str] = "AUTHENTIK_ISSUER_URL"
_AUDIENCE_ENV: Final[str] = "AUTHENTIK_AUDIENCE"
_JWKS_URI_ENV: Final[str] = "AUTHENTIK_JWKS_URL"


def _parse_csv_env(name: str) -> list[str]:
    """Return env var split on commas with whitespace trimmed.

    Both `AUTHENTIK_ISSUER_URL` and `AUTHENTIK_AUDIENCE` accept comma-separated
    lists because Authentik issues per-application providers — five apps means
    five distinct `iss` URLs and five `aud` client_ids (T-053 §5.4). Operators
    listing one value get back a one-element list; this stays compatible with
    the single-app deployment but unblocks multi-app use without restructuring
    the env contract. Empty / unset env returns the empty list — the caller
    decides whether that's a misconfiguration to reject.
    """
    raw = os.environ.get(name, "")
    return [v for v in (chunk.strip() for chunk in raw.split(",")) if v]


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
        # Timestamps of recent refresh-on-unknown-kid attempts; pruned to
        # the trailing `_JWKS_MISS_REFRESH_WINDOW_SECONDS`. Bounded budget
        # via `_JWKS_MISS_REFRESH_MAX_PER_WINDOW`. See top-of-module
        # comment for the rotation-vs-spam trade-off.
        self._miss_refresh_timestamps: list[float] = []

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
            key = self._entry.keys.get(kid)
            if key is not None:
                return key
            # Fresh cache but kid not present — likely Authentik just rotated
            # signing keys. Try a budgeted refresh (token bucket: up to
            # `MAX_PER_WINDOW` refreshes per `WINDOW_SECONDS`) so legitimate
            # rotation pickup isn't blocked by a single attacker probe.
            async with self._lock:
                # Re-check under the lock — another coroutine may have just
                # refreshed and populated the kid we want.
                assert self._entry is not None
                key = self._entry.keys.get(kid)
                if key is not None:
                    return key
                if not self._consume_miss_refresh_budget():
                    return None
                await self._refresh()
            assert self._entry is not None
            return self._entry.keys.get(kid)
        # Stale (TTL elapsed) or cold cache — full refresh under the lock.
        async with self._lock:
            if not self._is_fresh():
                await self._refresh()
            assert self._entry is not None
            return self._entry.keys.get(kid)

    def _consume_miss_refresh_budget(self) -> bool:
        """Try to take one slot from the kid-miss refresh budget.

        Prunes timestamps older than the window, then admits the caller if
        the remaining count is below the per-window ceiling. Must be called
        with `self._lock` held — the timestamp list is shared state.
        """
        now = time.time()
        self._miss_refresh_timestamps = [
            t for t in self._miss_refresh_timestamps if now - t < _JWKS_MISS_REFRESH_WINDOW_SECONDS
        ]
        if len(self._miss_refresh_timestamps) >= _JWKS_MISS_REFRESH_MAX_PER_WINDOW:
            return False
        self._miss_refresh_timestamps.append(now)
        return True

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
        callers MUST use `get_key` instead — the runtime guard makes that
        explicit.
        """
        _assert_test_seam_allowed("JWKSCache.seed_keys_for_test")
        self._entry = _JWKSEntry(keys=dict(keys), expires_at=time.time() + ttl_seconds)
        self._fetch_count = 0

    async def _refresh(self) -> None:
        # `follow_redirects=False` is explicit (it's also the httpx default)
        # so a future copy-paste can't flip it and let a hostile JWKS server
        # 302 us to its own JWKS document. Scheme validation happens in
        # `_validate_jwks_uri` at construction / first-fetch time.
        async with httpx.AsyncClient(
            timeout=_JWKS_FETCH_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            resp = await client.get(self._jwks_uri)
            resp.raise_for_status()
            data = resp.json()
        # Validate top-level shape before reaching into it. A misconfigured
        # proxy or wrong endpoint can deliver valid JSON whose top is a list,
        # null, or string — calling `.get(...)` on those raises AttributeError
        # which would bypass `verify_oauth_token`'s (httpx.HTTPError, ValueError)
        # filter and 500. Raise ValueError instead so the caller maps it to a
        # controlled AUTH_OAUTH_PROVIDER_UNAVAILABLE.
        if not isinstance(data, dict):
            raise ValueError(f"JWKS endpoint returned non-object JSON ({type(data).__name__})")
        raw_keys = data.get("keys", [])
        if not isinstance(raw_keys, list):
            raise ValueError(f"JWKS endpoint returned non-array 'keys' ({type(raw_keys).__name__})")
        keys: dict[str, PyJWK] = {}
        for key_data in raw_keys:
            if not isinstance(key_data, dict):
                # Individual entries must be objects too; skip non-dict entries
                # instead of failing the whole refresh, mirroring the malformed-
                # key behaviour below.
                _logger.warning(
                    "jwks_skipped_non_object_key_entry",
                    extra={"type": type(key_data).__name__},
                )
                continue
            kid = key_data.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = PyJWK(key_data)
            except jwt.PyJWTError as exc:
                # Skip malformed entries rather than failing the whole cache —
                # Authentik occasionally publishes keys with algorithms we
                # don't recognise and we shouldn't lock out the rest. WARN
                # so operators see the signal when a rotated key has a
                # format pyjwt can't parse and every fresh-signed token
                # then 401s with no obvious cause. Catching the broad
                # `PyJWTError` (not just `InvalidKeyError`) covers
                # `PyJWKError`, `PyJWKSetError`, etc., which are siblings
                # not subclasses — Codex round-3 P1.
                _logger.warning(
                    "jwks_skipped_invalid_key",
                    extra={"kid": kid, "reason": str(exc)},
                )
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


def _validate_jwks_uri(uri: str) -> None:
    """Reject obviously-hostile JWKS URIs at config time. Only `http` and
    `https` schemes are permitted — without this guard a misconfigured
    `AUTHENTIK_JWKS_URL=file:///etc/passwd` or `gopher://...` would still
    be passed to httpx, where the SSRF blast radius depends on whatever
    fetchers httpx links against. Host-level allowlisting (block loopback /
    RFC1918 in prod) is the harden-compose follow-up (T-067 scope).
    """
    parsed = urlparse(uri)
    if parsed.scheme not in _ALLOWED_JWKS_SCHEMES:
        raise RuntimeError(
            f"{_JWKS_URI_ENV} scheme {parsed.scheme!r} is not in the allowlist "
            f"{sorted(_ALLOWED_JWKS_SCHEMES)}; refusing to fetch."
        )


def get_jwks_cache() -> JWKSCache:
    global _default_cache
    if _default_cache is None:
        jwks_uri = os.environ.get(_JWKS_URI_ENV)
        if not jwks_uri:
            issuers = _parse_csv_env(_ISSUER_ENV)
            if not issuers:
                raise RuntimeError(
                    f"{_JWKS_URI_ENV} or {_ISSUER_ENV} must be set to verify "
                    "Authentik-issued OAuth tokens"
                )
            # Authentik convention: `<issuer>jwks/`. All applications in
            # one Authentik instance share the same JWKS endpoint, so any
            # listed issuer's host works for derivation. Pick the first to
            # keep behaviour deterministic when multiple issuers are set.
            # Operators can override via `AUTHENTIK_JWKS_URL` when the
            # deployment topology differs (e.g. nginx sub-path rewrite).
            jwks_uri = issuers[0].rstrip("/") + "/jwks/"
        _validate_jwks_uri(jwks_uri)
        _default_cache = JWKSCache(jwks_uri)
    return _default_cache


def _assert_test_seam_allowed(name: str) -> None:
    """Refuse `*_for_test` calls outside a pytest run. The seams have to be
    public so test fixtures can swap the cache, but a non-test caller
    swapping it would let an attacker pin attacker-controlled signing keys
    for a TTL window. The pytest contextvar is set the whole time tests
    are executing.
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError(
            f"{name} called outside a pytest run. This is a test-only seam "
            "and must never run in production — see app/auth/oauth.py docstring."
        )


def set_jwks_cache_for_test(cache: JWKSCache) -> None:
    _assert_test_seam_allowed("set_jwks_cache_for_test")
    global _default_cache
    _default_cache = cache


def reset_jwks_cache_for_test() -> None:
    _assert_test_seam_allowed("reset_jwks_cache_for_test")
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
    issuers = _parse_csv_env(_ISSUER_ENV)
    audiences = _parse_csv_env(_AUDIENCE_ENV)
    if not issuers or not audiences:
        # Misconfiguration. Log loud so operators see why every OAuth
        # request 401s; surface as invalid_token to clients so we don't
        # leak which env var is missing.
        _logger.error(
            "oauth_misconfigured",
            extra={
                "missing_issuer": not issuers,
                "missing_audience": not audiences,
            },
        )
        raise auth_invalid_token()

    # Cache init can raise RuntimeError when AUTHENTIK_JWKS_URL has a
    # disallowed scheme — that's a deploy-time misconfig, not a token
    # problem, so map it to provider-unavailable (retryable for ops to
    # fix) rather than letting it bubble as a 500.
    try:
        jwks_cache = cache if cache is not None else get_jwks_cache()
    except RuntimeError as exc:
        _logger.error("oauth_jwks_cache_init_failed", extra={"error": str(exc)})
        raise auth_oauth_provider_unavailable() from exc

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise auth_invalid_token() from exc
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise auth_invalid_token()

    # JWKS fetch can fail mid-flight (Authentik down, returning HTML behind
    # an error page, etc.) — catch httpx transport errors + JSON decode
    # errors and surface as provider-unavailable. Without this guard the
    # request 500s and every authenticated endpoint goes down with it.
    try:
        signing_key = await jwks_cache.get_key(kid)
    except (httpx.HTTPError, ValueError) as exc:
        _logger.error("oauth_jwks_fetch_failed", extra={"error": str(exc)})
        raise auth_oauth_provider_unavailable() from exc
    if signing_key is None:
        raise auth_invalid_token()

    try:
        # `algorithms=["RS256"]` is load-bearing for cross-stack security: it
        # blocks the classic RS256↔HS256 algorithm-confusion attack (a token
        # signed with the public key as an HMAC secret would otherwise verify
        # if the algorithm pin were missing or widened). Do NOT loosen this
        # without revisiting the dual-stack threat model.
        # PyJWT accepts both `audience` and `issuer` as a single value OR a
        # list. Lists let one process verify tokens from any of the registered
        # Authentik applications (multi-app support per T-053 §5.4) — at least
        # one entry in `aud` must match an entry in `audiences`, and `iss`
        # must match one entry in `issuers`.
        #
        # `options.require` is critical defense-in-depth: PyJWT only validates
        # `exp` / `iss` / `aud` *if those claims are present*, so a token
        # missing `exp` would be eternally valid. Requiring the 5 claims we
        # actually use ensures every token carries an expiration, an issuer
        # we accepted, an audience we accepted, and a subject we can map to
        # a User row / client. Codex round-5 P1.
        # `leeway` of 30s covers both `exp` slack (a token that just expired
        # by a second on a clock-drifting API node still passes) and `iat`
        # slack (a token minted a second ahead by Authentik isn't rejected
        # as "from the future"). NTP-synced containers normally drift well
        # under this; the trade-off is accepting tokens for at most 30s past
        # their declared expiry, which is acceptable for Phase 1's 1h TTL.
        # Codex round-6 P2 raised this once `iat` joined the require list.
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audiences,
            issuer=issuers,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise auth_expired() from exc
    except jwt.InvalidTokenError as exc:
        raise auth_invalid_token() from exc

    # `azp` (authorized party) is the OIDC-standard claim for the originating
    # client; Authentik emits both `azp` and `client_id` for client_credentials
    # tokens and `azp` only for auth-code tokens. Prefer `azp` so we treat
    # both grant types uniformly. If both are present they MUST agree — a
    # token splitting identity across the two claims is either a provider
    # misconfiguration or an attempt to confuse the allowlist check, so
    # reject rather than picking one.
    azp_claim = payload.get("azp")
    client_id_claim = payload.get("client_id")
    if (
        isinstance(azp_claim, str)
        and isinstance(client_id_claim, str)
        and azp_claim != client_id_claim
    ):
        raise auth_invalid_token()
    raw_client_id = azp_claim or client_id_claim
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

    # M2M vs delegated is a property of the allowlist entry, not the token —
    # `get_allowed_scopes` returns `None` for delegated clients (consent-time
    # scope) and a frozenset for capped M2M clients. Reading allowlist policy
    # is strictly more reliable than inferring from `email is None and
    # sub == client_id`, which Authentik can break with a custom email
    # mapping on a service user.
    is_m2m = cap is not None

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
    trust decision. Matches against the comma-separated set in
    `AUTHENTIK_ISSUER_URL` so any registered Authentik application's tokens
    route to `_resolve_oauth`."""
    iss = unverified_payload.get("iss")
    if not isinstance(iss, str):
        return False
    accepted = _parse_csv_env(_ISSUER_ENV)
    return bool(accepted) and iss in accepted
