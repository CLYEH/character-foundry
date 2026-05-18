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
from starlette.types import ASGIApp, Receive, Scope, Send

from app.mcp.auth import MCPAuthContextMiddleware
from app.mcp.tools.hello import register as register_hello

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
    # See `app/mcp/tools/hello.py` for tool implementation; T-081 lands
    # the registry pattern that replaces direct `register_hello(mcp)`
    # calls with a loop over `app.mcp.tools.REGISTRY`.
    mcp = FastMCP(
        "character-foundry",
        stateless_http=True,
        json_response=False,
        streamable_http_path="/",
        transport_security=_build_transport_security(),
    )
    register_hello(mcp)
    return mcp


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
