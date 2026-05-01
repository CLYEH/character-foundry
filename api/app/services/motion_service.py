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
from collections.abc import Sequence
from dataclasses import dataclass

from arq.connections import ArqRedis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AgentErrorException,
    not_found_character,
)
from app.core.permissions import (
    assert_can_modify_character,
    assert_can_read_character,
)
from app.models.generation_log import GenerationLog
from app.models.motion import Motion
from app.models.user import User
from app.prompt.errors import (
    conflict_motion_duplicate_name,
    conflict_motion_preset_already_exists,
    not_found_alias,
    not_found_motion,
    validation_motion_custom_requires_description,
    validation_motion_name_invalid,
    validation_preset_rename_forbidden,
)
from app.repositories import (
    alias_repo,
    base_repo,
    character_repo,
    generation_log_repo,
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


# ---------------------------------------------------------------------------
# T-034: read / rename / soft-delete
#
# Auth split mirrors `alias_service` (T-032):
#   - GET list / detail → team-wide read (`assert_can_read_character`).
#     A teammate can already see motion counts via embedded
#     `CharacterDetail.motions_summary`; per-id reads matching that
#     visibility avoids "summary works, standalone 403s" UX gotchas.
#     The ticket text "全部 endpoint owner-only" is read as
#     "writes only" — consistent with how T-032 interpreted the alias
#     CRUD ticket.
#   - PATCH / DELETE → owner only (`assert_can_modify_character`).
#
# Cross-team callers always see NOT_FOUND_MOTION (no team-existence
# probe). NOT_FOUND_CHARACTER raised by the perm helper is translated
# to NOT_FOUND_MOTION on the per-id surface so the response code stays
# anchored on the resource the caller asked about; the 403 (same-team-
# non-owner) passes through untouched.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedMotionParent:
    """Carrier for the motion's parent + owning character.

    Used by the per-id resolution helper to give the route layer enough
    context to mint signed URLs and run perm checks without re-querying.
    """

    parent_type: MotionParentType
    parent_id: uuid.UUID


async def _resolve_motion_with_perm(
    db: AsyncSession,
    *,
    user: User,
    motion_id: uuid.UUID,
    require_owner: bool,
) -> tuple[Motion, _ResolvedMotionParent]:
    """Fetch an active motion + run perm check against its owning Character.

    The motion → parent (Base | Alias) → Character chain has to be
    walked to find the team / owner. Soft-deleted parents collapse to
    NOT_FOUND_MOTION so the response can't leak parent state — same
    opacity convention `_resolve_alias_with_perm` uses.

    `require_owner=True` for writes (PATCH / DELETE), `False` for the
    GET detail surface.
    """
    motion = await motion_repo.get_active(db, motion_id)
    if motion is None:
        raise not_found_motion()

    parent_type: MotionParentType
    parent_id: uuid.UUID
    if motion.base_id is not None:
        parent_type = "base"
        parent_id = motion.base_id
        base = await base_repo.get(db, motion.base_id)
        if base is None:
            raise not_found_motion()
        character = await character_repo.get_active(db, base.character_id)
    elif motion.alias_id is not None:
        parent_type = "alias"
        parent_id = motion.alias_id
        alias = await alias_repo.get_active(db, motion.alias_id)
        if alias is None:
            raise not_found_motion()
        character = await character_repo.get_active(db, alias.character_id)
    else:  # pragma: no cover — `chk_motions_exactly_one_parent` guards this
        # Loud failure on schema drift, mirroring `motion_builder.build_motion_dto`.
        # Collapsing to 404 here would silently mask data corruption.
        raise RuntimeError(
            f"motion {motion.id} has neither base_id nor alias_id "
            "(chk_motions_exactly_one_parent invariant broken)"
        )

    if character is None:
        raise not_found_motion()

    try:
        if require_owner:
            assert_can_modify_character(character, user)
        else:
            assert_can_read_character(character, user)
    except AgentErrorException as exc:
        if exc.error.code == "NOT_FOUND_CHARACTER":
            raise not_found_motion() from exc
        raise

    return motion, _ResolvedMotionParent(parent_type=parent_type, parent_id=parent_id)


async def list_motions_for_parent(
    db: AsyncSession,
    *,
    user: User,
    parent_type: MotionParentType,
    parent_id: uuid.UUID,
) -> Sequence[Motion]:
    """Team-wide list of active motions under a Base or Alias.

    Cross-team / unknown parent → NOT_FOUND_CHARACTER for base and
    NOT_FOUND_ALIAS for alias (mirrors the create surface's resolution
    in `_resolve_parent_for_write`). Empty result is valid (200 with
    `items: []`).
    """
    if parent_type == "base":
        base = await base_repo.get(db, parent_id)
        if base is None:
            raise not_found_character()
        character = await character_repo.get_active(db, base.character_id)
        if character is None:
            raise not_found_character()
        assert_can_read_character(character, user)
    else:
        alias = await alias_repo.get_active(db, parent_id)
        if alias is None:
            raise not_found_alias()
        character = await character_repo.get_active(db, alias.character_id)
        if character is None or character.team_id != user.team_id:
            raise not_found_alias()
        # Same-team teammate falls through; no owner check on the read
        # path.

    return await motion_repo.list_active_for_parent(
        db, parent_type=parent_type, parent_id=parent_id
    )


@dataclass(frozen=True)
class MotionDetail:
    """Service-layer carrier for `GET /v1/motions/{id}`.

    Bundles the motion row with its (optional) generation log so the
    route can build the DTO without a second round-trip. Generation
    log lookup is best-effort: a missing log surfaces as `None`, not a
    404 — the audit row partition could have been pruned (Phase 1
    won't actually prune, but the contract should tolerate it).
    """

    motion: Motion
    parent_type: MotionParentType
    parent_id: uuid.UUID
    generation_log: GenerationLog | None


async def get_motion_detail(
    db: AsyncSession,
    *,
    user: User,
    motion_id: uuid.UUID,
) -> MotionDetail:
    """Team-wide detail read."""
    motion, parent = await _resolve_motion_with_perm(
        db, user=user, motion_id=motion_id, require_owner=False
    )
    generation_log: GenerationLog | None = None
    if motion.generation_log_id is not None:
        generation_log = await generation_log_repo.get_by_id(db, motion.generation_log_id)
    return MotionDetail(
        motion=motion,
        parent_type=parent.parent_type,
        parent_id=parent.parent_id,
        generation_log=generation_log,
    )


async def update_motion_name(
    db: AsyncSession,
    *,
    user: User,
    motion_id: uuid.UUID,
    new_name: str,
) -> Motion:
    """Rename a (custom) motion.

    Preset motions are name-locked → 422 `VALIDATION_PRESET_RENAME_FORBIDDEN`.
    Same-parent duplicate → 409 `CONFLICT_DUPLICATE_NAME`. Invalid
    chars → 400 `VALIDATION_INVALID_CHARS`. No-op rename short-circuits
    so PATCH stays idempotent (mirrors `update_alias_name`).
    """
    if not name_pattern_ok(new_name):
        raise validation_motion_name_invalid()

    motion, parent = await _resolve_motion_with_perm(
        db, user=user, motion_id=motion_id, require_owner=True
    )

    if motion.motion_type != "custom":
        # Preset rename is rejected after the motion has been resolved
        # (and the perm gate has fired), so a non-owner trying to rename
        # a preset still sees 403 — not 422 leaking the preset's
        # existence to a forbidden caller.
        raise validation_preset_rename_forbidden()

    if motion.name == new_name:
        return motion

    duplicate = await motion_repo.find_active_by_parent_and_name_excluding(
        db,
        parent_type=parent.parent_type,
        parent_id=parent.parent_id,
        name=new_name,
        exclude_id=motion.id,
    )
    if duplicate is not None:
        raise conflict_motion_duplicate_name()

    motion.name = new_name
    try:
        await db.commit()
    except IntegrityError as exc:
        # The partial UNIQUE indexes (`uq_motions_*_name`) are the
        # durable race guard. Translate to the same friendly 409 the
        # pre-check raises.
        await db.rollback()
        msg = str(exc.orig) if exc.orig is not None else str(exc)
        if "uq_motions_base_name" in msg or "uq_motions_alias_name" in msg:
            raise conflict_motion_duplicate_name() from exc
        raise
    await db.refresh(motion)
    return motion


async def soft_delete_motion(
    db: AsyncSession,
    *,
    user: User,
    motion_id: uuid.UUID,
) -> None:
    """Soft-delete a single motion.

    No cascade — motions are leaves in the (Base | Alias) → Motion
    hierarchy. Storage cleanup of the video / thumbnail is deferred to
    the Sprint 5 cleanup job (T-034 §Not in scope).
    """
    motion, _parent = await _resolve_motion_with_perm(
        db, user=user, motion_id=motion_id, require_owner=True
    )
    await motion_repo.soft_delete(db, motion)
    await db.commit()
