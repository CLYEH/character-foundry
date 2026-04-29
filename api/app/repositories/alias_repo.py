"""Pure DB ops for the `aliases` table.

T-035 introduces this module with the read-by-id helper; T-031 / T-032
will extend it with insert / list / soft-delete operations as their
write paths land. The shared module is created here so those tickets
add to it rather than re-introducing it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alias import Alias


async def get_active(db: AsyncSession, alias_id: uuid.UUID) -> Alias | None:
    """Fetch by id, ignoring soft-deleted rows.

    Soft-deleted aliases are invisible to read paths (mirrors
    `character_repo.get_active`). T-032 may add a sibling
    `get_including_deleted` for restore flows; T-035 only needs the
    active read.
    """
    stmt = select(Alias).where(
        Alias.id == alias_id,
        Alias.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
