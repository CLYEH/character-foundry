"""Pure DB ops for the `checkpoints` table.

Inserts use `INSERT ... RETURNING` so the worker can detect
`(creation_session_id, sequence)` UNIQUE collisions without first
re-flushing through the ORM identity map.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkpoint import Checkpoint


async def get(db: AsyncSession, checkpoint_id: uuid.UUID) -> Checkpoint | None:
    return await db.get(Checkpoint, checkpoint_id)


async def list_by_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
) -> Sequence[Checkpoint]:
    """Return checkpoints ordered by sequence ASC — matches the UI's
    iteration history rendering and is stable under retry."""
    stmt = (
        select(Checkpoint)
        .where(Checkpoint.creation_session_id == session_id)
        .order_by(Checkpoint.sequence.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def insert(
    db: AsyncSession,
    *,
    checkpoint_id: uuid.UUID,
    creation_session_id: uuid.UUID,
    sequence: int,
    prompt: str,
    user_menu_selections: dict[str, Any] | None,
    user_freeform_note: str | None,
    reference_image_keys: list[str] | None,
    seed: str | None,
    output_image_key: str,
    generation_log_id: uuid.UUID | None,
) -> Checkpoint:
    """Insert a row with caller-supplied id + sequence (both reserved by
    the enqueue path so the SSE result DTO carries a real id from the
    moment the task is created)."""
    row = Checkpoint(
        id=checkpoint_id,
        creation_session_id=creation_session_id,
        sequence=sequence,
        prompt=prompt,
        user_menu_selections=user_menu_selections,
        user_freeform_note=user_freeform_note,
        reference_image_keys=reference_image_keys,
        seed=seed,
        output_image_key=output_image_key,
        generation_log_id=generation_log_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row
