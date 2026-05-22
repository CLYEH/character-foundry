"""Tests for the packaged, polymorphic `motion.generate` MCP tool (T-086).

`motion.generate` orchestrates the full motion-generation flow as one tool call
for either a Base or an Alias target. These tests drive the handler directly with
the MCP auth contextvar set (`auth_as`) and an inline arq pool that runs the
motion worker synchronously on enqueue (`make_motion_generate_deps`), so the
tool's poll loop sees a terminal task without a real worker process or sleeps.

`VeoStub` returns a bundled placeholder mp4; the AI_STUB_MODE reconciler handles
the custom path. A failing video client drives the RAI-filter path, asserting the
MCP error carries the machine-readable `MODEL_CONTENT_FILTERED` code (not a
generic envelope) plus the `running_i2v` phase.

Progress notifications are asserted via a recording fake `ctx` — the real
streamable-HTTP round-trip of the shared `report_progress` helper stays covered
by `tests/mcp/test_skeleton.py::test_progress_notification_reaches_client`.
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
from app.mcp.tools.motion import motion_generate
from tests.mcp.tools.conftest import auth_as

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles + helpers
# ---------------------------------------------------------------------------


class _RecordingSession:
    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []

    async def send_progress_notification(
        self,
        *,
        progress_token: Any,
        progress: float,
        total: float | None,
        message: str | None,
        related_request_id: Any,
    ) -> None:
        self.notifications.append({"progress": progress, "total": total, "message": message})


class _RecordingMeta:
    progressToken = "test-progress-token"


class _RecordingRequestContext:
    def __init__(self, session: _RecordingSession) -> None:
        self.session = session
        self.meta = _RecordingMeta()


class RecordingContext:
    """Minimal `Context`-shaped fake that records progress notifications."""

    def __init__(self) -> None:
        self._session = _RecordingSession()
        self.request_context = _RecordingRequestContext(self._session)
        self.request_id = "test-request-id"

    @property
    def phases(self) -> list[str | None]:
        return [n["message"] for n in self._session.notifications]


class _RaiFilteredVeoStub(VeoStub):
    """Veo stub whose generate_i2v raises the RAI-filter AgentError (T-051).

    The worker catches the AgentError and marks the task `failed` with the
    `MODEL_CONTENT_FILTERED` envelope; the tool's poll loop then surfaces it as
    a `running_i2v` phase error carrying that machine-readable code.
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


# ---------------------------------------------------------------------------
# Happy paths — base/alias × preset/custom
# ---------------------------------------------------------------------------


async def test_generate_preset_on_base(
    make_motion_generate_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    make_motion_generate_deps(VeoStub())
    character_storage.put(seeded_character["base_image_key"], _png_bytes(), "image/png")
    ctx = RecordingContext()
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await motion_generate(
            target_type="base",
            target_id=seeded_character["base_id"],
            motion_type="preset_wave",
            name="Wave",
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert resp.motion.name == "Wave"
    assert resp.motion.parent.type == "base"
    assert resp.motion.parent.id == seeded_character["base_id"]
    assert resp.motion.video_url is not None
    # Detail output carries the generation subset (model name from the log).
    assert resp.motion.generation is not None
    assert resp.motion.generation.model_name == "veo-3.1"
    # All three phases fired, in order.
    assert "queueing" in ctx.phases
    assert "running_i2v" in ctx.phases
    assert "finalizing" in ctx.phases
    assert ctx.phases.index("queueing") < ctx.phases.index("finalizing")


async def test_generate_preset_on_alias(
    make_motion_generate_deps: Any,
    character_storage: Any,
    seeded_alias: dict[str, Any],
) -> None:
    make_motion_generate_deps(VeoStub())
    character_storage.put(seeded_alias["image_key"], _png_bytes(), "image/png")
    ctx = RecordingContext()
    with auth_as(user_id=seeded_alias["owner_id"]):
        resp = await motion_generate(
            target_type="alias",
            target_id=seeded_alias["id"],
            motion_type="preset_nod",
            name="Nod",
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert resp.motion.name == "Nod"
    assert resp.motion.parent.type == "alias"
    assert resp.motion.parent.id == seeded_alias["id"]
    assert resp.motion.video_url is not None
    assert "running_i2v" in ctx.phases


async def test_generate_custom_on_base(
    make_motion_generate_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    """Custom path routes the description through the (stub) reconciler."""
    make_motion_generate_deps(VeoStub())
    character_storage.put(seeded_character["base_image_key"], _png_bytes(), "image/png")
    ctx = RecordingContext()
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await motion_generate(
            target_type="base",
            target_id=seeded_character["base_id"],
            motion_type="custom",
            name="Spin",
            description="緩慢地原地轉一圈",
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert resp.motion.name == "Spin"
    assert resp.motion.motion_type == "custom"
    assert resp.motion.video_url is not None


# ---------------------------------------------------------------------------
# RAI / safety failure — machine-readable reason, phase-tagged
# ---------------------------------------------------------------------------


async def test_generate_rai_filter_surfaces_running_phase_with_reason(
    make_motion_generate_deps: Any,
    bind_motion_db: async_sessionmaker[Any],
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    """A Veo RAI-filter miss surfaces MODEL_CONTENT_FILTERED (not a generic
    envelope) tagged to the running_i2v phase; no motion row is left behind."""
    make_motion_generate_deps(_RaiFilteredVeoStub())
    character_storage.put(seeded_character["base_image_key"], _png_bytes(), "image/png")
    with auth_as(user_id=seeded_character["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await motion_generate(
                target_type="base",
                target_id=seeded_character["base_id"],
                motion_type="preset_wave",
                name="Doomed",
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "running_i2v"
    assert payload["error"]["code"] == "MODEL_CONTENT_FILTERED"
    assert payload["error"]["retryable"] is True
    # Worker marked the task failed and wrote no motion row.
    assert await _count_motions(bind_motion_db) == 0


# ---------------------------------------------------------------------------
# Scope + queueing-phase validation
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
    """An unknown base id is rejected by enqueue → queueing-phase NOT_FOUND."""
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
