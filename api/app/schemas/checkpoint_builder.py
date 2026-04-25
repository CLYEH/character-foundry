"""Build a `CheckpointDTO` from a Checkpoint row + StorageBackend.

Pulled out of the schema module so the schema stays import-clean (no
storage imports). Both the GET checkpoint route and the worker's SSE
result publisher call into here so the DTO shape is identical.
"""

from __future__ import annotations

import logging

from app.models.checkpoint import Checkpoint
from app.schemas.checkpoint import CheckpointDTO
from app.storage.backend import StorageBackend
from app.utils.prompt_summary import build_prompt_summary

_logger = logging.getLogger(__name__)

_SIGNED_URL_TTL_SECONDS = 3600
_THUMBNAIL_SUFFIX = "_thumb.png"


def _signed_url_or_none(storage: StorageBackend, key: str) -> str | None:
    """Mint a signed URL, swallowing storage errors so a single bad key
    doesn't 500 the whole DTO. The thumbnail path is fire-and-forget
    (we may have no thumbnail file at all), and even the main image is
    surfaced as `null` rather than letting the response crash."""
    try:
        return storage.get_signed_url(key, expires_in_seconds=_SIGNED_URL_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — defensive; storage layer raises StorageError
        _logger.exception("checkpoint DTO: signed URL mint failed for key %s", key)
        return None


def thumbnail_key_for(output_image_key: str) -> str:
    """Derive the thumbnail key from the output key.

    `checkpoints/{session}/{ckpt}.png` → `checkpoints/{session}/{ckpt}_thumb.png`.
    Keeping the rule in code (rather than persisting the thumbnail key
    on the row) means a future change to the suffix only touches one
    place — and the row schema stays minimal.
    """
    if output_image_key.endswith(".png"):
        return output_image_key[: -len(".png")] + _THUMBNAIL_SUFFIX
    return output_image_key + _THUMBNAIL_SUFFIX


def build_checkpoint_dto(checkpoint: Checkpoint, storage: StorageBackend) -> CheckpointDTO:
    output_url = _signed_url_or_none(storage, checkpoint.output_image_key)
    thumb_key = thumbnail_key_for(checkpoint.output_image_key)
    thumb_url: str | None
    if storage.exists(thumb_key):
        thumb_url = _signed_url_or_none(storage, thumb_key)
    else:
        thumb_url = None
    summary = build_prompt_summary(
        menu_selections=checkpoint.user_menu_selections,
        freeform_note=checkpoint.user_freeform_note,
    )
    return CheckpointDTO(
        id=checkpoint.id,
        creation_session_id=checkpoint.creation_session_id,
        sequence=checkpoint.sequence,
        prompt_summary=summary,
        output_image_url=output_url,
        thumbnail_url=thumb_url,
        selected_as_base=bool(checkpoint.selected_as_base),
        created_at=checkpoint.created_at,
    )
