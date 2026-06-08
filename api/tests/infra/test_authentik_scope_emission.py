"""Static assertion that the canonical OAuth scope mappings emit a `scope`
claim into Authentik's access-token JWT (S3.5-6 / T-093).

Authentik's access token is a JWT whose claims come only from the OIDC fields
plus whatever each *granted* scope's ScopeMapping `expression` returns
(`id_token.py::get_claims` merges the dicts). An empty `return {}` produces a
JWT with NO top-level `scope` claim, so the backend's `payload.get("scope")`
(`app/auth/oauth.py`) is always empty and every per-scope check on `/mcp/*`
fails — the root cause three tickets worked around (`/v1/*` grandfather, M2M
cap-fallback). This parses the e2e blueprint and pins that all 5 canonical
scope mappings carry the scope-emitting expression; a revert to `return {}`
fails CI here, before it can silently break `/mcp/` authorization end-to-end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BLUEPRINT = (
    Path(__file__).resolve().parents[3]
    / "infra"
    / "authentik"
    / "blueprints"
    / "cf-e2e-bootstrap.yaml"
)
CANONICAL_SCOPES = frozenset(
    {"character:read", "character:write", "task:read", "task:cancel", "usage:read"}
)


class _BlueprintLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Authentik's custom `!Find` / `!KeyOf` tags by
    resolving them to None — this test only inspects plain scope-mapping fields."""


_BlueprintLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _scope_mapping_expressions() -> dict[str, str]:
    doc: dict[str, Any] = yaml.load(BLUEPRINT.read_text(encoding="utf-8"), Loader=_BlueprintLoader)
    out: dict[str, str] = {}
    for entry in doc["entries"]:
        if entry.get("model") != "authentik_providers_oauth2.scopemapping":
            continue
        attrs = entry["attrs"]
        out[attrs["scope_name"]] = attrs["expression"]
    return out


def test_blueprint_exists() -> None:
    assert BLUEPRINT.is_file(), f"missing blueprint: {BLUEPRINT}"


def test_canonical_scope_mappings_emit_scope_claim() -> None:
    exprs = _scope_mapping_expressions()
    missing = CANONICAL_SCOPES - exprs.keys()
    assert not missing, f"e2e blueprint missing canonical scope mappings: {sorted(missing)}"
    for scope_name in sorted(CANONICAL_SCOPES):
        expr = exprs[scope_name].strip()
        # Must emit a `scope` claim from the granted scope set — NOT an empty
        # `return {}` (that produced a JWT with no scope claim: S3.5-6).
        assert expr != "return {}", (
            f"{scope_name} reverted to an empty expression — Authentik would emit "
            f"no `scope` claim and /mcp/ per-scope checks would all fail (S3.5-6)"
        )
        assert '"scope"' in expr and "token.scope" in expr, (
            f"{scope_name} expression must emit the `scope` claim, got: {expr!r}"
        )
