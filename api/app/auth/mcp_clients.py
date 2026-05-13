"""MCP server OAuth client allowlist (pre-registered, Figma mode).

Source of truth for which `client_id` values may obtain access tokens for the
MCP server surface (`/mcp/*`). The allowlist exists because OAuth 2.1 lets us
turn Dynamic Client Registration off; in Phase 1 we explicitly do not want
arbitrary self-registration (per `planning/agent-interface/open-questions.md`
Q7 sub-7c — Round 2 decision).

This module holds the *client → scope policy* mapping. Canonical scope
strings and the M2M narrow default themselves live in `app.auth.scopes`
(T-054 split) so the architecture-fitness test
`tests/arch/test_layering.py::test_oauth_scope_source_is_centralized` can
catch any sibling module hard-coding the literals. Enforcement (rejecting
unknown `client_id`, capping M2M tokens to the allowed scope set) lives in
`app.auth.oauth` and `app.api.deps`.
"""

from __future__ import annotations

from typing import Final, TypedDict

from app.auth.scopes import CANONICAL_SCOPES, M2M_DEFAULT_SCOPES

# Re-exports kept for the small set of callers (tests in `tests/arch/`) that
# historically imported these names from `mcp_clients`. New code should import
# from `app.auth.scopes` directly.
__all__ = [
    "ALLOWED_CLIENTS",
    "CANONICAL_SCOPES",
    "ClientPolicy",
    "M2M_DEFAULT_SCOPES",
    "get_allowed_scopes",
    "is_allowed_client",
]


class ClientPolicy(TypedDict):
    scopes: list[str] | None


# `_FULL_SCOPE_SET` is just a stable-ordered materialisation of
# CANONICAL_SCOPES — frozensets have no canonical order, so deriving the
# `cf-test-agent` scope list via `sorted(CANONICAL_SCOPES)` keeps the policy
# diff readable and avoids hard-coding the five strings here (which would
# violate the scope-source-centralization arch test).
_FULL_SCOPE_SET: Final[list[str]] = sorted(CANONICAL_SCOPES)


ALLOWED_CLIENTS: Final[dict[str, ClientPolicy]] = {
    # Delegated clients (Auth Code + PKCE). `scopes=None` means the access
    # token's scope set is decided at consent time by the human user — the
    # allowlist's job is only to recognize the `client_id`.
    "claude-code": {"scopes": None},
    "vs-code": {"scopes": None},
    "cursor": {"scopes": None},
    # M2M client (client_credentials). Explicit override granting the full
    # canonical scope set — used by CI smoke runs that exercise every
    # endpoint. Derived from CANONICAL_SCOPES (rather than redeclared) so
    # adding a sixth canonical scope automatically widens cf-test-agent's
    # coverage without a second edit here.
    "cf-test-agent": {"scopes": _FULL_SCOPE_SET},
}
# `character-foundry-spa` is NOT in this dict by design. The allowlist gates
# `/mcp/*` only (per Q7 sub-7c). The SPA is a human-driven web client that hits
# `/v1/*` with delegated user tokens; it doesn't reach the MCP server. T-054
# middleware on `/v1/*` does scope check against the token alone, no allowlist.


def is_allowed_client(client_id: str) -> bool:
    """Return True iff `client_id` is pre-registered for MCP access.

    The OAuth verifier short-circuits on this check before any scope
    comparison so an unknown client_id surfaces as `AUTH_CLIENT_NOT_ALLOWED`
    instead of a generic 401 — operators reviewing logs can tell the
    difference between "token forged / expired" and "we never sanctioned
    this client."
    """
    return client_id in ALLOWED_CLIENTS


def get_allowed_scopes(client_id: str) -> frozenset[str] | None:
    """Return the cap on scopes for `client_id`, or `None` if delegated.

    Return semantics:

      • `None` — delegated client (`scopes=None` in `ALLOWED_CLIENTS`). The
        token's `scope` claim is whatever consent the user granted; the
        allowlist doesn't add a second cap on top.
      • `frozenset[str]` — M2M (or explicitly-capped delegated) client. The
        token's `scope` claim must be a subset of this set; tokens whose
        scopes exceed it raise `AUTH_SCOPE_EXCEEDS_ALLOWLIST`.

    Callers MUST first verify `is_allowed_client(client_id)`; calling
    `get_allowed_scopes` on an unknown client raises KeyError rather than
    silently returning the narrow default. Treat "unknown client" and
    "delegated client" as distinct states.
    """
    policy = ALLOWED_CLIENTS[client_id]
    scopes = policy["scopes"]
    if scopes is None:
        return None
    return frozenset(scopes)
