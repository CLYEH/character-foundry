"""Alias orchestration: validate the create request, reserve an alias
id, enqueue the worker (T-031).

The route stays thin — it parses the request body and calls
`enqueue_alias`. The worker reads the same `input_payload` back and does
the heavy lifting (reconciler + AI call + storage write + DB insert).

Authorization:
- Character owner is the only writer (planning/data/storage-layout.md
  §5.2). Cross-team callers see 404, same-team-non-owner sees 403 — same
  pattern as character / checkpoint flows via `assert_can_modify_character`.

Validation matrix (per T-031 ticket §Scope):
- `inpaint`        → mask required (no refs ignored, no note ignored)
- `image`          → reference_image_ids required (>=1)
- `text`           → freeform_note required
- `mixed`          → at least one of (note / refs / mask)
The Pydantic schema is permissive on the optional fields; we apply the
matrix here so the AgentError envelope is structured.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from arq.connections import ArqRedis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AgentErrorException,
    conflict_duplicate_alias_name,
    not_found_alias,
    not_found_character,
    validation_alias_empty_input,
    validation_name_invalid,
)
from app.core.permissions import (
    assert_can_modify_character,
    assert_can_read_character,
)
from app.models.alias import Alias
from app.models.user import User
from app.prompt.errors import (
    conflict_base_not_set,
    not_found_mask,
    validation_alias_input_mode_mismatch,
)
from app.repositories import (
    alias_repo,
    base_repo,
    character_repo,
    checkpoint_repo,
    mask_repo,
    motion_repo,
    reference_image_repo,
)
from app.schemas.alias import CreateAliasRequest
from app.schemas.character import name_pattern_ok
from app.services import task_service

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnqueuedAlias:
    task_id: uuid.UUID
    alias_id: uuid.UUID


def _validate_input_mode_matrix(body: CreateAliasRequest) -> None:
    """Apply the per-mode payload requirements from T-031 §Scope.

    Raises VALIDATION_ALIAS_INPUT_MODE_MISMATCH for unmet single-field
    requirements (`inpaint` without mask, `image` without refs). The
    `text` and `mixed` modes only need at least one signal — that's
    handled by the bare empty-input check the caller does first, so
    we don't repeat it here.
    """
    has_note = bool((body.freeform_note or "").strip())
    has_refs = bool(body.reference_image_ids)
    has_mask = body.mask is not None

    if body.input_mode == "inpaint" and not has_mask:
        raise validation_alias_input_mode_mismatch(input_mode="inpaint", missing="mask")
    if body.input_mode == "image" and not has_refs:
        raise validation_alias_input_mode_mismatch(
            input_mode="image", missing="reference_image_ids"
        )
    if body.input_mode == "text" and not has_note:
        raise validation_alias_input_mode_mismatch(input_mode="text", missing="freeform_note")


async def _resolve_reference_keys(
    db: AsyncSession,
    *,
    base_source_session_id: uuid.UUID,
    reference_image_ids: Sequence[uuid.UUID],
) -> list[str]:
    """Resolve `reference_image_ids` to storage keys, scoped to the
    creation session that produced this character's Base.

    Mirrors `checkpoint_service._resolve_reference_images`: any
    requested id that doesn't belong to the same session collapses
    to NOT_FOUND_REFERENCE_IMAGE — so a caller can't probe other
    characters' / sessions' uploads via the alias-create surface.

    Why scope to the *Base's source session* specifically: aliases are
    derived from Base, and the only references the frontend can
    legitimately surface for an alias are the ones the user uploaded
    while iterating on that Base. Phase 1 has no separate "alias
    reference upload" endpoint — refs piggyback on the creation
    session that made the Base.
    """
    if not reference_image_ids:
        return []
    rows = await reference_image_repo.list_by_ids_in_session(
        db,
        session_id=base_source_session_id,
        reference_ids=list(reference_image_ids),
    )
    if len(rows) != len(set(reference_image_ids)):
        from app.core.errors import not_found_reference_image

        raise not_found_reference_image()
    # Preserve caller-specified ordering (first reference is the primary
    # conditioning input per ai-integration.md §3.2 — gpt-image-2 treats
    # the multi-image set positionally).
    by_id = {r.id: r for r in rows}
    return [by_id[rid].storage_key for rid in reference_image_ids if rid in by_id]


async def enqueue_alias(
    db: AsyncSession,
    arq_pool: ArqRedis,
    *,
    user: User,
    character_id: uuid.UUID,
    body: CreateAliasRequest,
) -> EnqueuedAlias:
    """Validate the alias-create request, reserve an alias id, enqueue
    the worker.

    No alias row is written here — the worker writes it after a
    successful AI call. The reserved id flows into `task.input_payload`
    so the SSE result publisher can emit an AliasDTO with the same id
    the row eventually carries (mirrors T-017 checkpoint flow).
    """
    character = await character_repo.get_active(db, character_id)
    if character is None:
        raise not_found_character()
    # Cross-team → 404 inside, same-team-non-owner → 403.
    assert_can_modify_character(character, user)

    # Validate name characters before any DB / queue work. `NameStr`
    # only enforces length + whitespace strip (Pydantic level); the
    # character-class regex (CJK + ASCII alphanumerics + _ + -) is the
    # DB CHECK constraint `chk_aliases_name_chars` and would otherwise
    # surface as a generic IntegrityError after the worker burns an AI
    # call. Mirror character_service.create_character's pattern (Codex
    # P1 round-1).
    if not name_pattern_ok(body.name):
        raise validation_name_invalid()

    # Cheap structural checks first — surface 422 / 409 before paying
    # for any DB lookups (mask, base, references) on a body that's
    # already known to be invalid.
    has_note = bool((body.freeform_note or "").strip())
    has_refs = bool(body.reference_image_ids)
    has_mask = body.mask is not None
    if not (has_note or has_refs or has_mask):
        raise validation_alias_empty_input()

    _validate_input_mode_matrix(body)

    # Name uniqueness probe before committing the task. The DB unique
    # index still backs us up against races (worker-side INSERT will
    # IntegrityError); this read just gives the caller a fast, friendly
    # 409 instead of a delayed task failure.
    if await alias_repo.name_exists_for_character(db, character_id=character.id, name=body.name):
        raise conflict_duplicate_alias_name()

    base = await base_repo.get_by_character_id(db, character.id)
    if base is None:
        # Aliases are derived from Base by definition; no Base means
        # alias creation is unreachable. Distinct from NOT_FOUND_CHARACTER
        # so the modal can render "請先確立基礎形象". Same code as the
        # T-035 preview surface raises.
        raise conflict_base_not_set()

    # Mask resolution: validate it exists and belongs to THIS character.
    # Cross-character mask access collapses to NOT_FOUND_MASK so the
    # response can't probe other characters' uploads (mirrors
    # NOT_FOUND_REFERENCE_IMAGE opacity). T-035 already enforces this on
    # the preview surface; mirror it on the write path.
    mask_storage_key: str | None = None
    if body.mask is not None:
        mask_row = await mask_repo.get(db, body.mask.mask_id)
        if mask_row is None or mask_row.character_id != character.id:
            raise not_found_mask()
        mask_storage_key = mask_row.storage_key

    # Resolve reference uploads against the Base's source creation session.
    # The Base row points at `from_checkpoint_id`, which carries the
    # session id on the checkpoint row.
    base_source_session_id: uuid.UUID
    base_checkpoint = await checkpoint_repo.get(db, base.from_checkpoint_id)
    if base_checkpoint is None:
        # Should be unreachable — `bases.from_checkpoint_id` has
        # `ON DELETE RESTRICT`, so deleting the source checkpoint is
        # blocked while the Base exists. Fail loudly rather than
        # silently broaden the reference scope.
        raise AssertionError(
            f"base {base.id} points at missing checkpoint {base.from_checkpoint_id}"
        )
    base_source_session_id = base_checkpoint.creation_session_id

    reference_keys = await _resolve_reference_keys(
        db,
        base_source_session_id=base_source_session_id,
        reference_image_ids=body.reference_image_ids or [],
    )

    alias_id = uuid.uuid4()
    payload: dict[str, object] = {
        "character_id": str(character.id),
        "alias_id": str(alias_id),
        "name": body.name,
        "input_mode": body.input_mode,
        "freeform_note": body.freeform_note,
        "reference_image_ids": [str(rid) for rid in (body.reference_image_ids or [])],
        "reference_image_keys": reference_keys,
        "mask_id": str(body.mask.mask_id) if body.mask is not None else None,
        "mask_key": mask_storage_key,
        "base_id": str(base.id),
        "base_image_key": base.image_key,
    }

    created = await task_service.create_task(
        db,
        arq_pool,
        user_id=user.id,
        task_type="create_alias",
        input_payload=payload,
    )
    return EnqueuedAlias(task_id=created.task.id, alias_id=alias_id)


# ---------------------------------------------------------------------------
# T-032: read / rename / soft-delete
#
# Auth split, by analogy with the character routes:
#   - GET  list / detail → team-wide (`assert_can_read_character`); a
#     teammate already sees `aliases: [...]` embedded in the character
#     detail response, so per-id reads matching that visibility avoids a
#     UX gotcha where the embedded view works but the standalone read
#     403s. The ticket text "全部 endpoint owner-only" is interpreted as
#     "writes only" — same convention character routes follow.
#   - PATCH / DELETE → owner only (`assert_can_modify_character`).
#
# In all cases cross-team callers see NOT_FOUND_* (no team-existence
# probe). NOT_FOUND_CHARACTER raised by the perm helper is translated to
# NOT_FOUND_ALIAS on the per-id surface so the response code stays
# anchored on the resource the caller asked about; the 403 (same-team-
# non-owner) is real authorization signal and passes through untouched.
# ---------------------------------------------------------------------------


async def _resolve_alias_with_perm(
    db: AsyncSession,
    *,
    user: User,
    alias_id: uuid.UUID,
    require_owner: bool,
) -> Alias:
    """Fetch an active alias and run the appropriate permission check
    against its parent character.

    `require_owner=True` for writes (PATCH/DELETE), `False` for reads
    (GET detail). The character `get_active` returning None is
    practically unreachable — the FK is `ON DELETE CASCADE`, so a
    hard-deleted character takes its aliases with it; the only way to
    hit the branch is a soft-deleted parent, which we collapse to
    NOT_FOUND_ALIAS so the response doesn't leak parent state.
    """
    alias = await alias_repo.get_active(db, alias_id)
    if alias is None:
        raise not_found_alias()
    character = await character_repo.get_active(db, alias.character_id)
    if character is None:
        raise not_found_alias()
    try:
        if require_owner:
            assert_can_modify_character(character, user)
        else:
            assert_can_read_character(character, user)
    except AgentErrorException as exc:
        if exc.error.code == "NOT_FOUND_CHARACTER":
            raise not_found_alias() from exc
        raise
    return alias


async def list_aliases_for_character(
    db: AsyncSession,
    *,
    user: User,
    character_id: uuid.UUID,
) -> Sequence[Alias]:
    """Team-wide list of active aliases sorted `created_at ASC`.

    Cross-team → NOT_FOUND_CHARACTER (404). Empty result is valid (200
    with `items: []`). Mirrors `GET /v1/characters/{id}` reachability:
    if the caller can see the character, they can see its aliases.
    """
    character = await character_repo.get_active(db, character_id)
    if character is None:
        raise not_found_character()
    assert_can_read_character(character, user)
    return await alias_repo.list_active_for_character(db, character_id=character.id)


@dataclass(frozen=True)
class AliasDetail:
    """Service-layer carrier for `GET /v1/aliases/{id}`.

    Bundles the alias row with its motion count so the route can build
    the DTO without re-querying. Kept as a dataclass (not a Pydantic
    model) so the service stays free of wire-shape concerns — the route
    layer owns the response envelope.
    """

    alias: Alias
    motion_count: int


async def get_alias_detail(
    db: AsyncSession,
    *,
    user: User,
    alias_id: uuid.UUID,
) -> AliasDetail:
    """Team-wide detail read. Returns the alias + active motion count."""
    alias = await _resolve_alias_with_perm(db, user=user, alias_id=alias_id, require_owner=False)
    motion_count = await motion_repo.count_active_for_alias(db, alias_id=alias.id)
    return AliasDetail(alias=alias, motion_count=motion_count)


async def update_alias_name(
    db: AsyncSession,
    *,
    user: User,
    alias_id: uuid.UUID,
    new_name: str,
) -> Alias:
    """Rename an alias. Same-character duplicate → 409, invalid chars →
    400. No-op rename short-circuits (mirrors `update_character_name`).
    """
    if not name_pattern_ok(new_name):
        raise validation_name_invalid()

    alias = await _resolve_alias_with_perm(db, user=user, alias_id=alias_id, require_owner=True)

    if alias.name == new_name:
        return alias

    if await alias_repo.name_exists_for_character(
        db, character_id=alias.character_id, name=new_name, exclude_id=alias.id
    ):
        raise conflict_duplicate_alias_name()

    alias.name = new_name
    try:
        await db.commit()
    except IntegrityError as exc:
        # The partial UNIQUE index `uq_aliases_character_name` is the
        # durable guard against a race between the probe and the commit.
        # Translate to the same friendly 409 either way.
        await db.rollback()
        if "uq_aliases_character_name" in (str(exc.orig) if exc.orig is not None else str(exc)):
            raise conflict_duplicate_alias_name() from exc
        raise
    await db.refresh(alias)
    return alias


async def soft_delete_alias(
    db: AsyncSession,
    *,
    user: User,
    alias_id: uuid.UUID,
) -> None:
    """Soft-delete an alias and cascade-soft-delete its motions in the
    same transaction (per T-032 §Scope, F-12).

    Both mutations land before a single `await db.commit()`, so observers
    see them atomically — never "alias gone, motions still live", which
    would let the per-id motion CRUD paths hit a row whose parent has
    vanished. The cascade runs first only because it's a bulk UPDATE we
    want flushed before the alias mutation joins the dirty set; the
    transaction boundary is what actually guarantees atomicity.

    Race window left for the Sprint 5 cleanup job: a concurrent
    `create_motion` task that started before this delete could commit a
    new active motion under the soft-deleted alias id. Phase 1 accepts
    that — closing it would need either `SELECT ... FOR UPDATE` on the
    alias here or a post-commit re-sweep.
    """
    alias = await _resolve_alias_with_perm(db, user=user, alias_id=alias_id, require_owner=True)
    await motion_repo.soft_delete_for_alias(db, alias_id=alias.id)
    await alias_repo.soft_delete(db, alias)
    await db.commit()
