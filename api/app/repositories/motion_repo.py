"""Pure DB ops for the `motions` table (T-033).

Polymorphic parent: every motion row has exactly one of (`base_id`,
`alias_id`) populated, enforced by `chk_motions_exactly_one_parent`.
Helpers fan out per parent kind so callers don't have to assemble the
right WHERE clause themselves.

Soft-delete-aware: every read filters `deleted_at IS NULL`. T-034 adds
the dedicated PATCH / DELETE write paths; this module only carries the
reads + insert that T-033's enqueue + worker need.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.motion import Motion


async def get_active(db: AsyncSession, motion_id: uuid.UUID) -> Motion | None:
    """Fetch by id, ignoring soft-deleted rows.

    Same convention as `alias_repo.get_active` — soft-deleted motions
    are invisible to read paths. T-034 may add a sibling
    `get_including_deleted` for restore flows.
    """
    stmt = select(Motion).where(
        Motion.id == motion_id,
        Motion.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def find_active_by_parent_and_name(
    db: AsyncSession,
    *,
    parent_type: str,
    parent_id: uuid.UUID,
    name: str,
) -> Motion | None:
    """Return a non-deleted motion under (`parent_type`, `parent_id`)
    whose name matches verbatim, or None.

    Used by the enqueue path to surface CONFLICT_DUPLICATE_NAME before
    we reserve a task — the partial UNIQUE indexes on the table
    (`uq_motions_base_name` / `uq_motions_alias_name`) are the durable
    guard, but checking up-front gives the caller a fast 409 instead
    of waiting for the worker to trip the constraint.
    """
    stmt = select(Motion).where(Motion.name == name, Motion.deleted_at.is_(None))
    if parent_type == "base":
        stmt = stmt.where(Motion.base_id == parent_id)
    elif parent_type == "alias":
        stmt = stmt.where(Motion.alias_id == parent_id)
    else:  # pragma: no cover — caller responsibility
        raise ValueError(f"unknown parent_type: {parent_type!r}")
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def find_active_preset_for_parent(
    db: AsyncSession,
    *,
    parent_type: str,
    parent_id: uuid.UUID,
    motion_type: str,
) -> Motion | None:
    """Return a non-deleted preset motion of the given type under the
    parent, or None.

    F-20 fixes the 5 preset slots per parent: each preset_* type can
    appear at most once. Surfaces as `CONFLICT_PRESET_ALREADY_EXISTS`
    in the service layer.

    Caller passes a preset `motion_type` only — preset uniqueness
    doesn't apply to `custom`.
    """
    stmt = select(Motion).where(
        Motion.motion_type == motion_type,
        Motion.deleted_at.is_(None),
    )
    if parent_type == "base":
        stmt = stmt.where(Motion.base_id == parent_id)
    elif parent_type == "alias":
        stmt = stmt.where(Motion.alias_id == parent_id)
    else:  # pragma: no cover
        raise ValueError(f"unknown parent_type: {parent_type!r}")
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def insert(
    db: AsyncSession,
    *,
    motion_id: uuid.UUID,
    parent_type: str,
    parent_id: uuid.UUID,
    motion_type: str,
    name: str,
    description: str | None,
    video_key: str,
    duration_ms: int | None,
    generation_log_id: uuid.UUID | None,
) -> Motion:
    """Insert a motion row with the caller-reserved id.

    `parent_type` selects which polymorphic FK to populate. The DB
    CHECK (`chk_motions_exactly_one_parent`) enforces the invariant so
    a caller mistake surfaces as an IntegrityError rather than a row
    that violates the type system at read time.
    """
    base_id: uuid.UUID | None = None
    alias_id: uuid.UUID | None = None
    if parent_type == "base":
        base_id = parent_id
    elif parent_type == "alias":
        alias_id = parent_id
    else:  # pragma: no cover
        raise ValueError(f"unknown parent_type: {parent_type!r}")

    row = Motion(
        id=motion_id,
        base_id=base_id,
        alias_id=alias_id,
        motion_type=motion_type,
        name=name,
        description=description,
        video_key=video_key,
        duration_ms=duration_ms,
        generation_log_id=generation_log_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row
