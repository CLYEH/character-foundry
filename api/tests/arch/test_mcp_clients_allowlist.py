"""Static integrity checks for `app.auth.mcp_clients` (T-053).

Post-T-054 the canonical scope strings live in `app.auth.scopes` and this
test imports them from there. `test_oauth_scope_source_is_centralized` in
`test_layering.py` activates the moment `app/auth/scopes.py` exists and
guards the broader "no other `app/*.py` redefines these literals" invariant.

The cross-pin against `tests.arch.test_layering.OAUTH_SCOPE_LITERALS` is
preserved as a deliberate redundancy: the test-side tuple is the answer-key
the production constants must equal, so a change to either side without the
matching change to the other surfaces as a single concrete assertion failure.
"""

from __future__ import annotations

from app.auth.mcp_clients import ALLOWED_CLIENTS
from app.auth.scopes import CANONICAL_SCOPES, M2M_DEFAULT_SCOPES
from tests.arch.test_layering import OAUTH_SCOPE_LITERALS


def test_canonical_scopes_match_phase1_decision() -> None:
    # `scopes.py` (production) and `test_layering.OAUTH_SCOPE_LITERALS`
    # (test-side answer-key) must both agree on the Phase 1 five-scope
    # decision row in `planning/auth/open-questions.md` Q3.
    assert CANONICAL_SCOPES == frozenset(OAUTH_SCOPE_LITERALS)


def test_m2m_default_scopes_are_canonical() -> None:
    extra = set(M2M_DEFAULT_SCOPES) - CANONICAL_SCOPES
    assert not extra, f"M2M_DEFAULT_SCOPES has non-canonical entries: {extra}"


def test_m2m_default_is_narrow() -> None:
    # Per Q5 sub-5a "narrow default": M2M_DEFAULT_SCOPES must be a strict
    # subset of CANONICAL_SCOPES. A future change that widens it to the full
    # set silently defeats the "narrow default + per-client override" model
    # — make that change loud.
    assert set(M2M_DEFAULT_SCOPES) < CANONICAL_SCOPES


def test_allowed_clients_have_phase1_membership() -> None:
    # The five pre-registered Phase 1 clients per Q7 sub-7c + T-054 round-4
    # SPA inclusion. Adding or removing a client should be a deliberate edit
    # reviewable in the diff.
    assert set(ALLOWED_CLIENTS) == {
        "character-foundry-spa",
        "claude-code",
        "vs-code",
        "cursor",
        "cf-test-agent",
    }


def test_spa_is_allowlisted_as_delegated() -> None:
    # SPA must be present (T-054 round-4 fix) and must have `scopes=None`
    # — the SPA serves human OAuth login; scope set is consent-driven, not
    # capped here.
    assert ALLOWED_CLIENTS["character-foundry-spa"]["scopes"] is None


def test_allowed_clients_scope_values_are_canonical() -> None:
    for client_id, policy in ALLOWED_CLIENTS.items():
        scopes = policy["scopes"]
        if scopes is None:
            continue
        extra = set(scopes) - CANONICAL_SCOPES
        assert not extra, f"Client {client_id!r} has non-canonical scopes: {extra}"


def test_cf_test_agent_gets_full_scope_set() -> None:
    # cf-test-agent is the CI smoke client and must exercise every endpoint
    # category. If a future change narrows it, every other agent's coverage
    # also stops being exercised in CI — this test makes that loud.
    policy = ALLOWED_CLIENTS["cf-test-agent"]
    assert policy["scopes"] is not None
    assert set(policy["scopes"]) == CANONICAL_SCOPES


def test_delegated_clients_use_none_scope() -> None:
    for client_id in ("character-foundry-spa", "claude-code", "vs-code", "cursor"):
        assert ALLOWED_CLIENTS[client_id]["scopes"] is None, (
            f"{client_id} is a delegated (Auth Code + PKCE) client; its scope set "
            "is decided at consent time, not in the allowlist."
        )
