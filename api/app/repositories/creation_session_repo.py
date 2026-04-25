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
