"""Static assertions on the nginx `/api/` reverse-proxy block (T-072).

The `/api/` block forwards browser/agent traffic to the FastAPI app. Unlike
`/storage/` and `/mcp/`, this block strips its prefix via a trailing slash on
`proxy_pass http://api_upstream/;` — that rewrite is what makes top-level
routes like `/health` reachable as `/api/health`. A future edit that drops
the trailing slash would silently break every `/api/v1/...` and `/api/health`
caller (the api app has no `/api`-prefixed routes), so this test pins it
alongside the forwarded-headers contract. Mirrors `test_nginx_mcp.py` for the
opposite-prefix-handling case.
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


@pytest.fixture(scope="module")
def api_block() -> str:
    return _extract_location_block(NGINX_CONF.read_text(encoding="utf-8"), "/api/")


def test_api_block_exists(api_block: str) -> None:
    assert api_block.strip()


def test_proxy_pass_strips_api_prefix(api_block: str) -> None:
    # Trailing slash → nginx rewrites `/api/<path>` to `/<path>` on the way
    # upstream. Without it, the api app sees `/api/...` and 404s (no FastAPI
    # route is mounted under `/api`). This is the opposite choice from the
    # `/storage/` and `/mcp/` blocks, which preserve their prefix.
    assert "proxy_pass http://api_upstream/;" in api_block
    assert re.search(r"proxy_pass\s+http://api_upstream\s*;", api_block) is None


def test_forwarded_headers(api_block: str) -> None:
    assert re.search(r"proxy_set_header\s+Host\s+\$host;", api_block)
    assert re.search(r"proxy_set_header\s+X-Real-IP\s+\$remote_addr;", api_block)
    assert re.search(r"proxy_set_header\s+X-Forwarded-Proto\s+\$scheme;", api_block)
    assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\S+;", api_block)
