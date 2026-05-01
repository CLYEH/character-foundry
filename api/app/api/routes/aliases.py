"""`/v1/characters/{character_id}/aliases` — alias create + mask upload (T-031).

Two endpoints:

  - `POST /v1/characters/{id}/aliases/masks` — multipart mask upload.
    Returns `{ mask_id, url }`; the caller embeds that id into the
    alias-create body (or the prompt-preview body, T-035) under
    `{ mask: { mask_id } }`. Mirrors the reference-image upload pattern.

  - `POST /v1/characters/{id}/aliases` — enqueue alias generation.
    Returns 202 `{ task_id, alias_id }`. The alias row is written by
    the worker (`run_create_alias`) on success.

T-032 will add list / detail / patch / delete; this ticket only owns
the write path so the frontend (T-036) can drive end-to-end.
"""

from __future__ import annotations

import io
import logging
import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Response, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    db_session,
    get_current_user,
    get_current_user_no_pin,
    get_storage,
)
from app.core.errors import (
    not_found_character,
    validation_reference_image_too_large,
    validation_reference_image_undecodable,
    validation_reference_image_unsupported_type,
)
from app.core.permissions import assert_can_modify_character
from app.core.redis_client import get_arq_pool
from app.db.session import async_session_factory
from app.models.user import User
from app.repositories import character_repo, mask_repo, motion_repo
from app.schemas.alias import (
    AliasListResponse,
    AliasResponse,
    CreateAliasRequest,
    CreateAliasResponse,
    MaskUploadResponse,
    PatchAliasRequest,
)
from app.schemas.alias_builder import build_alias_dto
from app.services import alias_service
from app.services.alias_service import EnqueuedAlias
from app.storage.backend import StorageBackend
from app.storage.errors import StorageError

router = APIRouter(prefix="/v1/characters", tags=["aliases"])
# Singular `/v1/aliases/{id}` surface — separate router because the
# write-side `/v1/characters/{id}/aliases` flow has a different prefix
# than the read-/mutate-by-id flow. Mirrors `motions.py` which sits at
# the bare prefix and uses fully-qualified paths.
singular_router = APIRouter(tags=["aliases"])
_logger = logging.getLogger(__name__)

# Mask uploads share the reference-image limits — same wire pattern, same
# memory pressure profile.
_MASK_SIZE_LIMIT_BYTES = 10 * 1024 * 1024
_MASK_ALLOWED_MIME_TYPES = frozenset({"image/png"})
_MASK_READ_CHUNK_SIZE = 256 * 1024


@router.post(
    "/{character_id}/aliases/masks",
    response_model=MaskUploadResponse,
    status_code=201,
)
async def upload_alias_mask(
    character_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user_no_pin)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    file: Annotated[UploadFile, File(...)],
) -> MaskUploadResponse:
    """Upload an inpaint mask PNG, return `{ mask_id, url }`.

    Masks are PNG-only (no JPEG/WebP) because the AI client treats
    transparent pixels as the edit region — JPEG drops alpha. Owned by
    the character, not a creation session: alias creation runs after
    Base is locked in, so the session has already closed.

    Same short-lived DB session pattern as the reference-image upload —
    avoids holding a connection across the multipart read.
    """
    content_type = (file.content_type or "").lower()
    if content_type not in _MASK_ALLOWED_MIME_TYPES:
        raise validation_reference_image_unsupported_type()

    # Auth gate in a SHORT-LIVED session — connection returns to the pool
    # before the multipart read starts. Mirror reference-image upload.
    factory = async_session_factory()
    async with factory() as auth_db:
        character = await character_repo.get_active(auth_db, character_id)
        if character is None:
            raise not_found_character()
        # Cross-team → 404 inside, same-team-non-owner → 403.
        assert_can_modify_character(character, user)

    # Stream into memory with a hard cap.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_MASK_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > _MASK_SIZE_LIMIT_BYTES:
            raise validation_reference_image_too_large(
                size_bytes=total,
                limit_bytes=_MASK_SIZE_LIMIT_BYTES,
            )
        chunks.append(chunk)

    payload = b"".join(chunks)

    # Decode-validate AND verify the actual bytes are PNG (not JPEG with
    # a lying Content-Type header). For masks the format matters
    # semantically — JPEG drops alpha, which is exactly the channel the
    # inpaint convention uses to mark the edit region. Accepting a
    # mislabelled JPEG and re-encoding to PNG (`ensure_png_bytes`'s
    # fallback) would discard alpha and produce a "no transparent
    # pixels" mask the worker rejects with VALIDATION_MASK_EMPTY —
    # surfacing as a confusing failure mid-pipeline instead of a clean
    # rejection at upload time (Codex P2 round-1).
    try:
        with Image.open(io.BytesIO(payload)) as im:
            actual_format = (im.format or "").upper()
            im.load()  # force real decode; truncated PNG fails here
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise validation_reference_image_undecodable() from exc
    if actual_format != "PNG":
        raise validation_reference_image_unsupported_type()

    # Storage layout per T-031 ticket Notes:
    #   creation-sessions/{character_id}/masks/{mask_id}.png
    mask_id = uuid.uuid4()
    storage_key = f"creation-sessions/{character_id}/masks/{mask_id}.png"

    storage.put(storage_key, payload, content_type)
    storage_committed = True
    try:
        signed_url = storage.get_signed_url(storage_key, expires_in_seconds=3600)
        async with factory() as insert_db:
            await mask_repo.insert(
                insert_db,
                mask_id=mask_id,
                character_id=character_id,
                uploaded_by_user_id=user.id,
                storage_key=storage_key,
                mime_type=content_type,
                size_bytes=total,
            )
            await insert_db.commit()
        storage_committed = False  # row now references the file
    except BaseException:
        if storage_committed:
            try:
                storage.delete(storage_key)
            except StorageError:
                _logger.warning(
                    "upload_alias_mask: orphan cleanup failed for %s",
                    storage_key,
                )
        raise

    return MaskUploadResponse(mask_id=mask_id, url=signed_url)


@router.post(
    "/{character_id}/aliases",
    response_model=CreateAliasResponse,
    status_code=202,
)
async def create_alias(
    character_id: uuid.UUID,
    body: CreateAliasRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> CreateAliasResponse:
    """Enqueue alias generation. Returns 202 with the reserved task +
    alias ids; the worker writes the alias row + storage on success."""
    enqueued: EnqueuedAlias = await alias_service.enqueue_alias(
        db,
        arq_pool,
        user=user,
        character_id=character_id,
        body=body,
    )
    return CreateAliasResponse(task_id=enqueued.task_id, alias_id=enqueued.alias_id)


# ---------------------------------------------------------------------------
# T-032: list / detail / patch / delete
# ---------------------------------------------------------------------------


@router.get(
    "/{character_id}/aliases",
    response_model=AliasListResponse,
)
async def list_aliases(
    character_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> AliasListResponse:
    """List active aliases for a character (sorted `created_at ASC`).

    Owner-gated per T-032 §Scope. Soft-deleted rows are excluded by
    `alias_repo.list_active_for_character`. Motion counts are computed
    per row — Phase 1 alias counts are tiny (single digits) so the
    per-row count query is cheap; if alias counts grow we'd switch to a
    single grouped query.
    """
    aliases = await alias_service.list_aliases_for_character(
        db, user=user, character_id=character_id
    )
    items = []
    for alias in aliases:
        motion_count = await motion_repo.count_active_for_alias(db, alias_id=alias.id)
        items.append(build_alias_dto(alias, storage, motion_count=motion_count))
    return AliasListResponse(items=items)


@singular_router.get(
    "/v1/aliases/{alias_id}",
    response_model=AliasResponse,
)
async def get_alias(
    alias_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> AliasResponse:
    """Owner-gated detail. Carries motion_count via the AliasDTO.

    The `generation` subset hinted at in api-shape §6.4 is intentionally
    omitted — Sprint 2 / 3 BaseDTO and CheckpointDTO follow the same
    deferral (see `app/schemas/base.py` module docstring). T-04x can
    backfill once a generation_log_repo helper exists.
    """
    detail = await alias_service.get_alias_detail(db, user=user, alias_id=alias_id)
    return AliasResponse(
        alias=build_alias_dto(detail.alias, storage, motion_count=detail.motion_count),
    )


@singular_router.patch(
    "/v1/aliases/{alias_id}",
    response_model=AliasResponse,
)
async def patch_alias(
    alias_id: uuid.UUID,
    body: PatchAliasRequest,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> AliasResponse:
    """Rename. Same-character duplicate → 409, invalid chars → 400."""
    alias = await alias_service.update_alias_name(
        db, user=user, alias_id=alias_id, new_name=body.name
    )
    # Rename doesn't touch motions, but we re-count so the response
    # DTO matches the `GET /v1/aliases/{id}` shape exactly. Cheap (one
    # scalar query) and keeps the contract honest.
    motion_count = await motion_repo.count_active_for_alias(db, alias_id=alias.id)
    return AliasResponse(alias=build_alias_dto(alias, storage, motion_count=motion_count))


@singular_router.delete(
    "/v1/aliases/{alias_id}",
    status_code=204,
)
async def delete_alias(
    alias_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Soft-delete + cascade-soft-delete motions (per F-12).

    Returns 204 with no body so FastAPI doesn't serialize None into a
    JSON `null` (mirrors `delete_character`). Storage cleanup of the
    alias's image and motion videos is deferred to the Sprint 5 cleanup
    job (T-032 §Notes).
    """
    await alias_service.soft_delete_alias(db, user=user, alias_id=alias_id)
    return Response(status_code=204)
