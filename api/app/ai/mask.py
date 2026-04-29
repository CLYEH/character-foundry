"""Inpaint-mask validation shared between the real client and the stub (T-030).

`edit_inpaint` callers may run against either GptImage2Client or StubAIClient
depending on `AI_STUB_MODE`; both must reject malformed masks at the same
point so dev / CI catches mask bugs that would otherwise only surface in
production. Centralising the rules here keeps the two implementations from
drifting.

The mask convention follows OpenAI's `/v1/images/edits` contract: a PNG
whose alpha channel marks regions to edit. Transparent pixels (alpha=0)
indicate edit; fully-opaque (alpha=255) means preserve. A mask with no
transparent pixels conveys no edit region and is rejected as EMPTY.
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from app.ai.errors import validation_mask_empty, validation_mask_size_mismatch


def validate_inpaint_mask(base_image_bytes: bytes, mask_png_bytes: bytes) -> None:
    """Raise an `AgentErrorException` if the mask doesn't match the base or is empty.

    Base-decode failures bubble up as Python exceptions: the base image
    came from storage and was decoded successfully on the upload path,
    so a failure here is a worker-level invariant violation — the worker
    converts those uniformly to its own structured error (see
    `VALIDATION_REFERENCE_IMAGE_UNDECODABLE` in
    `app.workers.jobs.create_checkpoint`).

    Mask-decode failures are treated as SIZE_MISMATCH because we can't
    determine the mask's actual dimensions to report. The original
    exception is preserved via `from exc` so operators can see the
    underlying decode error.
    """
    with Image.open(io.BytesIO(base_image_bytes)) as base_im:
        base_size = base_im.size

    try:
        with Image.open(io.BytesIO(mask_png_bytes)) as mask_im:
            mask_size = mask_im.size
            mask_alpha = mask_im.convert("RGBA").getchannel("A")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise validation_mask_size_mismatch(base_size=base_size, mask_size=(0, 0)) from exc

    if base_size != mask_size:
        raise validation_mask_size_mismatch(base_size=base_size, mask_size=mask_size)

    # After `convert("RGBA").getchannel("A")` the band is always single-band
    # 8-bit, so `getextrema` returns a `(min, max)` int tuple. Assert rather
    # than handle a "what if it isn't" branch — that path would be a PIL
    # contract violation, not user input, and conflating it with an EMPTY
    # mask would mislead the user with "draw something" guidance.
    extrema = mask_alpha.getextrema()
    assert isinstance(extrema, tuple) and len(extrema) == 2  # noqa: S101
    min_alpha, _ = extrema
    assert isinstance(min_alpha, int)  # noqa: S101
    if min_alpha >= 255:
        raise validation_mask_empty()
