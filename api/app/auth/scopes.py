"""Canonical OAuth scope literals + `require_scope` FastAPI dependency factory.

Single source of truth for the five Phase 1 scope strings (per
`planning/auth/open-questions.md` Q3 decision row). `app.auth.mcp_clients`
and any future `app.mcp.*` registry import from here rather than redefining
the strings inline. `tests/arch/test_layering.py::test_oauth_scope_source_is_centralized`
activates the moment this file exists and fails the build if a sibling
`app/**/*.py` hard-codes one of the literals — drift would mean two modules
disagree on what `character:read` actually grants.

`require_scope(*scopes)` is the gate every protected REST endpoint declares
once T-3.5b begins migrating existing endpoints onto the dual-stack token
model. T-054 only ships the dependency; rolling it across routes is its own
sequence of tickets per `planning/backend/oauth-mcp-integration.md` §2.

AND semantics only — OR cases split into separate endpoints per Step 3 §2.3,
which keeps the OpenAPI surface honest about what each endpoint actually
requires.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import Depends, Request

from app.core.errors import auth_insufficient_scope, auth_missing_token

# Phase 1's five canonical scopes — named constants so call sites
# (`require_scope(...)`, `require_mcp_scopes(...)`, MCPTool registry
# entries) reference these symbols rather than re-typing the literal
# strings. `tests/arch/test_layering.py::test_oauth_scope_source_is_centralized`
# rejects bare string literals matching these values anywhere outside
# this module; using the named constants is the sanctioned path. Any
# change here MUST be paired with the matching change in Authentik admin
# UI's Scope Mapping list — see `planning/devops/authentik-stack.md`
# §5.3. Adding / renaming / removing a scope is not a T-054 edit; open
# a fresh ticket per `planning/auth/open-questions.md` Q3 modification
# flow.
SCOPE_CHARACTER_READ: Final[str] = "character:read"
SCOPE_CHARACTER_WRITE: Final[str] = "character:write"
SCOPE_TASK_READ: Final[str] = "task:read"
SCOPE_TASK_CANCEL: Final[str] = "task:cancel"
SCOPE_USAGE_READ: Final[str] = "usage:read"

CANONICAL_SCOPES: Final[frozenset[str]] = frozenset(
    {
        SCOPE_CHARACTER_READ,
        SCOPE_CHARACTER_WRITE,
        SCOPE_TASK_READ,
        SCOPE_TASK_CANCEL,
        SCOPE_USAGE_READ,
    }
)


# Narrow default for unknown M2M clients per agent-interface Q5 sub-5a:
# allowlist entries with `scopes=None` are delegated (consent-driven) clients;
# M2M clients without an explicit `scopes` override fall back to this set.
# Stays a strict subset of CANONICAL_SCOPES so a future widening becomes a
# visible diff in this file rather than a silent escalation.
M2M_DEFAULT_SCOPES: Final[frozenset[str]] = frozenset(
    {
        "character:write",
        "task:read",
    }
)


def require_scope(
    *required_scopes: str,
) -> Callable[..., Awaitable[None]]:
    """FastAPI dependency factory that enforces AND-scope on the caller's token.

    The returned dep transitively triggers `app.api.deps.get_current_user`
    (which verifies the bearer token via either the legacy JWT path or the
    Authentik OAuth path and populates `request.state.token_scopes`), then
    asserts every `required_scope` is present. Raises:

      • 401 `AUTH_MISSING_TOKEN` if no token (auth dependency couldn't run).
      • 403 `AUTH_INSUFFICIENT_SCOPE` if the token is missing any required
        scope.

    Usage (per `planning/backend/oauth-mcp-integration.md` §2.2):

        @router.post("/characters")
        async def create_character(
            payload: CreateCharacterIn,
            _: None = Depends(require_scope("character:write")),
        ): ...

    Unknown scope strings (not in CANONICAL_SCOPES) raise immediately at
    dependency-construction time — a typo'd scope literal in a route would
    otherwise silently lock everyone out at runtime.
    """
    unknown = set(required_scopes) - CANONICAL_SCOPES
    if unknown:
        raise ValueError(
            f"require_scope() called with non-canonical scope(s): {sorted(unknown)}. "
            f"Canonical scopes are: {sorted(CANONICAL_SCOPES)}."
        )
    required = frozenset(required_scopes)

    # Lazy import is REQUIRED to break a real import cycle: `app.api.deps`
    # imports `CANONICAL_SCOPES` from this module, and we need
    # `get_current_user` from there. A top-level `from app.api.deps import ...`
    # in this file would partially-load scopes.py mid-loading-deps.py and
    # `from app.auth.scopes import CANONICAL_SCOPES` over there would
    # AttributeError because `CANONICAL_SCOPES` hasn't been assigned yet.
    #
    # The lazy import resolves at `require_scope(...)` invocation time
    # (i.e. when FastAPI decorators evaluate `Depends(require_scope(...))`),
    # which is always *after* both `scopes.py` and `deps.py` finish their
    # top-level execution. Safe by construction, not by accident.
    from app.api.deps import get_current_user

    async def dependency(
        request: Request,
        _user: object = Depends(get_current_user),
    ) -> None:
        token_scopes = getattr(request.state, "token_scopes", None)
        if token_scopes is None:
            # `get_current_user` runs first and always sets token_scopes on
            # success — reaching here means either an unauthenticated request
            # slipped through, or a path the dep didn't cover. Treat as missing
            # rather than insufficient so callers know to attach a token.
            raise auth_missing_token()
        if not required <= token_scopes:
            raise auth_insufficient_scope()
        return None

    return dependency
