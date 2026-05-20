"""Tests for the `task.*` MCP tools (T-088).

Handlers are driven directly with the auth contextvar set via `auth_as(...)`;
DB-backed cases hit real Postgres (cancel's row lock + ownership scoping can't
be faked honestly). Scope-reject / M2M cases need no DB and run regardless of
TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app.auth.scopes import (
    CANONICAL_SCOPES,
    SCOPE_CHARACTER_READ,
    SCOPE_TASK_CANCEL,
    SCOPE_TASK_READ,
)
from app.mcp.tools.task import task_cancel, task_get, task_list
from tests.mcp.tools.conftest import FakeArqPool, auth_as, seed_task, tool_error_code

# ---------------------------------------------------------------------------
# Auth / scope gating — no DB needed (fails before any resource access)
# ---------------------------------------------------------------------------


async def test_task_get_scope_reject() -> None:
    """A token without `task:read` is rejected at the tool layer."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_CHARACTER_READ})):
        with pytest.raises(ToolError) as ei:
            await task_get(uuid.uuid4())
    assert tool_error_code(ei.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_task_list_scope_reject() -> None:
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_CHARACTER_READ})):
        with pytest.raises(ToolError) as ei:
            await task_list()
    assert tool_error_code(ei.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_task_cancel_scope_reject() -> None:
    """`task:read` alone is NOT enough for cancel — it needs `task:cancel`."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_TASK_READ})):
        with pytest.raises(ToolError) as ei:
            await task_cancel(uuid.uuid4())
    assert tool_error_code(ei.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_task_get_m2m_no_user_context() -> None:
    """An M2M token (no user) calling a user-scoped tool fails closed."""
    with auth_as(
        user_id=None,
        scopes=CANONICAL_SCOPES,
        client_id="cf-test-agent",
        is_m2m=True,
    ):
        with pytest.raises(ToolError) as ei:
            await task_get(uuid.uuid4())
    assert tool_error_code(ei.value) == "AUTH_USER_CONTEXT_REQUIRED"


# ---------------------------------------------------------------------------
# task.get — DB-backed
# ---------------------------------------------------------------------------


async def test_task_get_happy(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    task_id = await seed_task(bind_tool_db, user_id=seeded_user["id"], arq_pool=fake_arq_pool)
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_READ})):
        resp = await task_get(task_id)
    assert resp.task.id == task_id
    assert resp.task.status == "queued"
    assert resp.task.task_type == "create_checkpoint"
    assert resp.task.queue_position == 1


async def test_task_get_404_unknown(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
) -> None:
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_READ})):
        with pytest.raises(ToolError) as ei:
            await task_get(uuid.uuid4())
    assert tool_error_code(ei.value) == "NOT_FOUND_TASK"


async def test_task_get_404_other_users_task(
    seeded_user: dict[str, Any],
    second_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    """A task owned by another user is invisible (NOT_FOUND, no ownership leak)."""
    other_task = await seed_task(bind_tool_db, user_id=second_user["id"], arq_pool=fake_arq_pool)
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_READ})):
        with pytest.raises(ToolError) as ei:
            await task_get(other_task)
    assert tool_error_code(ei.value) == "NOT_FOUND_TASK"


# ---------------------------------------------------------------------------
# task.list — DB-backed
# ---------------------------------------------------------------------------


async def test_task_list_filters_by_status(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    await seed_task(
        bind_tool_db, user_id=seeded_user["id"], status="queued", arq_pool=fake_arq_pool
    )
    await seed_task(
        bind_tool_db, user_id=seeded_user["id"], status="completed", arq_pool=fake_arq_pool
    )

    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_READ})):
        all_tasks = await task_list()
        queued = await task_list(status="queued")

    assert len(all_tasks.items) == 2
    assert len(queued.items) == 1
    assert queued.items[0].status == "queued"


async def test_task_list_scoped_to_caller(
    seeded_user: dict[str, Any],
    second_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    await seed_task(bind_tool_db, user_id=seeded_user["id"], arq_pool=fake_arq_pool)
    await seed_task(bind_tool_db, user_id=second_user["id"], arq_pool=fake_arq_pool)

    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_READ})):
        mine = await task_list()
    assert len(mine.items) == 1


# ---------------------------------------------------------------------------
# task.cancel — DB-backed; the four cancel outcomes + already-terminal 409
# ---------------------------------------------------------------------------


async def test_task_cancel_cancelled_immediately(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    task_id = await seed_task(
        bind_tool_db, user_id=seeded_user["id"], status="queued", arq_pool=fake_arq_pool
    )
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_CANCEL})):
        resp = await task_cancel(task_id)
    assert resp.cancel_outcome == "cancelled_immediately"
    assert resp.task.status == "cancelled"
    assert resp.task.cancel_requested is True


async def test_task_cancel_pending_for_running(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    task_id = await seed_task(
        bind_tool_db, user_id=seeded_user["id"], status="running", arq_pool=fake_arq_pool
    )
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_CANCEL})):
        resp = await task_cancel(task_id)
    assert resp.cancel_outcome == "cancel_pending"
    assert resp.task.cancel_requested is True


async def test_task_cancel_too_late_completed(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    task_id = await seed_task(
        bind_tool_db, user_id=seeded_user["id"], status="completed", arq_pool=fake_arq_pool
    )
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_CANCEL})):
        resp = await task_cancel(task_id)
    assert resp.cancel_outcome == "too_late_completed"
    assert resp.task.status == "completed"


async def test_task_cancel_too_late_failed(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    task_id = await seed_task(
        bind_tool_db, user_id=seeded_user["id"], status="failed", arq_pool=fake_arq_pool
    )
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_CANCEL})):
        resp = await task_cancel(task_id)
    assert resp.cancel_outcome == "too_late_failed"
    assert resp.task.status == "failed"


async def test_task_cancel_409_already_terminal(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_task_deps: None,
    fake_arq_pool: FakeArqPool,
) -> None:
    """Cancelling an already-cancelled task surfaces CONFLICT_TASK_ALREADY_TERMINAL."""
    task_id = await seed_task(
        bind_tool_db, user_id=seeded_user["id"], status="cancelled", arq_pool=fake_arq_pool
    )
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_TASK_CANCEL})):
        with pytest.raises(ToolError) as ei:
            await task_cancel(task_id)
    assert tool_error_code(ei.value) == "CONFLICT_TASK_ALREADY_TERMINAL"
