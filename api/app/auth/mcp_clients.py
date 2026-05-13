"""MCP server OAuth client allowlist (pre-registered, Figma mode).

Source of truth for which `client_id` values may obtain access tokens for the
MCP server surface (`/mcp/*`). The allowlist exists because OAuth 2.1 lets us
turn Dynamic Client Registration off; in Phase 1 we explicitly do not want
arbitrary self-registration (per `planning/agent-interface/open-questions.md`
Q7 sub-7c — Round 2 decision).

This module is **declarative data only**. Enforcement (rejecting unknown
`client_id`, capping M2M tokens to a client's allowed scopes, reading
`M2M_DEFAULT_SCOPES`) lives in T-054's middleware. No current call site
consumes `M2M_DEFAULT_SCOPES` — it is dormant policy data locked here so the
T-054 wiring is a pure data-to-import transformation rather than re-litigating
the constant. Keep it that way so the allowlist stays trivially reviewable.

Scope strings here MUST match the canonical 5 defined for Phase 1
(`planning/auth/open-questions.md` Q3 decision row); T-054 will additionally
introduce `app/auth/scopes.py` as the runtime canonical source and the
architecture fitness test in `api/tests/arch/test_layering.py` will then guard
against drift.
"""

from __future__ import annotations

from typing import Final, TypedDict

CANONICAL_SCOPES: Final[frozenset[str]] = frozenset(
    {
        "character:read",
        "character:write",
        "task:read",
        "task:cancel",
        "usage:read",
    }
)


# Narrow default per Q5 sub-5a: unknown / new M2M clients automatically get a
# write-but-not-read-usage capability set; clients needing more (e.g.
# `cf-test-agent`) override explicitly via `ALLOWED_CLIENTS`. Currently no
# consumer reads this — T-054 token middleware will wire it.
M2M_DEFAULT_SCOPES: Final[list[str]] = ["character:write", "task:read"]


class ClientPolicy(TypedDict):
    scopes: list[str] | None


ALLOWED_CLIENTS: Final[dict[str, ClientPolicy]] = {
    # Delegated clients (Auth Code + PKCE). `scopes=None` means the access
    # token's scope set is decided at consent time by the human user — the
    # allowlist's job is only to recognize the `client_id`.
    "claude-code": {"scopes": None},
    "vs-code": {"scopes": None},
    "cursor": {"scopes": None},
    # M2M client (client_credentials). Explicit override granting the full
    # canonical 5 — used by CI smoke runs that exercise every endpoint.
    "cf-test-agent": {
        "scopes": [
            "character:read",
            "character:write",
            "task:read",
            "task:cancel",
            "usage:read",
        ],
    },
}
# `character-foundry-spa` is NOT in this dict by design. The allowlist gates
# `/mcp/*` only (per Q7 sub-7c). The SPA is a human-driven web client that hits
# `/v1/*` with delegated user tokens; it doesn't reach the MCP server. T-054
# middleware on `/v1/*` does scope check against the token alone, no allowlist.
