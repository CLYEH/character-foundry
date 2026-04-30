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
from fastapi import APIRouter, Depends, File, UploadFile
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
from app.repositories import character_repo, mask_repo
from app.schemas.alias import (
    CreateAliasRequest,
    CreateAliasResponse,
    MaskUploadResponse,
)
from app.services import alias_service
from app.services.alias_service import EnqueuedAlias
from app.storage.backend import StorageBackend
from app.storage.errors import StorageError

router = APIRouter(prefix="/v1/characters", tags=["aliases"])
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
