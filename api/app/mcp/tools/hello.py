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
opens (where it becomes a health check). T-081 will move the wiring into
the registry pattern; the tool implementation itself doesn't change.
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from app.auth.scopes import SCOPE_CHARACTER_READ
from app.mcp.auth import require_mcp_scopes


class HelloIn(BaseModel):
    echo: str = Field(..., description="String the tool will echo back in the reply.")


class HelloOut(BaseModel):
    reply: str


async def _report_progress_with_request_id(
    ctx: Context[Any, Any, Any],
    *,
    progress: float,
    total: float | None,
    message: str | None,
) -> None:
    """Workaround for an unreleased fix in `Context.report_progress`.

    Per agent-interface Round 1 Q3 implementation gotcha 1 and the T-080
    ticket Notes, MCP Python SDK PR #2038 was supposed to make
    `ctx.report_progress` pass `related_request_id=self.request_id` so
    progress notifications route back over the per-request SSE response
    stream. As of v1.27.1 (the latest release at T-080 ship time on
    2026-05-18) that change has merged on `main` but has NOT been
    cut into a release tag — the installed `report_progress` still
    omits the parameter, so notifications get routed to the GET-stream
    bucket and the client never sees them.

    We pin `mcp>=1.27.0` for the future fix and call
    `send_progress_notification` directly here with the explicit
    `related_request_id` so the smoke test in
    `tests/mcp/test_skeleton.py::test_progress_notification_reaches_client`
    actually validates the round-trip TODAY. Once the SDK ships the fix
    in 1.28.x+ this helper becomes a passthrough that can be removed —
    the test stays valid either way.
    """
    progress_token = ctx.request_context.meta.progressToken if ctx.request_context.meta else None
    if progress_token is None:
        return
    await ctx.request_context.session.send_progress_notification(
        progress_token=progress_token,
        progress=progress,
        total=total,
        message=message,
        related_request_id=ctx.request_id,
    )


def register(mcp: FastMCP) -> None:
    """Register `hello.world` on the given FastMCP server.

    The tool name uses the dotted namespace decided in agent-interface
    Round 1 Q4 (`character.create`, `motion.generate`, ...). Even though
    this tool has no namespace siblings yet, keeping the convention from
    day one avoids a rename when T-084 ships `character.*`.
    """

    @mcp.tool(
        name="hello.world",
        description=(
            "Smoke tool — echoes input + emits one progress notification. "
            "Requires character:read scope. Wired up by T-080 so the MCP "
            "transport, dual-stack auth, and progress-notification round-trip "
            "can be validated before any real tools land in Wave B."
        ),
    )
    async def hello_world(echo: str, ctx: Context[Any, Any, Any]) -> HelloOut:
        # Per ticket §"Smoke tool — `hello.world`": sleep 200ms → report
        # progress 0.5 → sleep 200ms → return. The 200ms pauses bracket
        # the notification so the test can confirm it really crossed the
        # streamable HTTP stream mid-flight, not as part of the final
        # response payload. The `total=1.0` + halfway message mirrors what
        # any future long-running tool (i2v in T-086) emits — keeps the
        # smoke surface representative.
        auth = require_mcp_scopes(SCOPE_CHARACTER_READ)
        await anyio.sleep(0.2)
        await _report_progress_with_request_id(
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
