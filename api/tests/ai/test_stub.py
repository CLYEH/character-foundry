"""StubAIClient happy paths (T-014, extended T-030)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.ai.stub import StubAIClient
from app.core.errors import AgentErrorException


async def test_stub_returns_valid_png_for_text2image() -> None:
    stub = StubAIClient()
    result = await stub.generate_image_text2image("hello", aspect_ratio="1:1")
    assert result.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert result.model_version == StubAIClient.MODEL_VERSION
    assert result.duration_ms == StubAIClient.DEFAULT_DURATION_MS
    assert result.cost_units == StubAIClient.DEFAULT_COST_UNITS


async def test_stub_returns_same_bytes_for_all_modes() -> None:
    stub = StubAIClient()
    a = await stub.generate_image_text2image("p")
    b = await stub.generate_image_image2image("p", b"img")
    c = await stub.generate_image_inpaint("p", b"img", b"mask")
    assert a.image_bytes == b.image_bytes == c.image_bytes


async def test_stub_fixture_dimensions_match_spec() -> None:
    """Ticket says 512x768 transparent PNG. Sanity-check the IHDR chunk."""
    import struct

    stub = StubAIClient()
    data = stub.image_bytes
    # IHDR chunk starts at byte 8 (signature) + 4 (length) + 4 (type) = 16.
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (512, 768)


def _png(width: int, height: int, alpha: int) -> bytes:
    img = Image.new("RGBA", (width, height), (0, 0, 0, alpha))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def test_stub_edit_image2image_returns_distinct_fixture() -> None:
    """Per-mode fixtures let downstream tests assert which path fired."""
    stub = StubAIClient()
    result = await stub.edit_image2image(
        base_image_bytes=b"base", reference_image_bytes=[b"a"], prompt="p"
    )
    assert result.image_bytes == stub.edit_image_bytes
    assert result.image_bytes != stub.image_bytes


async def test_stub_edit_inpaint_returns_distinct_fixture() -> None:
    base = _png(64, 96, alpha=255)
    mask = _png(64, 96, alpha=0)
    stub = StubAIClient()
    result = await stub.edit_inpaint(base_image_bytes=base, mask_png_bytes=mask, prompt="p")
    assert result.image_bytes == stub.inpaint_image_bytes
    assert result.image_bytes != stub.edit_image_bytes


async def test_stub_edit_inpaint_validates_mask_size() -> None:
    """Stub mirrors the real client's validation so dev / CI catches
    bad masks instead of silently returning fixture bytes."""
    base = _png(64, 96, alpha=255)
    mask = _png(32, 48, alpha=0)  # wrong dims
    stub = StubAIClient()
    with pytest.raises(AgentErrorException) as info:
        await stub.edit_inpaint(base_image_bytes=base, mask_png_bytes=mask, prompt="p")
    assert info.value.error.code == "VALIDATION_MASK_SIZE_MISMATCH"


async def test_stub_edit_inpaint_validates_mask_emptiness() -> None:
    base = _png(64, 96, alpha=255)
    mask = _png(64, 96, alpha=255)  # no transparent pixel → empty
    stub = StubAIClient()
    with pytest.raises(AgentErrorException) as info:
        await stub.edit_inpaint(base_image_bytes=base, mask_png_bytes=mask, prompt="p")
    assert info.value.error.code == "VALIDATION_MASK_EMPTY"
