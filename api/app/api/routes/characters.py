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

from app.api.deps import db_session, get_current_user
from app.core.redis_client import get_redis
from app.models.character import Character
from app.models.user import User
from app.schemas.character import (
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
from app.schemas.creation_session import CreationSessionDTO
from app.services import character_service

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


def _character_to_dto_with_owners(
    character: Character, owners: dict[uuid.UUID, User]
) -> CharacterDTO:
    """List-path DTO builder — synchronous; takes a pre-fetched owner
    lookup so the caller can amortize the user query across the page.
    Single-character paths still go through `_character_to_dto` which
    does its own `_resolve_owner` lookup."""
    return CharacterDTO(
        id=character.id,
        name=character.name,
        slug=character.slug,
        owner=_owner_summary_from(character.owner_id, owners),
        # Sprint 2 placeholder — T-018 backfills via the bases table.
        base_thumbnail_url=None,
        # Sprint 2 placeholders — T-019 / T-020 backfill these.
        alias_count=0,
        motion_count=0,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


async def _character_to_dto(db: AsyncSession, character: Character) -> CharacterDTO:
    owner = await _resolve_owner(db, character.owner_id)
    return CharacterDTO(
        id=character.id,
        name=character.name,
        slug=character.slug,
        owner=OwnerSummary(id=owner.id, name=owner.name),
        # Sprint 2 placeholder — T-018 backfills via the bases table.
        base_thumbnail_url=None,
        # Sprint 2 placeholders — T-019 / T-020 backfill these.
        alias_count=0,
        motion_count=0,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


async def _character_to_detail_dto(
    db: AsyncSession,
    character: Character,
) -> CharacterDetailDTO:
    owner = await _resolve_owner(db, character.owner_id)
    copied_from: CopiedFromSummary | None = None
    if character.copied_from_character_id is not None:
        src = await db.get(Character, character.copied_from_character_id)
        if src is not None:
            copied_from = CopiedFromSummary(character_id=src.id, name=src.name)
    return CharacterDetailDTO(
        id=character.id,
        name=character.name,
        slug=character.slug,
        owner=OwnerSummary(id=owner.id, name=owner.name),
        # Sprint 2: base + aliases + motions all empty until T-018.
        base=None,
        aliases=[],
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


@router.get("", response_model=CharacterListResponse)
async def list_characters(
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
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
    items = [_character_to_dto_with_owners(c, owners) for c in result.items]
    return CharacterListResponse(items=items, next_cursor=result.next_cursor)


@router.post("", response_model=CreateCharacterResponse, status_code=201)
async def create_character(
    body: CreateCharacterRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[Redis | None, Depends(_maybe_redis)] = None,
) -> CreateCharacterResponse:
    created = await character_service.create_character(
        db,
        redis,
        user=user,
        name=body.name,
        input_mode=body.input_mode,
    )
    character_dto = await _character_to_dto(db, created.character)
    session_dto = _session_to_dto(created.creation_session, checkpoint_count=0)
    return CreateCharacterResponse(character=character_dto, creation_session=session_dto)


@router.get("/{character_id}", response_model=CharacterDetailResponse)
async def get_character(
    character_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CharacterDetailResponse:
    character = await character_service.get_character_for_read(
        db, user=user, character_id=character_id
    )
    return CharacterDetailResponse(character=await _character_to_detail_dto(db, character))


@router.patch("/{character_id}", response_model=CharacterResponse)
async def patch_character(
    character_id: uuid.UUID,
    body: PatchCharacterRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CharacterResponse:
    character = await character_service.update_character_name(
        db, user=user, character_id=character_id, new_name=body.name
    )
    return CharacterResponse(character=await _character_to_dto(db, character))


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
) -> CharacterResponse:
    character = await character_service.restore_character(db, user=user, character_id=character_id)
    return CharacterResponse(character=await _character_to_dto(db, character))
