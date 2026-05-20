"""`require_scope_no_pin` — scope enforcement WITHOUT pinning a DB connection (T-088).

The SSE endpoint `GET /v1/tasks/{task_id}/stream` uses this variant so the
scope check doesn't re-introduce the DB-connection pin that T-080 deliberately
removed (the standard `require_scope` chains `get_current_user` →
`Depends(db_session)`, held for the stream's lifetime). These tests pin two
things:

  1. Structural: the dependency chains `get_current_user_no_pin`, NOT
     `get_current_user` / `db_session`. A regression that swaps in the pinning
     variant would silently bring the bug back.
  2. Behavioural: the scope-AND check + missing-token / non-canonical-scope
     errors match `require_scope`.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi.params import Depends as DependsParam

from app.api.deps import db_session, get_current_user, get_current_user_no_pin
from app.auth.scopes import SCOPE_TASK_CANCEL, SCOPE_TASK_READ, require_scope_no_pin
from app.core.errors import AgentErrorException


def _depends_callables(func: object) -> list[object]:
    return [
        p.default.dependency
        for p in inspect.signature(func).parameters.values()  # type: ignore[arg-type]
        if isinstance(p.default, DependsParam)
    ]


def test_chains_no_pin_user_dependency_not_db_session() -> None:
    """The dependency must resolve through `get_current_user_no_pin` only."""
    dep = require_scope_no_pin(SCOPE_TASK_READ)
    callables = _depends_callables(dep)
    assert get_current_user_no_pin in callables
    assert get_current_user not in callables
    assert db_session not in callables


def test_no_pin_user_dep_itself_avoids_db_session() -> None:
    """`get_current_user_no_pin` opens its own short-lived session — it must
    NOT take `db_session` as a dependency (that's the whole point)."""
    assert db_session not in _depends_callables(get_current_user_no_pin)


def test_unknown_scope_raises_at_construction() -> None:
    with pytest.raises(ValueError, match="non-canonical scope"):
        require_scope_no_pin("task:read_typo")  # type: ignore[arg-type]


async def test_passes_when_scope_present() -> None:
    dep = require_scope_no_pin(SCOPE_TASK_READ)
    request = SimpleNamespace(state=SimpleNamespace(token_scopes=frozenset({SCOPE_TASK_READ})))
    # `_user` is normally injected by FastAPI; pass a sentinel directly.
    assert await dep(request, _user=object()) is None  # type: ignore[arg-type]


async def test_rejects_when_scope_missing() -> None:
    dep = require_scope_no_pin(SCOPE_TASK_CANCEL)
    request = SimpleNamespace(state=SimpleNamespace(token_scopes=frozenset({SCOPE_TASK_READ})))
    with pytest.raises(AgentErrorException) as ei:
        await dep(request, _user=object())  # type: ignore[arg-type]
    assert ei.value.error.code == "AUTH_INSUFFICIENT_SCOPE"
    assert ei.value.status_code == 403


async def test_missing_token_scopes_raises_missing_token() -> None:
    dep = require_scope_no_pin(SCOPE_TASK_READ)
    request = SimpleNamespace(state=SimpleNamespace())  # no token_scopes attribute
    with pytest.raises(AgentErrorException) as ei:
        await dep(request, _user=object())  # type: ignore[arg-type]
    assert ei.value.error.code == "AUTH_MISSING_TOKEN"
