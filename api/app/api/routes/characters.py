"""`/v1/characters/*` — Character CRUD + restore (T-016).

See planning/backend/api-shape.md §5.1 / §6.1 / §6.2 for the wire
contracts. Detail/list DTOs are intentionally minimal until T-018
populates `base` and T-019 populates aliases / motions counts.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_storage
from app.core.redis_client import get_redis
from app.models.character import Character
from app.models.user import User
from app.repositories import base_repo
from app.schemas.character import (
    CharacterDetailCreationSessionRef,
    CharacterDetailDTO,
    CharacterDetailResponse,
    CharacterDTO,
    CharacterListResponse,
    CharacterResponse,
    CopiedFromSummary,
    CreateCharacterRequest,
    CreateCharacterResponse,
    OwnerSummary,
    PatchCharacterRequest,
)
from app.schemas.checkpoint_builder import build_base_dto, thumbnail_key_for
from app.schemas.creation_session import CreationSessionDTO
from app.services import character_service
from app.storage.backend import StorageBackend

router = APIRouter(prefix="/v1/characters", tags=["characters"])
_logger = logging.getLogger(__name__)


async def _maybe_redis(request: Request) -> Redis | None:
    """Resolve Redis but tolerate failure — used by endpoints where
    Redis is best-effort (Codex round-6 P2). `get_redis` raises if
    REDIS_URL is unset; depending on it directly would 500 the
    request before the DB transaction runs even though the service
    layer documents Redis as optional. We honor `app.dependency_overrides`
    so test stubs still work; only the underlying call is wrapped.
    """
    resolver = request.app.dependency_overrides.get(get_redis, get_redis)
    try:
        client: Redis | None = await resolver()
    except Exception:  # noqa: BLE001 — best-effort, see docstring
        _logger.warning(
            "_maybe_redis: redis resolver raised; continuing without Redis",
            exc_info=True,
        )
        return None
    return client


# ---------------------------------------------------------------------------
# DTO builders
# ---------------------------------------------------------------------------


async def _resolve_owner(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Look up the embedded `owner` block. Used for single-character
    paths (detail, patch, restore). The list path bypasses this and
    batch-loads owners via `_owners_by_ids` to avoid N+1 (Codex
    round-7 P2)."""
    user = await db.get(User, user_id)
    if user is None:
        # The owner_id FK is RESTRICT, so this is theoretically
        # unreachable — but rather than crash with KeyError downstream,
        # synthesize a minimal stand-in. The DTO requires `id + name`.
        return User(id=user_id, name="(unknown)", team_id=uuid.UUID(int=0))
    return user


async def _owners_by_ids(db: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, User]:
    """Bulk-fetch users for a set of ids. One round-trip; the list
    endpoint uses this to build owner summaries without `db.get` per
    row (Codex round-7 P2 — N+1 on team-wide lists with many distinct
    owners). Empty input short-circuits to an empty dict."""
    if not user_ids:
        return {}
    stmt = select(User).where(User.id.in_(user_ids))
    result = await db.execute(stmt)
    return {u.id: u for u in result.scalars().all()}


def _owner_summary_from(owner_id: uuid.UUID, owners: dict[uuid.UUID, User]) -> OwnerSummary:
    """Same fall-through as `_resolve_owner` for the rare missing-row
    case (FK is RESTRICT so it shouldn't happen in practice)."""
    user = owners.get(owner_id)
    name = user.name if user is not None else "(unknown)"
    return OwnerSummary(id=owner_id, name=name)


def _base_thumb_url(
    storage: StorageBackend | None,
    image_key: str | None,
) -> str | None:
    """Mint the Base thumbnail signed URL for the list / DTO surfaces.

    Returns None when:
    - No Base is selected yet (image_key is None).
    - Storage was not provided (callers that don't need URLs).
    - The thumbnail file doesn't exist (Phase 1 worker writes it on
      every checkpoint commit, but we still preflight `exists` for
      defensive symmetry with `build_checkpoint_dto`).
    - Signed URL minting raises (logged, swallowed).
    """
    if storage is None or image_key is None:
        return None
    thumb_key = thumbnail_key_for(image_key)
    if not storage.exists(thumb_key):
        return None
    try:
        return storage.get_signed_url(thumb_key, expires_in_seconds=3600)
    except Exception:  # noqa: BLE001 — defensive parity with checkpoint DTO
        _logger.exception("character DTO: thumbnail signed URL mint failed for %s", thumb_key)
        return None


def build_character_list_dto_with_owners(
    character: Character,
    owners: dict[uuid.UUID, User],
    *,
    base_image_key: str | None = None,
    storage: StorageBackend | None = None,
) -> CharacterDTO:
    """List-path DTO builder — synchronous; takes a pre-fetched owner
    lookup so the caller can amortize the user query across the page.
    `base_image_key` is supplied by the caller (list endpoint batches
    a `bases` query keyed by `character_id`) so we don't re-fetch per
    row; the helper is sync to keep the per-row cost predictable.
    """
    return CharacterDTO(
        id=character.id,
        name=character.name,
        slug=character.slug,
        owner=_owner_summary_from(character.owner_id, owners),
        base_thumbnail_url=_base_thumb_url(storage, base_image_key),
        # Sprint 2 placeholders — T-019 / T-020 backfill these.
        alias_count=0,
        motion_count=0,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


async def build_character_list_dto(
    db: AsyncSession,
    character: Character,
    *,
    storage: StorageBackend | None = None,
) -> CharacterDTO:
    """Single-character DTO. Looks up the linked Base on demand —
    the route hits this only for create / detail / patch / restore,
    so one-extra-query is fine. List endpoint uses the bulk path."""
    owner = await _resolve_owner(db, character.owner_id)
    base_image_key: str | None = None
    if character.base_id is not None:
        base = await base_repo.get_by_character_id(db, character.id)
        if base is not None:
            base_image_key = base.image_key
    return CharacterDTO(
        id=character.id,
        name=character.name,
        slug=character.slug,
        owner=OwnerSummary(id=owner.id, name=owner.name),
        base_thumbnail_url=_base_thumb_url(storage, base_image_key),
        # Sprint 2 placeholders — T-019 / T-020 backfill these.
        alias_count=0,
        motion_count=0,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


async def _character_to_detail_dto(
    db: AsyncSession,
    character: Character,
    *,
    storage: StorageBackend | None = None,
) -> CharacterDetailDTO:
    owner = await _resolve_owner(db, character.owner_id)
    copied_from: CopiedFromSummary | None = None
    if character.copied_from_character_id is not None:
        src = await db.get(Character, character.copied_from_character_id)
        if src is not None:
            copied_from = CopiedFromSummary(character_id=src.id, name=src.name)
    base_payload: dict[str, object] | None = None
    if character.base_id is not None and storage is not None:
        base = await base_repo.get_by_character_id(db, character.id)
        if base is not None:
            # Use the same builder the select-base response uses so the
            # detail surface stays identical to the immediate-after-
            # select payload.
            base_payload = build_base_dto(base, storage).model_dump(mode="json")
    # Resolve the embedded session ref. The contract (api-shape §6.2)
    # is that this is populated only when `base` is null — confirming
    # a Base closes the session for routing purposes, so the resume
    # CTA doesn't need the ref. Skipping the lookup when base is set
    # mirrors the contract's payload-trim rule.
    #
    # The `creation_session_id is not None` guard catches the FK-orphan
    # case: `Character.creation_session_id` is `ON DELETE SET NULL`,
    # so a session row deleted out from under a Base-less character
    # leaves the column null. Frontend handles that as the "abnormal
    # state" fallback in IncompleteCharacterCard.
    session_ref: CharacterDetailCreationSessionRef | None = None
    if base_payload is None and character.creation_session_id is not None:
        from app.models.creation_session import CreationSession

        session = await db.get(CreationSession, character.creation_session_id)
        # `completed` collapses to None — the contract says completed
        # sessions always come with a Base, so seeing one here means the
        # row got into an inconsistent state we shouldn't paper over by
        # surfacing 'completed' to a status that the frontend types
        # don't allow.
        if session is not None and session.status in ("in_progress", "abandoned"):
            session_ref = CharacterDetailCreationSessionRef(
                id=session.id,
                status=session.status,  # type: ignore[arg-type]
            )
    return CharacterDetailDTO(
        id=character.id,
        name=character.name,
        slug=character.slug,
        owner=OwnerSummary(id=owner.id, name=owner.name),
        base=base_payload,
        # Aliases + motions still placeholders until T-019 / T-020.
        aliases=[],
        creation_session=session_ref,
        copied_from=copied_from,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


def _session_to_dto(session: object, checkpoint_count: int = 0) -> CreationSessionDTO:
    # `session` is a CreationSession; typed as `object` to keep the
    # import surface flat. The route ensures the right type lands here.
    from app.models.creation_session import CreationSession

    assert isinstance(session, CreationSession)
    return CreationSessionDTO(
        id=session.id,
        character_id=session.character_id,
        input_mode=session.input_mode,  # type: ignore[arg-type]
        status=session.status,  # type: ignore[arg-type]
        checkpoint_count=checkpoint_count,
        created_at=session.created_at,
        completed_at=session.completed_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def _bases_for_characters(
    db: AsyncSession,
    characters: list[Character],
) -> dict[uuid.UUID, str]:
    """Bulk-fetch `image_key` for the bases of a page of characters.
    Returns a `{character_id: image_key}` map; characters without a
    Base are absent from the map (caller treats missing as None).

    One query per page — cheap because `bases.character_id` is UNIQUE
    so the lookup hits the column's implicit btree index. Avoiding the
    N+1 a per-row `base_repo.get_by_character_id` would create."""
    from sqlalchemy import select

    from app.models.base import BaseAsset

    base_ids = {c.base_id for c in characters if c.base_id is not None}
    if not base_ids:
        return {}
    stmt = select(BaseAsset).where(BaseAsset.id.in_(base_ids))
    result = await db.execute(stmt)
    return {row.character_id: row.image_key for row in result.scalars().all()}


@router.get("", response_model=CharacterListResponse)
async def list_characters(
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    owner_id: Annotated[
        str | None,
        Query(
            description="`me` (caller), an explicit user UUID, or omitted (whole team).",
        ),
    ] = None,
    q: Annotated[str | None, Query(description="ILIKE substring match on name.")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> CharacterListResponse:
    resolved_owner_id: uuid.UUID | None
    if owner_id is None or owner_id == "":
        resolved_owner_id = None
    elif owner_id == "me":
        resolved_owner_id = user.id
    else:
        try:
            resolved_owner_id = uuid.UUID(owner_id)
        except ValueError:
            # Bad UUID → treat as "no match" (empty list) rather than
            # 400, so a stale frontend cache passing a non-uuid string
            # degrades gracefully.
            return CharacterListResponse(items=[], next_cursor=None)

    result = await character_service.list_characters(
        db,
        user=user,
        owner_id=resolved_owner_id,
        q=q,
        limit=limit,
        cursor_str=cursor,
    )
    # Bulk-fetch the unique owners in one round-trip so DTO assembly
    # is O(1) DB work, not O(page size). Codex round-7 P2.
    owner_ids = {c.owner_id for c in result.items}
    owners = await _owners_by_ids(db, owner_ids)
    base_keys = await _bases_for_characters(db, list(result.items))
    items = [
        build_character_list_dto_with_owners(
            c,
            owners,
            base_image_key=base_keys.get(c.id),
            storage=storage,
        )
        for c in result.items
    ]
    return CharacterListResponse(items=items, next_cursor=result.next_cursor)


@router.post("", response_model=CreateCharacterResponse, status_code=201)
async def create_character(
    body: CreateCharacterRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    redis: Annotated[Redis | None, Depends(_maybe_redis)] = None,
) -> CreateCharacterResponse:
    created = await character_service.create_character(
        db,
        redis,
        user=user,
        name=body.name,
        input_mode=body.input_mode,
    )
    character_dto = await build_character_list_dto(db, created.character, storage=storage)
    session_dto = _session_to_dto(created.creation_session, checkpoint_count=0)
    return CreateCharacterResponse(character=character_dto, creation_session=session_dto)


@router.get("/{character_id}", response_model=CharacterDetailResponse)
async def get_character(
    character_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> CharacterDetailResponse:
    character = await character_service.get_character_for_read(
        db, user=user, character_id=character_id
    )
    return CharacterDetailResponse(
        character=await _character_to_detail_dto(db, character, storage=storage),
    )


@router.patch("/{character_id}", response_model=CharacterResponse)
async def patch_character(
    character_id: uuid.UUID,
    body: PatchCharacterRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> CharacterResponse:
    character = await character_service.update_character_name(
        db, user=user, character_id=character_id, new_name=body.name
    )
    return CharacterResponse(
        character=await build_character_list_dto(db, character, storage=storage)
    )


@router.delete("/{character_id}", status_code=204)
async def delete_character(
    character_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    await character_service.soft_delete_character(db, user=user, character_id=character_id)
    # Manually return 204 with no body so FastAPI doesn't try to
    # serialize None into a JSON `null` (which some clients reject).
    return Response(status_code=204)


@router.post("/{character_id}/restore", response_model=CharacterResponse)
async def restore_character(
    character_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> CharacterResponse:
    character = await character_service.restore_character(db, user=user, character_id=character_id)
    return CharacterResponse(
        character=await build_character_list_dto(db, character, storage=storage)
    )
