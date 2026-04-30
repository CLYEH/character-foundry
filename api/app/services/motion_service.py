"""Motion orchestration: validate, reserve a motion id, enqueue the worker (T-033).

The route handlers stay thin — they pass the parsed request body and
parent ids into here. The worker (`run_create_motion`) reads the same
input_payload back from the DB.

Authorization mirrors the alias / motion read paths in
`app.services.prompt_service`:

  - Cross-team parents collapse to 404 (NOT_FOUND_CHARACTER for base,
    NOT_FOUND_ALIAS for alias) so the response can't reveal cross-team
    existence.
  - Same-team-non-owner → 403 (AUTH_INSUFFICIENT_PERMISSION) via
    `assert_can_modify_character` so the frontend can render
    "view only" affordances cleanly.

Dedup checks (`CONFLICT_PRESET_ALREADY_EXISTS`,
`CONFLICT_DUPLICATE_NAME`) run BEFORE we reserve a task row so a
malformed retry returns a fast 409 without paying for queue insertion.
The DB-side partial UNIQUE indexes are still the durable guard against
a concurrent racer slipping in between our pre-check and the worker
INSERT — the worker maps that IntegrityError back to the same code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found_character
from app.core.permissions import assert_can_modify_character
from app.models.user import User
from app.prompt.errors import (
    conflict_motion_duplicate_name,
    conflict_motion_preset_already_exists,
    not_found_alias,
    validation_motion_custom_requires_description,
    validation_motion_name_invalid,
)
from app.repositories import (
    alias_repo,
    base_repo,
    character_repo,
    motion_repo,
)
from app.schemas.character import name_pattern_ok
from app.schemas.prompt import MotionParentType, MotionType
from app.services import task_service


@dataclass(frozen=True)
class _ResolvedParent:
    parent_type: MotionParentType
    parent_id: uuid.UUID
    character_id: uuid.UUID
    image_key: str


@dataclass(frozen=True)
class EnqueuedMotion:
    task_id: uuid.UUID
    motion_id: uuid.UUID


async def _resolve_parent_for_write(
    db: AsyncSession,
    *,
    user: User,
    parent_type: MotionParentType,
    parent_id: uuid.UUID,
) -> _ResolvedParent:
    """Resolve (Base | Alias) into a `_ResolvedParent` while gating on
    write access to the owning Character.

    The image_key is captured here even though the worker rereads it —
    we use it as a presence check (the parent row exists AND the
    character can be modified by the caller) before reserving a task.
    The worker's separate read keeps the worker's idempotency guarantee
    intact even if the parent is mutated mid-flight.
    """
    if parent_type == "base":
        base = await base_repo.get(db, parent_id)
        if base is None:
            raise not_found_character()
        character = await character_repo.get_active(db, base.character_id)
        if character is None:
            raise not_found_character()
        assert_can_modify_character(character, user)
        return _ResolvedParent(
            parent_type="base",
            parent_id=base.id,
            character_id=character.id,
            image_key=base.image_key,
        )

    alias = await alias_repo.get_active(db, parent_id)
    if alias is None:
        raise not_found_alias()
    # Cross-team aliases collapse to NOT_FOUND_ALIAS (mirrors
    # `_resolve_motion_parent` in prompt_service for the alias branch).
    character = await character_repo.get_active(db, alias.character_id)
    if character is None or character.team_id != user.team_id:
        raise not_found_alias()
    # Same-team-non-owner falls through to the 403 raised by
    # `assert_can_modify_character`.
    assert_can_modify_character(character, user)
    return _ResolvedParent(
        parent_type="alias",
        parent_id=alias.id,
        character_id=character.id,
        image_key=alias.image_key,
    )


async def enqueue_motion(
    db: AsyncSession,
    arq_pool: ArqRedis,
    *,
    user: User,
    parent_type: MotionParentType,
    parent_id: uuid.UUID,
    motion_type: MotionType,
    name: str,
    description: str | None,
) -> EnqueuedMotion:
    """Validate, reserve a motion id, enqueue the create_motion worker.

    Pipeline:
      1. Resolve parent + assert write access (NOT_FOUND / 403 envelopes
         match the prompt-preview surface so frontend handlers stay
         uniform).
      2. Cross-field validation: name regex, custom requires
         description.
      3. Dedup pre-check (preset slot taken / duplicate name).
      4. Reserve a UUID + create a `create_motion` task row.

    `description` is normalised to None for preset motions even if the
    caller sent something — preset prompts are static templates per
    `app.prompt.motion_templates.PRESET_MOTION_PROMPTS`, so any
    description on the wire would be silently ignored downstream and
    saved on the row would be misleading.
    """
    parent = await _resolve_parent_for_write(
        db, user=user, parent_type=parent_type, parent_id=parent_id
    )

    if not name_pattern_ok(name):
        raise validation_motion_name_invalid()

    if motion_type == "custom":
        if not (description or "").strip():
            raise validation_motion_custom_requires_description()
        # Trim once at the boundary so the worker sees the same value
        # the dedup check below sees.
        normalised_description: str | None = description.strip() if description else None
    else:
        # Preset prompts are static; ignore any description the caller
        # supplied so the row reflects the actual prompt source.
        normalised_description = None

        existing_preset = await motion_repo.find_active_preset_for_parent(
            db,
            parent_type=parent.parent_type,
            parent_id=parent.parent_id,
            motion_type=motion_type,
        )
        if existing_preset is not None:
            raise conflict_motion_preset_already_exists()

    duplicate = await motion_repo.find_active_by_parent_and_name(
        db,
        parent_type=parent.parent_type,
        parent_id=parent.parent_id,
        name=name,
    )
    if duplicate is not None:
        raise conflict_motion_duplicate_name()

    motion_id = uuid.uuid4()
    payload: dict[str, object] = {
        "motion_id": str(motion_id),
        "parent_type": parent.parent_type,
        "parent_id": str(parent.parent_id),
        "character_id": str(parent.character_id),
        "parent_image_key": parent.image_key,
        "motion_type": motion_type,
        "name": name,
        "description": normalised_description,
    }

    created = await task_service.create_task(
        db,
        arq_pool,
        user_id=user.id,
        task_type="create_motion",
        input_payload=payload,
    )
    return EnqueuedMotion(task_id=created.task.id, motion_id=motion_id)
