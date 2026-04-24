"""`GET /storage/{key}` — serves files signed by the local storage backend.

Verifies the JWT in `?token=`, then streams the file. Outside `/v1` because
this is file serving, not API surface (per planning/backend/api-shape.md §5.8).
"""

from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_storage
from app.core.errors import AgentError, AgentErrorException
from app.storage.backend import StorageBackend
from app.storage.errors import NotFoundError, StorageError
from app.storage.signed_url import SignedURLExpired, SignedURLInvalid, verify_token

router = APIRouter()

_CHUNK_SIZE = 64 * 1024


def _stream(handle: BinaryIO) -> Iterator[bytes]:
    try:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        handle.close()


@router.get("/storage/{key:path}")
def serve_file(
    key: str,
    token: Annotated[str, Query(description="Signed URL token")],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> StreamingResponse:
    try:
        verify_token(token, expected_key=key)
    except SignedURLExpired as exc:
        raise AgentErrorException(
            AgentError(
                code="STORAGE_URL_EXPIRED",
                message="檔案連結已過期，請重新載入",
                problem="Signed storage URL has expired.",
                cause="The token's `exp` field is in the past.",
                fix="Re-fetch the parent resource (e.g. GET /v1/characters/{id}) to obtain a fresh signed URL.",
                retryable=True,
            ),
            status_code=403,
        ) from exc
    except SignedURLInvalid as exc:
        raise AgentErrorException(
            AgentError(
                code="AUTH_INVALID_TOKEN",
                message="無效的存取權杖",
                problem="Storage token signature is invalid or does not match the requested key.",
                cause="Token was tampered with, signed by a different secret, or generated for a different key.",
                fix="Re-authenticate and request a fresh signed URL.",
                retryable=False,
            ),
            status_code=403,
        ) from exc

    try:
        handle = storage.get_stream(key)
    except NotFoundError as exc:
        raise AgentErrorException(
            AgentError(
                code="STORAGE_NOT_FOUND",
                message="檔案不存在",
                problem=f"Key {key!r} not found in storage.",
                cause="The file was deleted, never created, or the key is wrong.",
                fix="Verify the key against the parent resource API.",
                retryable=False,
            ),
            status_code=404,
        ) from exc
    except StorageError as exc:
        raise AgentErrorException(
            AgentError(
                code="STORAGE_READ_FAILED",
                message="檔案讀取失敗",
                problem=f"Storage backend rejected key {key!r}: {exc}",
                cause="Backend returned an error while opening the requested key.",
                fix="Check storage logs; key may be malformed.",
                retryable=False,
            ),
            status_code=500,
        ) from exc

    content_type, _ = mimetypes.guess_type(key)
    return StreamingResponse(
        _stream(handle),
        media_type=content_type or "application/octet-stream",
    )
