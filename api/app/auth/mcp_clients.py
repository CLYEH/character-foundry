"""Universal OAuth client allowlist (pre-registered, Figma mode).

Source of truth for which `client_id` values may present access tokens to
either the human-user surface (`/v1/*`) or the MCP server surface
(`/mcp/*`). The allowlist exists because OAuth 2.1 lets us turn Dynamic
Client Registration off; in Phase 1 we explicitly do not want arbitrary
self-registration (per `planning/agent-interface/open-questions.md`
Q7 sub-7c — Round 2 decision).

Initially T-053 scoped the allowlist to `/mcp/*` only — but T-054's
`get_current_user` runs the same OAuth verifier on `/v1/*`, so the SPA
client (`character-foundry-spa`) belongs in the allowlist too. Without
it, T-056's "Sign in with Google" flow would 403 every request with
`AUTH_CLIENT_NOT_ALLOWED` (Codex round-4 P1).

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
    "M2M_SERVICE_ACCOUNT_CLIENTS",
    "get_allowed_scopes",
    "is_allowed_client",
    "is_m2m_service_account_client",
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
    # SPA — human-user OAuth login (T-056). `scopes=None` because the human
    # consent flow decides the actual scope set; the allowlist's job here is
    # to recognize the client_id, not to cap it. Added in T-054 round-4
    # after Codex flagged that `/v1/*` OAuth login would otherwise 403 with
    # AUTH_CLIENT_NOT_ALLOWED — the docstring originally over-narrowed the
    # allowlist to `/mcp/*`.
    "character-foundry-spa": {"scopes": None},
    # Dedicated MCP OAuth client (T-089). The single Authentik application all
    # human-driven MCP clients (Claude Desktop / claude.ai connector / MCP
    # Inspector) authenticate through — they all present `client_id=
    # character-foundry-mcp` and run Auth Code + PKCE after discovering the
    # server via RFC 9728 Protected Resource Metadata (`app/mcp/discovery.py`).
    # `scopes=None` (delegated): the human's consent decides the scope set; the
    # allowlist only recognizes the client_id. Its issuer is the one advertised
    # in the PRM's `authorization_servers`, so the discovered issuer matches the
    # token's `iss`.
    "character-foundry-mcp": {"scopes": None},
    # Delegated clients (Auth Code + PKCE). `scopes=None` means the access
    # token's scope set is decided at consent time by the human user — the
    # allowlist's job is only to recognize the `client_id`. Pre-registered but
    # NOT advertised in the PRM (T-089 advertises only `character-foundry-mcp`);
    # they remain accepted if a client is manually configured with one.
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


# M2M clients sanctioned to act as service-account principals (T-092). A
# client_credentials token from one of these resolves to a provisioned backend
# service-account `User` on `/mcp/*`, so the headless agent OWNS the characters
# / aliases / motions it creates — the industry-standard machine-principal model
# (Auth0 `<client_id>@clients`, GitHub App bot user, Authentik's auto service
# account). M2M clients NOT listed here keep `user_id=None` and stay read-only on
# user-owned resources (the T-084/85/86 default) — fail-closed, so registering a
# new M2M client can't silently grant it resource ownership.
#
# `is_m2m` stays True for these tokens, so `/v1/*` still rejects them via
# `auth_m2m_wrong_surface`; the service identity is confined to `/mcp/*`. Every
# entry MUST also be an M2M (scope-capped) client in ALLOWED_CLIENTS — a guard
# test (`tests/auth/test_m2m_service_account.py`) pins that invariant.
#
# ⚠ BLAST RADIUS before adding a client here: the service account is provisioned
# into the `default` team (Phase-1 single team, DECISIONS §6 B5) — the SAME team
# as every human user. Team character visibility is team-scoped, not owner-scoped
# (`character_service.list_characters` with `owner_id=None`), so a sanctioned
# agent holding `character:read` can list / read EVERY human user's characters in
# that team, not only the ones it created. Adding a client grants team-wide read,
# not just self-owned write. Per-agent team isolation is a Phase-2 concern.
M2M_SERVICE_ACCOUNT_CLIENTS: Final[frozenset[str]] = frozenset({"cf-test-agent"})


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


def is_m2m_service_account_client(client_id: str) -> bool:
    """Return True iff an M2M token from `client_id` should own its resources.

    When True, `app.mcp.auth.resolve_mcp_token` resolves the token to a
    provisioned backend service-account `User` (T-092) so the headless agent can
    run user-scoped tools (character.create, ...) and own what it creates. When
    False (the default for any M2M client not in the allowlist), the token keeps
    `user_id=None` and stays read-only on user-owned resources.
    """
    return client_id in M2M_SERVICE_ACCOUNT_CLIENTS
