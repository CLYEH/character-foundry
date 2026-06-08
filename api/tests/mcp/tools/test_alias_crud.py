"""Tests for the `alias.*` CRUD 1:1 MCP tools (T-085).

Drives each handler DIRECTLY with the MCP auth contextvar set via `auth_as`
(same approach as the T-084 character / T-088 task tool tests) — the
streamable-HTTP transport + registry wiring is already smoke-tested in
`tests/mcp/test_skeleton.py`. Rows are seeded into real Postgres via the
shared `tests/mcp/tools/conftest.py` fixtures.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_TASK_READ
from app.mcp.tools.alias import (
    alias_delete,
    alias_get,
    alias_list,
    alias_rename,
)
from tests.mcp.tools.conftest import auth_as, tool_error_code

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Happy paths — one per CRUD tool
# ---------------------------------------------------------------------------


async def test_list_returns_seeded_alias(
    bind_alias_db: async_sessionmaker[Any],
    bind_alias_storage: Any,
    seeded_alias: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_alias["owner_id"]):
        resp = await alias_list(seeded_alias["character_id"])
    ids = [a.id for a in resp.items]
    assert seeded_alias["id"] in ids


async def test_get_returns_detail(
    bind_alias_db: async_sessionmaker[Any],
    bind_alias_storage: Any,
    seeded_alias: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_alias["owner_id"]):
        resp = await alias_get(seeded_alias["id"])
    assert resp.alias.id == seeded_alias["id"]
    assert resp.alias.character_id == seeded_alias["character_id"]


async def test_rename_updates_name(
    bind_alias_db: async_sessionmaker[Any],
    bind_alias_storage: Any,
    seeded_alias: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_alias["owner_id"]):
        resp = await alias_rename(seeded_alias["id"], "renamed-alias")
    assert resp.alias.name == "renamed-alias"


async def test_delete_soft_deletes(
    bind_alias_db: async_sessionmaker[Any],
    bind_alias_storage: Any,
    seeded_alias: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_alias["owner_id"]):
        resp = await alias_delete(seeded_alias["id"])
    assert resp.alias_id == seeded_alias["id"]
    assert resp.status == "deleted"


# ---------------------------------------------------------------------------
# Auth / scope enforcement
# ---------------------------------------------------------------------------


async def test_read_tool_rejects_missing_read_scope() -> None:
    """A token without `character:read` fails closed at the handler (no DB hit)."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_TASK_READ})):
        with pytest.raises(ToolError) as excinfo:
            await alias_get(uuid.uuid4())
    assert tool_error_code(excinfo.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_write_tool_rejects_read_only_scope() -> None:
    """`character:read` alone can't drive a write tool like rename."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_CHARACTER_READ})):
        with pytest.raises(ToolError) as excinfo:
            await alias_rename(uuid.uuid4(), "Nope")
    assert tool_error_code(excinfo.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_m2m_token_without_user_context_fails_closed() -> None:
    """A non-service-account M2M token (no human, no service identity) can't
    drive user-scoped alias tools. (`agent-x` rather than `cf-test-agent` —
    T-092 makes the latter resolve a service-account user_id.)"""
    with auth_as(user_id=None, is_m2m=True, client_id="agent-x"):
        with pytest.raises(ToolError) as excinfo:
            await alias_list(uuid.uuid4())
    assert tool_error_code(excinfo.value) == "AUTH_USER_CONTEXT_REQUIRED"


async def test_get_unknown_alias_surfaces_not_found(
    bind_alias_db: async_sessionmaker[Any],
    bind_alias_storage: Any,
    seeded_alias: dict[str, Any],
) -> None:
    """A service-layer AgentError becomes a ToolError with the same code."""
    with auth_as(user_id=seeded_alias["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await alias_get(uuid.uuid4())
    assert tool_error_code(excinfo.value) == "NOT_FOUND_ALIAS"


async def test_rename_non_owner_denied(
    bind_alias_db: async_sessionmaker[Any],
    bind_alias_storage: Any,
    seeded_alias: dict[str, Any],
    second_user: dict[str, Any],
) -> None:
    """A same-team non-owner can't rename someone else's alias (IDOR guard)."""
    with auth_as(user_id=second_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await alias_rename(seeded_alias["id"], "hijacked")
    assert tool_error_code(excinfo.value) == "AUTH_INSUFFICIENT_PERMISSION"


async def test_delete_non_owner_denied(
    bind_alias_db: async_sessionmaker[Any],
    bind_alias_storage: Any,
    seeded_alias: dict[str, Any],
    second_user: dict[str, Any],
) -> None:
    """A same-team non-owner can't delete someone else's alias (IDOR guard)."""
    with auth_as(user_id=second_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await alias_delete(seeded_alias["id"])
    assert tool_error_code(excinfo.value) == "AUTH_INSUFFICIENT_PERMISSION"
