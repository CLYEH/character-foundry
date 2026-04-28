"""Pure DB ops for the `creation_sessions` table."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkpoint import Checkpoint
from app.models.creation_session import CreationSession


async def get(db: AsyncSession, session_id: uuid.UUID) -> CreationSession | None:
    return await db.get(CreationSession, session_id)


async def get_for_update(db: AsyncSession, session_id: uuid.UUID) -> CreationSession | None:
    """Lock the row for the duration of the current transaction.

    Used by select-base + abandon to serialize terminal-state
    transitions on the same session: without this, two writers can
    both observe `status='in_progress'` and commit conflicting end
    states (Codex T-018 round-2 P2). `SELECT ... FOR UPDATE` blocks
    the second caller until the first commits or rolls back, after
    which the loser sees the new terminal status and bails with the
    documented 409.
    """
    stmt = select(CreationSession).where(CreationSession.id == session_id).with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_checkpoints(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
) -> Sequence[Checkpoint]:
    """Ordered by `sequence ASC` so the UI's iteration view matches
    the order the user generated them."""
    stmt = (
        select(Checkpoint)
        .where(Checkpoint.creation_session_id == session_id)
        .order_by(Checkpoint.sequence.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def checkpoint_count(db: AsyncSession, session_id: uuid.UUID) -> int:
    stmt = select(func.count(Checkpoint.id)).where(Checkpoint.creation_session_id == session_id)
    result = await db.execute(stmt)
    return int(result.scalar_one())
