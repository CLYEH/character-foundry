"""Static integrity checks for `app.auth.mcp_clients` (T-053).

Pre-T-054 the OAuth scope strings live in two places: `OAUTH_SCOPE_LITERALS`
in `tests/arch/test_layering.py` and `CANONICAL_SCOPES` / `ALLOWED_CLIENTS`
values in `app.auth.mcp_clients`. This test cross-checks the module against
the test-side pin (`test_layering.OAUTH_SCOPE_LITERALS`), so editing the
5 strings still requires deliberate edits in both places, but we avoid
introducing a third source of truth in this file.

Once T-054 introduces `app.auth.scopes` as the runtime canonical source,
`test_oauth_scope_source_is_centralized` in `test_layering.py` will activate
and force `mcp_clients` to import the constants. At that point this test's
drift-lock job is taken over by the activated `test_layering` check, and
this file can be either deleted or rewritten to import from `app.auth.scopes`.
"""

from __future__ import annotations

from app.auth.mcp_clients import (
    ALLOWED_CLIENTS,
    CANONICAL_SCOPES,
    M2M_DEFAULT_SCOPES,
)

# TODO(T-054): once `app.auth.scopes` exists, replace this cross-test import
# with `from app.auth.scopes import CANONICAL_SCOPES` and delete the
# drift-lock test below.
from tests.arch.test_layering import OAUTH_SCOPE_LITERALS


def test_canonical_scopes_match_phase1_decision() -> None:
    # Both pins (module + test_layering) must agree on the Phase 1 5-scope
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
    # The four pre-registered Phase 1 clients per Q7 sub-7c. SPA is
    # intentionally absent (it hits /v1/*, not /mcp/*; see module docstring).
    # Adding or removing a client should be a deliberate two-line edit here
    # + in `app.auth.mcp_clients` and reviewable in the diff.
    assert set(ALLOWED_CLIENTS) == {"claude-code", "vs-code", "cursor", "cf-test-agent"}


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
    for client_id in ("claude-code", "vs-code", "cursor"):
        assert ALLOWED_CLIENTS[client_id]["scopes"] is None, (
            f"{client_id} is a delegated (Auth Code + PKCE) client; its scope set "
            "is decided at consent time, not in the allowlist."
        )
