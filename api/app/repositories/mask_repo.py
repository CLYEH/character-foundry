"""Pure DB ops for the `masks` table (T-035).

The `masks` row is written by the alias-mask upload endpoint (T-031).
T-035 only reads it — to validate that a `mask_id` referenced in a
prompt-preview body exists and belongs to a character the caller can
see.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mask import Mask


async def get(db: AsyncSession, mask_id: uuid.UUID) -> Mask | None:
    """Fetch by id. Caller is responsible for cross-checking the
    character_id against the requesting user's ownership — the row
    itself doesn't carry that signal directly (it points at a
    character)."""
    return await db.get(Mask, mask_id)
