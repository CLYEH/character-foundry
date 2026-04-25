"""Generate api/app/ai/_fixtures/sample_base.png — a transparent 512x768 PNG.

Used by `app.ai.stub.StubAIClient` so dev / CI / E2E exercise the AI
pipeline without paying the provider. Run when the dimensions or fixture
location need to change:

    python scripts/generate_stub_png.py
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def make_transparent_png(width: int, height: int) -> bytes:
    """Hand-roll a fully-transparent PNG (RGBA, 8-bit/channel).

    Avoids a Pillow dependency for one tiny build-time artefact. PNG layout
    follows the spec at https://www.w3.org/TR/PNG/.
    """
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    # IHDR — width, height, bit_depth=8, color_type=6 (RGBA),
    # compression=0, filter=0, interlace=0
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    # Raw scanlines: filter byte (0 = None) + RGBA pixels (all zero = transparent)
    scanline = b"\x00" + b"\x00\x00\x00\x00" * width
    raw = scanline * height
    idat = zlib.compress(raw, level=9)

    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def main() -> int:
    out = Path("api/app/ai/_fixtures/sample_base.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(make_transparent_png(512, 768))
    size = out.stat().st_size
    print(f"wrote {out} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
