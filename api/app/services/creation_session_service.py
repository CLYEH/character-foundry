"""Read-only access to creation sessions (T-016).

T-017 will add the checkpoint-create flow; for now this module just
backs `GET /v1/creation-sessions/{id}` and the embedded session DTO
that `POST /v1/characters` returns.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found_creation_session
from app.models.checkpoint import Checkpoint
from app.models.creation_session import CreationSession
from app.models.user import User
from app.repositories import character_repo, creation_session_repo


@dataclass(frozen=True)
class SessionWithCheckpoints:
    session: CreationSession
    checkpoints: Sequence[Checkpoint]
    checkpoint_count: int


async def get_session_for_read(
    db: AsyncSession,
    *,
    user: User,
    session_id: uuid.UUID,
) -> SessionWithCheckpoints:
    """Fetch a session + its checkpoints, gating on character team
    membership. Sessions whose `character_id` is null (theoretically
    possible per the schema) are visible to the initiator only."""
    session = await creation_session_repo.get(db, session_id)
    if session is None:
        raise not_found_creation_session()

    # Authorization: routed through the character (single-team Phase
    # 1 means same-team => visible). For sessions still without a
    # character row (shouldn't happen in T-016 since we always create
    # the pair atomically, but keep the guard for forward compat),
    # only the initiator can read.
    if session.character_id is not None:
        # `get_active` (not `get_including_deleted`) so a soft-deleted
        # character collapses the session to 404. Per the T-016 ticket
        # note: "若 character 已刪，session 不對外出" — internal fork
        # paths still need the deleted row, but they go through their
        # own repo call rather than this read-side surface.
        character = await character_repo.get_active(db, session.character_id)
        if character is None:
            # Character row vanished or was soft-deleted — surface as
            # not-found rather than expose orphaned-session state.
            raise not_found_creation_session()
        # Inline the team check rather than reusing
        # `assert_can_read_character` (which raises NOT_FOUND_CHARACTER):
        # the endpoint contract is "session not found" — leaking a
        # character-shaped error envelope on cross-team requests would
        # let callers distinguish "session id maps to other-team
        # character" from "session id is bogus" (Codex round-3 P2).
        if character.team_id != user.team_id:
            raise not_found_creation_session()
    elif session.initiator_id != user.id:
        raise not_found_creation_session()

    checkpoints = await creation_session_repo.list_checkpoints(db, session_id=session_id)
    return SessionWithCheckpoints(
        session=session,
        checkpoints=checkpoints,
        checkpoint_count=len(checkpoints),
    )
