"""Tests for the packaged, polymorphic `motion.generate` MCP tool (T-086 / T-087).

`motion.generate` is non-blocking (T-087): it submits the i2v job and returns a
`{task_id, motion_id, status}` handle immediately, leaving the agent to poll
`task.get` and then `motion.get`. These tests drive the handler directly with the
MCP auth contextvar set (`auth_as`) and an inline arq pool that runs the motion
worker synchronously on enqueue (`make_motion_generate_deps`) — so by the time
`motion.generate` returns its handle the task is already terminal and the outcome
is observable (via `motion.get` for the happy path, via the task row for the
failure path) without a real worker process or sleeps.

`VeoStub` returns a bundled placeholder mp4; the AI_STUB_MODE reconciler handles
the custom path. A failing video client drives the RAI-filter path: the tool
still returns its handle (submission succeeded), and the failure surfaces on the
task as `MODEL_CONTENT_FILTERED` — exactly what `task.get(task_id).error` returns
to a polling agent (T-051 preserved through polling instead of an inline raise).
"""

from __future__ import annotations

import json
import uuid
from io import BytesIO
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.base import VeoResult
from app.ai.errors import model_content_filtered
from app.ai.stub import VeoStub
from app.auth.scopes import SCOPE_CHARACTER_READ
from app.mcp.tools.motion import motion_generate, motion_get
from tests.mcp.tools.conftest import auth_as, tool_error_code

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles + helpers
# ---------------------------------------------------------------------------


class _RaiFilteredVeoStub(VeoStub):
    """Veo stub whose generate_i2v raises the RAI-filter AgentError (T-051).

    The worker catches the AgentError and marks the task `failed` with the
    `MODEL_CONTENT_FILTERED` envelope. `motion.generate` no longer polls, so it
    still returns its handle; the agent observes the failure via
    `task.get(task_id).error` (asserted here by reading the task row).
    """

    async def generate_i2v(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        duration_seconds: float | None = None,
    ) -> VeoResult:
        raise model_content_filtered("veo-3.1")


def _png_bytes(size: tuple[int, int] = (16, 16), color: str = "red") -> bytes:
    buf = BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _tool_error_payload(exc: ToolError) -> dict[str, Any]:
    text = str(exc.args[0])
    brace = text.find("{")
    assert brace != -1, f"expected JSON payload, got {text!r}"
    return json.loads(text[brace:])  # type: ignore[no-any-return]


async def _count_motions(factory: async_sessionmaker[Any]) -> int:
    from app.models.motion import Motion

    async with factory() as db:
        return (await db.execute(select(func.count()).select_from(Motion))).scalar_one()


async def _get_task(factory: async_sessionmaker[Any], task_id: uuid.UUID) -> Any:
    from app.models.task import Task

    async with factory() as db:
        return await db.get(Task, task_id)


# ---------------------------------------------------------------------------
# Happy paths — base/alias × preset/custom (submit → handle → fetch)
# ---------------------------------------------------------------------------


async def test_generate_preset_on_base(
    make_motion_generate_deps: Any,
    bind_motion_db: async_sessionmaker[Any],
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    make_motion_generate_deps(VeoStub())
    character_storage.put(seeded_character["base_image_key"], _png_bytes(), "image/png")
    with auth_as(user_id=seeded_character["owner_id"]):
        handle = await motion_generate(
            target_type="base",
            target_id=seeded_character["base_id"],
            motion_type="preset_wave",
            name="Wave",
        )
    # Non-blocking: returns a handle, not the finished motion.
    assert handle.status == "queued"
    assert handle.task_id is not None
    assert handle.motion_id is not None

    # The inline worker ran on enqueue → the task is already completed and the
    # motion row exists. The agent fetches it with the handle's motion_id.
    task = await _get_task(bind_motion_db, handle.task_id)
    assert task.status == "completed"
    assert task.entity_id == handle.motion_id
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await motion_get(motion_id=handle.motion_id)
    assert resp.motion.name == "Wave"
    assert resp.motion.parent.type == "base"
    assert resp.motion.parent.id == seeded_character["base_id"]
    assert resp.motion.video_url is not None
    # Detail output carries the generation subset (model name from the log).
    assert resp.motion.generation is not None
    assert resp.motion.generation.model_name == "veo-3.1"


async def test_generate_preset_on_alias(
    make_motion_generate_deps: Any,
    bind_motion_db: async_sessionmaker[Any],
    character_storage: Any,
    seeded_alias: dict[str, Any],
) -> None:
    make_motion_generate_deps(VeoStub())
    character_storage.put(seeded_alias["image_key"], _png_bytes(), "image/png")
    with auth_as(user_id=seeded_alias["owner_id"]):
        handle = await motion_generate(
            target_type="alias",
            target_id=seeded_alias["id"],
            motion_type="preset_nod",
            name="Nod",
        )
    assert handle.status == "queued"
    with auth_as(user_id=seeded_alias["owner_id"]):
        resp = await motion_get(motion_id=handle.motion_id)
    assert resp.motion.name == "Nod"
    assert resp.motion.parent.type == "alias"
    assert resp.motion.parent.id == seeded_alias["id"]
    assert resp.motion.video_url is not None


async def test_generate_custom_on_base(
    make_motion_generate_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    """Custom path routes the description through the (stub) reconciler."""
    make_motion_generate_deps(VeoStub())
    character_storage.put(seeded_character["base_image_key"], _png_bytes(), "image/png")
    with auth_as(user_id=seeded_character["owner_id"]):
        handle = await motion_generate(
            target_type="base",
            target_id=seeded_character["base_id"],
            motion_type="custom",
            name="Spin",
            description="緩慢地原地轉一圈",
        )
        resp = await motion_get(motion_id=handle.motion_id)
    assert resp.motion.name == "Spin"
    assert resp.motion.motion_type == "custom"
    assert resp.motion.video_url is not None


# ---------------------------------------------------------------------------
# RAI / safety failure — observed via the task, not an inline raise
# ---------------------------------------------------------------------------


async def test_generate_rai_filter_surfaces_on_task_not_submission(
    make_motion_generate_deps: Any,
    bind_motion_db: async_sessionmaker[Any],
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    """A Veo RAI-filter miss does NOT fail the submission (the handle still comes
    back); it surfaces on the task as MODEL_CONTENT_FILTERED — exactly what a
    polling agent reads from task.get(task_id).error — and leaves no motion row,
    so motion.get(motion_id) is NOT_FOUND_MOTION."""
    make_motion_generate_deps(_RaiFilteredVeoStub())
    character_storage.put(seeded_character["base_image_key"], _png_bytes(), "image/png")
    with auth_as(user_id=seeded_character["owner_id"]):
        handle = await motion_generate(
            target_type="base",
            target_id=seeded_character["base_id"],
            motion_type="preset_wave",
            name="Doomed",
        )
    # Submission succeeded — the agent has a handle to poll.
    assert handle.status == "queued"

    # The failure lives on the task (this is what task.get returns to the agent).
    task = await _get_task(bind_motion_db, handle.task_id)
    assert task.status == "failed"
    assert task.error["code"] == "MODEL_CONTENT_FILTERED"
    assert task.error["retryable"] is True

    # Worker wrote no motion row → the entity fetch is a clean not-found.
    assert await _count_motions(bind_motion_db) == 0
    with auth_as(user_id=seeded_character["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await motion_get(motion_id=handle.motion_id)
    assert tool_error_code(excinfo.value) == "NOT_FOUND_MOTION"


# ---------------------------------------------------------------------------
# Scope + synchronous (queueing-phase) validation
# ---------------------------------------------------------------------------


async def test_generate_read_only_scope_rejected() -> None:
    """motion.generate needs character:write + task:read; read-only fails closed."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_CHARACTER_READ})):
        with pytest.raises(ToolError) as excinfo:
            await motion_generate(
                target_type="base",
                target_id=uuid.uuid4(),
                motion_type="preset_wave",
                name="NoScope",
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["error"]["code"] == "AUTH_INSUFFICIENT_SCOPE"


async def test_generate_m2m_token_rejected() -> None:
    """M2M tokens carry no user; motion.generate is user-scoped → fail closed."""
    with auth_as(user_id=None, is_m2m=True, client_id="agent-x"):
        with pytest.raises(ToolError) as excinfo:
            await motion_generate(
                target_type="base",
                target_id=uuid.uuid4(),
                motion_type="preset_wave",
                name="NoUser",
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["error"]["code"] == "AUTH_USER_CONTEXT_REQUIRED"


async def test_generate_unknown_target_surfaces_queueing_phase(
    make_motion_generate_deps: Any,
    seeded_character: dict[str, Any],
) -> None:
    """An unknown base id is rejected synchronously by enqueue → queueing-phase
    NOT_FOUND (no task is created, so no handle is returned)."""
    make_motion_generate_deps(VeoStub())
    with auth_as(user_id=seeded_character["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await motion_generate(
                target_type="base",
                target_id=uuid.uuid4(),
                motion_type="preset_wave",
                name="Ghost",
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "queueing"
    assert payload["error"]["code"] == "NOT_FOUND_CHARACTER"
