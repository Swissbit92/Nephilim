#!/usr/bin/env python
"""mflux-generate-qwen-2.1 with an MLX memory ceiling — and a correction.

Runs under the mflux venv's interpreter, NOT the coordinator's.

⚠️ **`mx.set_memory_limit()` IS ADVISORY. It does not raise.** An earlier
version of this file claimed it "converts an overrun into an exception the
supervisor can report". That was written from MLX's API shape, never tested,
and it is **false** — measured on mlx 0.32.3 on this machine: a 4 GiB
allocation against a 2 GiB limit succeeded silently, `get_active_memory()`
read 4.00 GiB, and nothing was raised. The same measurement corrected a second
borrowed number in that docstring: MLX's default limit here is **45.60 GiB**,
not the "~1.5x the recommended working set, about 56 GiB" claimed — 1.5x is
not the rule this build uses.

So the ceiling is kept, but for what it actually buys: it caps MLX's own
allocator bookkeeping and makes the intended budget explicit and reviewable at
the point of spawn. **It is not the protection.** The protection is external —
`services/image_gen/supervisor.py` watches free system memory and kills the
job, because a limit the library declines to enforce can only be enforced by
something that can kill the process.

Why free memory and not the obvious instruments, both measured here:
  - `ps -o rss` is BLIND to MLX: 0.03 GiB reported while 8.00 GiB was held
    (Metal buffers are not in RSS). The same lie applies to Ollama's runner,
    which reported 8 MiB RSS holding 16.40 GiB. A watchdog on RSS would read
    "nothing is allocated" at the exact moment the machine was full.
  - `kern.memorystatus_vm_pressure_level` stayed at 1 (NORMAL) the whole way
    from 13.17 GiB free down to 7.23 GiB. It is a late signal, not an early
    warning, so it cannot gate anything.

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

        previous = mx.set_memory_limit(int(limit))
        # Printed, not silent: this is the only record of what the budget was
        # for a given run, and the limit is advisory (see the module docstring)
        # so the supervisor's kill is what the number actually relies on.
        print(
            f"[wrapper] mlx memory limit {int(limit) / 2**30:.1f} GiB "
            f"(was {previous / 2**30:.2f} GiB) — ADVISORY, not enforced by MLX",
            file=sys.stderr,
        )

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
