"""Unit tests for the MCP tool registry (T-081).

Covers the registry mechanism itself (register / lookup / collision) using an
injected throwaway dict so the process-wide `REGISTRY` is never mutated, plus
one integration assertion that importing `app.mcp.tools` auto-discovers the
`hello.world` smoke tool into the real `REGISTRY`.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.mcp.registry import MCPTool, register


class _DummyIn(BaseModel):
    x: str


class _DummyOut(BaseModel):
    y: str


async def _dummy_handler(x: str) -> _DummyOut:
    return _DummyOut(y=x)


def _make_tool(name: str = "dummy.tool") -> MCPTool:
    return MCPTool(
        name=name,
        description="dummy",
        scopes=["character:read"],
        bundles=[],
        input_schema=_DummyIn,
        output_schema=_DummyOut,
        handler=_dummy_handler,
    )


def test_register_adds_and_is_lookupable() -> None:
    registry: dict[str, MCPTool] = {}
    tool = _make_tool()
    returned = register(tool, registry=registry)

    assert returned is tool, "register() should return the tool for `X = register(...)` use"
    assert registry["dummy.tool"] is tool


def test_register_duplicate_name_raises() -> None:
    registry: dict[str, MCPTool] = {}
    register(_make_tool(), registry=registry)
    with pytest.raises(ValueError, match="name collision"):
        register(_make_tool(), registry=registry)


def test_register_distinct_names_coexist() -> None:
    registry: dict[str, MCPTool] = {}
    register(_make_tool("a.one"), registry=registry)
    register(_make_tool("a.two"), registry=registry)
    assert set(registry) == {"a.one", "a.two"}


def test_hello_world_is_auto_discovered() -> None:
    # Importing the tools package runs pkgutil discovery → hello.py's
    # module-level register() call populates the real REGISTRY.
    import app.mcp.tools  # noqa: F401  (imported for the discovery side effect)
    from app.mcp.registry import REGISTRY

    assert "hello.world" in REGISTRY
    hello = REGISTRY["hello.world"]
    assert hello.scopes == ["character:read"]
    assert hello.bundles == []
    assert hello.handler.__name__ == "hello_world"
