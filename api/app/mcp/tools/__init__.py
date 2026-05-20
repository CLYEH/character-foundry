"""MCP tool package — import-time discovery + FastMCP application (T-081).

Importing this package imports every tool submodule (`hello`, and the Wave B
`character` / `alias` / `motion` modules once they land), which triggers each
module's `register(MCPTool(...))` call as a side effect. After import,
`app.mcp.registry.REGISTRY` holds the full catalogue.

`register_all(mcp)` then applies that catalogue onto a freshly-built FastMCP.
`app/mcp/app.py::_build_mcp_server()` calls it on every lifespan rebuild (the
`StreamableHTTPSessionManager.run()` single-use contract). Discovery happens
once at package import; application is a cheap per-lifespan loop — so a
per-test FastMCP rebuild never re-triggers `register()` (which raises on a
duplicate name).

Discovery is automatic via `pkgutil.iter_modules` rather than an explicit
import list so a Wave B ticket only has to drop `character.py` into this
package — no second edit here to "remember" to import it. Modules whose name
starts with `_` are skipped (private helpers, not tool modules).
"""

from __future__ import annotations

import importlib
import pkgutil

from mcp.server.fastmcp import FastMCP

from app.mcp.registry import REGISTRY


def _discover() -> None:
    """Import every non-private submodule so its tools self-register."""
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{module_info.name}")


# Populate REGISTRY at package import time (idempotent — re-importing an
# already-loaded submodule is a no-op via sys.modules, so register() runs
# exactly once per tool).
_discover()


def register_all(mcp: FastMCP) -> None:
    """Apply every registered MCPTool onto `mcp`.

    Equivalent to T-080's `register_hello(mcp)` but driven by the registry,
    so adding a Wave B tool requires zero changes to `app/mcp/app.py`.
    FastMCP derives the wire input schema from each handler's signature;
    `MCPTool.input_schema` / `output_schema` are registry metadata only
    (see `app/mcp/registry.py`).
    """
    for tool in REGISTRY.values():
        mcp.tool(name=tool.name, description=tool.description)(tool.handler)
