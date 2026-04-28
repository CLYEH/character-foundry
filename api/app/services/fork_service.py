"""Fork-from-checkpoint orchestration (T-018).

`POST /v1/checkpoints/{id}/fork` opens a fresh Character + CreationSession
seeded from an existing checkpoint. The new session's first checkpoint
re-uses the source's prompt + generation_log_id (pure metadata, never
cleaned up) but **must own its image bytes** — the source session might
later be abandoned and the 7-day cleanup would otherwise leave the
forked character with broken images.

Storage copy uses `StorageBackend.copy(src, dst)` which on Local maps
to `os.link` (hardlink, inode-shared, zero-cost) and on S3 maps to
`copy_object` (server-side). Both are idempotent on the destination.

Storage ordering: the image copy happens **after `db.flush()` but
before `db.commit()`** so the operation is effectively atomic from
the caller's POV. A storage failure rolls back the DB rows and
surfaces an error; a commit failure after a successful storage copy
leaves an orphaned file (rare; cleanable by storage GC). The
alternative — post-commit copy — would leave half-broken characters
that the user couldn't easily retry around (the duplicate-name 409
on a second attempt).

Authorization is initiator-only on the source checkpoint per
storage-layout §5.1: even same-team callers can't fork another user's
checkpoint. Reuses `checkpoint_service.get_checkpoint_for_read` so the
404-collapsing rules stay aligned across read + fork.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    conflict_duplicate_name,
    not_found_checkpoint,
    not_found_creation_session,
    validation_name_invalid,
)
from app.models.character import Character
from app.models.checkpoint import Checkpoint
from app.models.creation_session import CreationSession
from app.models.user import User
from app.repositories import character_repo, creation_session_repo
from app.schemas.character import name_pattern_ok
from app.schemas.checkpoint_builder import thumbnail_key_for
from app.services import checkpoint_service
from app.storage.backend import StorageBackend
from app.storage.errors import NotFoundError, StorageError
from app.utils.slug import generate_unique_slug

_logger = logging.getLogger(__name__)

# Mirrors `character_service._SLUG_RETRY_LIMIT`. Keep in sync if either
# moves — both share the same tab-spam contention model.
_SLUG_RETRY_LIMIT = 10
_NAME_CONSTRAINT = "uq_characters_owner_name"
_SLUG_CONSTRAINT = "uq_characters_owner_slug"

# Sequence is hard-coded to 1: the forked session starts fresh, with
# the copied checkpoint as its only entry. Subsequent generations go
# through the regular sequence allocator (T-017) which will read MAX
# from the DB and pick 2 next.
_FIRST_CHECKPOINT_SEQUENCE = 1


def _is_name_violation(exc: IntegrityError) -> bool:
    msg = str(exc.orig) if exc.orig is not None else str(exc)
    return _NAME_CONSTRAINT in msg


def _is_slug_violation(exc: IntegrityError) -> bool:
    msg = str(exc.orig) if exc.orig is not None else str(exc)
    return _SLUG_CONSTRAINT in msg


def _new_checkpoint_keys(
    new_session_id: uuid.UUID, new_checkpoint_id: uuid.UUID
) -> tuple[str, str]:
    """Compute the destination keys for the forked checkpoint's image
    + thumbnail. Mirrors `storage-layout.md` §2 + the `_thumb.png`
    suffix convention used by `checkpoint_builder.thumbnail_key_for`.
    """
    image_key = f"checkpoints/{new_session_id}/{new_checkpoint_id}.png"
    thumb_key = thumbnail_key_for(image_key)
    return image_key, thumb_key


@dataclass(frozen=True)
class ForkedCharacter:
    character: Character
    creation_session: CreationSession
    first_checkpoint: Checkpoint


async def fork_from_checkpoint(
    db: AsyncSession,
    storage: StorageBackend,
    *,
    user: User,
    checkpoint_id: uuid.UUID,
    new_character_name: str,
) -> ForkedCharacter:
    """Open a new Character + CreationSession seeded from `checkpoint_id`.

    Order of operations:
      1. Load + authorize the source checkpoint (initiator-only).
      2. Validate the new character name (regex + per-owner uniqueness
         pre-check); allocate a unique slug.
      3. Insert character + session + first checkpoint via flush (no
         commit yet). The checkpoint row uses a freshly-minted id;
         storage keys are derived from `(new_session_id, new_checkpoint_id)`.
      4. Copy the image bytes (and thumbnail if it exists) — failures
         here roll back the un-committed DB rows so the caller can
         retry with the same name.
      5. Commit. A commit failure after a successful storage copy
         leaves an orphan file (rare; storage GC eventually reclaims).

    NOTE: `copied_from_character_id` is intentionally NOT set on the
    new character. Per ticket: "fork 是不同語義，由 copy 專用 flow 填".
    The new character has the same look at t=0 but is editable from
    here on; the copy_character flow (T-027+) is the one that records
    provenance.
    """
    # ── Authz + source lookup ──────────────────────────────────────────
    # Reuses checkpoint_service which enforces:
    #   - missing → NOT_FOUND_CHECKPOINT
    #   - cross-team session → NOT_FOUND_CHECKPOINT
    #   - same-team-but-not-initiator → NOT_FOUND_CHECKPOINT
    # Identical surface to GET /v1/checkpoints/{id}.
    source_ckpt = await checkpoint_service.get_checkpoint_for_read(
        db, user=user, checkpoint_id=checkpoint_id
    )
    source_session = await creation_session_repo.get(db, source_ckpt.creation_session_id)
    if source_session is None:
        # `get_checkpoint_for_read` already verified the session
        # existed — this is a defensive belt against a race where the
        # session was deleted between fetches.
        raise not_found_creation_session()

    # ── Name validation ────────────────────────────────────────────────
    if not name_pattern_ok(new_character_name):
        raise validation_name_invalid()
    if await character_repo.name_exists_for_owner(db, owner_id=user.id, name=new_character_name):
        raise conflict_duplicate_name()

    async def _slug_taken(slug: str) -> bool:
        return await character_repo.slug_exists_for_owner(db, owner_id=user.id, slug=slug)

    # ── Atomic insert (character + session + first checkpoint) ─────────
    new_checkpoint_id = uuid.uuid4()
    character: Character | None = None
    session: CreationSession | None = None
    first_checkpoint: Checkpoint | None = None
    last_slug_exc: IntegrityError | None = None

    for _attempt in range(_SLUG_RETRY_LIMIT):
        slug = await generate_unique_slug(new_character_name, is_taken=_slug_taken)
        try:
            character = Character(
                team_id=user.team_id,
                owner_id=user.id,
                name=new_character_name,
                slug=slug,
            )
            db.add(character)
            await db.flush()  # populates character.id

            session = CreationSession(
                character_id=character.id,
                initiator_id=user.id,
                # Inherit input_mode from the source — the fork's
                # downstream iteration uses the same conditioning style
                # as the original (template menus / reference uploads).
                input_mode=source_session.input_mode,
            )
            db.add(session)
            await db.flush()  # populates session.id

            character.creation_session_id = session.id

            new_image_key, new_thumb_key = _new_checkpoint_keys(session.id, new_checkpoint_id)

            first_checkpoint = Checkpoint(
                id=new_checkpoint_id,
                creation_session_id=session.id,
                sequence=_FIRST_CHECKPOINT_SEQUENCE,
                # Pure metadata is shared by reference (cleanup-safe
                # per ticket: "prompt / generation_log_id 可共用 reference").
                prompt=source_ckpt.prompt,
                user_menu_selections=source_ckpt.user_menu_selections,
                user_freeform_note=source_ckpt.user_freeform_note,
                # Reference image keys point at the source session's
                # references/ namespace — those rows live as long as
                # the source session does. Once the source session is
                # cleaned up, these keys 404 on signed-URL fetch but
                # the row stays serviceable. Refs are ancillary
                # (they conditioned the original generation, not the
                # forked output); treating them as best-effort is the
                # right tradeoff vs copying every reference image into
                # the new namespace.
                reference_image_keys=source_ckpt.reference_image_keys,
                seed=source_ckpt.seed,
                output_image_key=new_image_key,
                output_image_embedding=source_ckpt.output_image_embedding,
                generation_log_id=source_ckpt.generation_log_id,
                selected_as_base=False,
            )
            db.add(first_checkpoint)
            await db.flush()

            # ── Storage copy inside the transaction ────────────────
            # Image is mandatory; if the source bytes are missing or
            # the storage backend raises, roll back the un-committed
            # rows so the caller can retry with the same name.
            try:
                storage.copy(source_ckpt.output_image_key, new_image_key)
            except NotFoundError as exc:
                await db.rollback()
                _logger.warning(
                    "fork_from_checkpoint: source image missing for checkpoint %s "
                    "(key=%s); rolling back fork attempt",
                    source_ckpt.id,
                    source_ckpt.output_image_key,
                )
                # Re-use the existing 404 surface — semantically the
                # source checkpoint is unfetchable. Caller can't do
                # anything more useful than re-pick a different one.
                raise not_found_checkpoint() from exc

            # Thumbnail copy is best-effort: not every source has
            # one (early Phase 1 / older checkpoints predate the
            # `_thumb.png` write). Failure logs but doesn't roll back.
            source_thumb_key = thumbnail_key_for(source_ckpt.output_image_key)
            if storage.exists(source_thumb_key):
                try:
                    storage.copy(source_thumb_key, new_thumb_key)
                except StorageError:
                    _logger.warning(
                        "fork_from_checkpoint: thumbnail copy failed src=%s dst=%s; "
                        "thumbnail_url will be null on the forked checkpoint",
                        source_thumb_key,
                        new_thumb_key,
                        exc_info=True,
                    )

            await db.commit()
            break
        except IntegrityError as exc:
            await db.rollback()
            if _is_name_violation(exc):
                raise conflict_duplicate_name() from exc
            if _is_slug_violation(exc):
                last_slug_exc = exc
                continue
            raise
    else:
        assert last_slug_exc is not None
        raise last_slug_exc

    assert character is not None and session is not None and first_checkpoint is not None
    await db.refresh(character)
    await db.refresh(session)
    await db.refresh(first_checkpoint)

    return ForkedCharacter(
        character=character,
        creation_session=session,
        first_checkpoint=first_checkpoint,
    )
