#!/usr/bin/env python
"""mflux-generate-qwen-2.1 with a hard MLX memory ceiling.

Runs under the mflux venv's interpreter, NOT the coordinator's.

Exists because mflux exposes no way to set the allocation limit and MLX's
default is ~1.5x the recommended working set — on a 48 GB machine that is
~56 GiB, i.e. unreachable without swapping. Left alone, an overrun does not
raise: it allocates into compressed memory and swap, and the failure presents
as a twenty-minute unresponsive machine rather than an error. Capping below
physical RAM converts that into an exception the supervisor can report.

Tracked in the repo rather than installed as a `sitecustomize.py` in the mflux
venv deliberately: a sitecustomize is untracked, invisible to review, and would
silently apply to every manual mflux invocation too.

Also writes a `verified` marker after confirming the PNG decodes. Pillow is
present here and deliberately absent from the backend venv, so this is the only
place a real decode can happen; the coordinator additionally chunk-walks the
file with the stdlib. Both must agree.
"""

from __future__ import annotations

import os
import pathlib
import sys


def _output_path(argv: list[str]) -> pathlib.Path | None:
    for i, a in enumerate(argv):
        if a == "--output" and i + 1 < len(argv):
            return pathlib.Path(argv[i + 1])
        if a.startswith("--output="):
            return pathlib.Path(a.split("=", 1)[1])
    return None


def main() -> int:
    limit = os.environ.get("MFLUX_MEM_LIMIT_BYTES")
    if limit:
        import mlx.core as mx

        mx.set_memory_limit(int(limit))

    from mflux.models.qwen21.cli.qwen21_generate import main as mflux_main

    rc = mflux_main()
    rc = 0 if rc is None else int(rc)

    # mflux swallows save errors and still exits 0, so a zero return code is
    # not evidence an image exists. Decode it here while Pillow is available.
    out = _output_path(sys.argv)
    if rc == 0 and out is not None:
        try:
            from PIL import Image

            with Image.open(out) as im:
                im.verify()
            with Image.open(out) as im:
                im.load()
            out.with_name("verified").write_text("ok")
        except Exception as exc:  # noqa: BLE001 - report, never mask the rc
            print(f"[wrapper] output failed verification: {exc}", file=sys.stderr)
            return 90
    return rc


if __name__ == "__main__":
    sys.exit(main())
