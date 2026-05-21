"""`hello.world` MCP smoke tool (T-080).

Exists purely to validate three things end-to-end before any real tool
lands in Wave B (T-084 / T-085 / T-086):

  1. Streamable HTTP transport — a request actually reaches the FastMCP
     server through the FastAPI `/mcp` sub-app mount.
  2. Dual-stack token + per-tool scope enforcement — both legacy JWT and
     Authentik OAuth tokens accepted; `character:read` required to call.
  3. Progress notification round-trip — `ctx.report_progress` is the
     direct regression target of MCP Python SDK PR #2038 (`related_request_id`
     dropped on streamable HTTP); the smoke test in
     `tests/mcp/test_skeleton.py` asserts the notification actually
     arrives over real streamable HTTP, not an in-memory transport.

T-080 ACs explicitly forbid removing this tool until T-3.5c E2E smoke
opens (where it becomes a health check). T-081 moved the wiring into the
registry pattern (`app.mcp.registry`); the tool implementation itself is
unchanged — same name, description, scope, and behaviour.
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field

from app.auth.scopes import SCOPE_CHARACTER_READ
from app.mcp.auth import require_mcp_scopes
from app.mcp.progress import report_progress
from app.mcp.registry import MCPTool, register


class HelloIn(BaseModel):
    echo: str = Field(..., description="String the tool will echo back in the reply.")


class HelloOut(BaseModel):
    reply: str


# Description passed verbatim to `mcp.tool(description=...)` by
# `register_all` in `app/mcp/tools/__init__.py`. Extracted to a constant so
# the registry entry below and any future doc-export tooling share one
# source of truth.
_HELLO_DESCRIPTION = (
    "Smoke tool — echoes input + emits one progress notification. "
    "Requires character:read scope. Wired up by T-080 so the MCP "
    "transport, dual-stack auth, and progress-notification round-trip "
    "can be validated before any real tools land in Wave B."
)


async def hello_world(echo: str, ctx: Context[Any, Any, Any]) -> HelloOut:
    """`hello.world` handler — applied onto FastMCP via the registry.

    The tool name uses the dotted namespace decided in agent-interface
    Round 1 Q4 (`character.create`, `motion.generate`, ...). Even though
    this tool has no namespace siblings yet, keeping the convention from
    day one avoids a rename when T-084 ships `character.*`.

    Per ticket §"Smoke tool — `hello.world`": sleep 200ms → report progress
    0.5 → sleep 200ms → return. The 200ms pauses bracket the notification so
    the test can confirm it really crossed the streamable HTTP stream
    mid-flight, not as part of the final response payload. The `total=1.0` +
    halfway message mirrors what any future long-running tool (i2v in T-086)
    emits — keeps the smoke surface representative.
    """
    auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
    await anyio.sleep(0.2)
    await report_progress(
        ctx,
        progress=0.5,
        total=1.0,
        message="halfway",
    )
    await anyio.sleep(0.2)
    # `client_id` is None for legacy JWT-path callers (per T-080 ticket
    # acceptance criterion: JWT path skips the allowlist). Fall back to
    # a static label so the JWT smoke assertion gets a deterministic
    # reply string — `hello, jwt-user: <echo>` — rather than `None`.
    caller = auth.client_id or "jwt-user"
    return HelloOut(reply=f"hello, {caller}: {echo}")


HELLO_WORLD = register(
    MCPTool(
        name="hello.world",
        description=_HELLO_DESCRIPTION,
        scopes=[SCOPE_CHARACTER_READ],
        # Empty: `hello.world` is an MCP-only smoke tool with no REST
        # endpoint behind it. CI guardrail 2 skips the bundle-union check
        # for bundleless tools and only asserts scopes ⊆ canonical.
        bundles=[],
        input_schema=HelloIn,
        output_schema=HelloOut,
        handler=hello_world,
    )
)
