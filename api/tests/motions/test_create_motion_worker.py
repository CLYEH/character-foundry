"""End-to-end tests for the `run_create_motion` worker (T-033).

Drives the worker directly with a synthetic ctx. `VeoStub` returns a
bundled placeholder mp4; the stub reconciler (`AI_STUB_MODE=true`)
returns deterministic English output for the custom path.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.stub import VeoStub
from app.models.motion import Motion
from app.repositories import task_repo
from app.services import task_service
from app.storage.local import LocalFilesystemBackend
from app.workers.jobs.create_motion import run_create_motion
from tests.motions.conftest import (
    seed_alias_for_character_async,
    seed_base_for_character_async,
)
from tests.tasks.conftest import FakeArqPool


def _factory_for(database_url: str) -> Any:
    engine = create_async_engine(database_url, future=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _create_character_for_user(
    factory: Any,
    *,
    user_id: uuid.UUID,
    team_id: uuid.UUID,
    name: str = "MotionWorkerChar",
) -> dict[str, Any]:
    """Insert character + creation_session via ORM. Mirrors the API
    path but skips the route handler so worker tests don't need the
    full TestClient stack."""
    from app.models.character import Character
    from app.models.creation_session import CreationSession

    async with factory() as db:
        character = Character(
            team_id=team_id,
            owner_id=user_id,
            name=name,
            slug=name.lower(),
        )
        db.add(character)
        await db.flush()

        session = CreationSession(
            character_id=character.id,
            initiator_id=user_id,
            input_mode="template",
        )
        db.add(session)
        await db.flush()

        character.creation_session_id = session.id
        await db.commit()
        await db.refresh(character)
        await db.refresh(session)
        return {"character_id": character.id, "session_id": session.id}


def _ctx_for(
    factory: Any, fake_redis: Any, storage_root: Path, *, video_client: Any | None = None
) -> dict[str, Any]:
    storage = LocalFilesystemBackend(storage_root)
    ctx: dict[str, Any] = {
        "db_session_factory": factory,
        "redis": fake_redis,
        "storage": storage,
    }
    if video_client is not None:
        ctx["video_client"] = video_client
    return ctx


# Use a real PNG encoded by PIL so the thumbnail step can decode it
# successfully (the placeholder bytes the conftest writes by default
# are not a valid image).
def _png_parent_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    im = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_worker_preset_on_base_writes_motion_and_thumbnail(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """Full happy path with VeoStub for a preset motion under a Base.
    Verifies: task ends `completed`, motion row exists, video + thumbnail
    written to storage at the expected keys, generation_log linked."""
    engine, factory = _factory_for(database_url)
    try:
        ids = await _create_character_for_user(
            factory, user_id=seeded_user["id"], team_id=default_team_id, name="PresetBaseChar"
        )
        base_id, image_key = await seed_base_for_character_async(
            database_url,
            character_id=str(ids["character_id"]),
            creation_session_id=str(ids["session_id"]),
            storage_root=storage_root,
            image_bytes=_png_parent_bytes(),
        )

        motion_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_motion",
                input_payload={
                    "motion_id": str(motion_id),
                    "parent_type": "base",
                    "parent_id": base_id,
                    "character_id": str(ids["character_id"]),
                    "parent_image_key": image_key,
                    "motion_type": "preset_wave",
                    "name": "招手",
                    "description": None,
                },
            )

        ctx = _ctx_for(factory, fake_redis, storage_root, video_client=VeoStub())
        result = await run_create_motion(ctx, str(created.task.id))
        assert result == {"task_id": str(created.task.id), "ok": True}, result

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "completed"
            assert task.entity_type == "motion"
            assert task.entity_id == motion_id
            assert task.result is not None
            assert "motion" in task.result

            row = await db.get(Motion, motion_id)
            assert row is not None
            assert row.base_id == uuid.UUID(base_id)
            assert row.alias_id is None
            assert row.motion_type == "preset_wave"
            assert row.video_key == f"bases/{base_id}/motions/{motion_id}.mp4"
            assert row.generation_log_id is not None

        # Storage assertions: video + thumbnail both present.
        full = storage_root / "bases" / base_id / "motions" / f"{motion_id}.mp4"
        thumb = storage_root / "bases" / base_id / "motions" / f"{motion_id}_thumb.png"
        assert full.is_file()
        assert thumb.is_file()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_custom_on_alias_writes_motion(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """Custom motion on an alias parent — exercises the reconciler path
    (stubbed via AI_STUB_MODE) + the alias-prefixed storage key."""
    engine, factory = _factory_for(database_url)
    try:
        ids = await _create_character_for_user(
            factory, user_id=seeded_user["id"], team_id=default_team_id, name="CustomAliasChar"
        )
        alias_id, image_key = await seed_alias_for_character_async(
            database_url,
            character_id=str(ids["character_id"]),
            storage_root=storage_root,
            image_bytes=_png_parent_bytes(),
        )

        motion_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_motion",
                input_payload={
                    "motion_id": str(motion_id),
                    "parent_type": "alias",
                    "parent_id": alias_id,
                    "character_id": str(ids["character_id"]),
                    "parent_image_key": image_key,
                    "motion_type": "custom",
                    "name": "wave",
                    "description": "舉手揮一下",
                },
            )

        ctx = _ctx_for(factory, fake_redis, storage_root, video_client=VeoStub())
        result = await run_create_motion(ctx, str(created.task.id))
        assert result == {"task_id": str(created.task.id), "ok": True}, result

        async with factory() as db:
            row = await db.get(Motion, motion_id)
            assert row is not None
            assert row.alias_id == uuid.UUID(alias_id)
            assert row.base_id is None
            assert row.motion_type == "custom"
            assert row.description == "舉手揮一下"
            assert row.video_key == f"aliases/{alias_id}/motions/{motion_id}.mp4"

        full = storage_root / "aliases" / alias_id / "motions" / f"{motion_id}.mp4"
        assert full.is_file()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_cancel_before_video_does_not_write_motion(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """Cancel arrives before pickup → worker short-circuits, marks the
    task `cancelled`, and writes NO motion row."""
    engine, factory = _factory_for(database_url)
    try:
        ids = await _create_character_for_user(
            factory, user_id=seeded_user["id"], team_id=default_team_id, name="CancelMotionChar"
        )
        base_id, image_key = await seed_base_for_character_async(
            database_url,
            character_id=str(ids["character_id"]),
            creation_session_id=str(ids["session_id"]),
            storage_root=storage_root,
            image_bytes=_png_parent_bytes(),
        )

        motion_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_motion",
                input_payload={
                    "motion_id": str(motion_id),
                    "parent_type": "base",
                    "parent_id": base_id,
                    "character_id": str(ids["character_id"]),
                    "parent_image_key": image_key,
                    "motion_type": "preset_idle",
                    "name": "閒置",
                    "description": None,
                },
            )

        # Mirror the real cancel-route case A (queued → cancelled):
        # `task_service.cancel_task` sets BOTH cancel_requested AND
        # status='cancelled' for a queued task. Setting only the flag
        # would represent an incoherent state the route never produces
        # — and the worker (correctly) wouldn't transition status from
        # 'queued' on pickup. Same pattern as the checkpoint worker
        # cancel test (Codex P1 round-3 there).
        async def _cancel() -> None:
            ce = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
            try:
                async with ce.connect() as conn:
                    await conn.execute(
                        sql_text(
                            "UPDATE tasks SET cancel_requested = TRUE, "
                            "cancel_requested_at = NOW(), "
                            "status = 'cancelled', "
                            "completed_at = NOW() "
                            "WHERE id = :tid"
                        ),
                        {"tid": str(created.task.id)},
                    )
            finally:
                await ce.dispose()

        await _cancel()

        ctx = _ctx_for(factory, fake_redis, storage_root, video_client=VeoStub())
        result = await run_create_motion(ctx, str(created.task.id))
        # Pre-pickup cancel returns ok=False / reason=cancelled.
        assert result["ok"] is False
        assert result["reason"] == "cancelled"

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "cancelled"

            # No motion row.
            row = await db.get(Motion, motion_id)
            assert row is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_idempotent_retry_finalises_existing_motion(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """If a previous attempt committed the motion row but crashed before
    mark_completed, the retry's up-front idempotency lookup picks up
    the durable row and finalises the task as completed."""
    engine, factory = _factory_for(database_url)
    try:
        ids = await _create_character_for_user(
            factory,
            user_id=seeded_user["id"],
            team_id=default_team_id,
            name="IdempotentMotionChar",
        )
        base_id, image_key = await seed_base_for_character_async(
            database_url,
            character_id=str(ids["character_id"]),
            creation_session_id=str(ids["session_id"]),
            storage_root=storage_root,
            image_bytes=_png_parent_bytes(),
        )

        motion_id = uuid.uuid4()
        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_motion",
                input_payload={
                    "motion_id": str(motion_id),
                    "parent_type": "base",
                    "parent_id": base_id,
                    "character_id": str(ids["character_id"]),
                    "parent_image_key": image_key,
                    "motion_type": "preset_nod",
                    "name": "點頭",
                    "description": None,
                },
            )

        # Simulate the post-commit crash: insert the motion row directly
        # so the up-front idempotency lookup finds it on retry.
        async def _seed_committed() -> None:
            ce = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
            try:
                async with ce.connect() as conn:
                    await conn.execute(
                        sql_text(
                            "INSERT INTO motions (id, base_id, motion_type, name, video_key) "
                            "VALUES (:mid, :bid, 'preset_nod', '點頭', :vk)"
                        ),
                        {
                            "mid": str(motion_id),
                            "bid": base_id,
                            "vk": f"bases/{base_id}/motions/{motion_id}.mp4",
                        },
                    )
                    # Move the task into `running` so the up-front
                    # short-circuit hits the "not terminal" path and
                    # falls through to the idempotency lookup that
                    # mirrors the post-commit crash recovery.
                    await conn.execute(
                        sql_text(
                            "UPDATE tasks SET status='running', started_at=NOW() WHERE id=:tid"
                        ),
                        {"tid": str(created.task.id)},
                    )
            finally:
                await ce.dispose()

        await _seed_committed()

        ctx = _ctx_for(factory, fake_redis, storage_root, video_client=VeoStub())
        result = await run_create_motion(ctx, str(created.task.id))
        assert result["ok"] is True
        assert result.get("reason") == "already_committed"

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "completed"
            assert task.entity_type == "motion"
            assert task.entity_id == motion_id
    finally:
        await engine.dispose()
