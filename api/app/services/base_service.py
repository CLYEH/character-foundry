"""Select-base orchestration (T-018).

Closes the creation-session loop by promoting one of the iterated
checkpoints into the Character's immutable Base. The whole operation
sits in a single DB transaction — three rows are mutated in step
(`bases` insert, `characters.base_id`, `creation_sessions.status`,
`checkpoints.selected_as_base`) and a partial commit would leave the
character in an inconsistent "selecting base" state that no other
endpoint knows how to recover.

Authorization mirrors the checkpoint-write flow: the session
initiator is the only writer (storage-layout §5.2). Cross-team and
non-initiator collapse to NOT_FOUND_CREATION_SESSION / 403 the same
way `_get_writable_session` in checkpoint_service does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    auth_insufficient_permission,
    conflict_base_locked,
    not_found_checkpoint,
    not_found_creation_session,
)
from app.models.base import BaseAsset
from app.models.character import Character
from app.models.creation_session import CreationSession
from app.models.user import User
from app.repositories import (
    base_repo,
    character_repo,
    checkpoint_repo,
    creation_session_repo,
)


@dataclass(frozen=True)
class SelectedBase:
    character: Character
    base: BaseAsset


async def select_base(
    db: AsyncSession,
    *,
    user: User,
    session_id: uuid.UUID,
    checkpoint_id: uuid.UUID,
) -> SelectedBase:
    """Promote `checkpoint_id` into the Base row for the session's
    character. Atomic across all four mutations.

    Status semantics on retry:
    - Session in_progress → normal happy path.
    - Session completed (Base already chosen) → 409 CONFLICT_BASE_LOCKED.
      Idempotent retry would be tempting (same checkpoint already won)
      but Phase 1 Base is immutable per DECISIONS §5; "you tried to
      pick again" is genuine misuse and we surface that distinctly.
    - Session abandoned → 409 CONFLICT_BASE_LOCKED. The session is
      terminal; the user should start a new one.

    Concurrency: the session row is loaded with `SELECT ... FOR UPDATE`
    so a concurrent `abandon_session` blocks until this transaction
    finishes. Without the lock, both callers could read `in_progress`
    and commit conflicting end states (Codex round-2 P2).
    """
    session = await creation_session_repo.get_for_update(db, session_id)
    if session is None:
        raise not_found_creation_session()

    # Authorization (mirrors checkpoint_service._get_writable_session)
    # — must resolve through the character so cross-team requests can't
    # distinguish "wrong team" from "wrong id".
    if session.character_id is None:
        # Character-less session shouldn't reach select-base (you need
        # a character to attach a Base to). Treat as not-found rather
        # than mint a domain-specific error for an invariant that
        # T-016/T-017 already prevent in the normal flow.
        raise not_found_creation_session()

    character = await character_repo.get_active(db, session.character_id)
    if character is None or character.team_id != user.team_id:
        raise not_found_creation_session()
    if session.initiator_id != user.id:
        raise auth_insufficient_permission()

    if session.status != "in_progress":
        # Both completed and abandoned hit here. CONFLICT_BASE_LOCKED's
        # message ("Base 已確立 or 流程已結束") covers both shapes; the
        # frontend renders the same "this session is done" affordance.
        raise conflict_base_locked()

    checkpoint = await checkpoint_repo.get(db, checkpoint_id)
    if checkpoint is None or checkpoint.creation_session_id != session.id:
        # Cross-session ids collapse to NOT_FOUND_CHECKPOINT — same
        # reasoning as `_resolve_base_checkpoint` in checkpoint_service:
        # don't let callers distinguish "wrong id" from "id from a
        # sibling session".
        raise not_found_checkpoint()

    # Insert the Base row + flip the back-references. Two callers
    # hitting select-base concurrently can both read session.status
    # == 'in_progress' before either commits (read-then-write race).
    # The `bases.character_id` UNIQUE constraint catches the loser at
    # flush/commit time; we translate that to CONFLICT_BASE_LOCKED so
    # the response stays consistent with the documented terminal-
    # session contract instead of surfacing as a generic 500
    # (Codex round-1 P2).
    try:
        base = await base_repo.insert(
            db,
            character_id=character.id,
            from_checkpoint_id=checkpoint.id,
            image_key=checkpoint.output_image_key,
            image_embedding=checkpoint.output_image_embedding,
        )

        # SQLAlchemy bundles everything into one COMMIT.
        character.base_id = base.id
        session.status = "completed"
        session.completed_at = datetime.now(UTC)
        checkpoint.selected_as_base = True

        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # `bases_character_id_key` is the auto-generated name for the
        # column-level UNIQUE on `bases.character_id` (migration 007).
        # Only this collision maps to the user-facing 409. Other
        # IntegrityErrors (e.g. a FK violation from a concurrent
        # character soft-delete + cleanup) bubble as their real type —
        # they're not the "another writer already locked Base" case.
        msg = str(exc.orig) if exc.orig is not None else str(exc)
        if "bases_character_id_key" in msg:
            raise conflict_base_locked() from exc
        raise

    await db.refresh(character)
    await db.refresh(base)

    return SelectedBase(character=character, base=base)


async def abandon_session(
    db: AsyncSession,
    *,
    user: User,
    session_id: uuid.UUID,
) -> CreationSession:
    """Mark a session abandoned. Idempotent on already-abandoned;
    completed (Base selected) sessions reject with CONFLICT_BASE_LOCKED.

    Checkpoints stay alive — the scheduled cleanup that cascade-deletes
    them after 7 days is Sprint 5's job (lifecycle.md line 63).

    Concurrency: same `SELECT ... FOR UPDATE` lock as `select_base` so
    abandon-vs-select-base races serialize cleanly. Whoever wins
    commits its terminal state; the loser sees the new status and
    bails (409 from select-base, 409 here for completed → abandon).
    """
    session = await creation_session_repo.get_for_update(db, session_id)
    if session is None:
        raise not_found_creation_session()

    if session.character_id is None:
        raise not_found_creation_session()

    character = await character_repo.get_active(db, session.character_id)
    if character is None or character.team_id != user.team_id:
        raise not_found_creation_session()
    if session.initiator_id != user.id:
        raise auth_insufficient_permission()

    if session.status == "abandoned":
        # Idempotent — re-POSTing abandon should still 204 so a
        # double-click in the UI doesn't surface as an error.
        return session
    if session.status == "completed":
        # Base already locked in. The checkpoint cleanup window doesn't
        # apply to completed sessions (their checkpoint is now the
        # source for `bases.from_checkpoint_id ON DELETE RESTRICT`),
        # and Phase 1 has no "unselect" affordance.
        raise conflict_base_locked()

    session.status = "abandoned"
    await db.commit()
    await db.refresh(session)
    return session
