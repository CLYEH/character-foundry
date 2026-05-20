"""Tests for the `prompt.preview` MCP tool (T-088).

Covers the three preview modes (create_base / create_alias / create_motion)
and scope rejection. The reconciler is faked (no LLM); DB / storage are real.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app.auth.scopes import SCOPE_CHARACTER_READ, SCOPE_TASK_READ
from app.mcp.tools.prompt import prompt_preview
from app.schemas.prompt import (
    CreateAliasPreviewRequest,
    CreateBasePreviewRequest,
    CreateMotionPreviewRequest,
)
from tests.mcp.tools.conftest import auth_as, tool_error_code


async def test_prompt_preview_scope_reject() -> None:
    """A token without `character:read` is rejected before any work."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_TASK_READ})):
        with pytest.raises(ToolError) as ei:
            await prompt_preview(
                CreateBasePreviewRequest(mode="create_base", freeform_note="穿西裝")
            )
    assert tool_error_code(ei.value) == "AUTH_INSUFFICIENT_SCOPE"


async def test_prompt_preview_create_base(
    seeded_user: dict[str, Any],
    bind_tool_db: Any,
    bind_prompt_deps: None,
) -> None:
    with auth_as(user_id=seeded_user["id"], scopes=frozenset({SCOPE_CHARACTER_READ})):
        resp = await prompt_preview(
            CreateBasePreviewRequest(mode="create_base", freeform_note="穿西裝打領帶")
        )
    assert resp.final_prompt
    assert resp.reconciled_note_en
    # create_base populates none of the per-mode blocks.
    assert resp.derived_from is None
    assert resp.parent is None


async def test_prompt_preview_create_alias(
    seeded_character: dict[str, Any],
    bind_tool_db: Any,
    bind_prompt_deps: None,
) -> None:
    with auth_as(user_id=seeded_character["owner_id"], scopes=frozenset({SCOPE_CHARACTER_READ})):
        resp = await prompt_preview(
            CreateAliasPreviewRequest(
                mode="create_alias",
                character_id=seeded_character["id"],
                input_mode="text",
                freeform_note="換成正式西裝",
            )
        )
    assert resp.final_prompt
    assert resp.derived_from is not None
    assert resp.derived_from.base_id == seeded_character["base_id"]
    assert resp.derived_from.base_image_url


async def test_prompt_preview_create_motion(
    seeded_character: dict[str, Any],
    bind_tool_db: Any,
    bind_prompt_deps: None,
) -> None:
    with auth_as(user_id=seeded_character["owner_id"], scopes=frozenset({SCOPE_CHARACTER_READ})):
        resp = await prompt_preview(
            CreateMotionPreviewRequest(
                mode="create_motion",
                parent_type="base",
                parent_id=seeded_character["base_id"],
                motion_type="preset_wave",
            )
        )
    assert resp.parent is not None
    assert resp.parent.id == seeded_character["base_id"]
    assert resp.parent.type == "base"
    assert resp.motion_template_used == "preset_wave"
