"""Build a `MotionDTO` from a Motion row + StorageBackend (T-033).

Pulled out of the schema module so the schema stays import-clean (no
storage imports). The worker's SSE result publisher and (later)
T-034's read endpoints both call into here so the DTO shape stays
identical.

Storage-key convention (per T-033 Notes + storage-layout §2):
  - base motions  → `bases/{base_id}/motions/{motion_id}.mp4`
  - alias motions → `aliases/{alias_id}/motions/{motion_id}.mp4`
  - thumbnail     → derived by replacing the `.mp4` suffix with
    `_thumb.png` so a future tweak only touches one place.
"""

from __future__ import annotations

import logging

from app.models.generation_log import GenerationLog
from app.models.motion import Motion
from app.schemas.motion import (
    MotionDetailDTO,
    MotionDTO,
    MotionGenerationDTO,
    MotionParentRef,
)
from app.schemas.prompt import MotionType
from app.storage.backend import StorageBackend

_logger = logging.getLogger(__name__)

_SIGNED_URL_TTL_SECONDS = 3600
_VIDEO_SUFFIX = ".mp4"
_THUMBNAIL_SUFFIX = "_thumb.png"


def thumbnail_key_for(video_key: str) -> str:
    """Derive the thumbnail key from the video key.

    `aliases/{alias}/motions/{motion}.mp4` →
    `aliases/{alias}/motions/{motion}_thumb.png`.

    Keeping this rule in code (rather than persisting the thumbnail
    key on the row) means a future change to the suffix only touches
    one place — and the row schema stays minimal. Mirrors
    `app.schemas.checkpoint_builder.thumbnail_key_for`.
    """
    if video_key.endswith(_VIDEO_SUFFIX):
        return video_key[: -len(_VIDEO_SUFFIX)] + _THUMBNAIL_SUFFIX
    return video_key + _THUMBNAIL_SUFFIX


def _signed_url_or_none(storage: StorageBackend, key: str) -> str | None:
    """Mint a signed URL, swallowing storage errors so a single bad key
    doesn't 500 the whole DTO. Same defensive shape as
    `checkpoint_builder._signed_url_or_none` (Codex P2 round-1 there);
    the thumbnail path is fire-and-forget and even the main video URL
    is surfaced as `null` rather than letting the response crash."""
    try:
        return storage.get_signed_url(key, expires_in_seconds=_SIGNED_URL_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — defensive; storage layer raises StorageError
        _logger.exception("motion DTO: signed URL mint failed for key %s", key)
        return None


def build_motion_dto(motion: Motion, storage: StorageBackend) -> MotionDTO:
    """Assemble a MotionDTO with signed video + thumbnail URLs.

    The polymorphic parent ref comes from whichever of `base_id` /
    `alias_id` is populated (the DB CHECK constraint guarantees
    exactly one). A row that violates that invariant would surface
    here as a `RuntimeError` so we don't silently ship a half-built
    parent ref to the wire.
    """
    if motion.base_id is not None:
        parent_ref = MotionParentRef(type="base", id=motion.base_id)
    elif motion.alias_id is not None:
        parent_ref = MotionParentRef(type="alias", id=motion.alias_id)
    else:
        # `chk_motions_exactly_one_parent` should make this unreachable
        # — surface loudly if the invariant ever drifts.
        raise RuntimeError(f"motion {motion.id} has neither base_id nor alias_id")

    video_url = _signed_url_or_none(storage, motion.video_key)
    thumb_key = thumbnail_key_for(motion.video_key)
    thumb_url: str | None
    if storage.exists(thumb_key):
        thumb_url = _signed_url_or_none(storage, thumb_key)
    else:
        thumb_url = None

    return MotionDTO(
        id=motion.id,
        parent=parent_ref,
        # Cast through the Literal — the DB CHECK constraint already
        # restricts `motion_type` to the same set the Literal enumerates.
        motion_type=_cast_motion_type(motion.motion_type),
        name=motion.name,
        description=motion.description,
        video_url=video_url,
        thumbnail_url=thumb_url,
        duration_ms=motion.duration_ms,
        created_at=motion.created_at,
    )


def build_motion_detail_dto(
    motion: Motion,
    storage: StorageBackend,
    *,
    generation_log: GenerationLog | None,
) -> MotionDetailDTO:
    """Assemble a MotionDetailDTO with the same fields as MotionDTO plus
    the `generation` subset.

    `generation_log` is None when the motion's `generation_log_id` is
    null (e.g. a row that predates generation logging) or when the
    caller decides to skip the lookup. The wire field stays None in
    that case rather than synthesising a placeholder.
    """
    base_dto = build_motion_dto(motion, storage)
    if generation_log is None:
        generation_dto: MotionGenerationDTO | None = None
    else:
        generation_dto = MotionGenerationDTO(
            model_name=generation_log.model_name,
            model_version=generation_log.model_version,
            duration_ms=generation_log.duration_ms,
            completed_at=generation_log.completed_at,
        )
    return MotionDetailDTO(
        **base_dto.model_dump(),
        generation=generation_dto,
    )


def _cast_motion_type(raw: str) -> MotionType:
    """Narrow the DB-read string into the wire Literal.

    The DB CHECK (`chk_motions_type`) restricts `motion_type` to the
    same six values the Literal enumerates, so a row read here must
    already match. The runtime check is a last-line guard against
    schema drift — if it ever fires we want a loud RuntimeError, not
    a Pydantic validation error half-way through DTO construction.
    """
    if raw not in (
        "preset_wave",
        "preset_nod",
        "preset_gesture",
        "preset_happy",
        "preset_idle",
        "custom",
    ):  # pragma: no cover — DB constraint guards this
        raise RuntimeError(f"motion row has unexpected motion_type: {raw!r}")
    return raw  # type: ignore[return-value]
