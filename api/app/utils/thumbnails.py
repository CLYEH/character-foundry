"""PIL-backed image utilities — thumbnails + format normalisation.

Phase 1 only needs a 512-pixel-wide thumbnail per checkpoint output;
preserving alpha (PNG) is mandatory because the platform constraint
forces transparent-background output.

If PIL fails (corrupt source bytes, OOM, etc.) we return None — the
worker logs and continues, so a thumbnail-generation hiccup never
blocks the checkpoint write. The DTO surface tolerates a missing
thumbnail by returning a null thumbnail_url.
"""

from __future__ import annotations

import io
import logging

from PIL import Image, UnidentifiedImageError

_logger = logging.getLogger(__name__)

_DEFAULT_WIDTH = 512


def ensure_png_bytes(source: bytes) -> bytes:
    """Re-encode `source` as PNG if it isn't already.

    The OpenAI image-edits client labels every multipart upload as
    `image/png` regardless of the bytes — so a JPEG / WebP reference
    sent verbatim trips provider-side validation. Convert here before
    the AI call. Idempotent on PNG input (returns the original bytes
    unmodified) to avoid a needless re-encode roundtrip.

    Raises ValueError if PIL can't decode the source — callers should
    map that to a user-visible error since the upload route already
    validated the MIME type.
    """
    try:
        with Image.open(io.BytesIO(source)) as im:
            if im.format == "PNG":
                # PIL.Image.open is lazy — `im.format` reads only the
                # header, so a truncated PNG passes this check unless
                # we force a real decode. `.load()` triggers full pixel
                # decode and raises OSError on bad bytes (Codex P2
                # round-5). We discard the decoded buffer because we
                # return the original bytes for PNG fast-path.
                im.load()
                return source
            decoded = im.convert("RGBA")
            buf = io.BytesIO()
            decoded.save(buf, format="PNG")
            return buf.getvalue()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to decode image bytes: {exc}") from exc


def make_thumbnail_png(
    source: bytes,
    *,
    width: int = _DEFAULT_WIDTH,
) -> bytes | None:
    """Return PNG bytes of a `width`-pixel-wide thumbnail of `source`,
    or None if PIL can't decode / re-encode the input.

    Aspect ratio is preserved; LANCZOS resampling is the standard for
    photographic content with translucent backgrounds. We force RGBA
    on save so the output PNG always carries an alpha channel — even
    when the source is opaque, downstream <img> compositing must not
    pick up a different background color.
    """
    if width <= 0:
        return None
    try:
        with Image.open(io.BytesIO(source)) as im:
            # Open is lazy — calling .copy() forces a decode now so any
            # IO error fires inside our except block, not later.
            decoded = im.convert("RGBA")
            src_w, src_h = decoded.size
            if src_w <= 0 or src_h <= 0:
                return None
            # Don't upscale — if the source is already smaller than the
            # requested width, just re-encode at original size. Phase 1
            # AI outputs are always larger so this is a safety branch.
            if src_w <= width:
                target = decoded
            else:
                target_h = max(1, round(src_h * width / src_w))
                target = decoded.resize((width, target_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            target.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except (OSError, ValueError, UnidentifiedImageError):
        _logger.warning("make_thumbnail_png: PIL failed to process image", exc_info=True)
        return None
