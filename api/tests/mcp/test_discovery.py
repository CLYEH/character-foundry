"""MCP OAuth discovery — PRM endpoint + 401 discovery trigger (T-089).

Covers the two CI-checkable halves of the auto-login chain (the real end-to-end
auto-login against a live MCP client is a Manual E2E, AC #3):

  1. The RFC 9728 Protected Resource Metadata document served at
     `/.well-known/oauth-protected-resource` (and the `/mcp`-suffixed variant) —
     shape, scope source, base-URL derivation, and the `MCP_PUBLIC_BASE_URL`
     override.
  2. The discovery trigger: a no-`Authorization` request to `/mcp/` returns
     `401 + WWW-Authenticate: Bearer resource_metadata="<PRM URL>"`, pointing at
     the document from (1).

The "token present but bad still surfaces a 200 tool-error" half of Decision 2
is already pinned by the existing skeleton tests (malformed header / unknown
client / insufficient scope) — those stay green, proving the discovery 401 only
fires on a fully-absent header.
"""

from __future__ import annotations

import httpx
import pytest

from app.auth.scopes import CANONICAL_SCOPES
from tests.mcp.conftest import make_asgi_httpx_factory, mcp_runtime

PRM_PATH = "/.well-known/oauth-protected-resource"
MCP_OAUTH_APP_SLUG = "character-foundry-mcp"

# Base the in-process ASGI transport presents (httpx derives `Host` from this).
_TEST_BASE = "http://testserver"


def _new_client() -> httpx.AsyncClient:
    """An httpx client bound to the real FastAPI app via ASGI.

    The PRM route is a plain top-level FastAPI route (not under the `/mcp`
    mount), so it needs no MCP lifespan — a bare ASGI client suffices.
    """
    from app.main import app

    return make_asgi_httpx_factory(app)()


# ---------------------------------------------------------------------------
# PRM metadata document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prm_document_shape_and_scope_source() -> None:
    """The bare well-known path returns a valid RFC 9728 document.

    Asserts the four fields the discovering client reads, that `resource` is the
    `/mcp` server URI, that `authorization_servers` is the dedicated MCP app's
    per-provider issuer (so the token `iss` will match), and that
    `scopes_supported` is the centralised `CANONICAL_SCOPES` PLUS the OIDC
    identity scopes (`openid`/`email`/`profile`) a delegated client must request
    so the resource server can map the token to a backend user (T-094).
    """
    async with _new_client() as client:
        resp = await client.get(PRM_PATH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resource"] == f"{_TEST_BASE}/mcp"
    assert body["authorization_servers"] == [
        f"{_TEST_BASE}/oauth/application/o/{MCP_OAUTH_APP_SLUG}/"
    ]
    # App scopes PLUS the OIDC identity scopes: a delegated client must request
    # `email` so resolve_oauth_user_id can map the token to a backend user.
    # Without these advertised, the client never requests them and delegated
    # auth fails closed with AUTH_INVALID_TOKEN (T-094 — the bug the T-089
    # Manual E2E never caught because CI only runs the M2M path).
    assert body["scopes_supported"] == sorted(
        set(CANONICAL_SCOPES) | {"openid", "email", "profile"}
    )
    assert body["bearer_methods_supported"] == ["header"]
    # Public discovery doc — browser-context clients (MCP Inspector) read it
    # cross-origin.
    assert resp.headers.get("access-control-allow-origin") == "*"


@pytest.mark.asyncio
async def test_prm_document_served_at_mcp_suffixed_path() -> None:
    """The RFC 9728 path-insertion variant returns the identical document.

    Clients that derive the metadata URL by inserting the well-known segment
    before the `/mcp` resource path must get the same answer as clients that
    use the bare `WWW-Authenticate` hint.
    """
    async with _new_client() as client:
        bare = await client.get(PRM_PATH)
        suffixed = await client.get(f"{PRM_PATH}/mcp")

    assert suffixed.status_code == 200, suffixed.text
    assert suffixed.json() == bare.json()


@pytest.mark.asyncio
async def test_prm_public_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """`MCP_PUBLIC_BASE_URL` pins the advertised origin (prod posture)."""
    monkeypatch.setenv("MCP_PUBLIC_BASE_URL", "https://foundry.example.com")

    async with _new_client() as client:
        resp = await client.get(PRM_PATH)

    body = resp.json()
    assert body["resource"] == "https://foundry.example.com/mcp"
    assert body["authorization_servers"] == [
        f"https://foundry.example.com/oauth/application/o/{MCP_OAUTH_APP_SLUG}/"
    ]


@pytest.mark.asyncio
async def test_prm_derives_scheme_from_forwarded_proto() -> None:
    """With no override, the scheme comes from `X-Forwarded-Proto` (nginx sets it).

    Behind TLS-terminating nginx the ASGI request scheme is `http` but the
    public scheme is `https`; the PRM must advertise `https` so the client's
    redirect / issuer URLs are correct.
    """
    async with _new_client() as client:
        resp = await client.get(PRM_PATH, headers={"X-Forwarded-Proto": "https"})

    body = resp.json()
    assert body["resource"] == "https://testserver/mcp"


# ---------------------------------------------------------------------------
# Discovery trigger — 401 + WWW-Authenticate on a no-token /mcp/ request
# ---------------------------------------------------------------------------


def _initialize_body() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "discovery-smoke", "version": "0.1"},
        },
    }


@pytest.mark.asyncio
async def test_no_token_mcp_request_returns_discovery_challenge() -> None:
    """A no-`Authorization` POST to `/mcp/` returns 401 + WWW-Authenticate.

    This is the signal an OAuth-capable MCP client keys on to begin auto-login.
    The `resource_metadata` parameter must point at the PRM document this suite
    asserts above, and the body must still carry the structured
    `AUTH_MISSING_TOKEN` AgentError so non-OAuth clients / humans get a reason.
    Raw httpx (not the SDK client) so the 401 is observable wire-level —
    `ClientSession.initialize()` would raise on it before returning.
    """
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    async with mcp_runtime() as factory:
        async with factory() as client:
            resp = await client.post("/mcp/", json=_initialize_body(), headers=headers)

    assert resp.status_code == 401, f"expected 401 discovery trigger, got {resp.status_code}"
    www = resp.headers.get("www-authenticate")
    assert www is not None, "missing WWW-Authenticate header — OAuth clients won't discover"
    assert www.startswith("Bearer ")
    assert f'resource_metadata="{_TEST_BASE}{PRM_PATH}"' in www, www
    parsed = resp.json()
    assert parsed["error"]["code"] == "AUTH_MISSING_TOKEN", parsed


@pytest.mark.asyncio
async def test_discovery_challenge_honors_public_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The challenge's `resource_metadata` URL respects `MCP_PUBLIC_BASE_URL`.

    Guards that the 401 header and the PRM endpoint derive their base the same
    way — a client following the header must land on a reachable document.
    """
    monkeypatch.setenv("MCP_PUBLIC_BASE_URL", "https://foundry.example.com")
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    async with mcp_runtime() as factory:
        async with factory() as client:
            resp = await client.post("/mcp/", json=_initialize_body(), headers=headers)

    assert resp.status_code == 401
    www = resp.headers.get("www-authenticate", "")
    assert f'resource_metadata="https://foundry.example.com{PRM_PATH}"' in www, www
