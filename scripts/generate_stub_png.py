"""Generate api/app/ai/_fixtures/*.png — stub PNGs for the AI client.

Used by `app.ai.stub.StubAIClient` so dev / CI / E2E exercise the AI
pipeline without paying the provider. Run when dimensions, contents, or
fixture locations need to change:

    python scripts/generate_stub_png.py

Three fixtures are emitted:

  - sample_base.png    → returned for text2image (Sprint 2 baseline)
  - edit_sample.png    → returned for edit_image2image (Sprint 3 alias)
  - inpaint_sample.png → returned for edit_inpaint     (Sprint 3 alias)

All three are 512x768 RGBA at the same dimensions; the corner pixel
encodes which fixture it is so a test can assert "the right method
returned its stub." The bytes differ enough that `==` distinguishes them.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def make_marked_png(width: int, height: int, marker: tuple[int, int, int, int]) -> bytes:
    """Hand-roll a transparent PNG with a single non-zero pixel at (0, 0).

    The marker pixel lets tests differentiate fixtures by content without
    needing to inspect filenames. Everything else stays transparent so
    downstream PIL operations behave the same as the original blank stub.
    """
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    # IHDR — width, height, bit_depth=8, color_type=6 (RGBA),
    # compression=0, filter=0, interlace=0
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    transparent_pixel = b"\x00\x00\x00\x00"
    marker_pixel = bytes(marker)

    first_scanline = b"\x00" + marker_pixel + transparent_pixel * (width - 1)
    other_scanline = b"\x00" + transparent_pixel * width
    raw = first_scanline + other_scanline * (height - 1)
    idat = zlib.compress(raw, level=9)

    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


_FIXTURES: tuple[tuple[str, tuple[int, int, int, int]], ...] = (
    # (filename, marker RGBA at pixel (0, 0))
    ("sample_base.png", (0, 0, 0, 0)),
    ("edit_sample.png", (255, 0, 255, 255)),
    ("inpaint_sample.png", (0, 255, 255, 255)),
)


def main() -> int:
    out_dir = Path("api/app/ai/_fixtures")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, marker in _FIXTURES:
        out = out_dir / name
        out.write_bytes(make_marked_png(512, 768, marker))
        size = out.stat().st_size
        print(f"wrote {out} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
