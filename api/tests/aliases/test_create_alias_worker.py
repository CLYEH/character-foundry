"""End-to-end tests for the `run_create_alias` worker (T-031).

Drives the worker directly with a synthetic ctx (same pattern as
`test_create_checkpoint_worker.py`) so we don't need a real arq process.
The stub AI client returns a fixture PNG and the stub reconciler returns
empty output — both wired up under `AI_STUB_MODE=true`.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.stub import StubAIClient
from app.models.alias import Alias
from app.repositories import task_repo
from app.services import task_service
from app.storage.local import LocalFilesystemBackend
from app.workers.jobs.create_alias import run_create_alias
from tests.tasks.conftest import FakeArqPool


def _factory_for(database_url: str) -> Any:
    engine = create_async_engine(database_url, future=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _ctx_for(
    factory: Any, fake_redis: Any, storage_root: Path, *, ai_client: Any | None = None
) -> dict[str, Any]:
    storage = LocalFilesystemBackend(storage_root)
    ctx: dict[str, Any] = {
        "db_session_factory": factory,
        "redis": fake_redis,
        "storage": storage,
    }
    if ai_client is not None:
        ctx["ai_client"] = ai_client
    return ctx


def _enqueue_payload(
    *,
    seeded: dict[str, Any],
    alias_id: uuid.UUID,
    name: str = "RedDress",
    input_mode: str = "text",
    freeform_note: str | None = "穿著紅色洋裝",
    reference_keys: list[str] | None = None,
    mask_id: uuid.UUID | None = None,
    mask_key: str | None = None,
) -> dict[str, Any]:
    return {
        "character_id": str(seeded["id"]),
        "alias_id": str(alias_id),
        "name": name,
        "input_mode": input_mode,
        "freeform_note": freeform_note,
        "reference_image_ids": [],
        "reference_image_keys": reference_keys or [],
        "mask_id": str(mask_id) if mask_id else None,
        "mask_key": mask_key,
        "base_id": str(seeded["base_id"]),
        "base_image_key": seeded["base_image_key"],
    }


@pytest.mark.asyncio
async def test_worker_text_only_writes_alias_and_thumbnail(
    database_url: str,
    seeded_character_with_base: dict[str, Any],
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """Text-only happy path: stub AI dispatches to edit_image2image with
    no references. Verifies:
    - task ends up `completed` with a real Alias DTO in `result`
    - alias row exists with the reserved id + correct input_mode
    - storage holds the PNG and a thumbnail at `aliases/{alias_id}.png`
    """
    engine, factory = _factory_for(database_url)
    try:
        alias_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_alias",
                input_payload=_enqueue_payload(
                    seeded=seeded_character_with_base,
                    alias_id=alias_id,
                    name="TextAlias",
                    input_mode="text",
                ),
            )

        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=StubAIClient())
        result = await run_create_alias(ctx, str(created.task.id))
        assert result == {"task_id": str(created.task.id), "ok": True}

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "completed"
            assert task.entity_type == "alias"
            assert task.entity_id == alias_id
            assert task.result is not None
            assert "alias" in task.result

            alias = await db.get(Alias, alias_id)
            assert alias is not None
            assert alias.character_id == seeded_character_with_base["id"]
            assert alias.input_mode == "text2image"
            assert alias.image_key == f"aliases/{alias_id}.png"
            assert alias.generation_log_id is not None
            assert alias.mask_data is None

        # Storage assertions: full image + thumbnail both present.
        full = storage_root / "aliases" / f"{alias_id}.png"
        thumb = storage_root / "aliases" / f"{alias_id}_thumb.png"
        assert full.is_file()
        assert thumb.is_file()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_image_mode_dispatches_image2image(
    database_url: str,
    seeded_character_with_base: dict[str, Any],
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """`input_mode='image'` with a reference key → edit_image2image.
    The stub returns the `edit_sample.png` fixture for that path; we
    assert the persisted alias bytes match that fixture so we're sure
    the dispatch picked the right method."""
    engine, factory = _factory_for(database_url)
    try:
        # Seed a reference image at a real storage key.
        ref_key = "checkpoints/seed/refs/r1.png"
        ref_full = storage_root / ref_key
        ref_full.parent.mkdir(parents=True, exist_ok=True)
        # Use a small valid PNG so ensure_png_bytes accepts it.
        im = Image.new("RGBA", (32, 32), (10, 20, 30, 255))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        ref_full.write_bytes(buf.getvalue())

        alias_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_alias",
                input_payload=_enqueue_payload(
                    seeded=seeded_character_with_base,
                    alias_id=alias_id,
                    name="ImageAlias",
                    input_mode="image",
                    freeform_note=None,
                    reference_keys=[ref_key],
                ),
            )

        stub = StubAIClient()
        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=stub)
        result = await run_create_alias(ctx, str(created.task.id))
        assert result["ok"] is True

        # Bytes assertion: the stub returns `edit_sample.png` for
        # edit_image2image. The persisted file should match.
        full = storage_root / "aliases" / f"{alias_id}.png"
        assert full.read_bytes() == stub.edit_image_bytes

        async with factory() as db:
            alias = await db.get(Alias, alias_id)
            assert alias is not None
            assert alias.input_mode == "image2image"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_inpaint_mode_dispatches_inpaint(
    database_url: str,
    seeded_character_with_base: dict[str, Any],
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """`input_mode='inpaint'` with a mask key → edit_inpaint. Verifies
    bytes match the stub's inpaint fixture (proves dispatch picked the
    right method)."""
    engine, factory = _factory_for(database_url)
    try:
        # Mask must match the base image dimensions (the stub validates
        # this through `validate_inpaint_mask`). Base is 64x64 from the
        # `seeded_character_with_base` fixture.
        mask_id = uuid.uuid4()
        mask_key = f"creation-sessions/{seeded_character_with_base['id']}/masks/{mask_id}.png"
        mask_full = storage_root / mask_key
        mask_full.parent.mkdir(parents=True, exist_ok=True)
        # Build a 64x64 PNG with at least one transparent pixel so
        # `validate_inpaint_mask` accepts it (alpha=0 marks edit region).
        mask_im = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
        # Punch a transparent pixel.
        mask_im.putpixel((0, 0), (0, 0, 0, 0))
        buf = io.BytesIO()
        mask_im.save(buf, format="PNG")
        mask_full.write_bytes(buf.getvalue())

        # Also seed the masks DB row so worker idempotency / DTO reads
        # through the row don't trip on a missing parent record. Worker
        # reads `mask_key` directly so this is for completeness.
        from sqlalchemy import text

        async def _seed_mask() -> None:
            engine_local = create_async_engine(
                database_url, future=True, isolation_level="AUTOCOMMIT"
            )
            try:
                async with engine_local.connect() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO masks "
                            "(id, character_id, uploaded_by_user_id, "
                            " storage_key, mime_type, size_bytes) "
                            "VALUES (:i, :c, :u, :k, 'image/png', 1024)"
                        ),
                        {
                            "i": mask_id,
                            "c": seeded_character_with_base["id"],
                            "u": seeded_user["id"],
                            "k": mask_key,
                        },
                    )
            finally:
                await engine_local.dispose()

        await _seed_mask()

        alias_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_alias",
                input_payload=_enqueue_payload(
                    seeded=seeded_character_with_base,
                    alias_id=alias_id,
                    name="InpaintAlias",
                    input_mode="inpaint",
                    freeform_note=None,
                    mask_id=mask_id,
                    mask_key=mask_key,
                ),
            )

        stub = StubAIClient()
        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=stub)
        result = await run_create_alias(ctx, str(created.task.id))
        assert result["ok"] is True, result

        full = storage_root / "aliases" / f"{alias_id}.png"
        assert full.read_bytes() == stub.inpaint_image_bytes

        async with factory() as db:
            alias = await db.get(Alias, alias_id)
            assert alias is not None
            assert alias.input_mode == "inpaint"
            assert alias.mask_data is not None
            assert alias.mask_data["mask_id"] == str(mask_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_cancel_before_image_does_not_write_alias(
    database_url: str,
    seeded_character_with_base: dict[str, Any],
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """Cancel arrives before pickup → task short-circuits to cancelled
    and no alias row is written."""
    engine, factory = _factory_for(database_url)
    try:
        alias_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_alias",
                input_payload=_enqueue_payload(
                    seeded=seeded_character_with_base,
                    alias_id=alias_id,
                    name="CancelAlias",
                ),
            )
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            now = datetime.now(UTC)
            row.cancel_requested = True
            row.cancel_requested_at = now
            row.status = "cancelled"
            row.completed_at = now
            await db.commit()

        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=StubAIClient())
        result = await run_create_alias(ctx, str(created.task.id))
        assert result["ok"] is False
        assert result["reason"] == "cancelled"

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "cancelled"

            alias = await db.get(Alias, alias_id)
            assert alias is None
    finally:
        await engine.dispose()


class _ExplodingEditClient:
    """edit_image2image / edit_inpaint always raise — exercise the
    failure path. Other methods are stubs so the protocol still
    type-checks at runtime."""

    async def generate_image_text2image(
        self,
        prompt: str,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> Any:
        raise NotImplementedError

    async def generate_image_image2image(
        self,
        prompt: str,
        image: bytes,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> Any:
        raise NotImplementedError

    async def generate_image_inpaint(
        self,
        prompt: str,
        image: bytes,
        mask: bytes,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> Any:
        raise NotImplementedError

    async def edit_image2image(
        self,
        *,
        base_image_bytes: bytes,
        reference_image_bytes: list[bytes] | None,
        prompt: str,
    ) -> Any:
        from app.ai.errors import model_unavailable

        raise model_unavailable("gpt-image-2", cause="test-induced failure")

    async def edit_inpaint(
        self,
        *,
        base_image_bytes: bytes,
        mask_png_bytes: bytes,
        prompt: str,
    ) -> Any:
        from app.ai.errors import model_unavailable

        raise model_unavailable("gpt-image-2", cause="test-induced failure")


@pytest.mark.asyncio
async def test_worker_ai_failure_marks_task_failed_without_alias(
    database_url: str,
    seeded_character_with_base: dict[str, Any],
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """AI client raises MODEL_UNAVAILABLE → worker writes the AgentError
    onto the task row and never inserts an alias."""
    engine, factory = _factory_for(database_url)
    try:
        alias_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_alias",
                input_payload=_enqueue_payload(
                    seeded=seeded_character_with_base,
                    alias_id=alias_id,
                    name="FailAlias",
                ),
            )

        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=_ExplodingEditClient())
        result = await run_create_alias(ctx, str(created.task.id))
        assert result["ok"] is False

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "failed"
            assert task.error is not None
            assert task.error["code"] == "MODEL_UNAVAILABLE"

            alias = await db.get(Alias, alias_id)
            assert alias is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_publishes_completed_sse_with_alias_dto(
    database_url: str,
    seeded_character_with_base: dict[str, Any],
    seeded_user: dict[str, Any],
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """The completed SSE event carries the AliasDTO under `result.alias`.
    Subscribe before running the worker so we don't miss the publish."""
    engine, factory = _factory_for(database_url)
    try:
        alias_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_alias",
                input_payload=_enqueue_payload(
                    seeded=seeded_character_with_base,
                    alias_id=alias_id,
                    name="SseAlias",
                ),
            )

        # Subscribe to the task channel BEFORE the worker runs.
        from app.core.redis_client import task_channel

        pubsub = fake_redis.pubsub()
        await pubsub.subscribe(task_channel(created.task.id))

        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=StubAIClient())
        result = await run_create_alias(ctx, str(created.task.id))
        assert result["ok"] is True

        # Drain messages and find the `completed` event.
        import json

        completed_payload: dict[str, Any] | None = None
        for _ in range(20):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg is None:
                continue
            data = msg.get("data")
            if not isinstance(data, str):
                continue
            payload = json.loads(data)
            if payload.get("status") == "completed":
                completed_payload = payload
                break

        assert completed_payload is not None
        assert "result" in completed_payload
        assert "alias" in completed_payload["result"]
        assert completed_payload["result"]["alias"]["id"] == str(alias_id)
        await pubsub.unsubscribe()
        await pubsub.aclose()
    finally:
        await engine.dispose()
