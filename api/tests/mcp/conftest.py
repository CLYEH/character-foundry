"""Test infrastructure for `tests/mcp/*` (T-080).

Three pieces:

  • An ASGI test harness that runs `app.main:app` in-process — no real
    network, but the streamable HTTP transport still travels through
    `httpx.ASGITransport`, so PR #2038's progress-notification fix is
    exercised end-to-end (the smoke test would silently green on an
    in-memory transport, defeating the purpose of pinning the SDK).
  • An OAuth helper set re-exported from `tests/auth/conftest.py`. The
    cross-package import is the simplest way to share the synthetic
    Authentik JWT factory without duplicating the RSA keypair / JWKS
    document setup; the alternative — lifting those fixtures up to
    `tests/conftest.py` — would touch a file the existing 100+ auth
    tests depend on, and the gain isn't worth that blast radius for
    T-080 alone.
  • A `make_jwt_token` mint helper for the legacy HS256 path so the
    skeleton suite can assert dual-stack works without spinning up the
    full DB / user-row fixtures of `tests/auth/conftest.py::seeded_user`.
    The MCP JWT resolver returns just `user_id` from `sub`; no `User`
    row is touched, so synthetic UUIDs are fine.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

# Re-export OAuth fixtures from tests/auth so pytest discovers them when
# resolving fixture references in `test_skeleton.py`. The leading
# underscore on `_rsa_keypair` / `_private_key_pem` / `_jwks_document` /
# `_oauth_env` / `_preload_jwks_cache` is preserved — they are
# implementation-detail dependencies of `make_oauth_token`, not surface
# fixtures tests should request directly.
from tests.auth.conftest import (  # noqa: F401  (fixtures are used by pytest, not by import)
    OAUTH_TEST_AUDIENCE,
    OAUTH_TEST_ISSUER,
    OAUTH_TEST_JWKS_URI,
    OAUTH_TEST_KID,
    _jwks_document,
    _oauth_env,
    _preload_jwks_cache,
    _private_key_pem,
    _rsa_keypair,
    make_oauth_token,
)

JWT_SECRET = "test-mcp-jwt-secret-dont-use-in-prod"


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin env vars every MCP test depends on.

    `autouse` because every test in this package — even the OAuth-path
    ones — imports `app.main`, which transitively pulls in
    `app.auth.jwt` (via the auth router). Without the env var set, the
    first attempt to mint or verify a JWT raises `RuntimeError: JWT_SECRET
    is not set`. Pinning a fixed dummy value avoids leaking the actual
    `JWT_SECRET` if the developer happens to have one in their shell.

    Also widens `MCP_ALLOWED_HOSTS` so the in-process `httpx.ASGITransport`
    (which sends `Host: testserver`) survives FastMCP's DNS-rebinding
    protection. The production default (loopback only) would 421 every
    test request, masking real assertions behind a transport error.
    """
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv(
        "MCP_ALLOWED_HOSTS",
        "testserver,127.0.0.1:*,localhost:*,[::1]:*",
    )
    monkeypatch.setenv(
        "MCP_ALLOWED_ORIGINS",
        "http://testserver,http://127.0.0.1:*,http://localhost:*,http://[::1]:*",
    )

    # Intentionally do NOT reset `app.mcp.app._mcp_server` here. The mount
    # in `app.main` happens at first import and pins one FastMCP instance;
    # resetting the cache would hand subsequent tests a DIFFERENT FastMCP
    # whose `session_manager` we'd then enter, while `app.main`'s mounted
    # ASGI app still references the original — requests would hit a
    # session manager that was never started. None of the per-test
    # behaviour we care about (JWKS env, OAuth scopes, JWT secret) is
    # baked into FastMCP at construction time; everything is read at
    # request dispatch.


@pytest.fixture
def make_jwt_token() -> Callable[..., str]:
    """Mint a legacy HS256 access token without touching the DB.

    The MCP server's JWT-resolution path (`app.mcp.auth.resolve_mcp_token`)
    only verifies the signature and reads `sub` — no `User` row lookup
    happens (that's a /v1/* concern via `get_current_user`). So synthetic
    UUIDs are sufficient; the helper deliberately does NOT insert into
    `users`. If a future test needs a real row, depend on
    `tests/auth/conftest.py::seeded_user` instead.
    """
    from app.auth.jwt import sign_access_token

    def _make(
        *,
        user_id: uuid.UUID | None = None,
        team_id: uuid.UUID | None = None,
    ) -> str:
        token, _ttl = sign_access_token(
            user_id=user_id or uuid.uuid4(),
            team_id=team_id or uuid.uuid4(),
        )
        return token

    return _make


@contextlib.asynccontextmanager
async def mcp_runtime() -> AsyncIterator[Callable[..., httpx.AsyncClient]]:
    """Spin up the MCP server lifespan and return an httpx ASGI factory.

    Tests use this as `async with mcp_runtime() as factory: ...` rather
    than receiving the factory through an async generator fixture.
    pytest-asyncio runs fixture setup and teardown in different tasks
    under some configurations, which breaks anyio's cancel scope inside
    `session_manager.run()` (RuntimeError on exit: "Attempted to exit
    cancel scope in a different task than it was entered in"). Driving
    the context inside the test body keeps `__aenter__` and `__aexit__`
    on the same task.

    Each entry triggers a FRESH FastMCP build via the dispatcher pattern
    documented in `app/mcp/app.py` — necessary because
    `StreamableHTTPSessionManager.run()` is single-use per FastMCP
    instance. The dispatcher swap keeps `app.main`'s mount stable across
    rebuilds.
    """
    # Lazy import so env vars set by `_mcp_env` are visible when the
    # FastMCP / auth modules read them at construction time.
    from app.main import app
    from app.mcp.app import mcp_lifespan

    async with mcp_lifespan():
        yield make_asgi_httpx_factory(app)


def make_asgi_httpx_factory(app: Any) -> Callable[..., httpx.AsyncClient]:
    """Return a `streamablehttp_client`-compatible httpx client factory.

    `streamablehttp_client` calls its `httpx_client_factory` with kwargs
    `(headers, timeout, auth)` — see `mcp.shared._httpx_utils.McpHttpClientFactory`.
    We wrap `httpx.AsyncClient` against `ASGITransport(app=...)` so the
    real `app/main.py` request pipeline (auth middleware, FastMCP routes,
    progress streaming) runs in-process without binding a port. The
    `base_url` matches the URL passed to `streamablehttp_client` so the
    request resolves to the FastAPI mount.
    """
    transport = httpx.ASGITransport(app=app)

    def _factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=headers or {},
            # Generous timeout — ASGITransport runs synchronously inside
            # the event loop; the smoke tool only sleeps 400ms total, but
            # we don't want flake from a tight default if the harness is
            # warmed up cold.
            timeout=timeout or httpx.Timeout(30.0, read=300.0),
            auth=auth,
            # ASGITransport doesn't follow redirects (there shouldn't be
            # any here), but stay explicit so a future redirect doesn't
            # get silently dropped.
            follow_redirects=False,
        )

    return _factory
