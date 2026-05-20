"""CI guardrail 3 — every allowlisted client's scopes are canonical.

Per `planning/backend/oauth-mcp-integration.md` §5, the OAuth client allowlist
in `app.auth.mcp_clients.ALLOWED_CLIENTS` must only reference canonical scope
ids (`app.auth.scopes.CANONICAL_SCOPES`). A typo'd scope in a client policy
would otherwise silently cap (or fail to cap) that client's tokens.

Spec-vs-code note: the planning doc's §5 wording assumes a richer policy with
separate `allowed_scopes` / `default_scopes` fields and an extra
`default_scopes ⊆ allowed_scopes` invariant. The implemented `ClientPolicy`
(T-053) collapsed those into a single `scopes: list[str] | None` field —
`None` = delegated (consent-driven, uncapped), a list = the M2M cap. With one
field there is no default-vs-allowed relationship left to check, so this
guardrail enforces the one invariant that survives: every listed scope is
canonical. The pytest in `tests/arch/test_mcp_clients_allowlist.py` asserts
the same property; this script exists so `lint_mcp.sh` and the PR workflow can
run all three MCP guardrails as one standalone group.

Exit codes:
    0   every client's (non-None) scopes are all canonical.
    1   a client references a non-canonical scope id.
"""

from __future__ import annotations

import argparse
import sys

from app.auth.mcp_clients import ALLOWED_CLIENTS, ClientPolicy
from app.auth.scopes import CANONICAL_SCOPES


def check(
    allowed_clients: dict[str, ClientPolicy],
    *,
    canonical: frozenset[str] = CANONICAL_SCOPES,
) -> list[str]:
    """Return a list of violation messages (empty list = pass)."""
    violations: list[str] = []
    for client_id, policy in allowed_clients.items():
        scopes = policy["scopes"]
        if scopes is None:
            # Delegated client — scope set is decided at consent time, no cap.
            continue
        non_canonical = set(scopes) - canonical
        if non_canonical:
            violations.append(
                f"{client_id}: non-canonical scope(s) {sorted(non_canonical)}; "
                f"canonical scopes are {sorted(canonical)}"
            )
    return violations


def main(
    argv: list[str] | None = None,
    *,
    allowed_clients: dict[str, ClientPolicy] | None = None,
) -> int:
    # No real args, but keep argparse for `--help` parity with the sibling
    # scripts and so the workflow can invoke all three identically.
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    clients = allowed_clients if allowed_clients is not None else ALLOWED_CLIENTS
    violations = check(clients)
    if not violations:
        print(f"mcp-client-allowlist OK - {len(clients)} client(s) checked.")
        return 0

    print("::error::MCP client allowlist scope violations:", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
