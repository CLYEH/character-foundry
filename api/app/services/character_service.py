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

# Constraint-name sniffers for the partial UNIQUE indexes on
# `characters` (planning/data/db-schema.md §3.3). We match the rendered
# exception text rather than reach into asyncpg internals — both
# `pgcode='23505'` and the constraint name appear in the message
# regardless of driver version, so the string match keeps us decoupled
# from psycopg/asyncpg differences. The two indexes need separate
# handling: name conflicts are real duplicates (409), slug conflicts
# are a race the slug allocator can resolve on retry (Codex round-5 P2).
_NAME_CONSTRAINT = "uq_characters_owner_name"
_SLUG_CONSTRAINT = "uq_characters_owner_slug"


def _integrity_error_message(exc: IntegrityError) -> str:
    return str(exc.orig) if exc.orig is not None else str(exc)


def _is_name_constraint_violation(exc: IntegrityError) -> bool:
    return _NAME_CONSTRAINT in _integrity_error_message(exc)


def _is_slug_constraint_violation(exc: IntegrityError) -> bool:
    return _SLUG_CONSTRAINT in _integrity_error_message(exc)


def _is_name_or_slug_violation(exc: IntegrityError) -> bool:
    """Combined check used by PATCH/restore where slug retry isn't an
    option: PATCH only touches `name` (slug is stable), and restoring
    a soft-deleted row can't transparently mint a new slug without
    surprising the user (URL would change). Both paths therefore
    surface either index violation as 409.
    """
    return _is_name_constraint_violation(exc) or _is_slug_constraint_violation(exc)


# Bound the slug-collision retry loop. The worst-case race is N
# concurrent creates with pinyin-equal-but-distinct names from the
# same owner: each iteration only one winner commits, so the slowest
# request needs N-1 retries to find a free suffix (Codex round-6 P2).
# 10 covers any realistic Phase 1 contention (10 simultaneous tab-spam
# creates of slug-equal-but-name-distinct characters from one owner)
# while still tripping fast on a stuck allocator. The uuid4-prefix
# fallback kicks in inside `generate_unique_slug` after 100 numeric
# suffixes, so the cap here only bounds DB-side races, not allocator
# bugs in the deterministic part.
_SLUG_RETRY_LIMIT = 10


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
    redis: Redis | None,
    *,
    user: User,
    name: str,
    input_mode: str,
) -> CreatedCharacter:
    """Insert character + session in one transaction; bootstrap Redis.

    `redis` may be `None` when the dep failed to resolve at the route
    layer (e.g., `REDIS_URL` unset in dev) — Redis is best-effort here
    and a missing client takes the same fall-through as a runtime SET
    failure (Codex round-6 P2). The first call to T-017's INCR
    allocator will rebuild the cursor from the DB if the seed key
    isn't there.
    """

    if not name_pattern_ok(name):
        # Pydantic enforces length (1–50) at the route layer, but the
        # character-class regex lives here so the AgentError surface
        # uses VALIDATION_INVALID_CHARS instead of a generic 422.
        raise validation_name_invalid()

    if await character_repo.name_exists_for_owner(db, owner_id=user.id, name=name):
        raise conflict_duplicate_name()

    async def _is_taken(slug: str) -> bool:
        return await character_repo.slug_exists_for_owner(db, owner_id=user.id, slug=slug)

    # Slug allocation + insert sit in a retry loop. The two partial
    # UNIQUE indexes have different semantics:
    #   - `uq_characters_owner_name` → real duplicate, raise 409.
    #   - `uq_characters_owner_slug` → two pinyin-equal-but-distinct
    #     names raced; the freshly-committed row is now visible to
    #     `slug_exists_for_owner`, so re-running the allocator picks
    #     a different suffix. Retry rather than 409 with a misleading
    #     "name exists" (Codex round-5 P2 — the names are different).
    character: Character | None = None
    session: CreationSession | None = None
    last_slug_exc: IntegrityError | None = None
    for _attempt in range(_SLUG_RETRY_LIMIT):
        slug = await generate_unique_slug(name, is_taken=_is_taken)
        try:
            # Step 1: insert character (no creation_session_id yet — the
            # FK is ALTER-added at migration time with `use_alter=True`
            # so we don't need a deferred constraint here). The partial
            # UNIQUE indexes can fire at FIRST flush (asyncpg+RETURNING
            # raises before commit), so the handler must wrap the whole
            # insert sequence, not just commit (Codex round-1 P2).
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
            break
        except IntegrityError as exc:
            await db.rollback()
            if _is_name_constraint_violation(exc):
                raise conflict_duplicate_name() from exc
            if _is_slug_constraint_violation(exc):
                # Loop and re-allocate. The committing winner is now
                # visible to the next probe so the allocator picks a
                # different suffix.
                last_slug_exc = exc
                continue
            raise
    else:
        # Retry budget exhausted on slug constraint specifically. This
        # is rare enough to suggest a real bug (e.g., the allocator
        # keeps picking the same suffix for some reason) — surface
        # rather than swallow.
        assert last_slug_exc is not None
        raise last_slug_exc

    assert character is not None and session is not None
    await db.refresh(character)
    await db.refresh(session)

    # Step 4 (best-effort): bootstrap the Redis sequence allocator.
    # Redis being unreachable — or the dep failing to resolve at all,
    # in which case the route hands us `None` — is logged but not
    # raised. T-017's allocator falls back to `MAX(sequence) + 1`
    # from the DB if the key is missing.
    if redis is None:
        _logger.info(
            "create_character: redis client unavailable; skipping seq bootstrap for %s",
            _checkpoint_seq_key(session.id),
        )
    else:
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
        if _is_name_or_slug_violation(exc):
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
        if _is_name_or_slug_violation(exc):
            raise conflict_duplicate_name() from exc
        raise
    await db.refresh(character)
    return character
