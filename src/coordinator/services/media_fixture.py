# src/coordinator/services/media_fixture.py
"""A generated probe PNG, for proving the media transport without a backend.

Generated rather than checked in, for two reasons. Production code reading a
blob out of ``tests/fixtures/`` is backwards. And deterministic bytes have a
stable sha256, which a test can pin as a literal — a stronger artifact than a
blob, because a regression in the encoder fails immediately instead of being
silently re-committed.

Pure stdlib: ``zlib`` and ``struct``. Pillow is in neither venv and is not worth
adding to emit a gradient.
"""

from __future__ import annotations

import struct
import zlib

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length, tag, payload, CRC over tag+payload."""
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def build_probe_png(size: int = 256) -> bytes:
    """An 8-bit RGB gradient, ``size`` x ``size``.

    A gradient rather than a flat colour so that a human glancing at a Telegram
    client can tell a real render from a blank placeholder, and so that any
    recompression on the wire is visible as banding.
    """
    if size < 1 or size > 4096:
        raise ValueError("probe size must be between 1 and 4096")

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG per-scanline filter type: 0 = None
        for x in range(size):
            rows += bytes(
                (
                    (x * 255) // max(size - 1, 1),
                    (y * 255) // max(size - 1, 1),
                    0x80,
                )
            )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit, colour type 2 (RGB)
    return (
        _PNG_MAGIC
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _chunk(b"IEND", b"")
    )
