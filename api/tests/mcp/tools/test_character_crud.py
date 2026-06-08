"""Tests for the `character.*` CRUD 1:1 MCP tools (T-084).

Drives each handler DIRECTLY with the MCP auth contextvar set via `auth_as`
(same approach as the T-088 task/prompt tool tests) — the streamable-HTTP
transport + registry wiring is already smoke-tested in
`tests/mcp/test_skeleton.py`. Rows are seeded into real Postgres via the
shared `tests/mcp/tools/conftest.py` fixtures.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_TASK_READ
from app.mcp.tools.character import (
    character_abandon_session,
    character_delete,
    character_fork,
    character_get,
    character_get_checkpoint,
    character_get_session,
    character_list,
    character_rename,
    character_restore,
)
from tests.mcp.tools.conftest import auth_as, tool_error_code

pytestmark = pytest.mark.asyncio


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Happy paths — one per CRUD tool
# ---------------------------------------------------------------------------


async def test_list_returns_owned_character(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await character_list(owner_id="me")
    ids = [c.id for c in resp.items]
    assert seeded_character["id"] in ids


async def test_get_returns_detail_with_base(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await character_get(seeded_character["id"])
    assert resp.character.id == seeded_character["id"]
    # seeded_character locks a Base, so the detail DTO carries it.
    assert resp.character.base is not None


async def test_rename_updates_name(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await character_rename(seeded_character["id"], "Renamed-Char")
    assert resp.character.name == "Renamed-Char"


async def test_delete_soft_deletes(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await character_delete(seeded_character["id"])
    assert resp.character_id == seeded_character["id"]
    assert resp.status == "deleted"


async def test_restore_after_delete(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_character["owner_id"]):
        await character_delete(seeded_character["id"])
        resp = await character_restore(seeded_character["id"])
    assert resp.character.id == seeded_character["id"]


async def test_fork_opens_new_character(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    # fork copies the source checkpoint's image bytes, so the file must exist
    # in storage (the seed only writes DB rows). The key matches the seed's
    # output_image_key convention.
    source_key = f"checkpoints/{seeded_character['session_id']}/output/seq-1.png"
    bind_character_storage.put(source_key, _png_bytes(), "image/png")
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await character_fork(seeded_character["checkpoint_id"], "Forked-Char")
    assert resp.character.id != seeded_character["id"]
    assert resp.character.name == "Forked-Char"
    assert resp.creation_session.checkpoint_count == 1


async def test_get_session_returns_checkpoints(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await character_get_session(seeded_character["session_id"])
    assert resp.session.id == seeded_character["session_id"]
    # Initiator sees the one seeded checkpoint.
    assert resp.session.checkpoint_count == 1
    assert len(resp.checkpoints) == 1


async def test_abandon_session(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    in_progress_session: dict[str, Any],
    seeded_character: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await character_abandon_session(in_progress_session["id"])
    assert resp.session_id == in_progress_session["id"]
    assert resp.status == "abandoned"


async def test_get_checkpoint(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await character_get_checkpoint(seeded_character["checkpoint_id"])
    assert resp.checkpoint.id == seeded_character["checkpoint_id"]


# ---------------------------------------------------------------------------
# Auth / scope enforcement
# ---------------------------------------------------------------------------


async def test_read_tool_rejects_missing_read_scope() -> None:
    """A token without `character:read` fails closed at the handler (no DB hit)."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_TASK_READ})):
        with pytest.raises(ToolError) as excinfo:
            await character_get(uuid.uuid4())
    assert tool_error_code(excinfo.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_write_tool_rejects_read_only_scope() -> None:
    """`character:read` alone can't drive a write tool like rename."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_CHARACTER_READ})):
        with pytest.raises(ToolError) as excinfo:
            await character_rename(uuid.uuid4(), "Nope")
    assert tool_error_code(excinfo.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_m2m_token_without_user_context_fails_closed() -> None:
    """A non-service-account M2M token (no human, no service identity) can't
    drive user-scoped character tools. (`cf-test-agent` is no longer an example
    here — T-092 makes it a service-account client that DOES resolve a user_id;
    `agent-x` stands in for an ordinary read-only M2M client.)"""
    with auth_as(user_id=None, is_m2m=True, client_id="agent-x"):
        with pytest.raises(ToolError) as excinfo:
            await character_list()
    assert tool_error_code(excinfo.value) == "AUTH_USER_CONTEXT_REQUIRED"


async def test_get_unknown_character_surfaces_not_found(
    bind_character_db: async_sessionmaker[Any],
    bind_character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    """A service-layer AgentError becomes a ToolError with the same code."""
    with auth_as(user_id=seeded_character["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await character_get(uuid.uuid4())
    assert tool_error_code(excinfo.value) == "NOT_FOUND_CHARACTER"
