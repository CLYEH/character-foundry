"""Pure DB ops for the `reference_images` table."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reference_image import ReferenceImage


async def insert(
    db: AsyncSession,
    *,
    reference_id: uuid.UUID,
    creation_session_id: uuid.UUID,
    uploaded_by_user_id: uuid.UUID,
    storage_key: str,
    mime_type: str,
    size_bytes: int,
) -> ReferenceImage:
    """Insert with caller-supplied id so the route can derive the
    storage key from the same UUID it eventually exposes via
    `reference_image_id`. Keeping these two identifiers identical means
    log-tracing a stored file back to its row is a string match — and
    avoids a class of "key references the wrong row" bugs.
    """
    row = ReferenceImage(
        id=reference_id,
        creation_session_id=creation_session_id,
        uploaded_by_user_id=uploaded_by_user_id,
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def get(db: AsyncSession, reference_id: uuid.UUID) -> ReferenceImage | None:
    return await db.get(ReferenceImage, reference_id)


async def list_by_ids_in_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    reference_ids: Sequence[uuid.UUID],
) -> list[ReferenceImage]:
    """Fetch all rows whose id is in `reference_ids` AND that belong to
    `session_id`. Used by the checkpoint-create flow to validate that
    every supplied reference id belongs to the caller's session — never
    returns cross-session rows even if a malicious client knows another
    session's reference id."""
    if not reference_ids:
        return []
    stmt = select(ReferenceImage).where(
        ReferenceImage.creation_session_id == session_id,
        ReferenceImage.id.in_(list(reference_ids)),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
