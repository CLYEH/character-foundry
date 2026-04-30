"""Pure DB ops for the `aliases` table.

T-035 introduced the read-by-id helper; T-031 extends it with the
insert + name-uniqueness probe + mask-upload row (via `mask_repo`)
that the alias-create flow needs. T-032 will add list / soft-delete.
"""

from __future__ import annotations

import uuid
from typing import Any

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


async def name_exists_for_character(
    db: AsyncSession,
    *,
    character_id: uuid.UUID,
    name: str,
) -> bool:
    """Soft-delete-aware name uniqueness probe within a character.

    Mirrors `character_repo.name_exists_for_owner`: the partial UNIQUE
    index `uq_aliases_character_name` ignores soft-deleted rows, so the
    Python-side probe must too. The DB constraint still backs us up
    against races (insert path catches IntegrityError); this read just
    surfaces the friendly 409 before paying for the task enqueue.
    """
    stmt = (
        select(Alias.id)
        .where(
            Alias.character_id == character_id,
            Alias.name == name,
            Alias.deleted_at.is_(None),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def insert(
    db: AsyncSession,
    *,
    alias_id: uuid.UUID,
    character_id: uuid.UUID,
    name: str,
    prompt: str,
    user_freeform_note: str | None,
    input_mode: str,
    mask_data: dict[str, Any] | None,
    image_key: str,
    generation_log_id: uuid.UUID | None,
) -> Alias:
    """Insert with caller-supplied id so the worker can write storage
    files keyed by the same UUID before the row commits, and so the
    enqueue path can return `{ alias_id }` synchronously."""
    row = Alias(
        id=alias_id,
        character_id=character_id,
        name=name,
        prompt=prompt,
        user_freeform_note=user_freeform_note,
        input_mode=input_mode,
        mask_data=mask_data,
        image_key=image_key,
        generation_log_id=generation_log_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row
