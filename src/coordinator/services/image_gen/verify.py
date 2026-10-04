# src/coordinator/services/image_gen/verify.py
"""Is this file actually a complete, uncorrupted PNG?

Stdlib only. Pillow is deliberately absent from the backend venv — the image
transport was built without it and the bake-off confirmed Telegram generates
its own previews, so adding a C extension here to read four bytes would be a
dependency bought for nothing.

Two measured reasons this cannot be a magic-bytes check:

1. mflux writes the PNG **three times at the same path**, non-atomically:
   pixels, then an EXIF rewrite, then a pnginfo rewrite. Anything that reads
   between those writes gets a file whose header is perfect and whose tail is
   missing.
2. mflux's ``save_image`` catches every exception and the CLI returns None, so
   a failed save still exits 0.

Walking the chunk list and checking each CRC catches both. The generator's own
wrapper additionally decodes the image with Pillow (which it has) and leaves a
``verified`` marker; the worker requires both to agree, so a corruption that
fools one has to fool two different readers.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class PngCheck:
    ok: bool
    reason: str = ""
    width: int | None = None
    height: int | None = None
    bytes: int = 0


def walk_png(path: Path) -> PngCheck:
    """Validate a PNG by walking its chunks. Never raises."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return PngCheck(False, f"unreadable: {exc}")

    size = len(data)
    if size == 0:
        return PngCheck(False, "empty file")
    if not data.startswith(_MAGIC):
        return PngCheck(False, "not a PNG (bad magic)", bytes=size)

    pos = len(_MAGIC)
    width = height = None
    saw_ihdr = saw_iend = False

    while pos + 8 <= size:
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        payload_at = pos + 8
        crc_at = payload_at + length
        if crc_at + 4 > size:
            return PngCheck(
                False, f"truncated in chunk {tag!r} ({size} bytes)", bytes=size
            )

        payload = data[payload_at:crc_at]
        (declared,) = struct.unpack(">I", data[crc_at : crc_at + 4])
        if zlib.crc32(tag + payload) & 0xFFFFFFFF != declared:
            return PngCheck(False, f"CRC mismatch in chunk {tag!r}", bytes=size)

        if tag == b"IHDR":
            saw_ihdr = True
            if length < 8:
                return PngCheck(False, "IHDR too short", bytes=size)
            width, height = struct.unpack(">II", payload[:8])
        elif tag == b"IEND":
            saw_iend = True
            break
        pos = crc_at + 4

    if not saw_ihdr:
        return PngCheck(False, "no IHDR", bytes=size)
    if not saw_iend:
        # The exact signature of a read during one of mflux's three rewrites.
        return PngCheck(False, "no IEND — truncated or mid-rewrite", bytes=size)
    if not width or not height:
        return PngCheck(False, "zero dimensions", bytes=size)

    return PngCheck(True, "", width=width, height=height, bytes=size)
