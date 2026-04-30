"""Build an `AliasDTO` from an Alias row + StorageBackend.

Pulled out of the schema module so the schema stays import-clean (no
storage imports). Both the worker's SSE result publisher (T-031) and
the future `GET /v1/aliases/{id}` route (T-032) call into here so the
DTO shape is identical between create-time and read-time surfaces.
"""

from __future__ import annotations

import logging
from typing import Literal, cast

from app.models.alias import Alias
from app.schemas.alias import AliasDTO
from app.schemas.checkpoint_builder import thumbnail_key_for
from app.storage.backend import StorageBackend

_logger = logging.getLogger(__name__)

_SIGNED_URL_TTL_SECONDS = 3600

_AliasInputMode = Literal["image2image", "inpaint", "text2image", "mixed"]


def _signed_url_or_none(storage: StorageBackend, key: str) -> str | None:
    try:
        return storage.get_signed_url(key, expires_in_seconds=_SIGNED_URL_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — defensive; storage layer raises StorageError
        _logger.exception("alias DTO: signed URL mint failed for key %s", key)
        return None


def build_alias_dto(alias: Alias, storage: StorageBackend, *, motion_count: int = 0) -> AliasDTO:
    """Mint signed URLs from `alias.image_key` and assemble the DTO.

    `motion_count` defaults to 0 — T-031 doesn't yet wire motion counts
    into the alias DTO (motions are T-033+). T-037 / T-032 can pass a
    real count when the read paths land.
    """
    image_url = _signed_url_or_none(storage, alias.image_key)
    thumb_key = thumbnail_key_for(alias.image_key)
    thumb_url: str | None
    if storage.exists(thumb_key):
        thumb_url = _signed_url_or_none(storage, thumb_key)
    else:
        thumb_url = None
    return AliasDTO(
        id=alias.id,
        character_id=alias.character_id,
        name=alias.name,
        # The DB CHECK restricts `input_mode` to the literal set; the
        # cast keeps mypy happy without hand-rolling a runtime check we
        # already get from the database.
        input_mode=cast(_AliasInputMode, alias.input_mode),
        image_url=image_url,
        thumbnail_url=thumb_url,
        motion_count=motion_count,
        created_at=alias.created_at,
    )
