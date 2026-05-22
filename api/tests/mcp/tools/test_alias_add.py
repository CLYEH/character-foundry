"""Tests for the packaged `alias.add` MCP tool (T-085).

`alias.add` orchestrates the full alias-creation flow as one tool call across
the four input modes. These tests drive the handler directly with the MCP auth
contextvar set (`auth_as`) and an inline arq pool that runs the alias worker
synchronously on enqueue (`make_alias_add_deps`), so the tool's poll loop sees a
terminal task without a real worker process or any sleeps.

Progress notifications are asserted via a recording fake `ctx` — the real
streamable-HTTP round-trip of the shared `report_progress` helper stays covered
by `tests/mcp/test_skeleton.py::test_progress_notification_reaches_client`.
"""

from __future__ import annotations

import base64
import json
import uuid
from io import BytesIO
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.base import AIGenerationResult
from app.ai.stub import StubAIClient
from app.auth.scopes import SCOPE_CHARACTER_READ
from app.core.errors import AgentError, AgentErrorException
from app.mcp.tools.alias import alias_add
from app.services import alias_service
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


class _FailingAliasAIClient(StubAIClient):
    """Stub whose edit_image2image raises — drives the generation-failure path.

    The worker catches the AgentError and marks the task `failed`; the tool's
    poll loop then surfaces it as a `generating_alias` phase error.
    """

    async def edit_image2image(
        self,
        *,
        base_image_bytes: bytes,
        reference_image_bytes: list[bytes] | None,
        prompt: str,
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


def _png_bytes(size: tuple[int, int] = (16, 16), color: str = "red") -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _mask_b64(size: tuple[int, int] = (16, 16)) -> str:
    """A valid inpaint mask: same size as the base, with a transparent pixel
    marking the edit region (so `validate_inpaint_mask` accepts it)."""
    im = Image.new("RGBA", size, (0, 0, 0, 255))
    im.putpixel((0, 0), (0, 0, 0, 0))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _tool_error_payload(exc: ToolError) -> dict[str, Any]:
    text = str(exc.args[0])
    brace = text.find("{")
    assert brace != -1, f"expected JSON payload, got {text!r}"
    return json.loads(text[brace:])  # type: ignore[no-any-return]


async def _count_aliases(factory: async_sessionmaker[Any], *, character_id: uuid.UUID) -> int:
    from app.models.alias import Alias

    async with factory() as db:
        return (
            await db.execute(
                select(func.count()).select_from(Alias).where(Alias.character_id == character_id)
            )
        ).scalar_one()


def _write_base(storage: Any, seeded_character: dict[str, Any], size: tuple[int, int]) -> None:
    """Write the character's Base image to storage so the worker can read it."""
    storage.put(seeded_character["base_image_key"], _png_bytes(size), "image/png")


# ---------------------------------------------------------------------------
# Happy paths — one per input mode
# ---------------------------------------------------------------------------


async def test_text_mode_creates_alias(
    make_alias_add_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    make_alias_add_deps(StubAIClient())
    _write_base(character_storage, seeded_character, (16, 16))
    ctx = RecordingContext()
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await alias_add(
            character_id=seeded_character["id"],
            name="Text-Alias",
            input_mode="text",
            freeform_note="加上紅色斗篷",
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert resp.alias.name == "Text-Alias"
    assert resp.alias.character_id == seeded_character["id"]
    # No mask → no uploading_mask phase; generating_alias is always emitted.
    assert "uploading_mask" not in ctx.phases
    assert "generating_alias" in ctx.phases


async def test_image_mode_uses_existing_reference_ids(
    make_alias_add_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
    seeded_reference_image: dict[str, Any],
) -> None:
    make_alias_add_deps(StubAIClient())
    _write_base(character_storage, seeded_character, (16, 16))
    character_storage.put(seeded_reference_image["storage_key"], _png_bytes(), "image/png")
    ctx = RecordingContext()
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await alias_add(
            character_id=seeded_character["id"],
            name="Image-Alias",
            input_mode="image",
            reference_image_ids=[seeded_reference_image["id"]],
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert resp.alias.name == "Image-Alias"
    assert "uploading_mask" not in ctx.phases
    assert "generating_alias" in ctx.phases


async def test_inpaint_mode_mask_file_uploads_then_binds_mask_id(
    make_alias_add_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mask_file path: the tool uploads the mask first, then passes
    `{ mask: { mask_id } }` (NOT raw bytes) into the alias-create body."""
    make_alias_add_deps(StubAIClient())
    _write_base(character_storage, seeded_character, (16, 16))

    captured: dict[str, Any] = {}
    real_enqueue = alias_service.enqueue_alias

    async def _spy(db: Any, arq_pool: Any, *, user: Any, character_id: Any, body: Any) -> Any:
        captured["body"] = body
        return await real_enqueue(db, arq_pool, user=user, character_id=character_id, body=body)

    monkeypatch.setattr("app.services.alias_service.enqueue_alias", _spy)

    ctx = RecordingContext()
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await alias_add(
            character_id=seeded_character["id"],
            name="Inpaint-Alias",
            input_mode="inpaint",
            mask_file=_mask_b64((16, 16)),
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert resp.alias.name == "Inpaint-Alias"
    # Both phases fired, in order.
    assert "uploading_mask" in ctx.phases
    assert "generating_alias" in ctx.phases
    assert ctx.phases.index("uploading_mask") < ctx.phases.index("generating_alias")
    # Contract lock-in: the create body carried a MaskInput { mask_id: UUID },
    # not raw bytes (the schema makes raw bytes impossible, asserted explicitly).
    body = captured["body"]
    assert body.mask is not None
    assert isinstance(body.mask.mask_id, uuid.UUID)


async def test_inpaint_mode_mask_id_reuse_skips_upload(
    make_alias_add_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
    seeded_mask: dict[str, Any],
) -> None:
    """mask_id path: agent reuses a prior mask → no uploading_mask phase."""
    make_alias_add_deps(StubAIClient())
    _write_base(character_storage, seeded_character, (16, 16))
    # The worker reads the reused mask's bytes from storage at its key.
    character_storage.put(seeded_mask["storage_key"], _mask_bytes_for_reuse((16, 16)), "image/png")
    ctx = RecordingContext()
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await alias_add(
            character_id=seeded_character["id"],
            name="Reuse-Mask-Alias",
            input_mode="inpaint",
            mask_id=seeded_mask["id"],
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert resp.alias.name == "Reuse-Mask-Alias"
    assert "uploading_mask" not in ctx.phases
    assert "generating_alias" in ctx.phases


async def test_mixed_mode_refs_note_and_mask(
    make_alias_add_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
    seeded_reference_image: dict[str, Any],
) -> None:
    make_alias_add_deps(StubAIClient())
    _write_base(character_storage, seeded_character, (16, 16))
    character_storage.put(seeded_reference_image["storage_key"], _png_bytes(), "image/png")
    ctx = RecordingContext()
    with auth_as(user_id=seeded_character["owner_id"]):
        resp = await alias_add(
            character_id=seeded_character["id"],
            name="Mixed-Alias",
            input_mode="mixed",
            freeform_note="混合模式",
            reference_image_ids=[seeded_reference_image["id"]],
            mask_file=_mask_b64((16, 16)),
            ctx=ctx,  # type: ignore[arg-type]
        )
    assert resp.alias.name == "Mixed-Alias"
    assert "uploading_mask" in ctx.phases
    assert "generating_alias" in ctx.phases


# ---------------------------------------------------------------------------
# Tool-entry validation (pre-phase) — plain tool errors, no phase tag
# ---------------------------------------------------------------------------


async def test_reference_images_inline_bytes_rejected() -> None:
    """Q-D7: inline reference bytes are rejected with guidance toward ids."""
    with auth_as(user_id=uuid.uuid4()):
        with pytest.raises(ToolError) as excinfo:
            await alias_add(
                character_id=uuid.uuid4(),
                name="Bad-Image-Alias",
                input_mode="image",
                reference_images=[_png_b64_inline()],
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["error"]["code"] == "VALIDATION_ALIAS_REFERENCE_UPLOAD_UNSUPPORTED"
    assert "phase" not in payload  # entry validation runs before any phase


async def test_mask_file_and_mask_id_mutually_exclusive() -> None:
    with auth_as(user_id=uuid.uuid4()):
        with pytest.raises(ToolError) as excinfo:
            await alias_add(
                character_id=uuid.uuid4(),
                name="Conflict-Alias",
                input_mode="inpaint",
                mask_file=_mask_b64(),
                mask_id=uuid.uuid4(),
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["error"]["code"] == "VALIDATION_MASK_INPUT_CONFLICT"
    assert "phase" not in payload


async def test_read_only_scope_rejected() -> None:
    """alias.add needs character:write + task:read; read-only fails closed."""
    with auth_as(user_id=uuid.uuid4(), scopes=frozenset({SCOPE_CHARACTER_READ})):
        with pytest.raises(ToolError) as excinfo:
            await alias_add(
                character_id=uuid.uuid4(), name="NoScope", input_mode="text", freeform_note="x"
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["error"]["code"] == "AUTH_INSUFFICIENT_SCOPE"


# ---------------------------------------------------------------------------
# Failure paths — phase-tagged, no alias row left behind
# ---------------------------------------------------------------------------


async def test_mask_upload_failure_surfaces_uploading_phase(
    make_alias_add_deps: Any,
    bind_alias_db: async_sessionmaker[Any],
    seeded_character: dict[str, Any],
) -> None:
    make_alias_add_deps(StubAIClient())
    with auth_as(user_id=seeded_character["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await alias_add(
                character_id=seeded_character["id"],
                name="Bad-Mask-Alias",
                input_mode="inpaint",
                mask_file="!!!not-valid-base64!!!",
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "uploading_mask"
    assert payload["error"]["code"] == "VALIDATION_REFERENCE_IMAGE_UNDECODABLE"
    # No alias was created (failure happened before enqueue).
    assert await _count_aliases(bind_alias_db, character_id=seeded_character["id"]) == 0


async def test_generation_failure_surfaces_generating_phase(
    make_alias_add_deps: Any,
    bind_alias_db: async_sessionmaker[Any],
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    make_alias_add_deps(_FailingAliasAIClient())
    _write_base(character_storage, seeded_character, (16, 16))
    with auth_as(user_id=seeded_character["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await alias_add(
                character_id=seeded_character["id"],
                name="Doomed-Alias",
                input_mode="text",
                freeform_note="x",
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "generating_alias"
    assert payload["error"]["code"] == "MODEL_INVALID_REQUEST"
    # The worker marked the task failed and wrote no alias row.
    assert await _count_aliases(bind_alias_db, character_id=seeded_character["id"]) == 0


async def test_reference_id_not_in_base_session_surfaces_not_found(
    make_alias_add_deps: Any,
    character_storage: Any,
    seeded_character: dict[str, Any],
) -> None:
    """image mode with a reference id NOT from the Base source session →
    NOT_FOUND_REFERENCE_IMAGE raised at enqueue (generating_alias phase)."""
    make_alias_add_deps(StubAIClient())
    _write_base(character_storage, seeded_character, (16, 16))
    with auth_as(user_id=seeded_character["owner_id"]):
        with pytest.raises(ToolError) as excinfo:
            await alias_add(
                character_id=seeded_character["id"],
                name="Bad-Ref-Alias",
                input_mode="image",
                reference_image_ids=[uuid.uuid4()],
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "generating_alias"
    assert payload["error"]["code"] == "NOT_FOUND_REFERENCE_IMAGE"


async def test_add_non_owner_denied_at_mask_upload(
    make_alias_add_deps: Any,
    bind_alias_db: async_sessionmaker[Any],
    seeded_character: dict[str, Any],
    second_user: dict[str, Any],
) -> None:
    """A same-team non-owner can't add an alias (mask upload ownership gate);
    fails closed in the uploading_mask phase before any blob is written."""
    make_alias_add_deps(StubAIClient())
    with auth_as(user_id=second_user["id"]):
        with pytest.raises(ToolError) as excinfo:
            await alias_add(
                character_id=seeded_character["id"],
                name="Hijack-Alias",
                input_mode="inpaint",
                mask_file=_mask_b64((16, 16)),
            )
    payload = _tool_error_payload(excinfo.value)
    assert payload["phase"] == "uploading_mask"
    assert payload["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"
    assert await _count_aliases(bind_alias_db, character_id=seeded_character["id"]) == 0


# ---------------------------------------------------------------------------
# Local helpers that need to live below the test doubles
# ---------------------------------------------------------------------------


def _png_b64_inline() -> str:
    return base64.b64encode(_png_bytes()).decode("ascii")


def _mask_bytes_for_reuse(size: tuple[int, int]) -> bytes:
    im = Image.new("RGBA", size, (0, 0, 0, 255))
    im.putpixel((0, 0), (0, 0, 0, 0))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()
