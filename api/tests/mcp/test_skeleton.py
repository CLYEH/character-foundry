"""End-to-end smoke for the MCP server skeleton (T-080).

Five assertions, mapped to the ticket acceptance criteria:

  1. Legacy JWT path: `hello.world` returns 200 with the expected echo
     reply and progress notification.
  2. OAuth path (M2M client_credentials): same success behaviour.
  3. Missing token: `hello.world` returns a structured AgentError as a
     tool error (NOT HTTP 401).
  4. Insufficient scope: token has only `task:read`, missing
     `character:read` — same structured AgentError surface.
  5. Progress notification REALLY arrives client-side across streamable
     HTTP. This is the direct regression target for MCP Python SDK PR
     #2038; without that fix the notification routes to the wrong stream
     and the client-side `progress_callback` never fires.

All five run against the real FastAPI app via `httpx.ASGITransport` —
in-process but with the full streamable HTTP transport on the wire. We
deliberately do NOT use the FastMCP in-memory client; per ticket Notes
that would NOT reproduce the streamable-HTTP-specific bug PR #2038
fixed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.mcp.conftest import mcp_runtime

# Path matches `app/main.py::app.mount("/mcp", ...)` + FastMCP's
# `streamable_http_path="/"` configuration. The trailing slash is
# required: the inner Starlette routes the streamable HTTP endpoint at
# exactly `/`, and a missing slash would 404 / 307 (depending on Starlette
# version) — neither is what the JSON-RPC client expects.
MCP_URL = "http://testserver/mcp/"


async def _call_hello(
    factory: Callable[..., httpx.AsyncClient],
    *,
    token: str | None,
    arguments: dict[str, Any],
    capture_progress: bool = False,
) -> tuple[Any, list[tuple[float, float | None, str | None]]]:
    """Open a streamable HTTP session, call hello.world, return result + progress.

    Centralized so each test case differs only in the auth header and the
    progress assertion, not in the boilerplate of session setup. Returns
    the raw `CallToolResult` so tests can inspect both happy-path
    `structuredContent` and error-path `isError + content[0].text`.
    """
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    progress_events: list[tuple[float, float | None, str | None]] = []

    async def _progress_cb(
        progress: float,
        total: float | None,
        message: str | None,
    ) -> None:
        progress_events.append((progress, total, message))

    async with streamablehttp_client(
        url=MCP_URL,
        headers=headers,
        httpx_client_factory=factory,
    ) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                name="hello.world",
                arguments=arguments,
                progress_callback=_progress_cb if capture_progress else None,
            )
    return result, progress_events


def _assert_agent_error_payload(text: str, *, expected_code: str) -> dict[str, Any]:
    """The tool's error envelope is a JSON-serialised AgentError under
    `error.code`. Asserts both the JSON shape and the specific code so
    callers can tell `AUTH_MISSING_TOKEN` apart from `AUTH_INSUFFICIENT_SCOPE`
    instead of just "some auth error happened".

    FastMCP wraps any tool-raised exception's `str()` with the prefix
    `Error executing tool <name>: ` (see `mcp/server/fastmcp/tools/base.py
    :117`). The JSON payload still rides intact behind the prefix — we
    slice from the first `{` so the assertion stays robust against future
    prefix tweaks in the SDK.
    """
    brace_idx = text.find("{")
    assert brace_idx != -1, f"Expected JSON payload in tool error text, got {text!r}"
    parsed = json.loads(text[brace_idx:])
    assert "error" in parsed, f"Expected `error` key in tool result, got {parsed!r}"
    assert parsed["error"]["code"] == expected_code, (
        f"Expected error code {expected_code!r}, got {parsed['error']['code']!r}"
    )
    return parsed["error"]


# ---------------------------------------------------------------------------
# Happy path — legacy JWT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwt_token_hello_world_success(
    make_jwt_token: Callable[..., str],
) -> None:
    """JWT token (with no client_id) passes auth + scope and gets a reply.

    Per ticket §"Token / scope 整合": legacy JWTs SKIP the allowlist check
    (no `client_id` claim). The dual-stack guarantee is that they still
    see the full canonical scope set granted via `app.auth.scopes.
    CANONICAL_SCOPES`, so `character:read` passes.
    """
    token = make_jwt_token()

    async with mcp_runtime() as factory:
        result, _progress = await _call_hello(
            factory,
            token=token,
            arguments={"echo": "from-jwt"},
        )

    assert result.isError is False, (
        f"JWT call unexpectedly errored: {result.content[0].text if result.content else result!r}"
    )
    # The reply lands in both `structuredContent.reply` (because we
    # declared HelloOut as a pydantic schema) and as JSON in the text
    # content. Asserting structuredContent specifically — that's the
    # contract surface agents will read; the text fallback is for clients
    # that don't parse structured output.
    assert result.structuredContent is not None
    assert result.structuredContent["reply"] == "hello, jwt-user: from-jwt", (
        f"Got reply: {result.structuredContent['reply']!r}"
    )


# ---------------------------------------------------------------------------
# Happy path — Authentik OAuth (M2M)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_token_hello_world_success(
    _preload_jwks_cache: Any,
    make_oauth_token: Callable[..., str],
) -> None:
    """OAuth M2M token (cf-test-agent) reaches the tool with character:read.

    Mirrors the JWT happy path but routes through `_resolve_oauth` — same
    smoke tool, same client-side observable, different verification path
    inside the server. The reply uses `client_id` (not the JWT static
    "jwt-user") so the assertion doubles as a check that the OAuth path
    populated `MCPAuthContext.client_id` correctly.
    """
    token = make_oauth_token(
        client_id="cf-test-agent",
        scopes=["character:read"],
        email=None,  # M2M tokens have no email claim
    )

    async with mcp_runtime() as factory:
        result, _progress = await _call_hello(
            factory,
            token=token,
            arguments={"echo": "from-oauth"},
        )

    assert result.isError is False, (
        f"OAuth call unexpectedly errored: {result.content[0].text if result.content else result!r}"
    )
    assert result.structuredContent is not None
    assert result.structuredContent["reply"] == "hello, cf-test-agent: from-oauth"


# ---------------------------------------------------------------------------
# Error path — missing token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_token_surfaces_mcp_error() -> None:
    """No `Authorization` header → AUTH_MISSING_TOKEN as a TOOL error.

    Critical ticket guarantee (Note "MCP error vs HTTP status"): the
    transport response stays 200 and the failure is reported inside the
    JSON-RPC envelope as `CallToolResult.isError=True`. If the streamable
    HTTP layer ever 401'd here, `streamablehttp_client` would raise
    before `call_tool` returned and this assertion path would never run.
    """
    async with mcp_runtime() as factory:
        result, _progress = await _call_hello(
            factory,
            token=None,
            arguments={"echo": "anon"},
        )

    assert result.isError is True, "Missing token should surface as tool error, not silent success"
    assert result.content, "isError=True should carry at least one content block"
    text_blocks = [block for block in result.content if block.type == "text"]
    assert text_blocks, f"Expected text content, got {result.content!r}"
    _assert_agent_error_payload(
        text_blocks[0].text,
        expected_code="AUTH_MISSING_TOKEN",
    )


# ---------------------------------------------------------------------------
# Error path — insufficient scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_token_missing_scope_surfaces_mcp_error(
    _preload_jwks_cache: Any,
    make_oauth_token: Callable[..., str],
) -> None:
    """Token with only `task:read` lacks the required `character:read`.

    Confirms per-tool scope enforcement runs inside the tool handler and
    surfaces as an AgentError tool result rather than HTTP 403. Without
    this assertion the regression where someone wires a tool's scope
    declaration but forgets to call `require_mcp_scopes(...)` inside the
    handler would go undetected — T-081's CI guardrails make the
    declaration mandatory; this test makes the enforcement mandatory too.
    """
    token = make_oauth_token(
        client_id="cf-test-agent",
        scopes=["task:read"],  # NOT character:read
        email=None,
    )

    async with mcp_runtime() as factory:
        result, _progress = await _call_hello(
            factory,
            token=token,
            arguments={"echo": "should-not-arrive"},
        )

    assert result.isError is True, (
        "Token without character:read should fail closed at the tool layer"
    )
    text_blocks = [block for block in result.content if block.type == "text"]
    assert text_blocks
    _assert_agent_error_payload(
        text_blocks[0].text,
        expected_code="AUTH_INSUFFICIENT_SCOPE",
    )


# ---------------------------------------------------------------------------
# Verifier error code preserved through the tool layer (Codex round-1 P2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_token_unknown_client_id_preserves_verifier_code(
    _preload_jwks_cache: Any,
    make_oauth_token: Callable[..., str],
) -> None:
    """Unknown client_id should surface as AUTH_CLIENT_NOT_ALLOWED, not
    AUTH_MISSING_TOKEN.

    Codex round-1 P2 against PR #107 flagged that the earlier
    "collapse all verification failures to None" design dropped the
    specific verifier code (`AUTH_CLIENT_NOT_ALLOWED`,
    `AUTH_SCOPE_EXCEEDS_ALLOWLIST`, `AUTH_OAUTH_EXPIRED`, ...) on the
    way to the tool layer. After the discriminated-state refactor the
    code is preserved via `MCPAuthFailure(error=...)`. Without this
    test the regression returns silently — anyone debugging "why is my
    agent client failing?" would see a misleading "missing token"
    instead of the actionable "your client_id isn't in the allowlist".
    """
    token = make_oauth_token(
        client_id="unsanctioned-bot",  # NOT in ALLOWED_CLIENTS
        scopes=["character:read"],
        email=None,
    )

    async with mcp_runtime() as factory:
        result, _progress = await _call_hello(
            factory,
            token=token,
            arguments={"echo": "should-not-arrive"},
        )

    assert result.isError is True
    text_blocks = [block for block in result.content if block.type == "text"]
    assert text_blocks
    _assert_agent_error_payload(
        text_blocks[0].text,
        expected_code="AUTH_CLIENT_NOT_ALLOWED",
    )


# ---------------------------------------------------------------------------
# Progress notification round-trip — the regression target for PR #2038
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_notification_reaches_client(
    make_jwt_token: Callable[..., str],
) -> None:
    """Client-side progress callback fires at least once during the call.

    This is the smoke test that pinning `mcp>=1.27.0` actually buys
    something. PR #2038 fixed `ctx.report_progress` dropping
    `related_request_id` on the streamable HTTP transport — before that
    fix the notification was emitted to a separate, unobserved stream
    and the client-side callback never ran. We assert >= 1 call AND
    that the call carries the value we passed server-side (`progress=0.5,
    total=1.0, message="halfway"`). The double assertion catches both
    "no notification at all" (PR #2038 regression) and "notification
    came through with mangled payload" (a future regression in the
    transport encoding).
    """
    token = make_jwt_token()

    async with mcp_runtime() as factory:
        result, progress_events = await _call_hello(
            factory,
            token=token,
            arguments={"echo": "progress-test"},
            capture_progress=True,
        )

    assert result.isError is False
    assert len(progress_events) >= 1, (
        "Expected at least one `notifications/progress` event from hello.world. "
        "If this fails on a green CI, suspect the mcp SDK version regressed "
        "below PR #2038 (>=1.27.0)."
    )
    progress, total, message = progress_events[0]
    assert progress == pytest.approx(0.5), f"Got progress={progress!r}"
    assert total == pytest.approx(1.0), f"Got total={total!r}"
    assert message == "halfway", f"Got message={message!r}"


# ---------------------------------------------------------------------------
# Unknown / bad scope literals — guard against typos in tool declarations
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Malformed Authorization header → AUTH_INVALID_TOKEN (Codex round-2 P2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_authorization_header_surfaces_invalid_token() -> None:
    """`Basic ...` or `Bearer ` with empty token → AUTH_INVALID_TOKEN, not
    AUTH_MISSING_TOKEN.

    Codex round-2 P2 against PR #107 flagged that an `Authorization` header
    that's present but not a well-formed Bearer used to collapse into the
    same "no token" symptom as a missing header. That broke parity with
    `/v1/*`, where `_extract_bearer` in `app/api/deps.py` raises
    `auth_invalid_token` for the same shape. Without this assertion the
    fix could silently regress — `streamablehttp_client` initialises with
    headers we control, so we set a bad Bearer directly and look for the
    correct error code on the first tool call.

    We can't use `_call_hello` because it formats `f"Bearer {token}"`;
    here we need to set the raw header value. Drop down to the lower-level
    streamable_http client + httpx call.
    """
    bad_header = "Bearer "  # well-formed scheme, empty token

    async with mcp_runtime() as factory:
        async with streamablehttp_client(
            url=MCP_URL,
            headers={"Authorization": bad_header},
            httpx_client_factory=factory,
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    name="hello.world",
                    arguments={"echo": "garbage"},
                )

    assert result.isError is True
    text_blocks = [block for block in result.content if block.type == "text"]
    assert text_blocks
    _assert_agent_error_payload(
        text_blocks[0].text,
        expected_code="AUTH_INVALID_TOKEN",
    )


def test_require_mcp_scopes_rejects_unknown_scope() -> None:
    """`require_mcp_scopes("character:write_typo")` should fail loud.

    Lives here rather than in a separate unit file because it's the same
    cluster of behaviour: scope enforcement. Without this guard, a tool
    that declares a typo'd scope would silently lock everyone out at
    runtime — mirrors the parallel guard in `app.auth.scopes.require_scope`.
    """
    from app.mcp.auth import require_mcp_scopes

    with pytest.raises(ValueError, match="non-canonical scope"):
        require_mcp_scopes("character:write_typo")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Path normalization — /mcp and /mcp/ both work (Codex round-3 P1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_trailing_slash_path_works_without_redirect(
    make_jwt_token: Callable[..., str],
) -> None:
    """`POST /mcp` (no trailing slash) must reach the MCP handler directly,
    not 307-redirect to `/mcp/`.

    Codex round-3 P1 against PR #107 verified empirically that the bare
    `POST /mcp` returned 307. JSON-RPC / MCP clients vary in how they
    handle 307 on POST — some refuse silently, breaking initialise
    against a misconfigured base URL. `MCPPathNormalizationMiddleware`
    rewrites the scope's `path` in-place so both URLs hit the same
    handler. Without this assertion the redirect can sneak back in
    (e.g. someone removes the middleware while refactoring) and break
    real-world clients that the test harness's `streamablehttp_client`
    happens to tolerate.

    We use raw httpx + ASGI transport here (not `streamablehttp_client`
    + `ClientSession.initialize`) because the SDK normalises trailing
    slashes for us — we need to verify the WIRE-LEVEL behaviour at `/mcp`
    exactly, with `follow_redirects=False` so a 307 would be visible.
    """
    token = make_jwt_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "no-slash-smoke", "version": "0.1"},
        },
    }

    async with mcp_runtime() as factory:
        # `factory()` builds an httpx client bound to the FastAPI ASGI
        # via our test transport — same path real requests take, no
        # redirect-following so a 307 would surface in the assertion.
        async with factory() as client:
            client.follow_redirects = False
            no_slash_resp = await client.post("/mcp", json=body, headers=headers)
            with_slash_resp = await client.post("/mcp/", json=body, headers=headers)

    assert no_slash_resp.status_code != 307, (
        f"POST /mcp (no slash) should NOT redirect; got 307 → "
        f"{no_slash_resp.headers.get('location')!r}. "
        "MCPPathNormalizationMiddleware regressed — JSON-RPC clients that "
        "don't follow POST 307s will break."
    )
    assert no_slash_resp.status_code == 200, (
        f"POST /mcp should reach the MCP handler directly; got "
        f"{no_slash_resp.status_code}: {no_slash_resp.text[:200]!r}"
    )
    # Sanity: `/mcp/` was already known to work; both should now behave
    # identically. Confirms the rewrite isn't just hiding a regression
    # on the trailing-slash path.
    assert with_slash_resp.status_code == 200, (
        f"POST /mcp/ regressed: {with_slash_resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Legacy JWT — reject stale user rows (Codex round-3 P2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_jwt_with_deleted_user_surfaces_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
    make_jwt_token: Callable[..., str],
) -> None:
    """A validly-signed legacy JWT whose `sub` points at a deleted user
    must be rejected, mirroring `/v1/*`'s `get_current_user` behaviour.

    Codex round-3 P2 against PR #107 flagged that `resolve_mcp_token`'s
    JWT branch returned an authenticated `MCPAuthContext` with full
    canonical scopes WITHOUT checking the DB — so a token signed before
    the user was deleted would continue to authorise `/mcp/*` calls
    even though `/v1/*` would 401 the same token. The fix opens a
    short-lived AsyncSession and calls `db.get(User, user_id)`,
    returning `MCPAuthFailure(auth_invalid_token())` when the row is
    missing.

    Same mock pattern as `test_delegated_oauth_token_resolves_user_id`:
    patch the session factory so the test doesn't need a real DB, and
    stub `db.get(User, ...)` to return None (the "stale user" state).
    Asserting the failure path keeps the parity guarantee anchored —
    without it, a future refactor that drops the DB check on the JWT
    branch would silently regress the gap Codex flagged.
    """
    import contextlib

    from app.mcp.auth import MCPAuthFailure, resolve_mcp_token

    class _StaleSession:
        async def get(self, _cls: Any, _user_id: Any) -> None:
            # Simulates a user row that's been deleted since the token
            # was minted — `db.get` returns None for missing primary keys.
            return None

    def _stale_factory() -> Any:
        @contextlib.asynccontextmanager
        async def _ctx() -> Any:
            yield _StaleSession()

        return _ctx

    monkeypatch.setattr("app.mcp.auth.async_session_factory", _stale_factory)

    token = make_jwt_token()
    result = await resolve_mcp_token(token)

    assert isinstance(result, MCPAuthFailure), (
        f"Expected MCPAuthFailure for stale JWT user, got {type(result).__name__}"
    )
    assert result.error.error.code == "AUTH_INVALID_TOKEN"


# ---------------------------------------------------------------------------
# Delegated OAuth token → user_id resolution (Codex round-2 P1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegated_oauth_token_resolves_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegated OAuth tokens route through `resolve_oauth_user_id` and
    land a `user_id` on the resolved `MCPAuthContext`.

    Codex round-2 P1 against PR #107 flagged that the earlier
    "always `user_id=None` on the OAuth path" implementation broke
    delegated-user semantics — any future tool that scopes data to
    the calling user would see no user identity for human-driven
    requests through `claude-code` / `vs-code` / `cursor`. After the
    fix, M2M tokens still get `user_id=None` (sanctioned — no human
    behind them), but delegated tokens (`is_m2m=False`) resolve via
    the shared `app.auth.user_resolution.resolve_oauth_user_id`
    helper to a backend `User.id`.

    This is a focused unit test — patches the verifier + user
    resolution so the assertion isolates the routing decision
    (delegated → user-lookup branch) without spinning up a DB. The
    end-to-end DB path is already covered by
    `tests/auth/test_oauth_auto_provisioning.py` against `/v1/*`;
    that suite exercises the same `resolve_oauth_user_id` helper
    via `app.api.deps._resolve_oauth`.
    """
    import uuid

    from app.auth.oauth import OAuthClaims
    from app.mcp.auth import MCPAuthContext, resolve_mcp_token

    expected_user_id = uuid.uuid4()
    delegated_claims = OAuthClaims(
        sub="auth0|leo",
        client_id="claude-code",  # delegated client in ALLOWED_CLIENTS
        scopes=frozenset({"character:read"}),
        email="leo@example.com",
        name="Leo",
        is_m2m=False,
    )

    async def _fake_verify(token: str) -> OAuthClaims:
        return delegated_claims

    async def _fake_resolve_user_id(claims: OAuthClaims, db: Any) -> uuid.UUID:
        # Make sure the helper actually got the verified claims through.
        assert claims is delegated_claims
        return expected_user_id

    # We also need to stub the session factory — `resolve_mcp_token`
    # opens an AsyncSession before calling the (already-mocked) user
    # resolver, and the real factory requires `DATABASE_URL`. The fake
    # session is never used because `resolve_oauth_user_id` is patched
    # out, so a sentinel object is sufficient.
    import contextlib

    class _FakeSession:
        pass

    def _fake_factory() -> Any:
        @contextlib.asynccontextmanager
        async def _ctx() -> Any:
            yield _FakeSession()

        return _ctx

    # `is_authentik_token` reads the env-var-driven issuer set; force it
    # True so the OAuth branch fires regardless of unverified iss claim.
    monkeypatch.setattr("app.mcp.auth.is_authentik_token", lambda _: True)
    monkeypatch.setattr("app.mcp.auth.verify_oauth_token", _fake_verify)
    monkeypatch.setattr("app.mcp.auth.resolve_oauth_user_id", _fake_resolve_user_id)
    monkeypatch.setattr("app.mcp.auth.async_session_factory", _fake_factory)

    # The token bytes themselves are irrelevant because the verifier is
    # stubbed — the JWT-shape decode at the top of resolve_mcp_token only
    # checks that the string is parseable, not signed.
    import time

    import jwt as pyjwt

    fake_token = pyjwt.encode(
        {"iss": "any", "sub": "any", "exp": int(time.time()) + 60},
        "irrelevant-secret",
        algorithm="HS256",
    )

    result = await resolve_mcp_token(fake_token)

    assert isinstance(result, MCPAuthContext), (
        f"Expected MCPAuthContext for delegated success, got {type(result).__name__}"
    )
    assert result.user_id == expected_user_id
    assert result.client_id == "claude-code"
    assert result.is_m2m is False
    assert result.scopes == frozenset({"character:read"})
