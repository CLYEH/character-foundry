"""StubAIClient happy paths (T-014)."""

from __future__ import annotations

from app.ai.stub import StubAIClient


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
