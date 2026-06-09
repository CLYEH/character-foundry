"""OAuth 2.0 Protected Resource Metadata (RFC 9728) for the MCP server (T-089).

A human-driven MCP client (Claude Desktop / claude.ai connector / MCP Inspector)
that supports OAuth discovers how to authenticate to `/mcp/` in two steps:

  1. It hits `/mcp/` with no token, gets `401 + WWW-Authenticate: Bearer
     resource_metadata="<this endpoint>"` (the challenge is emitted by
     `app.mcp.auth.MCPAuthContextMiddleware` — see Decision 2 in the T-089 plan).
  2. It GETs the URL in `resource_metadata` — THIS endpoint — which returns the
     RFC 9728 document pointing at the Authentik authorization server + the
     scopes this resource accepts. The client then runs Auth Code + PKCE against
     Authentik (discovered via `<issuer>/.well-known/openid-configuration`),
     gets a delegated token, and retries the MCP request.

Why a dedicated Authentik app (`character-foundry-mcp`): Authentik runs in
per-provider issuer mode, so each application mints a distinct `iss`. Advertising
ONE authorization server (this one app's issuer) keeps RFC 9728 discovery clean —
the `iss` in the token the client gets back matches the issuer it discovered. All
human MCP clients share `client_id=character-foundry-mcp`; their individual
redirect_uris are registered on that single app (T-089 plan Decision 1).

This router is intentionally OUTSIDE `app/api/routes/` (it's transport discovery,
not a product `/v1/*` endpoint): it stays cohesive with the rest of `app/mcp/`
and the `check_scope_coverage.py` static scanner — which only walks
`app/api/routes/` — doesn't need a whitelist entry for a public, scope-less route.
"""

from __future__ import annotations

import os
from typing import Final

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from app.auth.scopes import CANONICAL_SCOPES

# The MCP server's canonical resource path (mounted by `app/main.py` at `/mcp`).
_MCP_RESOURCE_PATH: Final[str] = "/mcp"

# The dedicated Authentik application slug all human MCP clients authenticate
# through (T-089 plan Decision 1). Authentik's per-provider issuer for it is
# `<base>/oauth/application/o/<slug>/`. Fixed (not env-driven): a client's
# `client_id` and the advertised authorization server must agree, and that
# agreement is the whole point of the dedicated app.
_MCP_OAUTH_APP_SLUG: Final[str] = "character-foundry-mcp"

# RFC 9728 well-known path. The middleware's WWW-Authenticate header points here;
# kept as a module constant so `app.mcp.auth` references the same string and the
# header can't drift from the route.
PRM_WELL_KNOWN_PATH: Final[str] = "/.well-known/oauth-protected-resource"

# Optional operator override for the public base URL used to build the `resource`
# / `authorization_servers` / metadata URLs. Prefer this in prod (robust against
# Host-header spoofing); when unset we derive the base from the request, which
# auto-adapts to localhost / ngrok / prod since nginx forwards `Host` +
# `X-Forwarded-Proto` (see `infra/nginx/nginx.conf`).
_PUBLIC_BASE_URL_ENV: Final[str] = "MCP_PUBLIC_BASE_URL"


def public_base_url(*, host: str | None, forwarded_proto: str | None, scheme: str) -> str:
    """Compute the public origin (`scheme://host`, no trailing slash).

    Shared by this router AND `app.mcp.auth`'s 401 challenge so the metadata
    URL advertised in `WWW-Authenticate` is byte-identical to the one this
    endpoint serves. Resolution order:

      1. `MCP_PUBLIC_BASE_URL` env override (returned verbatim, slash-trimmed).
      2. `<X-Forwarded-Proto>://<Host>` derived from the request — nginx sets
         both (`proxy_set_header Host $host; X-Forwarded-Proto $scheme;`).

    `forwarded_proto` may be a comma list (`https, http`) when chained proxies
    append; take the first hop. Falls back to `localhost` only if no Host header
    is present at all (shouldn't happen behind nginx, but keeps the function
    total rather than returning a malformed `scheme://`).
    """
    override = os.environ.get(_PUBLIC_BASE_URL_ENV)
    if override:
        return override.rstrip("/")
    proto = (forwarded_proto or scheme or "http").split(",")[0].strip() or "http"
    resolved_host = (host or "").strip()
    if not resolved_host:
        resolved_host = "localhost"
    return f"{proto}://{resolved_host}"


def build_protected_resource_metadata(base_url: str) -> dict[str, object]:
    """Assemble the RFC 9728 Protected Resource Metadata document.

    `scopes_supported` comes from `CANONICAL_SCOPES` (sorted for a stable wire
    order) rather than literal strings — `tests/arch/test_layering.py
    ::test_oauth_scope_source_is_centralized` forbids re-typing the scope
    literals anywhere outside `app.auth.scopes`.
    """
    base = base_url.rstrip("/")
    # Delegated (human) MCP clients run Auth Code + PKCE; the resource server
    # maps the token to a backend user by its `email` claim
    # (app.auth.user_resolution.resolve_oauth_user_id), which Authentik only
    # emits when the OIDC identity scopes are granted. Advertise them alongside
    # the app scopes so a delegated client requests them — without this the
    # delegated token carries no email and user resolution fails closed with
    # AUTH_INVALID_TOKEN. (T-094: the T-089 delegated flow was never run E2E; CI
    # exercises only the M2M path, which resolves via a synthetic service-account
    # email and so never needed an `email` claim.) M2M clients ignore this list
    # and request only their capped app scopes; `character-foundry-mcp` is an
    # uncapped delegated client (mcp_clients.ALLOWED_CLIENTS) so the identity
    # scopes never trip the scope-cap check in verify_oauth_token.
    oidc_identity_scopes = {"openid", "email", "profile"}
    return {
        "resource": f"{base}{_MCP_RESOURCE_PATH}",
        "authorization_servers": [f"{base}/oauth/application/o/{_MCP_OAUTH_APP_SLUG}/"],
        "scopes_supported": sorted(set(CANONICAL_SCOPES) | oidc_identity_scopes),
        "bearer_methods_supported": ["header"],
    }


router = APIRouter(tags=["mcp-discovery"])


@router.get(PRM_WELL_KNOWN_PATH, include_in_schema=False)
@router.get(f"{PRM_WELL_KNOWN_PATH}/mcp", include_in_schema=False)
async def oauth_protected_resource(request: Request) -> JSONResponse:
    """Serve the PRM document at both the bare and `/mcp`-suffixed well-known path.

    RFC 9728 §3.1 lets a client derive the metadata URL by inserting the
    well-known segment before the resource path (`.../oauth-protected-resource/mcp`)
    OR using the URL from the `WWW-Authenticate` hint (the bare path). Serving
    both, with identical bodies, covers either client convention.

    Public (no auth dependency) — this is discovery metadata. A permissive CORS
    header lets browser-context MCP clients (e.g. MCP Inspector) read it
    cross-origin; the document carries no secrets.
    """
    base = public_base_url(
        host=request.headers.get("host"),
        forwarded_proto=request.headers.get("x-forwarded-proto"),
        scheme=request.url.scheme,
    )
    return JSONResponse(
        build_protected_resource_metadata(base),
        headers={"Access-Control-Allow-Origin": "*"},
    )
