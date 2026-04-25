"""End-to-end tests for the `run_create_checkpoint` worker.

Drives the worker directly with a synthetic ctx (same pattern as
`tests/tasks/test_noop_worker.py`) so we don't need a real arq process.
The stub AI client returns a fixture PNG and the stub reconciler returns
empty output — both already wired up under `AI_STUB_MODE=true`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.stub import StubAIClient
from app.models.checkpoint import Checkpoint
from app.repositories import task_repo
from app.services import task_service
from app.storage.local import LocalFilesystemBackend
from app.workers.jobs.create_checkpoint import run_create_checkpoint
from tests.tasks.conftest import FakeArqPool


def _factory_for(database_url: str) -> Any:
    engine = create_async_engine(database_url, future=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _create_session_for_user(
    factory: Any,
    *,
    user_id: uuid.UUID,
    team_id: uuid.UUID,
    name: str = "Worker_Char",
    input_mode: str = "template",
) -> dict[str, Any]:
    """Insert character + creation_session directly via ORM. Mirrors
    the API path but skips the route handler so worker tests don't need
    the full TestClient stack."""
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
            input_mode=input_mode,
        )
        db.add(session)
        await db.flush()

        character.creation_session_id = session.id
        await db.commit()
        await db.refresh(character)
        await db.refresh(session)

        return {"character_id": character.id, "session_id": session.id}


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


@pytest.mark.asyncio
async def test_worker_fresh_text2image_writes_checkpoint_and_thumbnail(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """Full happy path: stub AI + stub reconciler. Verifies:
    - task ends up `completed` with a real Checkpoint DTO in `result`
    - checkpoint row exists with the reserved id + sequence
    - storage holds the PNG and a thumbnail at the expected key
    """
    engine, factory = _factory_for(database_url)
    try:
        ids = await _create_session_for_user(
            factory,
            user_id=seeded_user["id"],
            team_id=default_team_id,
        )
        session_id = ids["session_id"]
        checkpoint_id = uuid.uuid4()

        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_checkpoint",
                input_payload={
                    "session_id": str(session_id),
                    "character_id": str(ids["character_id"]),
                    "input_mode": "template",
                    "checkpoint_id": str(checkpoint_id),
                    "sequence": 1,
                    "mode": "fresh",
                    "base_checkpoint_id": None,
                    "menu_selections": {"gender": "female"},
                    "freeform_note": "古風",
                    "reference_image_ids": [],
                    "reference_image_keys": [],
                },
            )

        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=StubAIClient())
        result = await run_create_checkpoint(ctx, str(created.task.id))
        assert result == {"task_id": str(created.task.id), "ok": True}

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "completed"
            assert task.entity_type == "checkpoint"
            assert task.entity_id == checkpoint_id
            assert task.result is not None
            assert "checkpoint" in task.result

            ckpt = await db.get(Checkpoint, checkpoint_id)
            assert ckpt is not None
            assert ckpt.creation_session_id == session_id
            assert ckpt.sequence == 1
            assert ckpt.output_image_key == f"checkpoints/{session_id}/{checkpoint_id}.png"
            assert ckpt.generation_log_id is not None

        # Storage assertions: full image + thumbnail both present.
        full = storage_root / "checkpoints" / str(session_id) / f"{checkpoint_id}.png"
        thumb = storage_root / "checkpoints" / str(session_id) / f"{checkpoint_id}_thumb.png"
        assert full.is_file()
        assert thumb.is_file()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_cancel_before_image_does_not_write_checkpoint(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """Cancel arrives before pickup → worker short-circuits, marks the
    task `cancelled`, and writes NO checkpoint row (output_image_key is
    NOT NULL — there's nothing valid to persist)."""
    engine, factory = _factory_for(database_url)
    try:
        ids = await _create_session_for_user(
            factory,
            user_id=seeded_user["id"],
            team_id=default_team_id,
            name="CancelChar",
        )
        checkpoint_id = uuid.uuid4()

        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_checkpoint",
                input_payload={
                    "session_id": str(ids["session_id"]),
                    "character_id": str(ids["character_id"]),
                    "input_mode": "template",
                    "checkpoint_id": str(checkpoint_id),
                    "sequence": 1,
                    "mode": "fresh",
                    "base_checkpoint_id": None,
                    "menu_selections": None,
                    "freeform_note": None,
                    "reference_image_ids": [],
                    "reference_image_keys": [],
                },
            )
            row = await task_repo.get(db, created.task.id)
            assert row is not None
            # Mirror the real cancel-route case A (queued → cancelled):
            # `task_service.cancel_task` sets BOTH cancel_requested AND
            # status='cancelled' for a queued task. Setting just the
            # flag would represent an incoherent state the route never
            # produces — and the worker (correctly) wouldn't transition
            # status from 'queued' on pickup.
            now = datetime.now(UTC)
            row.cancel_requested = True
            row.cancel_requested_at = now
            row.status = "cancelled"
            row.completed_at = now
            await db.commit()

        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=StubAIClient())
        result = await run_create_checkpoint(ctx, str(created.task.id))
        assert result["ok"] is False
        assert result["reason"] == "cancelled"

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "cancelled"

            ckpt = await db.get(Checkpoint, checkpoint_id)
            assert ckpt is None  # row never written
    finally:
        await engine.dispose()


class _ExplodingAIClient:
    """Image client that always raises — exercise the failure-path
    plumbing in the worker (task → failed + AgentError, no row)."""

    async def generate_image_text2image(
        self, prompt: str, *, aspect_ratio: str = "1:1", seed: int | None = None
    ) -> Any:
        from app.ai.errors import model_unavailable

        raise model_unavailable("gpt-image-2", cause="test-induced failure")

    async def generate_image_image2image(
        self,
        prompt: str,
        image: bytes,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> Any:
        return await self.generate_image_text2image(prompt, seed=seed)

    async def generate_image_inpaint(
        self,
        prompt: str,
        image: bytes,
        mask: bytes,
        *,
        aspect_ratio: str = "1:1",
        seed: int | None = None,
    ) -> Any:
        return await self.generate_image_text2image(prompt, seed=seed)


@pytest.mark.asyncio
async def test_worker_ai_failure_marks_task_failed_without_checkpoint(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
    fake_arq_pool: FakeArqPool,
    storage_root: Path,
) -> None:
    """AI client raises MODEL_UNAVAILABLE → worker writes the AgentError
    onto the task row and never inserts a checkpoint. Acceptance bullet
    #7 from the ticket."""
    engine, factory = _factory_for(database_url)
    try:
        ids = await _create_session_for_user(
            factory,
            user_id=seeded_user["id"],
            team_id=default_team_id,
            name="FailChar",
        )
        checkpoint_id = uuid.uuid4()

        async with factory() as db:
            created = await task_service.create_task(
                db,
                fake_arq_pool,  # type: ignore[arg-type]
                user_id=seeded_user["id"],
                task_type="create_checkpoint",
                input_payload={
                    "session_id": str(ids["session_id"]),
                    "character_id": str(ids["character_id"]),
                    "input_mode": "template",
                    "checkpoint_id": str(checkpoint_id),
                    "sequence": 1,
                    "mode": "fresh",
                    "base_checkpoint_id": None,
                    "menu_selections": None,
                    "freeform_note": None,
                    "reference_image_ids": [],
                    "reference_image_keys": [],
                },
            )

        ctx = _ctx_for(factory, fake_redis, storage_root, ai_client=_ExplodingAIClient())
        result = await run_create_checkpoint(ctx, str(created.task.id))
        assert result["ok"] is False

        async with factory() as db:
            task = await task_repo.get(db, created.task.id)
            assert task is not None
            assert task.status == "failed"
            assert task.error is not None
            assert task.error["code"] == "MODEL_UNAVAILABLE"

            ckpt = await db.get(Checkpoint, checkpoint_id)
            assert ckpt is None
    finally:
        await engine.dispose()
