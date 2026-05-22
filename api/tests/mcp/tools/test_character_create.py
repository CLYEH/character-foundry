"""Tests for the packaged `character.create` MCP tool (T-084 / T-087).

`character.create` orchestrates the full Base-creation flow as one tool call.
Unlike the non-blocking `motion.generate` / `alias.add` it stays blocking (it
runs select-base server-side after the checkpoint task), but per T-087 it emits
an early `recovery_handle` progress notification (character_id + session_id, then
+ the checkpoint task_id) so a dropped connection can resume via character.get /
task.get. These tests drive the handler directly with the MCP auth contextvar set
(`auth_as`) and an inline arq pool that runs the checkpoint worker synchronously
on enqueue (`make_character_create_deps`), so the tool's poll loop sees a terminal
task without a real worker process or any sleeps.

Progress notifications are asserted via a recording fake `ctx` — the real
streamable-HTTP round-trip of the shared `report_progress` helper stays
covered by `tests/mcp/test_skeleton.py::test_progress_notification_reaches_client`.
"""

from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.base import AIGenerationResult
from app.ai.stub import StubAIClient
from app.core.errors import AgentError, AgentErrorException
from app.mcp.tools.character import character_create
from tests.mcp.tools.conftest import auth_as

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles
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


class _FailingAIClient(StubAIClient):
    """Stub whose text2image raises — drives the checkpoint-failure path.

    The worker catches the AgentError and marks the task `failed`; the tool's
    poll loop then surfaces it as a `running_checkpoint` phase error.
    """

    async def generate_image_text2image(
        self,
        prompt: str,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> AIGenerationResult:
        raise AgentErrorException(
            AgentError(
                code="MODEL_INVALID_REQUEST",
                message="模型拒絕了這個請求",
                problem="Stub forced a generation failure.",
                cause="test double",
                fix="n/a (test)",
                retryable=False,
            )
        )


def _png_b64() -> str:
    buf = BytesIO()
    Image.new("RGB", (16, 16), "red").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _gif_b64() -> str:
    buf = BytesIO()
    Image.new("RGB", (16, 16), "green").save(buf, format="GIF")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _tool_error_payload(exc: ToolError) -> dict[str, Any]:
    text = str(exc.args[0])
    brace = text.find("{")
    assert brace != -1, f"expected JSON payload, got {text!r}"
    return json.loads(text[brace:])  # type: ignore[no-any-return]


def _recovery_handles(ctx: RecordingContext) -> list[dict[str, str]]:
    """Extract the T-087 recovery handles from the recorded progress messages.

    The recovery handle rides a progress notification whose `message` is JSON
    keyed `recovery_handle`; the plain phase-label notifications are ignored.
    """
    out: list[dict[str, str]] = []
    for msg in ctx.phases:
        if not msg:
            continue
        try:
            parsed = json.loads(msg)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and "recovery_handle" in parsed:
            out.append(parsed["recovery_handle"])
    return out


async def _session_status_for_character(
    factory: async_sessionmaker[Any], *, owner_id: Any, name: str
) -> str | None:
    """Look up the creation-session status of a character created by the tool.

    Used by the failure tests to assert the half-built session was abandoned
    (the character row itself persists — only the session is abandoned).
    """
    from app.models.character import Character
    from app.models.creation_session import CreationSession

    async with factory() as db:
        character = (
            await db.execute(
                select(Character).where(Character.owner_id == owner_id, Character.name == name)
            )
        ).scalar_one_or_none()
        if character is None or character.creation_session_id is None:
            return None
        session = await db.get(CreationSession, character.creation_session_id)
        return session.status if session is not None else None


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_template_mode_creates_character_with_base(
    make_character_create_deps: Any,
    seeded_user: dict[str, Any],
) -> None:
    make_character_create_deps(StubAIClient())
    ctx = RecordingContext()
    with auth_as(user_id=seeded_user["id"]):
        result = await character_create(
            name="Tmpl-Hero",
            input_mode="template",
            menu_selections={"gender": "female"},
            freeform_note="古風",
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert result.character.name == "Tmpl-Hero"
    assert result.base is not None
    assert result.base.character_id == result.character.id
    # The detail DTO has the Base populated (creation completed end-to-end).
    assert result.character.base is not None
    # Per-phase progress notifications arrived.
    assert "creating_session" in ctx.phases
    assert "running_checkpoint" in ctx.phases
    assert "selecting_base" in ctx.phases


async def test_reference_mode_uploads_then_creates(
    make_character_create_deps: Any,
    seeded_user: dict[str, Any],
) -> None:
    make_character_create_deps(StubAIClient())
    ctx = RecordingContext()
    with auth_as(user_id=seeded_user["id"]):
        result = await character_create(
            name="Ref-Hero",
            input_mode="reference",
            reference_images=[_png_b64()],
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert result.character.name == "Ref-Hero"
    assert result.base is not None
    assert "uploading_references" in ctx.phases
    assert "running_checkpoint" in ctx.phases
    assert "selecting_base" in ctx.phases


async def test_emits_early_recovery_handle(
    make_character_create_deps: Any,
    seeded_user: dict[str, Any],
) -> None:
    """T-087: character.create stays blocking but pushes a recovery handle through
    progress early — character_id + session_id right after the session is created,
    then the checkpoint task_id once enqueued — so a dropped connection can resume
    via character.get / character.get_session / task.get."""
    make_character_create_deps(StubAIClient())
    ctx = RecordingContext()
    with auth_as(user_id=seeded_user["id"]):
        result = await character_create(
            name="Recoverable-Hero",
            input_mode="template",
            ctx=ctx,  # type: ignore[arg-type]
        )
    handles = _recovery_handles(ctx)
    assert handles, "expected at least one recovery_handle progress notification"
    # The first handle (after session creation) carries character_id + session_id.
    first = handles[0]
    assert first["character_id"] == str(result.character.id)
    assert "session_id" in first
    # A later handle (after checkpoint enqueue) adds the task_id to poll.
    assert any("task_id" in h for h in handles)


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_checkpoint_failure_surfaces_phase_and_abandons(
    make_character_create_deps: Any,
    bind_character_db: async_sessionmaker[Any],
    seeded_user: dict[str, Any],
) -> None:
    make_character_create_deps(_FailingAIClient())
    with auth_as(user_id=seeded_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await character_create(name="Doomed-Hero", input_mode="template")
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "running_checkpoint"
    assert payload["error"]["code"] == "MODEL_INVALID_REQUEST"
    # The half-built session was abandoned (no in_progress leak).
    status = await _session_status_for_character(
        bind_character_db, owner_id=seeded_user["id"], name="Doomed-Hero"
    )
    assert status == "abandoned"


async def test_reference_upload_failure_surfaces_phase_and_abandons(
    make_character_create_deps: Any,
    bind_character_db: async_sessionmaker[Any],
    seeded_user: dict[str, Any],
) -> None:
    make_character_create_deps(StubAIClient())
    with auth_as(user_id=seeded_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await character_create(
                name="Bad-Ref-Hero",
                input_mode="reference",
                reference_images=["!!!not-valid-base64!!!"],
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "uploading_references"
    assert payload["error"]["code"] == "VALIDATION_REFERENCE_IMAGE_UNDECODABLE"
    status = await _session_status_for_character(
        bind_character_db, owner_id=seeded_user["id"], name="Bad-Ref-Hero"
    )
    assert status == "abandoned"


async def test_reference_mode_rejects_unsupported_format(
    make_character_create_deps: Any,
    bind_character_db: async_sessionmaker[Any],
    seeded_user: dict[str, Any],
) -> None:
    """A decodable-but-disallowed format (GIF) is rejected, matching the REST
    upload allowlist (PNG/JPEG/WebP), and the session is abandoned."""
    make_character_create_deps(StubAIClient())
    with auth_as(user_id=seeded_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await character_create(
                name="Gif-Hero",
                input_mode="reference",
                reference_images=[_gif_b64()],
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "uploading_references"
    assert payload["error"]["code"] == "VALIDATION_REFERENCE_IMAGE_TYPE"
    status = await _session_status_for_character(
        bind_character_db, owner_id=seeded_user["id"], name="Gif-Hero"
    )
    assert status == "abandoned"


async def test_storage_failure_surfaces_phase_and_abandons(
    make_character_create_deps: Any,
    bind_character_db: async_sessionmaker[Any],
    character_storage: Any,
    seeded_user: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-AgentError infra failure (StorageError from storage.put) inside a
    phase must still abandon the session + return a phase-tagged structured
    error — not leave the session stuck in_progress with an unstructured failure
    (Codex PR #111 P1)."""
    from app.storage.errors import StorageError

    make_character_create_deps(StubAIClient())

    def _raise_put(*_args: Any, **_kwargs: Any) -> None:
        raise StorageError("simulated storage outage (test)")

    monkeypatch.setattr(character_storage, "put", _raise_put)

    with auth_as(user_id=seeded_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await character_create(
                name="Storage-Fail-Hero",
                input_mode="reference",
                reference_images=[_png_b64()],
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "uploading_references"
    assert payload["error"]["code"] == "INTERNAL_UNEXPECTED_ERROR"
    status = await _session_status_for_character(
        bind_character_db, owner_id=seeded_user["id"], name="Storage-Fail-Hero"
    )
    assert status == "abandoned"


async def test_preflight_infra_failure_surfaces_creating_phase(
    make_character_create_deps: Any,
    seeded_user: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preflight dep failure (Redis/arq outage during `get_redis()`) surfaces
    as a `creating_session` phase-tagged error, not a raw exception — the dep
    accessors are awaited inside the phase-1 try (Codex PR #111 P2). No session
    is created yet, so there's nothing to abandon."""
    make_character_create_deps(StubAIClient())

    async def _boom() -> Any:
        raise RuntimeError("redis outage (test)")

    monkeypatch.setattr("app.mcp.tools.character.get_redis", _boom)

    with auth_as(user_id=seeded_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await character_create(name="Redis-Down-Hero", input_mode="template")
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "creating_session"
    assert payload["error"]["code"] == "INTERNAL_UNEXPECTED_ERROR"


async def test_reference_mode_requires_images(
    make_character_create_deps: Any,
    bind_character_db: async_sessionmaker[Any],
    seeded_user: dict[str, Any],
) -> None:
    """Reference mode with no images fails at the uploading phase, not later."""
    make_character_create_deps(StubAIClient())
    with auth_as(user_id=seeded_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await character_create(name="No-Ref-Hero", input_mode="reference")
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "uploading_references"
    assert payload["error"]["code"] == "VALIDATION_REFERENCE_IMAGE_REQUIRED"
    status = await _session_status_for_character(
        bind_character_db, owner_id=seeded_user["id"], name="No-Ref-Hero"
    )
    assert status == "abandoned"
