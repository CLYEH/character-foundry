"""`POST /v1/creation-sessions/{id}/reference-images` (T-017).

Validates MIME / size, writes bytes through `StorageBackend`, persists
a `reference_images` row, returns `{ reference_image_id, url }`. The
url is a short-lived signed URL — frontend uses it for previews and as
the `<img src>` while iterating.

The route is registered under the creation-sessions prefix (rather than
a top-level `/reference-images` collection) because every reference is
session-scoped: there's no cross-session catalogue, and ownership
follows the session.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user, get_storage
from app.core.errors import (
    validation_reference_image_too_large,
    validation_reference_image_unsupported_type,
)
from app.models.user import User
from app.schemas.reference_image import ReferenceImageUploadResponse
from app.services import checkpoint_service
from app.storage.backend import StorageBackend

router = APIRouter(prefix="/v1/creation-sessions", tags=["creation_sessions"])
_logger = logging.getLogger(__name__)

# 10 MB cap (T-017 ticket).
_SIZE_LIMIT_BYTES = 10 * 1024 * 1024
_ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
# Read in chunks so a >10MB upload trips the limit before we have the whole
# blob in memory. Picked a small chunk so the early-abort window is tight;
# the upload is small enough that the per-chunk overhead is irrelevant.
_READ_CHUNK_SIZE = 256 * 1024

_MIME_EXTENSION = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


@router.post(
    "/{session_id}/reference-images",
    response_model=ReferenceImageUploadResponse,
    status_code=201,
)
async def upload_reference_image(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(db_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    file: Annotated[UploadFile, File(...)],
) -> ReferenceImageUploadResponse:
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_MIME_TYPES:
        raise validation_reference_image_unsupported_type()

    # Stream the upload into memory with an upper bound so a hostile
    # client can't blow up the worker by sending a 1 GB blob with a
    # forged Content-Length header. UploadFile.size is server-supplied
    # but unreliable across multipart parsers, so we count bytes as we
    # read.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > _SIZE_LIMIT_BYTES:
            raise validation_reference_image_too_large(
                size_bytes=total,
                limit_bytes=_SIZE_LIMIT_BYTES,
            )
        chunks.append(chunk)

    payload = b"".join(chunks)

    # Reserve the storage key BEFORE writing — gives us a stable id we
    # can hand back to the client even if the DB INSERT later races
    # with a session abort. Storage layout per planning §2 / §4.1:
    #   checkpoints/{session_id}/references/{reference_id}.{ext}
    reference_id = uuid.uuid4()
    extension = _MIME_EXTENSION[content_type]
    storage_key = f"checkpoints/{session_id}/references/{reference_id}.{extension}"

    storage.put(storage_key, payload, content_type)
    signed_url = storage.get_signed_url(storage_key, expires_in_seconds=3600)

    # Authorization happens inside the service via _get_writable_session.
    # The storage write above precedes the DB insert; if the service
    # rejects the upload (wrong session, not initiator, session not
    # active) the bytes are orphaned. Lifecycle cleanup reaps them when
    # the parent session is hard-deleted, which is acceptable for
    # Phase 1 — the alternative (delete on validation failure) would
    # add a storage round-trip on the cold path.
    created = await checkpoint_service.upload_reference_image(
        db,
        user=user,
        session_id=session_id,
        reference_id=reference_id,
        storage_key=storage_key,
        mime_type=content_type,
        size_bytes=total,
        signed_url=signed_url,
    )
    return ReferenceImageUploadResponse(
        reference_image_id=created.reference.id,
        url=signed_url,
    )
