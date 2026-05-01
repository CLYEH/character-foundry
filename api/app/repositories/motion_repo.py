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
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, literal_column, select, update
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


async def get_any(db: AsyncSession, motion_id: uuid.UUID) -> Motion | None:
    """Fetch by id, including soft-deleted rows.

    Used by worker idempotency lookups. The active read path (and any
    user-visible read endpoint T-034 ships) should keep using
    `get_active` so soft-deleted motions stay invisible — this helper
    exists specifically so a worker retry that runs AFTER T-034's
    soft-delete races with the original attempt can still recognise
    the durable row and finalise the task. Without it, the pattern
    would be:

      1. Worker attempt A commits the motion row, crashes pre-mark_completed.
      2. T-034 soft-delete fires (or a future T-034 cleanup job).
      3. Arq retries; up-front `get_active` returns None → worker
         re-runs Veo (paying again), `motion_repo.insert` PK-collides,
         `get_active` returns None again → falls into `else: raise` →
         `INTERNAL_UNEXPECTED_ERROR` masks what was actually a
         successful (then deleted) generation.

    `get_any` lets the worker treat "row durable but invisible" the
    same as "row durable and visible": the task finalises against the
    soft-deleted row's id, and the user/API surface still sees the
    delete via the active read path. Codex review on the T-033 PR
    flagged this as a divergence from `create_checkpoint.py`
    (checkpoints aren't soft-deleted, so the issue doesn't exist
    there).
    """
    return await db.get(Motion, motion_id)


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


async def find_active_by_parent_and_name_excluding(
    db: AsyncSession,
    *,
    parent_type: str,
    parent_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID,
) -> Motion | None:
    """Like `find_active_by_parent_and_name` but skips a row by id.

    Used by the rename path so a no-op-ish rename (case differs but row
    is the same row) doesn't false-positive on its own existence. The
    DB-side partial UNIQUE indexes (`uq_motions_*_name`) are still the
    durable race guard.
    """
    stmt = select(Motion).where(
        Motion.name == name,
        Motion.deleted_at.is_(None),
        Motion.id != exclude_id,
    )
    if parent_type == "base":
        stmt = stmt.where(Motion.base_id == parent_id)
    elif parent_type == "alias":
        stmt = stmt.where(Motion.alias_id == parent_id)
    else:  # pragma: no cover — caller responsibility
        raise ValueError(f"unknown parent_type: {parent_type!r}")
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_active_for_parent(
    db: AsyncSession,
    *,
    parent_type: str,
    parent_id: uuid.UUID,
) -> Sequence[Motion]:
    """Active motions under a parent (Base | Alias), preset rows first.

    Sort order per T-034 §Scope: preset motions are pinned ahead of
    customs (the frontend renders 5 fixed preset slots, then customs
    after); within each group order is `created_at ASC` for
    reproducibility. The preset-first split is encoded as a
    `motion_type LIKE 'preset_%'` boolean — DESC puts True (preset)
    ahead of False (custom). The id tiebreaker keeps multi-row tests
    deterministic when wall-clock collisions happen.
    """
    preset_first: ColumnElement[bool] = literal_column("motion_type LIKE 'preset_%'")
    stmt = select(Motion).where(Motion.deleted_at.is_(None))
    if parent_type == "base":
        stmt = stmt.where(Motion.base_id == parent_id)
    elif parent_type == "alias":
        stmt = stmt.where(Motion.alias_id == parent_id)
    else:  # pragma: no cover — caller responsibility
        raise ValueError(f"unknown parent_type: {parent_type!r}")
    stmt = stmt.order_by(preset_first.desc(), Motion.created_at.asc(), Motion.id.asc())
    result = await db.execute(stmt)
    return result.scalars().all()


async def soft_delete(db: AsyncSession, motion: Motion) -> None:
    """Stamp `deleted_at` on a motion row in-place. Caller commits.

    Mirrors `alias_repo.soft_delete` — keep the mutation local so the
    service layer can compose it inside a larger transaction (e.g. a
    future cascade from a base deletion) without each repo committing
    independently.
    """
    motion.deleted_at = datetime.now(UTC)


async def count_active_for_alias(
    db: AsyncSession,
    *,
    alias_id: uuid.UUID,
) -> int:
    """Number of non-deleted motions under an alias.

    Used by the alias detail surface (T-032) so the API matches the
    `motion_count` field on `AliasDTO`. A scalar count keeps the read
    cheap — the alias detail already pays for one row fetch + an
    ownership check; an extra `SELECT COUNT(*)` is the cheapest way to
    keep the contract honest without loading rows we don't render.
    """
    stmt = select(func.count(Motion.id)).where(
        Motion.alias_id == alias_id,
        Motion.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    return int(result.scalar_one())


async def soft_delete_for_alias(
    db: AsyncSession,
    *,
    alias_id: uuid.UUID,
) -> None:
    """Cascade soft-delete: stamp `deleted_at` on every active motion
    bound to the given alias.

    Why bulk UPDATE instead of FK ON DELETE CASCADE: the FK cascade
    (defined on `motions.alias_id`) hard-deletes rows, which would
    discard the audit trail this codebase preserves via the
    `deleted_at` convention (lifecycle.md §soft delete). The service
    layer pairs this with `alias_repo.soft_delete` inside the same
    transaction so observers see both the alias and its motions
    disappear atomically.
    """
    stmt = (
        update(Motion)
        .where(Motion.alias_id == alias_id, Motion.deleted_at.is_(None))
        .values(deleted_at=datetime.now(UTC))
    )
    await db.execute(stmt)


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
