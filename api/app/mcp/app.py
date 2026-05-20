"""MCP streamable HTTP server — mounted at `/mcp` by `app/main.py`.

Per agent-interface Round 2 Q7 sub-7a, the MCP server runs in-process as a
FastAPI sub-app: same DB session factory, same `AgentError` envelope, same
arq task system. The streamable HTTP transport itself comes from
`mcp.server.fastmcp.FastMCP`.

Why we don't use FastMCP's built-in `auth=AuthSettings(...)` +
`TokenVerifier` machinery:

  • FastMCP's `RequireAuthMiddleware` returns HTTP 401/403 on auth failure,
    which violates the T-080 ticket Note "auth errors are MCP errors, not
    HTTP status".
  • Its `required_scopes` is a single global list applied to every tool —
    no per-tool granularity. The registry pattern landing in T-081 is
    explicitly per-tool, so we'd be replacing this layer immediately.

Instead, `app/mcp/auth.py::MCPAuthContextMiddleware` parses the bearer at
the ASGI layer and stashes a `MCPAuthContext` in a contextvar. Each tool
calls `require_mcp_scopes(...)` at entry and the failure surfaces as a
structured MCP `ToolError` with the same AgentError envelope `/v1/*`
returns.

Lifespan / per-test rebuild contract:

  FastMCP's `StreamableHTTPSessionManager.run()` is single-use — the SDK
  guards it with `_has_started` and raises if entered twice on the same
  instance. The FastAPI lifespan therefore builds a FRESH FastMCP on
  every startup (production fires it once; tests using TestClient fire
  it per-test). The `_MCPDispatcher` indirection lets `app.main` mount a
  stable ASGI callable at `/mcp` while the underlying FastMCP gets
  swapped on each lifespan cycle.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator
from typing import Final

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ListToolsResult
from starlette.types import ASGIApp, Receive, Scope, Send

from app.mcp.auth import MCPAuthContextMiddleware
from app.mcp.tools import register_all
from app.services import degraded_services

_logger = logging.getLogger(__name__)

_DEFAULT_ALLOWED_HOSTS: Final[str] = "127.0.0.1:*,localhost:*,[::1]:*"
_DEFAULT_ALLOWED_ORIGINS: Final[str] = "http://127.0.0.1:*,http://localhost:*,http://[::1]:*"


def _parse_csv_env(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item for item in (chunk.strip() for chunk in raw.split(",")) if item]


def _build_transport_security() -> TransportSecuritySettings:
    """Configure FastMCP's DNS-rebinding-protection allowlist.

    FastMCP auto-enables DNS rebinding protection when bound to a loopback
    host and seeds the allowlist with `["127.0.0.1:*", "localhost:*",
    "[::1]:*"]`. That default is sane for local dev but rejects any other
    Host header — production behind nginx + the test harness's
    `Host: testserver` both need explicit allowlist entries. Building this
    here (instead of letting FastMCP auto-decide) keeps the configuration
    visible and overrideable via `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS`
    env vars without touching code.

    Per agent-interface Q7 sub-7b, the real security guarantee on this
    surface is the OAuth client allowlist + the dual-stack token verifier,
    not Host-header validation. Keeping DNS rebinding protection on is
    defense-in-depth.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_parse_csv_env("MCP_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS),
        allowed_origins=_parse_csv_env("MCP_ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS),
    )


def _build_mcp_server() -> FastMCP:
    """Construct a fresh FastMCP + register every tool on it.

    Per the module docstring "lifespan / per-test rebuild contract", this
    is called by the lifespan on EACH startup. Do NOT cache the result
    at module level — `StreamableHTTPSessionManager.run()` is single-use
    and a cached FastMCP would carry `_has_started=True` into the next
    lifespan cycle and crash.
    """
    # See `_build_transport_security` docstring for the security knob set.
    # `register_all` (T-081) applies every tool in `app.mcp.registry.REGISTRY`
    # — populated at import time by `app/mcp/tools/__init__.py` auto-discovery
    # — onto this fresh FastMCP instance. Adding a Wave B tool needs no edit
    # here.
    mcp = FastMCP(
        "character-foundry",
        stateless_http=True,
        json_response=False,
        streamable_http_path="/",
        transport_security=_build_transport_security(),
    )
    register_all(mcp)
    _install_tools_list_meta_extension(mcp)
    return mcp


def _install_tools_list_meta_extension(mcp: FastMCP) -> None:
    """Surface `degraded_services` on the `tools/list` response `_meta` (T-088).

    Per `planning/agent-interface/endpoint-mcp-mapping.md` §5, agents must be
    able to read degraded state WITHOUT an explicit `meta.get` call, so the same
    Redis-aggregated list that `/v1/meta` and the `meta.get` tool serve also
    rides on every `tools/list` response's `_meta` field. This is the only MCP
    surface with two views of one datum (a tool AND a transport-level extension).

    Mechanism: FastMCP wires a default `list_tools` handler in its `__init__`
    that returns `list[Tool]`. The low-level server's `list_tools()` decorator
    (`mcp/server/lowlevel/server.py`) ALSO accepts a handler returning a full
    `ListToolsResult` and passes its `_meta` through unchanged (plus refreshes
    the tool cache from `result.tools`). We re-register such a handler here,
    reusing `FastMCP.list_tools()` for the tool list itself so the per-tool
    schemas stay identical — we only attach the response-level `_meta`.

    Both this extension and `meta.get` call the SAME
    `degraded_services.aggregate_degraded_services()` (resolved at call time via
    the module attribute), so they can't drift — and a Redis outage degrades to
    an empty list rather than failing `tools/list` (which agents call on every
    connect).
    """

    async def _list_tools_with_meta() -> ListToolsResult:
        tools = await mcp.list_tools()
        degraded = await degraded_services.aggregate_degraded_services()
        return ListToolsResult(tools=tools, _meta={"degraded_services": degraded})

    # `mcp._mcp_server` is the underlying low-level Server; re-registering the
    # ListToolsRequest handler overrides FastMCP's default. This is the SDK's
    # documented extension seam (the low-level `@server.list_tools()` API).
    # `list_tools()` is an untyped decorator factory in the SDK stubs.
    mcp._mcp_server.list_tools()(_list_tools_with_meta)  # type: ignore[no-untyped-call]


class MCPPathNormalizationMiddleware:
    """Rewrite `/mcp` to `/mcp/` BEFORE FastAPI's router sees the request.

    Without this, `POST /mcp` (no trailing slash) lands on FastAPI's
    default `redirect_slashes=True` behaviour and returns 307 → `/mcp/`.
    JSON-RPC / MCP clients vary in how they handle 307 on POST: some
    re-POST to the redirect target (works), some silently fall back to
    GET (fails), some refuse without user confirmation (fails). Rewriting
    the scope's `path` in-place before routing avoids the redirect
    entirely — both `/mcp` and `/mcp/` reach the same handler with a
    single round-trip.

    Codex round-3 P1 against PR #107 verified empirically: bare `POST /mcp`
    returned 307 from `TestClient(app, follow_redirects=False)`. Per
    ticket Note "MCP error vs HTTP status", we already promise auth
    errors won't be HTTP 3xx; consistency says the transport URL
    shouldn't be either.

    Scoped to the exact `/mcp` path so unrelated routes keep their
    normal slash-handling. Listed as middleware on the FastAPI app in
    `app/main.py`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            # Build a new scope dict instead of mutating — the same scope
            # may be in flight for cross-cutting middleware that snapshots
            # values. `raw_path` must be updated in lockstep with `path`
            # or downstream Starlette path matching gets confused.
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


class _MCPDispatcher:
    """Stable ASGI callable that forwards to whichever FastMCP is current.

    `app/main.py` mounts this at `/mcp` ONCE at module import. The
    lifespan rebuilds the FastMCP per startup (necessary because
    `session_manager.run()` is single-use) and swaps `self._current` to
    point at the new instance. Request handling is decoupled from
    lifecycle this way — restarting the session manager (e.g. in tests
    via repeated TestClient enters) doesn't require re-mounting.

    Requests arriving while `_current is None` (lifespan not yet started,
    or already torn down) get a 503 — the safe failure mode is to refuse
    the request instead of silently dispatching to a half-constructed
    server.
    """

    def __init__(self) -> None:
        self._current: ASGIApp | None = None

    def set_app(self, app: ASGIApp | None) -> None:
        self._current = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Inner Starlette has its own lifespan but we're driving its
            # session_manager ourselves via `mcp_lifespan`. Swallow
            # lifespan events so the FastAPI parent doesn't try to nest
            # them, and so an idle `/mcp` mount doesn't crash on startup
            # before the dispatcher is populated.
            #
            # The dispatcher is wired into the parent FastAPI's lifespan
            # in `app/main.py`; the parent's lifespan is what actually
            # starts/stops the FastMCP. ASGI lifespan spec says the
            # mounted app should still drain its lifespan messages —
            # respond to startup with `lifespan.startup.complete` etc.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
                else:  # pragma: no cover
                    return
        if self._current is None:
            # 503 plain-text — the JSON-RPC client will surface this as
            # a transport error, which is the right symptom for "server
            # not ready". We can't return an AgentError JSON because the
            # MCP protocol layer isn't reachable without the FastMCP
            # being live.
            if scope["type"] != "http":
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"MCP server not started",
                    "more_body": False,
                }
            )
            return
        await self._current(scope, receive, send)


# Single dispatcher instance. Module-level so `app.main`'s mount stays
# pinned across reloads / re-imports.
mcp_dispatcher: Final[_MCPDispatcher] = _MCPDispatcher()


def get_mcp_dispatcher() -> _MCPDispatcher:
    """Public accessor for the module-level dispatcher.

    Returned for `app.main` to mount and for tests that want to verify
    the dispatch wiring. Don't introduce a setter — replacing the
    dispatcher would orphan the mount.
    """
    return mcp_dispatcher


@contextlib.asynccontextmanager
async def mcp_lifespan() -> AsyncIterator[None]:
    """Build + run a fresh FastMCP for the duration of this lifespan.

    Called from `app.main:lifespan`. Each entry:

      1. Constructs a fresh FastMCP (per "lifespan / per-test rebuild"
         contract — `session_manager.run()` is single-use).
      2. Wraps it with `MCPAuthContextMiddleware` and installs the
         resulting ASGI app on the module-level dispatcher.
      3. Starts the session manager.

    Exit reverses the install so the dispatcher returns 503 between
    lifespans — important for tests that share the FastAPI app across
    multiple TestClient instances.
    """
    mcp = _build_mcp_server()
    inner = mcp.streamable_http_app()  # also lazily initialises session_manager
    wrapped: ASGIApp = MCPAuthContextMiddleware(inner)
    mcp_dispatcher.set_app(wrapped)
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        mcp_dispatcher.set_app(None)
