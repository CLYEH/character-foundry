"""CI guardrail 1 — every protected REST endpoint declares require_scope.

Per `planning/backend/oauth-mcp-integration.md` §2 / §5 and the T-081 ticket:
each `@router.<method>(...)` endpoint must enforce an OAuth scope via
`Depends(require_scope(...))`. Public endpoints (auth, health, meta, signed
storage) are exempt. The scan is static AST (see `_route_scan` for why we
don't import the app).

Exit codes:
    0   every endpoint either declares require_scope, is on the public
        whitelist, or is a KNOWN-MISSING endpoint awaiting the require_scope
        rollout — AND no stale KNOWN-MISSING entry remains.
    1   a NEW endpoint landed without scope (not whitelisted, not in
        KNOWN_MISSING_SCOPE), or a KNOWN_MISSING_SCOPE entry is now covered /
        removed and should be deleted from the list.

KNOWN_MISSING_SCOPE — why it exists:
    T-054 shipped `require_scope` as a dependency but deliberately did NOT
    roll it across the existing 31 endpoints (its ticket said so; the rollout
    is a sequence of follow-up tickets per `oauth-mcp-integration.md` §2). So
    on day one almost no endpoint is covered. Failing CI for all 31 would
    block every PR. Instead we baseline them here: the gate passes today, but
    a *new* endpoint landing without scope still fails (it isn't on the list),
    and migrating an endpoint forces its removal from the list (a stale entry
    fails the gate). Drive this set to empty as the rollout proceeds —
    tracked by STATUS.md backlog row S3.5-1 / the require_scope rollout
    follow-up ticket.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _route_scan import Endpoint, scan_routes

API_ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = API_ROOT / "app" / "api" / "routes"

# Endpoints that never require a scope (per api-shape §5.9 + auth flow):
#   • /health, /v1/meta — unauthenticated platform metadata / liveness.
#   • /v1/auth/*        — login / refresh / logout / me (the auth surface
#                         itself; `me`/`logout` authenticate but need no scope).
#   • /storage/*        — served via an independent signed-URL JWT, decoupled
#                         from OAuth scopes (DECISIONS §B2 / signed-URL design).
PUBLIC_PATHS_EXACT: frozenset[str] = frozenset({"/health", "/v1/meta"})
PUBLIC_PATH_PREFIXES: tuple[str, ...] = ("/v1/auth/", "/storage/")

# Existing endpoints awaiting the require_scope rollout (see module docstring).
# Format: (METHOD, logical-path). Remove an entry the moment its endpoint
# starts declaring require_scope.
KNOWN_MISSING_SCOPE: frozenset[tuple[str, str]] = frozenset(
    {
        # aliases.py
        ("POST", "/v1/characters/{character_id}/aliases/masks"),
        ("POST", "/v1/characters/{character_id}/aliases"),
        ("GET", "/v1/characters/{character_id}/aliases"),
        ("GET", "/v1/aliases/{alias_id}"),
        ("PATCH", "/v1/aliases/{alias_id}"),
        ("DELETE", "/v1/aliases/{alias_id}"),
        # characters.py
        ("GET", "/v1/characters"),
        ("POST", "/v1/characters"),
        ("GET", "/v1/characters/{character_id}"),
        ("PATCH", "/v1/characters/{character_id}"),
        ("DELETE", "/v1/characters/{character_id}"),
        ("POST", "/v1/characters/{character_id}/restore"),
        # checkpoints.py
        ("GET", "/v1/checkpoints/{checkpoint_id}"),
        ("POST", "/v1/checkpoints/{checkpoint_id}/fork"),
        # creation_sessions.py
        ("GET", "/v1/creation-sessions/{session_id}"),
        ("POST", "/v1/creation-sessions/{session_id}/checkpoints"),
        ("POST", "/v1/creation-sessions/{session_id}/select-base"),
        ("POST", "/v1/creation-sessions/{session_id}/abandon"),
        # motions.py
        ("POST", "/v1/bases/{base_id}/motions"),
        ("POST", "/v1/aliases/{alias_id}/motions"),
        ("GET", "/v1/bases/{base_id}/motions"),
        ("GET", "/v1/aliases/{alias_id}/motions"),
        ("GET", "/v1/motions/{motion_id}"),
        ("PATCH", "/v1/motions/{motion_id}"),
        ("DELETE", "/v1/motions/{motion_id}"),
        # reference_images.py
        ("POST", "/v1/creation-sessions/{session_id}/reference-images"),
        # NOTE: tasks.py (GET /{id}, GET, POST /{id}/cancel, GET /{id}/stream)
        # and prompt.py (POST /preview) migrated onto require_scope* in T-088 —
        # removed from this baseline so a future regression (someone dropping
        # the scope dep) fails the gate instead of being silently re-baselined.
    }
)


def _is_public(
    path: str,
    *,
    exact: frozenset[str],
    prefixes: tuple[str, ...],
) -> bool:
    return path in exact or any(path.startswith(p) for p in prefixes)


def check(
    endpoints: list[Endpoint],
    *,
    public_exact: frozenset[str],
    public_prefixes: tuple[str, ...],
    known_missing: frozenset[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (unexpected_missing, stale_known_missing).

    `unexpected_missing` — endpoints without scope that are neither public
    nor baselined: a NEW gap → fail.
    `stale_known_missing` — baselined entries that are now covered or no
    longer present: clean them up → fail.
    """
    missing: set[tuple[str, str]] = set()
    for ep in endpoints:
        if ep.has_scope:
            continue
        if _is_public(ep.path, exact=public_exact, prefixes=public_prefixes):
            continue
        missing.add((ep.method, ep.path))
    unexpected = sorted(missing - known_missing)
    stale = sorted(known_missing - missing)
    return unexpected, stale


def main(
    argv: list[str] | None = None,
    *,
    endpoints: list[Endpoint] | None = None,
    known_missing: frozenset[tuple[str, str]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--routes-dir",
        type=Path,
        default=ROUTES_DIR,
        help="Directory of FastAPI route modules to scan (default: app/api/routes).",
    )
    args = parser.parse_args(argv)

    eps = endpoints if endpoints is not None else scan_routes(args.routes_dir)
    baseline = known_missing if known_missing is not None else KNOWN_MISSING_SCOPE

    # Floor: a scan that finds zero endpoints means the routes dir is wrong or a
    # packaging/glob regression happened. Today the non-empty baseline catches
    # this (all entries go stale), but once the rollout drives the baseline to
    # empty that safety net disappears — so refuse an empty scan explicitly.
    if not eps:
        print(
            f"::error::scope-coverage scanned 0 endpoints from {args.routes_dir} - "
            "wrong directory or a packaging/glob regression. Refusing to pass.",
            file=sys.stderr,
        )
        return 1

    unexpected, stale = check(
        eps,
        public_exact=PUBLIC_PATHS_EXACT,
        public_prefixes=PUBLIC_PATH_PREFIXES,
        known_missing=baseline,
    )

    if not unexpected and not stale:
        print(
            f"scope-coverage OK - {len(eps)} endpoints scanned, all covered/whitelisted/baselined."
        )
        return 0

    if unexpected:
        print("::error::Endpoints missing Depends(require_scope(...)):", file=sys.stderr)
        for method, path in unexpected:
            print(f"  - {method} {path}", file=sys.stderr)
        print(
            "\nAdd `_: None = Depends(require_scope(<scope>))` to the handler, or — if "
            "it is genuinely public — add it to PUBLIC_PATHS_EXACT / PUBLIC_PATH_PREFIXES "
            "in check_scope_coverage.py.",
            file=sys.stderr,
        )
    if stale:
        print(
            "::error::KNOWN_MISSING_SCOPE entries that are now covered or removed "
            "(delete them from check_scope_coverage.py):",
            file=sys.stderr,
        )
        for method, path in stale:
            print(f"  - {method} {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
