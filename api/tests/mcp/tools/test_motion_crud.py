"""Tests for the `motion.*` CRUD 1:1 MCP tools (T-086).

`motion.list_for_base` / `motion.list_for_alias` / `motion.get` /
`motion.rename` / `motion.delete` wrap the REST motion CRUD endpoints. They
resolve the caller from the MCP auth contextvar and call the same
`motion_service` layer the routes use, opening their own short-lived sessions.
These tests drive the handlers directly with the auth contextvar set and seed
real Postgres rows (ownership scoping can't be faked honestly).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.scopes import SCOPE_CHARACTER_READ
from app.mcp.tools.motion import (
    motion_delete,
    motion_get,
    motion_list_for_alias,
    motion_list_for_base,
    motion_rename,
)
from tests.mcp.tools.conftest import auth_as, tool_error_code

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_list_for_base(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_base_preset_motion: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_base_preset_motion["owner_id"]):
        resp = await motion_list_for_base(seeded_base_preset_motion["base_id"])
    assert [m.id for m in resp.items] == [seeded_base_preset_motion["id"]]
    assert resp.items[0].parent.type == "base"


async def test_list_for_alias(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_alias_preset_motion: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_alias_preset_motion["owner_id"]):
        resp = await motion_list_for_alias(seeded_alias_preset_motion["alias_id"])
    assert [m.id for m in resp.items] == [seeded_alias_preset_motion["id"]]
    assert resp.items[0].parent.type == "alias"


async def test_get(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_base_preset_motion: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_base_preset_motion["owner_id"]):
        resp = await motion_get(seeded_base_preset_motion["id"])
    assert resp.motion.id == seeded_base_preset_motion["id"]
    # No generation_log seeded → the detail `generation` subset is None.
    assert resp.motion.generation is None


async def test_rename_custom(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_base_custom_motion: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_base_custom_motion["owner_id"]):
        resp = await motion_rename(seeded_base_custom_motion["id"], name="新名字")
    assert resp.motion.name == "新名字"


async def test_delete(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_base_preset_motion: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_base_preset_motion["owner_id"]):
        result = await motion_delete(seeded_base_preset_motion["id"])
    assert result.motion_id == seeded_base_preset_motion["id"]
    assert result.status == "deleted"
    # Soft-deleted → no longer listed.
    with auth_as(user_id=seeded_base_preset_motion["owner_id"]):
        resp = await motion_list_for_base(seeded_base_preset_motion["base_id"])
    assert resp.items == []


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


async def test_rename_preset_rejected(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_base_preset_motion: dict[str, Any],
) -> None:
    """Preset motions are name-locked → VALIDATION_PRESET_RENAME_FORBIDDEN."""
    with auth_as(user_id=seeded_base_preset_motion["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await motion_rename(seeded_base_preset_motion["id"], name="不准改")
    assert tool_error_code(excinfo.value) == "VALIDATION_PRESET_RENAME_FORBIDDEN"


async def test_get_unknown_motion_not_found(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_user: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await motion_get(uuid.uuid4())
    assert tool_error_code(excinfo.value) == "NOT_FOUND_MOTION"


async def test_rename_read_only_scope_rejected(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_base_custom_motion: dict[str, Any],
) -> None:
    """rename needs character:write; a read-only token fails closed."""
    with auth_as(
        user_id=seeded_base_custom_motion["owner_id"],
        scopes=frozenset({SCOPE_CHARACTER_READ}),
    ):
        with pytest.raises(ToolError) as excinfo:
            await motion_rename(seeded_base_custom_motion["id"], name="x")
    assert tool_error_code(excinfo.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_list_m2m_token_rejected(
    bind_motion_db: async_sessionmaker[Any],
    bind_motion_storage: Any,
    seeded_base_preset_motion: dict[str, Any],
) -> None:
    """M2M tokens carry no user; motion CRUD is user-scoped → fail closed."""
    with auth_as(user_id=None, is_m2m=True, client_id="agent-x"):
        with pytest.raises(ToolError) as excinfo:
            await motion_list_for_base(seeded_base_preset_motion["base_id"])
    assert tool_error_code(excinfo.value) == "AUTH_USER_CONTEXT_REQUIRED"
