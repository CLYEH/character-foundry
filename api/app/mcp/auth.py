"""MCP request auth — dual-stack token resolution shared with /v1/*.

Per `tickets/T-080-mcp-server-skeleton.md`, MCP server requests follow the
same dual-stack model as REST endpoints (T-054): legacy HS256 JWT and
Authentik OAuth tokens both accepted, dispatched via `iss` claim. The
client_id allowlist is enforced on the OAuth path only — legacy JWTs have
no `client_id` concept, so applying the allowlist to them would lock out
existing SPA sessions during the M3.5 migration window.

Auth state is stashed in a `ContextVar` (`mcp_auth_state_var`) rather than
the ASGI request scope so MCP tool handlers — invoked from inside the
JSON-RPC dispatch loop, several call frames removed from any ASGI request
— can read it via `current_mcp_auth_context()`. Tools then call
`require_mcp_scopes(...)` at entry to enforce the per-tool scope contract
declared in their `MCPTool` registry entry (registry itself lands in T-081).

The contextvar carries a discriminated union of three states:

  • `None`         — no Authorization header sent. `require_mcp_scopes`
                     raises AUTH_MISSING_TOKEN.
  • `MCPAuthFailure(error=...)` — header present but verifier rejected
                     it. `require_mcp_scopes` re-raises the verifier's
                     `AgentErrorException`, preserving the original
                     code (`AUTH_CLIENT_NOT_ALLOWED`,
                     `AUTH_SCOPE_EXCEEDS_ALLOWLIST`,
                     `AUTH_OAUTH_EXPIRED`, ...). Codex round-1 P2 flagged
                     the earlier "collapse all failures to None"
                     implementation as losing actionable error semantics
                     for clients and operators; this discriminated union
                     restores parity with the `/v1/*` dual-stack contract
                     where every verifier code is distinguishable.
  • `MCPAuthContext` — token verified. `require_mcp_scopes` checks
                       per-tool scope and returns the context on pass.

Per the ticket Note on "MCP error vs HTTP status": auth failures do NOT
return HTTP 401/403. The streamable HTTP response stays 200 and the
JSON-RPC envelope carries the error so MCP clients see a structured
`CallToolResult` with `isError=True` — same surface as any other tool
failure. To achieve that, the middleware NEVER blocks unauthenticated
requests at the ASGI layer; it just installs the resolved state on the
contextvar and lets `require_mcp_scopes` raise a `ToolError` inside the
tool handler.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import jwt as pyjwt
from mcp.server.fastmcp.exceptions import ToolError
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.jwt import JWTExpired, JWTInvalid, verify_access_token
from app.auth.mcp_clients import is_m2m_service_account_client
from app.auth.oauth import is_authentik_token, verify_oauth_token
from app.auth.scopes import CANONICAL_SCOPES
from app.auth.user_resolution import resolve_m2m_service_user_id, resolve_oauth_user_id
from app.core.errors import (
    AgentErrorException,
    auth_expired,
    auth_insufficient_scope,
    auth_invalid_token,
    auth_missing_token,
    auth_user_context_required,
)
from app.db.session import async_session_factory
from app.models.user import User

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPAuthContext:
    """Resolved auth state for one MCP request.

    `user_id` is `None` for M2M client_credentials tokens by default — they
    have no human behind them and the MCP surface tolerates that by design
    (per agent-interface Q5 Round 2 — headless agents are first-class on
    /mcp/*, they just can't reach /v1/* per T-054 `auth_m2m_wrong_surface`).
    EXCEPTION (T-092): an M2M client in `M2M_SERVICE_ACCOUNT_CLIENTS` resolves
    to a provisioned backend service-account `User.id`, so the headless agent
    can run user-scoped tools and own what it creates — `is_m2m` stays True
    (still rejected on /v1/*), but `user_id` is populated.
    Legacy JWT-path requests always have a `user_id`, no `client_id`, and
    `is_m2m=False` — mirroring `_resolve_jwt` in `app/api/deps.py`.
    """

    user_id: uuid.UUID | None
    client_id: str | None
    scopes: frozenset[str]
    is_m2m: bool


@dataclass(frozen=True)
class MCPAuthFailure:
    """Token verification failed with a specific verifier-level error.

    Wraps the `AgentErrorException` the verifier raised so the tool layer
    can re-raise it verbatim and preserve the original code — `AUTH_
    CLIENT_NOT_ALLOWED` and `AUTH_OAUTH_EXPIRED` should look different
    from `AUTH_MISSING_TOKEN` in the client's tool-error envelope, same
    way they look different on the `/v1/*` REST surface.
    """

    error: AgentErrorException


# Union of the three states the auth resolver can produce:
#   None              → no token supplied
#   MCPAuthFailure    → token rejected (preserve the verifier code)
#   MCPAuthContext    → token verified
mcp_auth_state_var: ContextVar[MCPAuthContext | MCPAuthFailure | None] = ContextVar(
    "mcp_auth_state_var", default=None
)


def current_mcp_auth_context() -> MCPAuthContext | None:
    """Read the verified context for the current request, or None if absent.

    Returns the `MCPAuthContext` ONLY when the verifier succeeded. Returns
    `None` both when no token was sent AND when verification failed — the
    `MCPAuthFailure` state is intentionally invisible to this accessor so
    accidental callers can't act on a half-resolved auth. Tools that need
    to act on auth call `require_mcp_scopes` instead, which surfaces the
    three states distinctly (missing / failed / insufficient).
    """
    state = mcp_auth_state_var.get()
    return state if isinstance(state, MCPAuthContext) else None


def _agent_error_payload(exc: AgentErrorException) -> str:
    """Serialize the AgentError envelope as JSON for tool-error transport.

    The MCP low-level handler turns a raised `ToolError` into a
    `CallToolResult(isError=True, content=[TextContent(text=str(exc))])`.
    By stuffing our standard AgentError shape into that text, clients get
    the same machine-readable error structure they already parse from
    `/v1/*` (`{"error": {code, message, problem, cause, fix, ...}}`),
    just inside the tool-call envelope instead of an HTTP body.
    """
    return json.dumps({"error": exc.error.model_dump(mode="json")})


def require_mcp_scopes(*required_scopes: str) -> MCPAuthContext:
    """Tool-side gate: assert auth + scope, return the context, or raise.

    Call at the top of every MCP tool implementation. Raises a `ToolError`
    whose `args[0]` is a JSON-serialized AgentError envelope. The error
    code depends on the auth state:

      • No token sent              → AUTH_MISSING_TOKEN
      • Token rejected by verifier → original verifier code
                                     (AUTH_CLIENT_NOT_ALLOWED,
                                      AUTH_OAUTH_EXPIRED, etc.)
      • Token valid, scope missing → AUTH_INSUFFICIENT_SCOPE

    Unknown scope literals raise `ValueError` immediately — same defense
    as `app.auth.scopes.require_scope`, so a typo in a tool declaration
    fails at tool-registration time, not at first call.
    """
    unknown = set(required_scopes) - CANONICAL_SCOPES
    if unknown:
        raise ValueError(
            f"require_mcp_scopes() called with non-canonical scope(s): {sorted(unknown)}. "
            f"Canonical scopes are: {sorted(CANONICAL_SCOPES)}."
        )
    state = mcp_auth_state_var.get()
    if state is None:
        raise ToolError(_agent_error_payload(auth_missing_token()))
    if isinstance(state, MCPAuthFailure):
        raise ToolError(_agent_error_payload(state.error))
    required = frozenset(required_scopes)
    if not required <= state.scopes:
        raise ToolError(_agent_error_payload(auth_insufficient_scope()))
    return state


def require_user_context(auth: MCPAuthContext) -> uuid.UUID:
    """Return the calling user's id, or raise for M2M tokens that have none.

    Call this in tools that operate on user-owned resources (tasks, prompt
    preview) AFTER `require_mcp_scopes(...)`. M2M (client_credentials) tokens
    are sanctioned on `/mcp/*` but carry no human identity — `auth.user_id` is
    None — so a user-scoped tool can't resolve an owner. Fail closed with
    `AUTH_USER_CONTEXT_REQUIRED` rather than letting `None` flow into an
    ownership query (which would 404) or a typed `uuid.UUID` parameter (which
    would 500 / fail mypy). Tools that need no user (e.g. `meta.get`) never
    call this.
    """
    if auth.user_id is None:
        raise ToolError(_agent_error_payload(auth_user_context_required()))
    return auth.user_id


@contextlib.contextmanager
def translate_agent_errors() -> Iterator[None]:
    """Convert a service-layer `AgentErrorException` into an MCP `ToolError`.

    The service / repository layer raises `AgentErrorException` (e.g.
    `not_found_task()`, `conflict_task_already_terminal()`). On `/v1/*` the
    `agent_error_handler` turns those into a JSON body with an HTTP status; on
    `/mcp/*` there is no HTTP error contract (auth + tool failures both ride
    inside a 200 `CallToolResult` with `isError=True`). Wrapping a tool's
    service calls in this context manager re-raises any `AgentErrorException`
    as a `ToolError` carrying the SAME serialized AgentError envelope clients
    already parse from `/v1/*` and from the auth failures above — so a
    `NOT_FOUND_TASK` looks identical whether it surfaces over REST or MCP.

    Used as a sync `with` around `async with` / `await` blocks; the exception
    propagates synchronously through the context manager regardless.
    """
    try:
        yield
    except AgentErrorException as exc:
        raise ToolError(_agent_error_payload(exc)) from exc


async def resolve_mcp_token(token: str) -> MCPAuthContext | MCPAuthFailure:
    """Verify the bearer token via the legacy JWT or Authentik OAuth path.

    Returns `MCPAuthContext` on success and `MCPAuthFailure` (wrapping the
    verifier's `AgentErrorException`) on every failure path. Distinct from
    "no token sent" — that case never reaches this function because the
    middleware only calls it when an `Authorization: Bearer ...` header is
    present.

    Per ticket Note "MCP server 不回 HTTP 401 / 403" the middleware does
    not raise from this state; instead it installs the result on the
    contextvar and `require_mcp_scopes` surfaces the failure as a tool
    error with the preserved code.
    """
    # Routing-only peek; signature is re-verified by the chosen path against
    # the correct key. Same pattern as `_peek_unverified_payload` in
    # `app/api/deps.py` — see that file's nosemgrep comment for context.
    try:
        # nosemgrep: python.jwt.security.unverified-jwt-decode.unverified-jwt-decode
        unverified: dict[str, Any] = pyjwt.decode(token, options={"verify_signature": False})
    except pyjwt.InvalidTokenError:
        # Malformed token shape — can't route to a verifier, so it's
        # neither OAuth-invalid nor JWT-invalid specifically. Treat as
        # generic AUTH_INVALID_TOKEN (matching the /v1/* dispatcher's
        # behaviour for the same shape).
        return MCPAuthFailure(error=auth_invalid_token())

    if is_authentik_token(unverified):
        try:
            claims = await verify_oauth_token(token)
        except AgentErrorException as exc:
            # Preserve the verifier's specific code — AUTH_CLIENT_NOT_ALLOWED,
            # AUTH_SCOPE_EXCEEDS_ALLOWLIST, AUTH_OAUTH_EXPIRED, etc.
            # Tools see the original semantic on tool-call failure
            # rather than a flattened "missing token" symptom (Codex
            # round-1 P2). The verifier already logs at warn/error;
            # log here at debug so the audit trail isn't doubled.
            _logger.debug(
                "mcp_oauth_token_rejected",
                extra={"code": exc.error.code},
            )
            return MCPAuthFailure(error=exc)
        # M2M tokens are SANCTIONED on /mcp/* — `auth_m2m_wrong_surface`
        # only fires on `/v1/*` (T-054 `_resolve_oauth`). Headless agents use
        # the OAuth client_credentials grant against this surface.
        #
        # A client in `M2M_SERVICE_ACCOUNT_CLIENTS` (T-092) resolves to a
        # provisioned backend service-account User, so it can run user-scoped
        # tools (character.create, ...) and OWN what it creates — the
        # industry-standard machine-principal model. Any other M2M client stays
        # `user_id=None` and is read-only on user-owned resources
        # (require_user_context fails closed). `is_m2m` stays True either way,
        # so /v1/* still rejects. The short-lived session mirrors the delegated
        # branch below — the MCP middleware runs before any FastAPI Depends, so
        # there's no injected `db` to share. A missing `default` team raises
        # RuntimeError (deploy misconfig) and propagates loud, same as the
        # delegated path's `auto_provision_oauth_user`.
        if claims.is_m2m:
            m2m_user_id: uuid.UUID | None = None
            if is_m2m_service_account_client(claims.client_id):
                factory = async_session_factory()
                async with factory() as db:
                    m2m_user_id = await resolve_m2m_service_user_id(claims.client_id, db)
            return MCPAuthContext(
                user_id=m2m_user_id,
                client_id=claims.client_id,
                scopes=claims.scopes,
                is_m2m=True,
            )
        # Delegated token (Auth Code + PKCE) — a human is acting through
        # an agent client. Resolve to a backend `User.id` via the shared
        # `resolve_oauth_user_id` helper (same path `/v1/*` uses via
        # `app.api.deps._resolve_oauth`). Without this, tools that scope
        # data to the calling user would see `user_id=None` and either
        # 500 or silently leak across users (Codex PR #107 round-2 P1).
        # Opens a short-lived AsyncSession via `async_session_factory()`
        # so we don't reuse a request-scoped session here — the MCP
        # middleware runs before any FastAPI Depends, so there's no
        # injected `db` to share.
        try:
            factory = async_session_factory()
            async with factory() as db:
                user_id = await resolve_oauth_user_id(claims, db)
        except AgentErrorException as exc:
            _logger.debug(
                "mcp_oauth_user_resolution_failed",
                extra={"code": exc.error.code, "client_id": claims.client_id},
            )
            return MCPAuthFailure(error=exc)
        return MCPAuthContext(
            user_id=user_id,
            client_id=claims.client_id,
            scopes=claims.scopes,
            is_m2m=False,
        )

    # Legacy JWT path. No client_id concept — per ticket §"Token / scope
    # 整合", the allowlist check does NOT apply here. Once JWTs are gone
    # post-M3.5 ship, the entire dual-stack branch collapses and the
    # allowlist becomes the sole client gate.
    try:
        payload = verify_access_token(token)
    except JWTExpired:
        return MCPAuthFailure(error=auth_expired())
    except JWTInvalid:
        return MCPAuthFailure(error=auth_invalid_token())
    sub_raw = payload.get("sub")
    try:
        user_id = uuid.UUID(str(sub_raw))
    except (TypeError, ValueError):
        # Token verified but `sub` is not a UUID — treat as invalid,
        # parallel to `_resolve_jwt`'s same branch in `app/api/deps.py`.
        return MCPAuthFailure(error=auth_invalid_token())

    # Verify the user row still exists — same check `/v1/*`'s
    # `get_current_user` runs immediately after `_resolve_jwt` (see
    # `app/api/deps.py::get_current_user`). Without it, a validly-signed
    # JWT whose `sub` points at a since-deleted user row would still grant
    # full canonical scope access on `/mcp/*` — Codex round-3 P2 against
    # PR #107. Same short-lived AsyncSession pattern as the delegated
    # OAuth branch above.
    factory = async_session_factory()
    async with factory() as db:
        user = await db.get(User, user_id)
        if user is None:
            return MCPAuthFailure(error=auth_invalid_token())

    return MCPAuthContext(
        user_id=user_id,
        client_id=None,
        # Legacy JWTs grandfather the full canonical scope set, mirroring
        # `_resolve_jwt` in `app/api/deps.py`. Safe only because /mcp/* tools
        # are scoped to character/task/usage operations — none of which a
        # legacy human session shouldn't already be able to perform.
        scopes=CANONICAL_SCOPES,
        is_m2m=False,
    )


def _extract_authorization_header(scope: Scope) -> bytes | None:
    """Return the raw `Authorization` header value, or None if absent.

    Distinct from `_parse_bearer` below so the middleware can tell
    "no header at all" (→ AUTH_MISSING_TOKEN) apart from "header present
    but malformed" (→ AUTH_INVALID_TOKEN). Codex round-2 P2 against PR
    #107 flagged the earlier "either case collapses to None" behaviour
    as breaking parity with `/v1/*`, where `_extract_bearer` raises
    `auth_invalid_token` on a `Basic ...` or `Bearer ` (empty) header.
    """
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            # ASGI scope headers are typed as Iterable[tuple[bytes, bytes]]
            # in spec but mypy infers `Any` from the dynamic `.get(...)`,
            # so the explicit `bytes(...)` widens-then-narrows back to
            # the declared return type without copy if it already is one.
            return bytes(value)
    return None


def _parse_bearer(raw: bytes) -> str | None:
    """Decode a known-present `Authorization` value into the bearer token.

    Returns the token string on success or `None` if the header is not a
    well-formed `Bearer <non-empty>` value. Callers distinguish this from
    "header absent" via `_extract_authorization_header` returning None.
    """
    try:
        decoded = raw.decode("latin-1")
    except UnicodeDecodeError:
        return None
    parts = decoded.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


class MCPAuthContextMiddleware:
    """ASGI middleware: parse bearer, resolve dual-stack, set contextvar.

    Wraps the FastMCP streamable HTTP ASGI app from `app/mcp/app.py`.
    Deliberately does NOT block requests on missing/invalid auth — see
    module docstring. The contextvar is set before delegating downstream
    and reset on the way out so cross-request leakage is impossible even
    when the underlying event loop multiplexes coroutines.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Lifespan / WebSocket events bypass auth resolution — they carry no
        # request headers and shouldn't pin a context. Pass through.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state: MCPAuthContext | MCPAuthFailure | None
        raw_header = _extract_authorization_header(scope)
        if raw_header is None:
            # No Authorization header sent at all.
            state = None
        else:
            token = _parse_bearer(raw_header)
            if token is None:
                # Header present but not a well-formed Bearer (e.g.
                # `Basic ...`, `Bearer ` with empty token, non-latin-1
                # bytes). Surface as AUTH_INVALID_TOKEN — same code
                # `/v1/*` returns for the equivalent shape (Codex
                # round-2 P2). Without this, the failure would
                # masquerade as AUTH_MISSING_TOKEN and clients /
                # auth telemetry couldn't distinguish "client forgot
                # to send a token" from "client sent garbage".
                state = MCPAuthFailure(error=auth_invalid_token())
            else:
                state = await resolve_mcp_token(token)

        var_token = mcp_auth_state_var.set(state)
        try:
            await self.app(scope, receive, send)
        finally:
            mcp_auth_state_var.reset(var_token)
