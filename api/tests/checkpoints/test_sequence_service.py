"""Sequence allocator tests — happy path + recovery.

The recovery path is the load-bearing piece: if a deployed Redis
restarts mid-session, fresh INCRs must NOT regress the sequence counter
or two workers will write a duplicate `(creation_session_id, sequence)`
pair.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.checkpoint import Checkpoint
from app.services import sequence_service


def _factory(database_url: str) -> Any:
    engine = create_async_engine(database_url, future=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.mark.asyncio
async def test_reserve_returns_one_when_seeded_at_zero(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
) -> None:
    """T-016 SETs the key to 0 at session creation; the first INCR
    returns 1 — matches the natural 1-based sequence in the UI."""
    engine, factory = _factory(database_url)
    try:
        from app.models.character import Character
        from app.models.creation_session import CreationSession

        async with factory() as db:
            char = Character(
                team_id=default_team_id,
                owner_id=seeded_user["id"],
                name="SeqChar",
                slug="seqchar",
            )
            db.add(char)
            await db.flush()
            session = CreationSession(
                character_id=char.id,
                initiator_id=seeded_user["id"],
                input_mode="template",
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)

        # Mimic T-016's bootstrap.
        await fake_redis.set(sequence_service.checkpoint_seq_key(session.id), 0)

        async with factory() as db:
            n1 = await sequence_service.reserve_next_sequence(db, fake_redis, session_id=session.id)
            n2 = await sequence_service.reserve_next_sequence(db, fake_redis, session_id=session.id)
            n3 = await sequence_service.reserve_next_sequence(db, fake_redis, session_id=session.id)
        assert (n1, n2, n3) == (1, 2, 3)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_uses_db_baseline_when_redis_key_lost(
    database_url: str,
    seeded_user: dict[str, Any],
    default_team_id: uuid.UUID,
    fake_redis: Any,
) -> None:
    """If Redis loses the seed (eviction / restart) and there are
    already-persisted checkpoints, the next reservation must skip past
    the existing sequence numbers — otherwise the worker INSERT collides
    on UNIQUE."""
    engine, factory = _factory(database_url)
    try:
        from app.models.character import Character
        from app.models.creation_session import CreationSession

        async with factory() as db:
            char = Character(
                team_id=default_team_id,
                owner_id=seeded_user["id"],
                name="RecoverChar",
                slug="recoverchar",
            )
            db.add(char)
            await db.flush()
            session = CreationSession(
                character_id=char.id,
                initiator_id=seeded_user["id"],
                input_mode="template",
            )
            db.add(session)
            await db.flush()

            # Pre-existing checkpoints with sequence 1..3.
            for seq in (1, 2, 3):
                ckpt = Checkpoint(
                    id=uuid.uuid4(),
                    creation_session_id=session.id,
                    sequence=seq,
                    prompt="x",
                    output_image_key=f"checkpoints/{session.id}/seq{seq}.png",
                )
                db.add(ckpt)
            await db.commit()
            await db.refresh(session)

        # Don't seed Redis — simulate post-restart state where T-016's
        # SET=0 didn't survive.
        async with factory() as db:
            n = await sequence_service.reserve_next_sequence(db, fake_redis, session_id=session.id)
        assert n == 4
    finally:
        await engine.dispose()
