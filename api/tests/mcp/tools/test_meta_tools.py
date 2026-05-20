"""Tests for `meta.get` + the `tools/list` `_meta.degraded_services` extension (T-088).

These run over the REAL streamable-HTTP transport (via `mcp_runtime`), since
`meta.get` is public (no DB / no token) and the `_meta` extension lives on the
transport layer. The shared aggregator
(`app.services.degraded_services.aggregate_degraded_services`) is monkeypatched
so both the tool and the `tools/list` extension are driven by one value —
proving they can't drift (contract lock-in).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.mcp.conftest import mcp_runtime

MCP_URL = "http://testserver/mcp/"

_DEGRADED_SAMPLE = [{"service": "gpt-image-2", "reason": "circuit_open", "retry_at": "soon"}]


async def _call_meta(
    factory: Callable[..., httpx.AsyncClient],
    *,
    token: str | None = None,
) -> Any:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with streamablehttp_client(
        url=MCP_URL, headers=headers, httpx_client_factory=factory
    ) as (read_stream, write_stream, _sid):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(name="meta.get", arguments={})


async def _list_tools(factory: Callable[..., httpx.AsyncClient]) -> Any:
    async with streamablehttp_client(url=MCP_URL, headers={}, httpx_client_factory=factory) as (
        read_stream,
        write_stream,
        _sid,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.list_tools()


def _patch_aggregator(monkeypatch: pytest.MonkeyPatch, value: list[dict[str, Any]]) -> None:
    async def _fake() -> list[dict[str, Any]]:
        return value

    monkeypatch.setattr("app.services.degraded_services.aggregate_degraded_services", _fake)


async def test_meta_get_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_aggregator(monkeypatch, [])
    async with mcp_runtime() as factory:
        result = await _call_meta(factory)
    assert result.isError is False
    payload = result.structuredContent
    assert payload is not None
    assert payload["models"]["image"] == "gpt-image-2"
    assert len(payload["preset_motions"]) == 5
    assert payload["api_version"] == "v1"
    assert payload["degraded_services"] == []


async def test_meta_get_surfaces_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_aggregator(monkeypatch, _DEGRADED_SAMPLE)
    async with mcp_runtime() as factory:
        result = await _call_meta(factory)
    assert result.isError is False
    degraded = result.structuredContent["degraded_services"]
    assert len(degraded) == 1
    assert degraded[0]["service"] == "gpt-image-2"
    assert degraded[0]["reason"] == "circuit_open"


async def test_meta_get_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Authorization header → `meta.get` still succeeds (public capability info)."""
    _patch_aggregator(monkeypatch, [])
    async with mcp_runtime() as factory:
        result = await _call_meta(factory, token=None)
    assert result.isError is False, (
        f"meta.get must be public; got error: "
        f"{result.content[0].text if result.content else result!r}"
    )


async def test_tools_list_meta_carries_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """`tools/list` `_meta.degraded_services` mirrors `meta.get`, same source."""
    _patch_aggregator(monkeypatch, _DEGRADED_SAMPLE)
    async with mcp_runtime() as factory:
        listing = await _list_tools(factory)
        meta_result = await _call_meta(factory)

    # tools/list response carries the degraded list on its _meta.
    assert listing.meta is not None, "tools/list response should carry _meta"
    list_degraded = listing.meta["degraded_services"]
    assert [d["service"] for d in list_degraded] == ["gpt-image-2"]

    # ...and it's the SAME source meta.get reads (contract lock-in).
    tool_degraded = meta_result.structuredContent["degraded_services"]
    assert [d["service"] for d in tool_degraded] == [d["service"] for d in list_degraded]

    # All 5 T-088 tools + the T-080 smoke tool are listed.
    names = {t.name for t in listing.tools}
    assert {"task.get", "task.list", "task.cancel", "prompt.preview", "meta.get"} <= names
