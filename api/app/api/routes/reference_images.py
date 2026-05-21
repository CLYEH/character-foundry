"""`POST /v1/creation-sessions/{id}/reference-images` (T-017).

Validates MIME / size, writes bytes through `StorageBackend`, persists
a `reference_images` row, returns `{ reference_image_id, url }`. The
url is a short-lived signed URL — frontend uses it for previews and as
the `<img src>` while iterating.

The route is registered under the creation-sessions prefix (rather than
a top-level `/reference-images` collection) because every reference is
session-scoped: there's no cross-session catalogue, and ownership
follows the session.

DB connection lifetime: this route owns two short-lived `AsyncSession`s
rather than depending on the request-scoped `db_session` (Codex P1
round-6). Holding one session across a 10MB multipart read + storage
write would pin a connection for the duration of the whole upload,
which under concurrent traffic exhausts the pool. Same pattern that
`tasks.py` SSE endpoint uses for its long-lived stream.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from app.api.deps import get_current_user_no_pin, get_storage
from app.auth.scopes import SCOPE_CHARACTER_WRITE, require_scope_no_pin
from app.core.errors import (
    validation_reference_image_too_large,
    validation_reference_image_undecodable,
    validation_reference_image_unsupported_type,
)
from app.db.session import async_session_factory
from app.models.user import User
from app.schemas.reference_image import ReferenceImageUploadResponse
from app.services import checkpoint_service
from app.storage.backend import StorageBackend
from app.storage.errors import StorageError
from app.utils.thumbnails import ensure_png_bytes

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
    user: Annotated[User, Depends(get_current_user_no_pin)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    file: Annotated[UploadFile, File(...)],
    _: None = Depends(require_scope_no_pin(SCOPE_CHARACTER_WRITE)),
) -> ReferenceImageUploadResponse:
    # MIME validation first — cheap and gives the caller a fast reject
    # before we even touch the DB.
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_MIME_TYPES:
        raise validation_reference_image_unsupported_type()

    # Auth gate in a SHORT-LIVED session (Codex P1 round-6). Connection
    # is returned to the pool the moment we exit this `async with`,
    # before the multipart read starts. Same reason `tasks.py` SSE
    # endpoint avoids `Depends(db_session)`.
    factory = async_session_factory()
    async with factory() as auth_db:
        await checkpoint_service.assert_session_writable(auth_db, user=user, session_id=session_id)

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

    # Decode-validate at upload time so corrupted bytes / mislabeled
    # MIME types fail with a 400 next to the upload, not as a delayed
    # task failure when the worker calls `ensure_png_bytes` later
    # (Codex P2 round-2). We only need the validation side-effect — the
    # raw bytes are still what we persist; PIL's lazy decode is forced
    # by the `.load()` call inside `ensure_png_bytes`.
    try:
        ensure_png_bytes(payload)
    except ValueError as exc:
        raise validation_reference_image_undecodable() from exc

    # Storage layout per planning §2 / §4.1:
    #   checkpoints/{session_id}/references/{reference_id}.{ext}
    reference_id = uuid.uuid4()
    extension = _MIME_EXTENSION[content_type]
    storage_key = f"checkpoints/{session_id}/references/{reference_id}.{extension}"

    # Single try/except covers EVERY step that can leave bytes in
    # storage without a row pointing at them: signed-URL minting (e.g.
    # missing secret), the second short-lived DB session, and the
    # INSERT itself. Without this, an exception between `storage.put`
    # and the original DB-only try/except (e.g. URL signing fails)
    # leaks an orphan blob (Codex P2 round-10). The flag flips after
    # `storage.put` succeeds; cleanup only runs if we got that far.
    storage.put(storage_key, payload, content_type)
    storage_committed = True
    try:
        signed_url = storage.get_signed_url(storage_key, expires_in_seconds=3600)
        async with factory() as insert_db:
            created = await checkpoint_service.upload_reference_image(
                insert_db,
                user=user,
                session_id=session_id,
                reference_id=reference_id,
                storage_key=storage_key,
                mime_type=content_type,
                size_bytes=total,
                signed_url=signed_url,
            )
        storage_committed = False  # row now references the file
    except BaseException:
        if storage_committed:
            try:
                storage.delete(storage_key)
            except StorageError:
                _logger.warning(
                    "upload_reference_image: orphan cleanup failed for %s",
                    storage_key,
                )
        raise
    return ReferenceImageUploadResponse(
        reference_image_id=created.reference.id,
        url=signed_url,
    )
