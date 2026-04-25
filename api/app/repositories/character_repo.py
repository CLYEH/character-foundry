"""Pure DB ops for the `characters` table.

Soft-delete-aware throughout: every read filters `deleted_at IS NULL`
unless the caller explicitly opts into the "include deleted" path
(needed by `/restore` to find rows currently hidden from list views).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.character import Character

__all__ = [
    "Cursor",
    "decode_cursor",
    "get_active",
    "get_including_deleted",
    "list_for_team",
    "name_exists_for_owner",
    "slug_exists_for_owner",
]


# ---------------------------------------------------------------------------
# Cursor encoding — opaque to callers; we just need a stable round-trip.
# Format: base64(`<ISO8601 with +00:00>|<uuid>`). Why not just (created_at,
# id)? `(updated_at, id)` is what the list endpoint sorts on; pagination
# must use the same key or the second page can skip / repeat rows.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cursor:
    updated_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        raw = f"{self.updated_at.isoformat()}|{self.id}"
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> Cursor | None:
    """Best-effort cursor decode. Returns None for malformed input —
    treats a bad cursor like "first page" rather than 400ing, so a
    client that lost track of state can recover by sending an empty
    cursor instead of needing to handle a new error code."""
    try:
        # Re-pad to a multiple of 4 chars (base64.urlsafe_b64decode is
        # strict about padding, but we strip it for cleaner URLs).
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        ts_str, id_str = raw.split("|", 1)
        return Cursor(updated_at=datetime.fromisoformat(ts_str), id=uuid.UUID(id_str))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def get_active(db: AsyncSession, character_id: uuid.UUID) -> Character | None:
    """Fetch by id, ignoring soft-deleted rows."""
    stmt = select(Character).where(
        Character.id == character_id,
        Character.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_including_deleted(db: AsyncSession, character_id: uuid.UUID) -> Character | None:
    """Fetch by id including soft-deleted rows. Used by `/restore`."""
    return await db.get(Character, character_id)


async def slug_exists_for_owner(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    slug: str,
) -> bool:
    """Used by the slug uniqueness probe. Soft-deleted rows don't
    count: their slug is hidden from list views and the partial unique
    index lets a new active row reclaim it."""
    stmt = (
        select(Character.id)
        .where(
            Character.owner_id == owner_id,
            Character.slug == slug,
            Character.deleted_at.is_(None),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def name_exists_for_owner(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    """Soft-delete-aware name uniqueness probe. `exclude_id` lets PATCH
    skip the row being updated when checking for conflicts."""
    stmt = select(Character.id).where(
        Character.owner_id == owner_id,
        Character.name == name,
        Character.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Character.id != exclude_id)
    stmt = stmt.limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def list_for_team(
    db: AsyncSession,
    *,
    team_id: uuid.UUID,
    owner_id: uuid.UUID | None = None,
    q: str | None = None,
    limit: int,
    cursor: Cursor | None = None,
) -> Sequence[Character]:
    """Cursor-paginated, ordered by `updated_at DESC, id DESC`.

    `owner_id` filters to that user's characters; `None` means "all
    characters in the team" (single-team Phase 1 means this is the
    whole grid). `q` is a simple ILIKE on `name`.
    """
    stmt = select(Character).where(
        Character.team_id == team_id,
        Character.deleted_at.is_(None),
    )
    if owner_id is not None:
        stmt = stmt.where(Character.owner_id == owner_id)
    if q:
        # ilike binds the value as a literal so `%` / `_` from the user
        # would be treated as wildcards. Escape them so a search for
        # "100%" doesn't degenerate into "match everything".
        escaped = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        stmt = stmt.where(Character.name.ilike(f"%{escaped}%", escape="\\"))
    if cursor is not None:
        # Composite "after this point" — strict less-than on updated_at,
        # equal-and-strict-less-than on id for the tie-break.
        stmt = stmt.where(
            or_(
                Character.updated_at < cursor.updated_at,
                and_(
                    Character.updated_at == cursor.updated_at,
                    Character.id < cursor.id,
                ),
            )
        )
    stmt = stmt.order_by(Character.updated_at.desc(), Character.id.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()
