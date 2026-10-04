"""Validation of coordinator-named media paths.

The gateway's standing virtue is having no file-system discretion. Accepting a
path from the coordinator and opening it is an arbitrary-file-read primitive
the moment the coordinator is confused or compromised, so the gateway enforces
its OWN allowlist root here — independently of, and in addition to, the
coordinator's own containment guard. Belt to its braces.

Pure: no PTB import, no I/O beyond stat. Unit-testable like ``relay``.

Rejection reasons are logged locally and NEVER surfaced to Telegram. A refusal
message that echoes the path tells a probing sender what exists on disk, and
the user cannot act on the detail anyway.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import TelegramConfig

logger = logging.getLogger(__name__)


class MediaRejectedError(Exception):
    """A coordinator-named media path failed the gateway's own checks."""


def resolve_media_path(cfg: TelegramConfig, raw: str) -> Path:
    """Return a safe, existing path inside the gateway's media root.

    Raises :class:`MediaRejectedError` otherwise. Every branch is a distinct message
    so the gateway log says which check failed, while the Telegram reply stays
    a fixed string.
    """
    if not cfg.media_enabled:
        raise MediaRejectedError("media delivery is disabled (TG_MEDIA_ENABLED)")
    if cfg.media_root is None:
        # Unreachable via load_config, which fails fast. Reachable if a
        # TelegramConfig is constructed directly, e.g. in a test.
        raise MediaRejectedError("no media root configured (TG_MEDIA_ROOT)")
    if not isinstance(raw, str) or not raw:
        raise MediaRejectedError("media path is empty")

    candidate = Path(raw)
    if not candidate.is_absolute():
        # The coordinator resolves before sending. A relative path means the two
        # processes disagree about the working directory, which is the mismatch
        # most likely to bite in deployment.
        raise MediaRejectedError("media path is not absolute")

    root = cfg.media_root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        # resolve() first: an allowlist check on the raw string is defeated by a
        # symlink anywhere along the path.
        raise MediaRejectedError("media path is outside the gateway's media root")
    if not resolved.is_file():
        raise MediaRejectedError("media path is not a file")

    size = resolved.stat().st_size
    if size == 0:
        raise MediaRejectedError("media file is empty")
    if size > cfg.media_max_bytes:
        raise MediaRejectedError(f"media file is {size} bytes, over the configured limit")

    return resolved
