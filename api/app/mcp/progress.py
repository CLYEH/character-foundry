"""Shared MCP progress-notification helper (T-084).

`character.create` / `alias.add` / `motion.generate` are long-running
packaged tools that emit `notifications/progress` mid-flight so an agent
sees a moving bar instead of a silent block. They all need the same
workaround the T-080 `hello.world` smoke tool documented: as of mcp
v1.27.1 the released `Context.report_progress` still omits
`related_request_id` (PR #2038 merged on main but isn't in a release
tag), so notifications get routed to the GET-stream bucket and the
streamable-HTTP client never sees them. We call
`send_progress_notification` directly with the explicit
`related_request_id` instead.

This module is the single home for that workaround so that when the SDK
ships the fix there's exactly one call site to simplify (rather than one
copy per packaged tool). `hello.py` delegates here too.

`ctx` is tolerated as `None` / token-less so unit tests can drive a tool
handler directly without standing up a streamable-HTTP transport — the
real wire round-trip stays covered by
`tests/mcp/test_skeleton.py::test_progress_notification_reaches_client`
(hello.world over real streamable HTTP).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context


async def report_progress(
    ctx: Context[Any, Any, Any] | None,
    *,
    progress: float,
    total: float | None,
    message: str | None,
) -> None:
    """Emit one `notifications/progress` with the related_request_id workaround.

    No-ops when `ctx` is None or the client supplied no `progressToken`
    (progress notifications are opt-in per request in the MCP spec).
    """
    if ctx is None:
        return
    request_context = ctx.request_context
    meta = request_context.meta if request_context else None
    progress_token = meta.progressToken if meta else None
    if progress_token is None:
        return
    await request_context.session.send_progress_notification(
        progress_token=progress_token,
        progress=progress,
        total=total,
        message=message,
        related_request_id=ctx.request_id,
    )
