"""Tests for `meta.get` + the `tools/list` `_meta.degraded_services` extension (T-088).

These run over the REAL streamable-HTTP transport (via `mcp_runtime`); the
`_meta` extension lives on the transport layer. The shared aggregator
(`app.services.degraded_services.aggregate_degraded_services`) is monkeypatched
so both the tool and the `tools/list` extension are driven by one value —
proving they can't drift (contract lock-in).

⚠ T-089 changed the auth posture: a no-`Authorization` request to `/mcp/` now
returns `401 + WWW-Authenticate` (the OAuth-discovery trigger), so the WHOLE MCP
transport — `initialize` / `tools/list` / every tool, `meta.get` included —
requires an authenticated client. `meta.get` is still gated by NO SCOPE (any
authenticated caller can read capability info, see
`test_meta_get_requires_no_scope`), and the same data stays anonymously public
over REST at `/v1/meta`. So these tests now present a token; the bearer just has
to verify, the scope set is irrelevant for meta/list.
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
    token: str,
) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(
        url=MCP_URL, headers=headers, httpx_client_factory=factory
    ) as (read_stream, write_stream, _sid):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(name="meta.get", arguments={})


async def _list_tools(factory: Callable[..., httpx.AsyncClient], *, token: str) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(
        url=MCP_URL, headers=headers, httpx_client_factory=factory
    ) as (
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


async def test_meta_get_returns_payload(
    monkeypatch: pytest.MonkeyPatch,
    make_jwt_token: Callable[..., str],
) -> None:
    _patch_aggregator(monkeypatch, [])
    async with mcp_runtime() as factory:
        result = await _call_meta(factory, token=make_jwt_token())
    assert result.isError is False
    payload = result.structuredContent
    assert payload is not None
    assert payload["models"]["image"] == "gpt-image-2"
    assert len(payload["preset_motions"]) == 5
    assert payload["api_version"] == "v1"
    assert payload["degraded_services"] == []


async def test_meta_get_surfaces_degraded(
    monkeypatch: pytest.MonkeyPatch,
    make_jwt_token: Callable[..., str],
) -> None:
    _patch_aggregator(monkeypatch, _DEGRADED_SAMPLE)
    async with mcp_runtime() as factory:
        result = await _call_meta(factory, token=make_jwt_token())
    assert result.isError is False
    degraded = result.structuredContent["degraded_services"]
    assert len(degraded) == 1
    assert degraded[0]["service"] == "gpt-image-2"
    assert degraded[0]["reason"] == "circuit_open"


async def test_meta_get_requires_no_scope(
    _preload_jwks_cache: Any,
    monkeypatch: pytest.MonkeyPatch,
    make_oauth_token: Callable[..., str],
) -> None:
    """`meta.get` is reachable by any authenticated caller regardless of scope.

    Post-T-089 the MCP transport requires authentication (no-token → 401
    discovery), but `meta.get`'s scope bundle is empty — so a token holding only
    an unrelated scope (here `task:read`, none of meta's concern) still calls it
    successfully. This is the surviving half of T-088's "meta.get is public":
    gated by authentication, not authorization. (The same data is still
    anonymously public over REST `/v1/meta`.)
    """
    _patch_aggregator(monkeypatch, [])
    token = make_oauth_token(client_id="cf-test-agent", scopes=["task:read"], email=None)
    async with mcp_runtime() as factory:
        result = await _call_meta(factory, token=token)
    assert result.isError is False, (
        f"meta.get must need no scope; got error: "
        f"{result.content[0].text if result.content else result!r}"
    )


async def test_tools_list_meta_carries_degraded(
    monkeypatch: pytest.MonkeyPatch,
    make_jwt_token: Callable[..., str],
) -> None:
    """`tools/list` `_meta.degraded_services` mirrors `meta.get`, same source."""
    _patch_aggregator(monkeypatch, _DEGRADED_SAMPLE)
    token = make_jwt_token()
    async with mcp_runtime() as factory:
        listing = await _list_tools(factory, token=token)
        meta_result = await _call_meta(factory, token=token)

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
