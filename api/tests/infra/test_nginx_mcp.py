"""Static assertions on the nginx `/mcp/` reverse-proxy block (T-082).

The MCP streamable-HTTP path needs a long read timeout and unbuffered
streaming or i2v generation (30–120s) gets severed mid-stream. nginx isn't
exercised in the backend unit job, so this parses `infra/nginx/nginx.conf`
directly and pins the load-bearing directives — a future edit that drops the
180s timeout or re-enables buffering fails CI here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NGINX_CONF = Path(__file__).resolve().parents[3] / "infra" / "nginx" / "nginx.conf"


def _extract_location_block(conf: str, location: str) -> str:
    """Return the body of `location <location> { ... }` (brace-matched)."""
    marker = f"location {location} {{"
    start = conf.find(marker)
    assert start != -1, f"no `location {location}` block in nginx.conf"
    depth = 0
    body_start = start + len(marker) - 1  # at the opening brace
    for i in range(body_start, len(conf)):
        if conf[i] == "{":
            depth += 1
        elif conf[i] == "}":
            depth -= 1
            if depth == 0:
                return conf[body_start + 1 : i]
    raise AssertionError(f"unbalanced braces in `location {location}` block")


def _duration_to_seconds(value: str) -> int:
    """Parse an nginx time literal (`180s`, `3m`, `180`) to seconds."""
    m = re.fullmatch(r"(\d+)(ms|s|m|h)?", value)
    assert m, f"unparseable nginx duration: {value!r}"
    n = int(m.group(1))
    unit = m.group(2) or "s"
    return {"ms": n // 1000, "s": n, "m": n * 60, "h": n * 3600}[unit]


@pytest.fixture(scope="module")
def mcp_block() -> str:
    return _extract_location_block(NGINX_CONF.read_text(encoding="utf-8"), "/mcp/")


def test_mcp_block_exists(mcp_block: str) -> None:
    assert mcp_block.strip()


def test_proxy_pass_preserves_mcp_prefix(mcp_block: str) -> None:
    # No trailing slash → the `/mcp/` prefix is forwarded intact (the FastMCP
    # sub-app is mounted at `/mcp`). A trailing slash would strip it.
    assert "proxy_pass http://api_upstream;" in mcp_block
    assert "proxy_pass http://api_upstream/;" not in mcp_block


def test_read_timeout_at_least_180s(mcp_block: str) -> None:
    m = re.search(r"proxy_read_timeout\s+(\S+);", mcp_block)
    assert m, "no proxy_read_timeout directive"
    assert _duration_to_seconds(m.group(1)) >= 180


def test_send_timeout_at_least_180s(mcp_block: str) -> None:
    m = re.search(r"proxy_send_timeout\s+(\S+);", mcp_block)
    assert m, "no proxy_send_timeout directive"
    assert _duration_to_seconds(m.group(1)) >= 180


def test_buffering_disabled_for_streaming(mcp_block: str) -> None:
    assert re.search(r"proxy_buffering\s+off;", mcp_block)


def test_http_1_1_with_empty_connection(mcp_block: str) -> None:
    # HTTP/1.1 chunked transfer; empty Connection avoids `Connection: close`.
    # MCP is NOT WebSocket, so no Upgrade/Connection: upgrade headers.
    assert "proxy_http_version 1.1;" in mcp_block
    assert re.search(r'proxy_set_header\s+Connection\s+"";', mcp_block)
    assert "$connection_upgrade" not in mcp_block


def test_forwarded_headers(mcp_block: str) -> None:
    assert re.search(r"proxy_set_header\s+Host\s+\$host;", mcp_block)
    assert re.search(r"proxy_set_header\s+X-Real-IP\s+\$remote_addr;", mcp_block)
    assert re.search(r"proxy_set_header\s+X-Forwarded-Proto\s+\$scheme;", mcp_block)
    # Hardened: replace (not append) so a client can't spoof X-Forwarded-For.
    assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr;", mcp_block)
    assert "$proxy_add_x_forwarded_for" not in mcp_block
