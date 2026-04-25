"""Character + creation_session orchestration (T-016).

The load-bearing piece is `create_character`: it has to land both rows
plus the back-reference (`characters.creation_session_id`) atomically,
and bootstrap the Redis sequence allocator that T-017's checkpoint flow
will later read. DB failure rolls everything back; a Redis hiccup logs
a warning but doesn't fail the request — T-017's allocator falls back
to `MAX(sequence) + 1` if the key is missing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    conflict_duplicate_name,
    gone_character_restore_window,
    not_found_character,
    validation_name_invalid,
)
from app.core.permissions import (
    assert_can_modify_character,
    assert_can_read_character,
)
from app.models.character import Character
from app.models.creation_session import CreationSession
from app.models.user import User
from app.repositories import character_repo
from app.schemas.character import name_pattern_ok
from app.utils.slug import generate_unique_slug

_logger = logging.getLogger(__name__)

RESTORE_WINDOW_DAYS = 30

# Names of the partial UNIQUE indexes that map to CONFLICT_DUPLICATE_NAME.
# When a `name_exists_for_owner` pre-check passes but a concurrent insert
# wins the race, Postgres raises an IntegrityError carrying one of these
# constraint names — translate it to the structured AgentError instead of
# bubbling a 500 (Codex P2 review).
_NAME_CONFLICT_CONSTRAINTS = ("uq_characters_owner_name", "uq_characters_owner_slug")


def _is_duplicate_name_violation(exc: IntegrityError) -> bool:
    """Best-effort match against the partial UNIQUE indexes that guard
    `(owner_id, name)` and `(owner_id, slug)` (planning/data/db-schema.md
    §3.3). We sniff the rendered exception text rather than reach into
    asyncpg internals — both `pgcode='23505'` and the constraint name
    appear in the message regardless of driver version, and the string
    match keeps us decoupled from psycopg/asyncpg differences.
    """
    message = str(exc.orig) if exc.orig is not None else str(exc)
    return any(name in message for name in _NAME_CONFLICT_CONSTRAINTS)


@dataclass(frozen=True)
class CreatedCharacter:
    character: Character
    creation_session: CreationSession


def _checkpoint_seq_key(session_id: uuid.UUID) -> str:
    """Redis key holding the next-checkpoint sequence cursor for a
    session. T-017 increments this with INCR; T-016 just SETs it to 0
    so the first INCR returns 1 (matching the natural 1-based sequence
    in the UI). Out of band of the DB transaction by design — the
    fallback path is documented in the module docstring."""
    return f"seq:checkpoint:{session_id}"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_character(
    db: AsyncSession,
    redis: Redis,
    *,
    user: User,
    name: str,
    input_mode: str,
) -> CreatedCharacter:
    """Insert character + session in one transaction; bootstrap Redis."""

    if not name_pattern_ok(name):
        # Pydantic enforces length (1–50) at the route layer, but the
        # character-class regex lives here so the AgentError surface
        # uses VALIDATION_INVALID_CHARS instead of a generic 422.
        raise validation_name_invalid()

    if await character_repo.name_exists_for_owner(db, owner_id=user.id, name=name):
        raise conflict_duplicate_name()

    async def _is_taken(slug: str) -> bool:
        return await character_repo.slug_exists_for_owner(db, owner_id=user.id, slug=slug)

    slug = await generate_unique_slug(name, is_taken=_is_taken)

    # Steps 1–3 in one try/except: the partial UNIQUE indexes on
    # `(owner_id, name)` and `(owner_id, slug)` can fire at the FIRST
    # `await db.flush()` (asyncpg+RETURNING raises before commit), so
    # the handler must wrap the whole insert sequence — not just commit
    # — to catch the race the pre-check missed (Codex P2 review).
    try:
        # Step 1: insert character (no creation_session_id yet — the
        # FK is ALTER-added at migration time with `use_alter=True` so
        # we don't need a deferred constraint here).
        character = Character(
            team_id=user.team_id,
            owner_id=user.id,
            name=name,
            slug=slug,
        )
        db.add(character)
        await db.flush()  # populates character.id

        # Step 2: insert session pointing at the brand-new character.
        session = CreationSession(
            character_id=character.id,
            initiator_id=user.id,
            input_mode=input_mode,
        )
        db.add(session)
        await db.flush()  # populates session.id

        # Step 3: back-reference the session on the character.
        # SQLAlchemy turns this into a single UPDATE on commit.
        character.creation_session_id = session.id

        await db.commit()
    except IntegrityError as exc:
        # Pre-check + insert is racy: two concurrent POSTs with the
        # same `(owner_id, name)` (or pinyin-equal slugs) can both
        # pass `name_exists_for_owner` and only fail in the DB. We
        # translate the driver error into the structured 409.
        await db.rollback()
        if _is_duplicate_name_violation(exc):
            raise conflict_duplicate_name() from exc
        raise
    await db.refresh(character)
    await db.refresh(session)

    # Step 4 (best-effort): bootstrap the Redis sequence allocator.
    # Redis being unreachable is logged but not raised — T-017's
    # allocator falls back to `MAX(sequence) + 1` from the DB if the
    # key is missing.
    try:
        await redis.set(_checkpoint_seq_key(session.id), 0)
    except Exception:  # noqa: BLE001 — best-effort; DB is the source of truth
        _logger.warning(
            "create_character: redis SET %s failed; T-017 allocator will rebuild from DB",
            _checkpoint_seq_key(session.id),
            exc_info=True,
        )

    return CreatedCharacter(character=character, creation_session=session)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListResult:
    items: Sequence[Character]
    next_cursor: str | None


async def list_characters(
    db: AsyncSession,
    *,
    user: User,
    owner_id: uuid.UUID | None,
    q: str | None,
    limit: int,
    cursor_str: str | None,
) -> ListResult:
    """Cursor-paginated team list. Owner filter is optional — `None`
    means "everyone in the team"."""
    cursor = character_repo.decode_cursor(cursor_str) if cursor_str else None
    rows = await character_repo.list_for_team(
        db,
        team_id=user.team_id,
        owner_id=owner_id,
        q=q,
        limit=limit,
        cursor=cursor,
    )
    next_cursor: str | None = None
    if len(rows) == limit and rows:
        last = rows[-1]
        next_cursor = character_repo.Cursor(updated_at=last.updated_at, id=last.id).encode()
    return ListResult(items=rows, next_cursor=next_cursor)


async def get_character_for_read(
    db: AsyncSession,
    *,
    user: User,
    character_id: uuid.UUID,
) -> Character:
    """Read-mode fetch — collapses cross-team to 404 to avoid leaking
    team membership."""
    character = await character_repo.get_active(db, character_id)
    if character is None:
        raise not_found_character()
    assert_can_read_character(character, user)
    return character


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def update_character_name(
    db: AsyncSession,
    *,
    user: User,
    character_id: uuid.UUID,
    new_name: str,
) -> Character:
    if not name_pattern_ok(new_name):
        raise validation_name_invalid()

    character = await character_repo.get_active(db, character_id)
    if character is None:
        raise not_found_character()
    assert_can_modify_character(character, user)

    if character.name == new_name:
        # No-op rename — short-circuit the duplicate check so the
        # owner can PATCH with the same name without 409ing themselves.
        return character

    if await character_repo.name_exists_for_owner(
        db, owner_id=user.id, name=new_name, exclude_id=character.id
    ):
        raise conflict_duplicate_name()

    character.name = new_name
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_name_violation(exc):
            raise conflict_duplicate_name() from exc
        raise
    await db.refresh(character)
    return character


# ---------------------------------------------------------------------------
# Soft delete + restore
# ---------------------------------------------------------------------------


async def soft_delete_character(
    db: AsyncSession,
    *,
    user: User,
    character_id: uuid.UUID,
) -> None:
    character = await character_repo.get_active(db, character_id)
    if character is None:
        raise not_found_character()
    assert_can_modify_character(character, user)
    character.deleted_at = datetime.now(UTC)
    await db.commit()


async def restore_character(
    db: AsyncSession,
    *,
    user: User,
    character_id: uuid.UUID,
) -> Character:
    """Restore a soft-deleted character if it's within the 30-day
    window. Outside the window the row is treated as gone — same code
    as NOT_FOUND_CHARACTER but 410 status, so a UI can offer "create
    a fresh one" instead of "try again"."""
    character = await character_repo.get_including_deleted(db, character_id)
    if character is None:
        raise not_found_character()
    # Permission first: a non-owner attempting to restore should get 403,
    # not leak whether the row exists.
    assert_can_modify_character(character, user)

    if character.deleted_at is None:
        # Already active — nothing to restore. Treat as success and
        # return the row as-is so PATCH-style retries are idempotent.
        return character

    age = datetime.now(UTC) - character.deleted_at
    if age > timedelta(days=RESTORE_WINDOW_DAYS):
        raise gone_character_restore_window()

    # Pre-check for duplicate name: while soft-deleted the unique-name
    # partial index ignores this row, so another character may have
    # taken the same name. Surface as CONFLICT_DUPLICATE_NAME instead
    # of a generic IntegrityError.
    if await character_repo.name_exists_for_owner(
        db, owner_id=character.owner_id, name=character.name, exclude_id=character.id
    ):
        raise conflict_duplicate_name()

    character.deleted_at = None
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_name_violation(exc):
            raise conflict_duplicate_name() from exc
        raise
    await db.refresh(character)
    return character
