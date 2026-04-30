"""Pure DB ops for the `masks` table.

The row is a thin handle over the storage key — bytes live under
`creation-sessions/{character_id}/masks/{mask_id}.png` per T-031 ticket
Notes. T-035 reads via `get`; T-031 adds `insert` for the upload route.
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


async def insert(
    db: AsyncSession,
    *,
    mask_id: uuid.UUID,
    character_id: uuid.UUID,
    uploaded_by_user_id: uuid.UUID,
    storage_key: str,
    mime_type: str,
    size_bytes: int,
) -> Mask:
    """Insert with caller-supplied id so the storage key derived from the
    same UUID stays in sync with the row's id (mirrors
    `reference_image_repo.insert`)."""
    row = Mask(
        id=mask_id,
        character_id=character_id,
        uploaded_by_user_id=uploaded_by_user_id,
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row
