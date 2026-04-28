"""Pure DB ops for the `bases` table (T-018).

The `bases` row is written once by the select-base flow and
never updated thereafter — `bases.image_key` shares the storage
key of the source checkpoint (no file copy), and the schema has
`from_checkpoint_id ON DELETE RESTRICT` so the source row stays
pinned in place.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BaseAsset


async def get_by_character_id(
    db: AsyncSession,
    character_id: uuid.UUID,
) -> BaseAsset | None:
    """One-Base-per-Character is enforced by the column-level UNIQUE on
    `bases.character_id`. Used by DTO builders to derive
    `base_thumbnail_url` from `image_key` at read time."""
    stmt = select(BaseAsset).where(BaseAsset.character_id == character_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def insert(
    db: AsyncSession,
    *,
    character_id: uuid.UUID,
    from_checkpoint_id: uuid.UUID,
    image_key: str,
    image_embedding: list[float] | None,
) -> BaseAsset:
    """Insert with caller-supplied attributes (image_key + embedding
    are inherited from the source checkpoint per ticket T-018; we
    don't copy the storage file — both rows reference the same key)."""
    row = BaseAsset(
        character_id=character_id,
        from_checkpoint_id=from_checkpoint_id,
        image_key=image_key,
        image_embedding=image_embedding,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row
