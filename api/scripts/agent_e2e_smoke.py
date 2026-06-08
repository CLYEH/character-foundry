#!/usr/bin/env python3
"""External agent E2E smoke (T-091, Sprint 3.5c) — the M3.5 ship gate.

Acts as a REAL external, headless M2M agent. It reads ONLY OAuth config + the
MCP tool schema (never this repo's REST docs or app internals), mints an access
token via the OAuth2 `client_credentials` grant against Authentik, opens a
streamable-HTTP MCP session through nginx, and drives the M3 flow end-to-end:

    login (client_credentials) → character.create → alias.add → motion.generate

This proves `planning/agent-interface/scope.md` §1: an agent that only read the
OAuth config + tool schema can run the whole M3-scope flow without REST docs.

Run against a live stack (the e2e docker-compose topology, AI_STUB_MODE=true):

    python api/scripts/agent_e2e_smoke.py --base-url http://localhost

Exit 0 on success; any failed step prints a diagnostic to stderr and exits
non-zero so CI red-gates. Imports ONLY the public `mcp` client SDK + `httpx` +
stdlib — deliberately NO `app.*` import (a grep guard enforces this), because
the whole point is to prove the agent surface works without privileged access.

Token / MCP host notes:
  • The token endpoint and `/mcp/` are reached through nginx on the public
    origin (default `http://localhost`), so the `Host` header is `localhost`
    — which the MCP transport-security allowlist admits (T-090). Hitting the
    in-container `http://nginx/mcp/` would send `Host: nginx` and 421.
  • `client_secret` is cf-test-agent's provider secret (Authentik "Option 3"
    machine-to-machine). In CI it's the throwaway seeded in
    `infra/authentik/blueprints/cf-e2e-bootstrap.yaml`; override via
    `CF_TEST_AGENT_CLIENT_SECRET` for any other deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import os
import sys
import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent

# The 5 canonical app scopes (must stay ⊆ cf-test-agent's allowlist cap, else
# the token is rejected with AUTH_SCOPE_EXCEEDS_ALLOWLIST). Requesting exactly
# these — NOT openid/profile/email — keeps the token's `scope` claim within the
# cap.
SCOPES = ("character:read", "character:write", "task:read", "task:cancel", "usage:read")

# CI-only throwaway, identical to the literal seeded in
# infra/authentik/blueprints/cf-e2e-bootstrap.yaml. NOT a real secret — prod
# overrides via CF_TEST_AGENT_CLIENT_SECRET.
_DEFAULT_CI_SECRET = "cf-e2e-test-agent-secret-not-for-prod"  # noqa: S105

# Required tools the agent must discover from `tools/list` alone.
_REQUIRED_TOOLS = {"character.create", "alias.add", "motion.generate", "task.get"}

# character.create blocks server-side (poll loop ≤170s under the nginx /mcp
# read timeout); give the client read budget a little above that.
_CALL_READ_TIMEOUT = timedelta(seconds=210)
# Per-task poll budget for the async-submit tools (alias.add / motion.generate).
_TASK_POLL_DEADLINE_S = 200.0
_TASK_POLL_INTERVAL_S = 2.0


class SmokeError(RuntimeError):
    """A smoke-step failure. Carries a human diagnostic; main() maps it to a
    non-zero exit so CI fails loud."""


def log(message: str) -> None:
    print(f"[agent-smoke] {message}", flush=True)


# ---------------------------------------------------------------------------
# OAuth client_credentials token
# ---------------------------------------------------------------------------


async def fetch_m2m_token(
    base_url: str,
    client_id: str,
    client_secret: str,
    *,
    attempts: int = 6,
    delay_s: float = 3.0,
) -> str:
    """Mint an M2M access token via client_credentials, with bounded retries.

    Retries absorb blueprint-apply lag (the cf-test-agent provider may land a
    few seconds after the stack reports healthy). A 4xx is retried too — a
    transient `invalid_client` can occur before the provider row exists — but
    only within the small attempt budget, after which the last response body is
    surfaced so a real misconfig is debuggable.
    """
    token_url = base_url.rstrip("/") + "/oauth/application/o/token/"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": " ".join(SCOPES),
    }
    last_detail = "no attempt made"
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(1, attempts + 1):
            try:
                resp = await client.post(token_url, data=data)
            except httpx.HTTPError as exc:
                last_detail = f"transport error: {exc}"
            else:
                if resp.status_code == 200:
                    token = resp.json().get("access_token")
                    if token:
                        return str(token)
                    last_detail = f"200 but no access_token: {resp.text[:300]}"
                else:
                    last_detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if attempt < attempts:
                log(f"token attempt {attempt}/{attempts} not ready ({last_detail}); retrying")
                await asyncio.sleep(delay_s)
    raise SmokeError(f"could not obtain M2M token from {token_url}: {last_detail}")


def _log_token_scopes(token: str) -> None:
    """Decode the (unverified) JWT payload just to log the granted scopes —
    a fast sanity signal that the provider emitted the 5 app scopes."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, binascii.Error, ValueError, json.JSONDecodeError):
        log("token acquired (could not decode payload for scope logging — continuing)")
        return
    log(f"token acquired — scope={payload.get('scope')!r} aud={payload.get('aud')!r}")


# ---------------------------------------------------------------------------
# MCP tool calls
# ---------------------------------------------------------------------------


async def _call(session: ClientSession, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Call an MCP tool; return its structured output or raise SmokeError.

    An `isError` result carries the AgentError envelope JSON in the first text
    block — surfaced verbatim so a failure is debuggable (e.g. a phase-tagged
    `{error, phase}` from a packaged tool)."""
    result = await session.call_tool(
        name=name,
        arguments=dict(arguments),
        read_timeout_seconds=_CALL_READ_TIMEOUT,
    )
    if result.isError:
        text = next((b.text for b in result.content if isinstance(b, TextContent)), "")
        raise SmokeError(f"tool {name} returned isError: {text or result.content!r}")
    structured = result.structuredContent
    if not isinstance(structured, dict):
        raise SmokeError(f"tool {name} returned no structuredContent (got {structured!r})")
    return structured


async def _poll_task(session: ClientSession, task_id: str, label: str) -> dict[str, Any]:
    """Poll task.get until the task reaches a terminal state.

    Returns the completed task dict; raises on failed/cancelled or timeout.
    This is the async-submit + poll-by-task-id contract (T-087): alias.add and
    motion.generate return a handle, the work runs in the arq worker, and the
    agent re-queries with the id it holds."""
    elapsed = 0.0
    last_status = "<none>"
    while True:
        task: dict[str, Any] = (await _call(session, "task.get", {"task_id": task_id}))["task"]
        last_status = task.get("status", "<missing>")
        if last_status == "completed":
            return task
        if last_status in ("failed", "cancelled"):
            raise SmokeError(
                f"{label}: task {task_id} ended status={last_status} error={task.get('error')}"
            )
        if elapsed >= _TASK_POLL_DEADLINE_S:
            raise SmokeError(
                f"{label}: task {task_id} not terminal after {int(_TASK_POLL_DEADLINE_S)}s "
                f"(last status={last_status})"
            )
        await asyncio.sleep(_TASK_POLL_INTERVAL_S)
        elapsed += _TASK_POLL_INTERVAL_S


async def run_smoke(base_url: str, token: str) -> None:
    """Open an MCP session and run the full M3 flow end-to-end."""
    mcp_url = base_url.rstrip("/") + "/mcp/"
    headers = {"Authorization": f"Bearer {token}"}
    suffix = uuid.uuid4().hex[:8]

    async with streamablehttp_client(url=mcp_url, headers=headers) as (read, write, _get_sid):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # tools/list — the agent discovers what it can do from schema alone.
            tool_names = {tool.name for tool in (await session.list_tools()).tools}
            missing = _REQUIRED_TOOLS - tool_names
            if missing:
                raise SmokeError(f"tools/list missing required tool(s) {sorted(missing)}")
            log(f"tools/list OK — {len(tool_names)} tools advertised, all required present")

            # 1. character.create (template mode; stub AI) — blocks, returns Base.
            created = await _call(
                session,
                "character.create",
                {
                    "name": f"E2E-T091-{suffix}",
                    "input_mode": "template",
                    "menu_selections": {"gender": "female"},
                    "freeform_note": "agent e2e smoke",
                },
            )
            character_id = created["character"]["id"]
            base_id = created["base"]["id"]
            log(f"character.create OK — character={character_id} base={base_id}")

            # 2. alias.add (text mode) — async submit + poll.
            alias_handle = await _call(
                session,
                "alias.add",
                {
                    "character_id": character_id,
                    "name": f"E2E-T091-alias-{suffix}",
                    "input_mode": "text",
                    "freeform_note": "紅色斗篷版本",
                },
            )
            await _poll_task(session, alias_handle["task_id"], "alias.add")
            log(f"alias.add OK — alias={alias_handle['alias_id']}")

            # 3. motion.generate (Base, preset_wave) — async submit + poll.
            motion_handle = await _call(
                session,
                "motion.generate",
                {
                    "target_type": "base",
                    "target_id": base_id,
                    "motion_type": "preset_wave",
                    "name": "招手",
                },
            )
            await _poll_task(session, motion_handle["task_id"], "motion.generate")
            log(f"motion.generate OK — motion={motion_handle['motion_id']}")

    log("SMOKE PASSED — external M2M agent ran login → character → base → alias → motion")


async def _run(base_url: str, client_id: str, client_secret: str) -> None:
    log(f"acquiring M2M token (client_id={client_id}) from {base_url}")
    token = await fetch_m2m_token(base_url, client_id, client_secret)
    _log_token_scopes(token)
    await run_smoke(base_url, token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_e2e_smoke", description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CF_SMOKE_BASE_URL", "http://localhost"),
        help="Public origin of the stack (nginx). Default: http://localhost",
    )
    parser.add_argument(
        "--client-id",
        default="cf-test-agent",
        help="M2M OAuth client_id (must be a sanctioned service-account client).",
    )
    args = parser.parse_args(argv)
    client_secret = os.environ.get("CF_TEST_AGENT_CLIENT_SECRET", _DEFAULT_CI_SECRET)

    try:
        asyncio.run(_run(args.base_url, args.client_id, client_secret))
    except SmokeError as exc:
        print(f"[agent-smoke] SMOKE FAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
