"""Checkpoint orchestration: validate the request, reserve a sequence +
checkpoint id, enqueue the worker (T-017).

The route handlers stay thin — they pass the parsed request body and
call into here. The worker (`run_create_checkpoint`) reads the same
input_payload back from the DB.

Authorization:
- Read access uses team scope (matches `creation_session_service`).
- Write access (this module + reference image upload) is restricted
  to the session initiator per storage-layout.md §5.2. Same-team
  callers who aren't the initiator see 403, not 404 — they can read
  the session but not push into it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from arq.connections import ArqRedis
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    auth_insufficient_permission,
    conflict_session_not_active,
    not_found_checkpoint,
    not_found_creation_session,
    not_found_reference_image,
    queue_unavailable,
    validation_checkpoint_mode,
    validation_reference_image_required,
)
from app.models.checkpoint import Checkpoint
from app.models.creation_session import CreationSession
from app.models.reference_image import ReferenceImage
from app.models.user import User
from app.repositories import (
    character_repo,
    checkpoint_repo,
    creation_session_repo,
    reference_image_repo,
)
from app.schemas.checkpoint import CheckpointMode
from app.services import sequence_service, task_service

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Authorization + lookup helpers
# ---------------------------------------------------------------------------


async def _get_writable_session(
    db: AsyncSession,
    *,
    user: User,
    session_id: uuid.UUID,
) -> CreationSession:
    """Resolve a session for write access.

    Phase 1 contract: the session initiator is the only writer. We
    deliberately surface team mismatches as 404 (avoid leaking team
    boundaries) and same-team-non-initiator as 403 (so a frontend can
    render "view only" affordances cleanly).
    """
    session = await creation_session_repo.get(db, session_id)
    if session is None:
        raise not_found_creation_session()

    if session.character_id is not None:
        # Character-attached session: cross-team mismatch is 404 (don't
        # leak team boundaries), same-team-but-non-initiator is 403 so
        # the frontend can render "view only" affordances.
        character = await character_repo.get_active(db, session.character_id)
        if character is None or character.team_id != user.team_id:
            raise not_found_creation_session()
        if session.initiator_id != user.id:
            raise auth_insufficient_permission()
    else:
        # Character-less session: visible only to its initiator. Any
        # other caller — even on the same team — sees 404, not 403.
        # Otherwise a cross-team request would leak that the id exists
        # and break the function's "cross-team mismatch → 404" contract
        # (Codex P2 round-11). The 403 path is reserved for cases where
        # the caller has clear cross-team-but-not-owner standing, which
        # only the character-attached branch can establish.
        if session.initiator_id != user.id:
            raise not_found_creation_session()

    if session.status != "in_progress":
        # An abandoned / completed session can be GETd but not mutated.
        raise conflict_session_not_active()

    return session


async def assert_session_writable(
    db: AsyncSession,
    *,
    user: User,
    session_id: uuid.UUID,
) -> CreationSession:
    """Public wrapper around `_get_writable_session` for callers that
    need an early authorization gate before doing expensive work
    (e.g. the reference-image route reading and storing 10MB before
    the DB-backed authz check, Codex P1 round-1)."""
    return await _get_writable_session(db, user=user, session_id=session_id)


# ---------------------------------------------------------------------------
# Reference image upload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreatedReferenceImage:
    reference: ReferenceImage
    signed_url: str


async def upload_reference_image(
    db: AsyncSession,
    *,
    user: User,
    session_id: uuid.UUID,
    reference_id: uuid.UUID,
    storage_key: str,
    mime_type: str,
    size_bytes: int,
    signed_url: str,
) -> CreatedReferenceImage:
    """Persist a reference upload row.

    The route handler is responsible for validating MIME / size / storing
    bytes BEFORE this method is called — once we commit a row, a future
    worker assumes the storage key resolves. If the route's storage put
    succeeds but our INSERT fails, the file is orphaned; lifecycle
    cleanup picks it up alongside the session itself.

    `reference_id` is supplied by the caller (route) so the storage key
    derived from the same uuid stays in sync with the row's id.
    """
    session = await _get_writable_session(db, user=user, session_id=session_id)
    row = await reference_image_repo.insert(
        db,
        reference_id=reference_id,
        creation_session_id=session.id,
        uploaded_by_user_id=user.id,
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )
    await db.commit()
    # Post-commit refresh failures must NOT propagate (Codex P1
    # round-13). The row is already durable in the DB; the route's
    # outer storage-cleanup `except` would otherwise delete the blob
    # and leave a committed row pointing at a missing file. The
    # `id` / `created_at` we hand back are already populated from
    # the flush during insert, so a missing refresh costs us nothing
    # observable.
    try:
        await db.refresh(row)
    except Exception:  # noqa: BLE001 — recoverable; row is already durable
        _logger.warning(
            "upload_reference_image: post-commit refresh failed for %s; "
            "returning the in-memory row",
            row.id,
            exc_info=True,
        )
    return CreatedReferenceImage(reference=row, signed_url=signed_url)


# ---------------------------------------------------------------------------
# Checkpoint enqueue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnqueuedCheckpoint:
    task_id: uuid.UUID
    checkpoint_id: uuid.UUID


def _validate_mode_combination(
    mode: CheckpointMode,
    base_checkpoint_id: uuid.UUID | None,
) -> None:
    """Cross-check `mode` against `base_checkpoint_id` per the request
    contract. The matrix is small enough that a top-level branch reads
    clearer than a Pydantic validator.
    """
    if mode in ("retry_same", "remix"):
        if base_checkpoint_id is None:
            raise validation_checkpoint_mode()
    elif mode == "fresh":
        if base_checkpoint_id is not None:
            raise validation_checkpoint_mode()


async def _resolve_base_checkpoint(
    db: AsyncSession,
    *,
    session: CreationSession,
    base_checkpoint_id: uuid.UUID,
) -> Checkpoint:
    """Fetch the source checkpoint for `retry_same` / `remix` modes.

    The checkpoint must belong to the SAME session — otherwise a caller
    could remix a sibling user's checkpoint by guessing its id. Cross-
    session lookups collapse to NOT_FOUND_CHECKPOINT, same as a missing
    row.
    """
    base = await checkpoint_repo.get(db, base_checkpoint_id)
    if base is None or base.creation_session_id != session.id:
        raise not_found_checkpoint()
    return base


async def _resolve_reference_images(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    reference_image_ids: Sequence[uuid.UUID],
) -> list[ReferenceImage]:
    """Fetch reference uploads for a checkpoint create. Validate that
    every requested id matches a row scoped to this session — a wrong
    or cross-session id raises NOT_FOUND_REFERENCE_IMAGE."""
    rows = await reference_image_repo.list_by_ids_in_session(
        db,
        session_id=session_id,
        reference_ids=reference_image_ids,
    )
    if len(rows) != len(set(reference_image_ids)):
        raise not_found_reference_image()
    # Preserve caller-specified ordering (the first reference is the
    # primary image conditioning input per ai-integration.md §3.2).
    by_id = {r.id: r for r in rows}
    return [by_id[rid] for rid in reference_image_ids if rid in by_id]


async def enqueue_checkpoint(
    db: AsyncSession,
    redis: Redis,
    arq_pool: ArqRedis,
    *,
    user: User,
    session_id: uuid.UUID,
    mode: CheckpointMode,
    base_checkpoint_id: uuid.UUID | None,
    menu_selections: dict[str, object] | None,
    freeform_note: str | None,
    reference_image_ids: list[uuid.UUID] | None,
) -> EnqueuedCheckpoint:
    """Validate, reserve a sequence + checkpoint UUID, enqueue the
    worker.

    The checkpoint row is not written here — the worker writes it
    after a successful image generation (planning/backend/task-queue.md
    §3.5). The reserved id flows into `task.input_payload` so the SSE
    path can emit a Checkpoint DTO with the same id the row eventually
    carries.
    """
    session = await _get_writable_session(db, user=user, session_id=session_id)
    _validate_mode_combination(mode, base_checkpoint_id)

    # Mode-specific server-side fixups for the worker payload.
    refs: list[ReferenceImage] = []
    base_checkpoint: Checkpoint | None = None
    final_menu = menu_selections
    final_freeform = freeform_note
    final_reference_ids = reference_image_ids or []

    if mode in ("retry_same", "remix"):
        assert base_checkpoint_id is not None  # validated above
        base_checkpoint = await _resolve_base_checkpoint(
            db, session=session, base_checkpoint_id=base_checkpoint_id
        )
        if mode == "retry_same":
            # Re-use the source's user inputs verbatim. Worker will
            # also reuse the source's prompt directly to avoid a
            # second reconciler call (the deterministic path) — a
            # different seed is what makes "retry" produce variation.
            final_menu = base_checkpoint.user_menu_selections
            final_freeform = base_checkpoint.user_freeform_note
            # Reference ids: re-resolve from the source's stored keys
            # so retry behaves identically to the original request.
            # We don't have the original ref ids (the row stores keys,
            # not ids), so the worker uses keys directly. Pass empty
            # list here to keep payload schema consistent.
            final_reference_ids = []

    if final_reference_ids:
        refs = await _resolve_reference_images(
            db,
            session_id=session.id,
            reference_image_ids=final_reference_ids,
        )

    # input_mode=reference + fresh → must have a reference image. We
    # don't enforce this on remix (the source checkpoint's image is
    # the conditioning input) or retry_same (re-uses the source's keys).
    if session.input_mode == "reference" and mode == "fresh" and not refs:
        raise validation_reference_image_required()

    checkpoint_id = uuid.uuid4()
    # Reserve the next sequence number. Catch ONLY `RedisError` —
    # `reserve_next_sequence` also runs DB recovery queries
    # (`_max_persisted_sequence`, `_max_in_flight_sequence`) that
    # raise SQLAlchemyError on Postgres trouble. Mislabeling those as
    # QUEUE_UNAVAILABLE would mis-route ops triage (Codex P2 round-10
    # — same lesson as the round-8 task_service fix). DB errors bubble
    # as their real type.
    try:
        sequence = await sequence_service.reserve_next_sequence(db, redis, session_id=session.id)
    except RedisError as exc:
        _logger.exception(
            "enqueue_checkpoint: redis-backed sequence reservation failed for session %s",
            session.id,
        )
        raise queue_unavailable() from exc

    payload: dict[str, object] = {
        "session_id": str(session.id),
        "character_id": str(session.character_id) if session.character_id else None,
        "input_mode": session.input_mode,
        "checkpoint_id": str(checkpoint_id),
        "sequence": sequence,
        "mode": mode,
        "base_checkpoint_id": str(base_checkpoint_id) if base_checkpoint_id else None,
        "menu_selections": final_menu,
        "freeform_note": final_freeform,
        "reference_image_ids": [str(r.id) for r in refs],
        "reference_image_keys": [r.storage_key for r in refs],
    }
    if mode == "retry_same" and base_checkpoint is not None:
        # Carry the reference keys forward so the retry has identical
        # conditioning to the source. Stored on the source row when it
        # was first written.
        if base_checkpoint.reference_image_keys:
            payload["reference_image_keys"] = list(base_checkpoint.reference_image_keys)

    # `task_service.create_task` raises `QUEUE_UNAVAILABLE` itself when
    # arq enqueue fails (Codex P2 round-8 — the prior round-7 wrapper
    # in this function was over-broad and labeled DB-path failures as
    # queue outages). Pre-commit DB failures bubble as their real
    # exception, which is the right surface for ops triage.
    created = await task_service.create_task(
        db,
        arq_pool,
        user_id=user.id,
        task_type="create_checkpoint",
        input_payload=payload,
    )
    return EnqueuedCheckpoint(task_id=created.task.id, checkpoint_id=checkpoint_id)


# ---------------------------------------------------------------------------
# Read — single checkpoint
# ---------------------------------------------------------------------------


async def get_checkpoint_for_read(
    db: AsyncSession,
    *,
    user: User,
    checkpoint_id: uuid.UUID,
) -> Checkpoint:
    """Fetch a checkpoint scoped to the caller's team. Used by
    `GET /v1/checkpoints/{id}` and (later) the fork endpoint.

    Cross-team / missing → NOT_FOUND_CHECKPOINT (does not leak team
    membership).
    """
    checkpoint = await checkpoint_repo.get(db, checkpoint_id)
    if checkpoint is None:
        raise not_found_checkpoint()
    session = await creation_session_repo.get(db, checkpoint.creation_session_id)
    if session is None:
        raise not_found_checkpoint()
    if session.character_id is not None:
        character = await character_repo.get_active(db, session.character_id)
        if character is None or character.team_id != user.team_id:
            raise not_found_checkpoint()
    elif session.initiator_id != user.id:
        # Character-less session: only the initiator can see the
        # checkpoints (matches the read rule on the session itself).
        raise not_found_checkpoint()
    return checkpoint
