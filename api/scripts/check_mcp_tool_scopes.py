"""CI guardrail 2 — MCP tool scopes are canonical + ⊆ their bundle's scopes.

Per `planning/backend/oauth-mcp-integration.md` §3.4 / §5, every `MCPTool`'s
`scopes` must be:

  1. a subset of the canonical scope set (`app.auth.scopes.CANONICAL_SCOPES`);
  2. when `bundles` is non-empty, a subset of the union of the scopes declared
     by the bundled REST endpoints — a tool must not grant itself more than the
     endpoints it packages require.

Bundleless tools (e.g. the `hello.world` smoke tool) skip check #2: an empty
union is the empty set, against which any non-empty scope would falsely fail.
That edge was Codex review #106 round-2 P1 — preserved here as the explicit
`if tool.bundles:` guard.

Exit codes:
    0   all tools pass both checks.
    1   a tool has a non-canonical scope, exceeds its bundle union, or names a
        bundle endpoint that doesn't exist among the scanned routes.

Endpoint scopes are read statically from source via `_route_scan`. The
`require_scope(...)` arguments are identifiers (`SCOPE_CHARACTER_WRITE`, ...)
because the architecture-fitness test forbids bare scope-string literals
outside `app/auth/scopes.py`; we resolve those identifiers to their values
through the `app.auth.scopes` module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _route_scan import scan_routes

from app.auth import scopes as scopes_mod
from app.auth.scopes import CANONICAL_SCOPES
from app.mcp.registry import MCPTool

API_ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = API_ROOT / "app" / "api" / "routes"

# Identifier → value map for the SCOPE_* constants, so a require_scope token
# like "SCOPE_CHARACTER_WRITE" resolves to "character:write".
_SCOPE_CONST_TO_VALUE: dict[str, str] = {
    name: getattr(scopes_mod, name)
    for name in dir(scopes_mod)
    if name.startswith("SCOPE_") and isinstance(getattr(scopes_mod, name), str)
}


def _resolve_token(token: str) -> str | None:
    """Resolve a require_scope arg token to a scope value, or None if unknown."""
    if token in _SCOPE_CONST_TO_VALUE:
        return _SCOPE_CONST_TO_VALUE[token]
    if token in CANONICAL_SCOPES:  # defensive: a literal string slipped through
        return token
    return None


def _real_endpoint_scopes(routes_dir: Path) -> dict[tuple[str, str], frozenset[str]]:
    """Scan routes and return {(METHOD, path): resolved scope value set}."""
    mapping: dict[tuple[str, str], frozenset[str]] = {}
    for ep in scan_routes(routes_dir):
        values = {v for v in (_resolve_token(t) for t in ep.scope_tokens) if v is not None}
        mapping[(ep.method, ep.path)] = frozenset(values)
    return mapping


def _parse_bundle(bundle: str) -> tuple[str, str] | None:
    """Parse a `"METHOD /path"` bundle string into (METHOD, path)."""
    parts = bundle.split(maxsplit=1)
    if len(parts) != 2:
        return None
    return parts[0].upper(), parts[1]


def check(
    tools: dict[str, MCPTool],
    endpoint_scopes: dict[tuple[str, str], frozenset[str]],
    *,
    canonical: frozenset[str] = CANONICAL_SCOPES,
) -> list[str]:
    """Return a list of violation messages (empty list = pass)."""
    violations: list[str] = []
    for name, tool in tools.items():
        tool_scopes = set(tool.scopes)

        non_canonical = tool_scopes - canonical
        if non_canonical:
            violations.append(
                f"{name}: non-canonical scope(s) {sorted(non_canonical)}; "
                f"canonical scopes are {sorted(canonical)}"
            )

        if not tool.bundles:
            # MCP-only tool: no REST endpoints to union against (see docstring).
            continue

        union: set[str] = set()
        for bundle in tool.bundles:
            key = _parse_bundle(bundle)
            if key is None:
                violations.append(
                    f"{name}: malformed bundle string {bundle!r} (expected 'METHOD /path')"
                )
                continue
            if key not in endpoint_scopes:
                violations.append(
                    f"{name}: bundle {bundle!r} does not match any scanned route "
                    "(check method + exact logical path, including param names)"
                )
                continue
            union |= endpoint_scopes[key]

        exceeding = tool_scopes - union
        if exceeding:
            violations.append(
                f"{name}: scope(s) {sorted(exceeding)} exceed the union of bundled "
                f"endpoint scopes {sorted(union)} — a tool can't grant more than it packages"
            )
    return violations


def main(
    argv: list[str] | None = None,
    *,
    tools: dict[str, MCPTool] | None = None,
    endpoint_scopes: dict[tuple[str, str], frozenset[str]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--routes-dir",
        type=Path,
        default=ROUTES_DIR,
        help="Directory of FastAPI route modules to scan (default: app/api/routes).",
    )
    args = parser.parse_args(argv)

    if tools is None:
        # Import triggers tool auto-discovery → REGISTRY is populated.
        import app.mcp.tools  # noqa: F401
        from app.mcp.registry import REGISTRY

        tools = dict(REGISTRY)

    eps = endpoint_scopes if endpoint_scopes is not None else _real_endpoint_scopes(args.routes_dir)

    violations = check(tools, eps)
    if not violations:
        print(f"mcp-tool-scopes OK - {len(tools)} tool(s) checked.")
        return 0

    print("::error::MCP tool scope consistency violations:", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
