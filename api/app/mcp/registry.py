"""MCP tool registry (T-081).

The single in-process catalogue of every MCP tool. Each `app/mcp/tools/*.py`
module constructs one or more `MCPTool` entries and `register()`s them at
import time; `app/mcp/tools/__init__.py` auto-imports every module so the
catalogue is populated as a side effect of importing the package, and
`register_all(mcp)` applies the catalogue onto a freshly-built FastMCP each
lifespan.

Why a registry instead of decorating handlers on FastMCP directly (the T-080
`hello.world` pattern):

  • The CI guardrails in `api/scripts/check_mcp_tool_scopes.py` need a
    declarative view of every tool's `scopes` + `bundles` to assert
    `tool.scopes ⊆ union(scopes of bundled endpoints)` (per
    `planning/backend/oauth-mcp-integration.md` §3.4 / §5). A decorator on a
    closure hides that metadata; a dataclass surfaces it.
  • `_build_mcp_server()` rebuilds FastMCP on every lifespan (the
    `StreamableHTTPSessionManager.run()` single-use contract — see
    `app/mcp/app.py`). Registration into `REGISTRY` happens ONCE at module
    import; applying the catalogue onto the per-lifespan FastMCP is a cheap
    loop. Keeping the two phases separate means a per-test rebuild never
    re-triggers `register()` (which would raise on the duplicate name).

`input_schema` / `output_schema` are carried as metadata for documentation
and future schema-export tooling. FastMCP itself still derives the wire
schema from the handler's signature (the Wave A `hello.world` handler takes
`echo: str` directly), so these fields are not passed to `mcp.tool(...)` —
they exist so the registry is self-describing without re-reading signatures.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class MCPTool:
    """One MCP tool: its identity, auth contract, REST mapping, and handler.

    Fields:
      • `name` — dotted namespace id (`character.create`, `hello.world`),
        per agent-interface Round 1 Q4. Unique across the registry.
      • `description` — passed verbatim to `mcp.tool(description=...)`.
      • `scopes` — OAuth scopes the handler enforces via
        `require_mcp_scopes(...)`. Must be canonical (`app.auth.scopes`) and,
        when `bundles` is non-empty, a subset of the union of the bundled
        endpoints' scopes (CI guardrail 2).
      • `bundles` — REST endpoints this tool packages, each `"METHOD /path"`
        (e.g. `"POST /v1/characters"`). Empty for MCP-only tools like the
        `hello.world` smoke tool — guardrail 2 skips the union check then.
      • `input_schema` / `output_schema` — pydantic models, metadata only
        (see module docstring).
      • `handler` — the async callable applied via `mcp.tool(...)(handler)`.
    """

    name: str
    description: str
    scopes: list[str]
    bundles: list[str]
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler: Callable[..., Awaitable[Any]]


# Module-level catalogue. Populated at import time by each tool module's
# `register(...)` call (triggered by `app/mcp/tools/__init__.py` auto-import).
REGISTRY: dict[str, MCPTool] = {}


def register(tool: MCPTool, *, registry: dict[str, MCPTool] = REGISTRY) -> MCPTool:
    """Add `tool` to `registry` (defaults to the module-level `REGISTRY`).

    Returns the tool so a module can do `MY_TOOL = register(MCPTool(...))`.

    Raises `ValueError` on a duplicate `name`. Two tools answering to the same
    dotted id would mean one silently shadows the other on the FastMCP server
    — failing loud at import time turns that into an immediate, traceable
    error instead of a confusing runtime "wrong tool ran" symptom.

    The `registry` parameter is injectable so tests can register dummy tools
    into a throwaway dict without polluting the process-wide `REGISTRY`.
    """
    if tool.name in registry:
        raise ValueError(
            f"MCP tool name collision: {tool.name!r} is already registered. "
            "Each tool must have a unique dotted name."
        )
    registry[tool.name] = tool
    return tool
