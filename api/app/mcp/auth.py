"""MCP request auth — dual-stack token resolution shared with /v1/*.

Per `tickets/T-080-mcp-server-skeleton.md`, MCP server requests follow the
same dual-stack model as REST endpoints (T-054): legacy HS256 JWT and
Authentik OAuth tokens both accepted, dispatched via `iss` claim. The
client_id allowlist is enforced on the OAuth path only — legacy JWTs have
no `client_id` concept, so applying the allowlist to them would lock out
existing SPA sessions during the M3.5 migration window.

Auth state is stashed in a `ContextVar` (`mcp_auth_context_var`) rather
than the ASGI request scope so MCP tool handlers — invoked from inside the
JSON-RPC dispatch loop, several call frames removed from any ASGI request
— can read it via `current_mcp_auth_context()`. Tools then call
`require_mcp_scopes(...)` at entry to enforce the per-tool scope contract
declared in their `MCPTool` registry entry (registry itself lands in T-081).

Per the ticket Note on "MCP error vs HTTP status": auth failures do NOT
return HTTP 401/403. The streamable HTTP response stays 200 and the
JSON-RPC envelope carries the error so MCP clients see a structured
`CallToolResult` with `isError=True` — same surface as any other tool
failure. To achieve that, the middleware NEVER blocks unauthenticated
requests at the ASGI layer; it just leaves the contextvar empty and lets
`require_mcp_scopes` raise a `ToolError` inside the tool handler.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import jwt as pyjwt
from mcp.server.fastmcp.exceptions import ToolError
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.jwt import JWTExpired, JWTInvalid, verify_access_token
from app.auth.oauth import is_authentik_token, verify_oauth_token
from app.auth.scopes import CANONICAL_SCOPES
from app.core.errors import (
    AgentErrorException,
    auth_insufficient_scope,
    auth_missing_token,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPAuthContext:
    """Resolved auth state for one MCP request.

    `user_id` is `None` for M2M client_credentials tokens — they have no
    human behind them and the MCP surface tolerates that by design (per
    agent-interface Q5 Round 2 — headless agents are first-class on /mcp/*,
    they just can't reach /v1/* per T-054 `auth_m2m_wrong_surface`).
    Legacy JWT-path requests always have a `user_id`, no `client_id`, and
    `is_m2m=False` — mirroring `_resolve_jwt` in `app/api/deps.py`.
    """

    user_id: uuid.UUID | None
    client_id: str | None
    scopes: frozenset[str]
    is_m2m: bool


mcp_auth_context_var: ContextVar[MCPAuthContext | None] = ContextVar(
    "mcp_auth_context_var", default=None
)


def current_mcp_auth_context() -> MCPAuthContext | None:
    """Read the current request's MCP auth context, or None if unauthenticated.

    Returns `None` both when no `Authorization` header was sent and when
    the token failed verification — see `resolve_mcp_token` for why both
    collapse to the same state. Tools that need auth call
    `require_mcp_scopes` instead, which surfaces the missing-vs-invalid
    distinction back to the caller via a structured AgentError code.
    """
    return mcp_auth_context_var.get()


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
    whose `args[0]` is a JSON-serialized AgentError envelope when the
    contextvar is empty (no/invalid token → AUTH_MISSING_TOKEN) or when
    the token's scopes don't cover the required set (AUTH_INSUFFICIENT_SCOPE).

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
    ctx = mcp_auth_context_var.get()
    if ctx is None:
        raise ToolError(_agent_error_payload(auth_missing_token()))
    required = frozenset(required_scopes)
    if not required <= ctx.scopes:
        raise ToolError(_agent_error_payload(auth_insufficient_scope()))
    return ctx


async def resolve_mcp_token(token: str) -> MCPAuthContext | None:
    """Verify the bearer token via the legacy JWT or Authentik OAuth path.

    Returns `MCPAuthContext` on success and `None` on any verification
    failure. The middleware does NOT distinguish "no token" from "invalid
    token" at the ASGI layer — both surface to tools as an empty contextvar
    so `require_mcp_scopes` can emit AUTH_MISSING_TOKEN as a structured
    MCP error rather than an HTTP 401 (per ticket Note: "MCP server 不回
    HTTP 401 / 403"). Logging happens at the verifier layer; this wrapper
    only swallows the exception type.
    """
    # Routing-only peek; signature is re-verified by the chosen path against
    # the correct key. Same pattern as `_peek_unverified_payload` in
    # `app/api/deps.py` — see that file's nosemgrep comment for context.
    try:
        # nosemgrep: python.jwt.security.unverified-jwt-decode.unverified-jwt-decode
        unverified: dict[str, Any] = pyjwt.decode(token, options={"verify_signature": False})
    except pyjwt.InvalidTokenError:
        return None

    if is_authentik_token(unverified):
        try:
            claims = await verify_oauth_token(token)
        except AgentErrorException as exc:
            # All token-shape failures funnel through AgentErrorException.
            # Log at debug so the audit trail stays in the verifier layer
            # (it already logs at warn/error); here we just collapse the
            # symptom to a uniform "unauthenticated" state.
            _logger.debug(
                "mcp_oauth_token_rejected",
                extra={"code": exc.error.code},
            )
            return None
        # M2M tokens are SANCTIONED on /mcp/* — `auth_m2m_wrong_surface`
        # only fires on `/v1/*` (T-054 `_resolve_oauth`). Headless agents
        # use the OAuth client_credentials grant against this surface.
        return MCPAuthContext(
            user_id=None,
            client_id=claims.client_id,
            scopes=claims.scopes,
            is_m2m=claims.is_m2m,
        )

    # Legacy JWT path. No client_id concept — per ticket §"Token / scope
    # 整合", the allowlist check does NOT apply here. Once JWTs are gone
    # post-M3.5 ship, the entire dual-stack branch collapses and the
    # allowlist becomes the sole client gate.
    try:
        payload = verify_access_token(token)
    except (JWTExpired, JWTInvalid):
        return None
    sub_raw = payload.get("sub")
    try:
        user_id = uuid.UUID(str(sub_raw))
    except (TypeError, ValueError):
        return None
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


def _extract_bearer_from_scope(scope: Scope) -> str | None:
    """Pull the bearer token from an ASGI scope's request headers.

    ASGI headers are `list[tuple[bytes, bytes]]` with lowercase names.
    Returns the raw token (no `Bearer ` prefix) or `None` if the header
    is missing or malformed — uniform with `_extract_bearer` in
    `app/api/deps.py`, just operating on raw bytes instead of a parsed
    `Authorization` header string.
    """
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            try:
                decoded = value.decode("latin-1")
            except UnicodeDecodeError:
                return None
            parts = decoded.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip()
                return token or None
            return None
    return None


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

        token = _extract_bearer_from_scope(scope)
        ctx: MCPAuthContext | None = None
        if token:
            ctx = await resolve_mcp_token(token)

        var_token = mcp_auth_context_var.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            mcp_auth_context_var.reset(var_token)
