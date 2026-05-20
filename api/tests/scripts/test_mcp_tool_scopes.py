"""Tests for `scripts/check_mcp_tool_scopes.py` (CI guardrail 2, T-081)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import check_mcp_tool_scopes as cts
import pytest
from pydantic import BaseModel

from app.mcp.registry import MCPTool

API_ROOT = Path(__file__).resolve().parents[2]


class _In(BaseModel):
    x: str


class _Out(BaseModel):
    y: str


async def _handler(x: str) -> _Out:
    return _Out(y=x)


def _tool(name: str, scopes: list[str], bundles: list[str]) -> MCPTool:
    return MCPTool(
        name=name,
        description="d",
        scopes=scopes,
        bundles=bundles,
        input_schema=_In,
        output_schema=_Out,
        handler=_handler,
    )


def test_bundleless_tool_passes_canonical() -> None:
    tools = {"hello.world": _tool("hello.world", ["character:read"], [])}
    assert cts.main([], tools=tools, endpoint_scopes={}) == 0


def test_non_canonical_scope_fails(capsys: pytest.CaptureFixture[str]) -> None:
    tools = {"bad.tool": _tool("bad.tool", ["character:teleport"], [])}
    rc = cts.main([], tools=tools, endpoint_scopes={})
    assert rc == 1
    assert "non-canonical scope" in capsys.readouterr().err


def test_scope_within_bundle_union_passes() -> None:
    tools = {"x.tool": _tool("x.tool", ["character:write"], ["POST /v1/x"])}
    endpoint_scopes = {("POST", "/v1/x"): frozenset({"character:write", "task:read"})}
    assert cts.main([], tools=tools, endpoint_scopes=endpoint_scopes) == 0


def test_scope_exceeding_bundle_union_fails(capsys: pytest.CaptureFixture[str]) -> None:
    tools = {"x.tool": _tool("x.tool", ["character:write"], ["POST /v1/x"])}
    endpoint_scopes = {("POST", "/v1/x"): frozenset({"character:read"})}
    rc = cts.main([], tools=tools, endpoint_scopes=endpoint_scopes)
    assert rc == 1
    err = capsys.readouterr().err
    assert "exceed the union" in err


def test_unknown_bundle_endpoint_fails(capsys: pytest.CaptureFixture[str]) -> None:
    tools = {"x.tool": _tool("x.tool", ["character:read"], ["POST /v1/missing"])}
    rc = cts.main([], tools=tools, endpoint_scopes={})
    assert rc == 1
    assert "does not match any scanned route" in capsys.readouterr().err


def test_real_registry_passes_via_subprocess() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/check_mcp_tool_scopes.py"],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "mcp-tool-scopes OK" in proc.stdout
