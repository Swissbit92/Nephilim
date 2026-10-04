# src/coordinator/services/media_storage.py
"""Filesystem layer for generated media (Telegram image transport, phase 1).

The ONLY module in the coordinator that writes generated images to disk. Every
path is built here so the guards cannot be bypassed by a caller that forgets
them.

Three properties this module exists to guarantee:

1. **A reader never observes a partial file.** Every write is tempfile →
   fsync → ``os.replace`` → directory fsync. ``os.replace`` is atomic per POSIX
   and the temp file is created under the same media root as the target, so the
   rename can never cross a filesystem (``EXDEV``). ``shutil.move`` is never
   used — it silently degrades to a non-atomic copy on EXDEV, which is the
   exact failure this sequence exists to prevent.

2. **No path this module writes to can escape the media root.** ``POST
   /sessions/import`` accepts a client-supplied session id, so a name reaching
   this module may be attacker-shaped. Two independent guards apply: a strict
   lowercase-hex allowlist, AND resolve-then-contain. Neither alone is enough —
   the allowlist is defeated by a symlink planted at a legal name, and
   containment alone would accept creative but legal-looking names.

   Note the wording: *no path written to*, not *no directory name*. The earlier
   phrasing was the weaker claim and the code matched the weaker claim —
   ``session_dir()`` was containment-checked and the caller then appended
   ``/ "img"``, so a symlink one level deeper escaped. Every component now goes
   through the check together. A docstring that promises more than the code
   delivers is the defect, not the documentation of it.

3. **Nothing is emitted for a file that is not already in place.** ``store_png``
   returns only after ``os.replace`` has returned, so a ``StoredMedia`` always
   describes a file that exists.

``F_FULLFSYNC`` is deliberately NOT used. It is roughly an order of magnitude
dearer than plain ``fsync`` on APFS (two independent spot-measurements on this
machine differed by 2x in absolute terms but agreed on the magnitude, so treat
the ratio as indicative and re-measure before relying on a figure). What it
buys is durability across a power loss, for a regenerable image — not worth it.
``os.replace`` is what buys atomicity, and it needs no fsync at all; the fsyncs
are cheap insurance on top.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import re
import struct
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)

#: A media directory name is exactly 32 lowercase hex characters (uuid4().hex).
#: Lowercase is load-bearing, not cosmetic: APFS is case-insensitive by default,
#: so accepting mixed case would let two distinct names collapse into one
#: directory — and a reset of either would destroy both.
#: ``fullmatch`` is required, not ``match``: ``$`` matches BEFORE a trailing
#: newline, so "a"*32 + "\n" passed the old check and put a newline into a
#: filename that later travels to the gateway and into logs.
_MEDIA_DIR_RE = re.compile(r"[0-9a-f]{32}")

#: Subdirectory names are ours, never a caller's — a tight allowlist so a
#: future caller cannot smuggle a traversal through ``parts``.
_SUBDIR_RE = re.compile(r"[a-z0-9_]{1,32}")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class MediaStorageError(Exception):
    """A media write or path resolution was refused."""


@dataclass(frozen=True)
class StoredMedia:
    """A file that is already on disk at ``path``."""

    media_id: str
    path: Path
    bytes: int
    sha256: str
    width: int | None = None
    height: int | None = None


def media_root() -> Path:
    """Absolute media root. Resolved per call — the setting may be relative."""
    return Path(get_settings().media.root_dir).resolve()


def _contained(candidate: Path, root: Path) -> Path:
    """Return ``candidate`` resolved, or raise if it escapes ``root``."""
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        # Reached when any component of the path is a symlink pointing out.
        raise MediaStorageError("resolved media directory escapes the media root")
    return resolved


def session_dir(media_dir: str, *parts: str) -> Path:
    """Resolve a path under a session's media directory, refusing any escape.

    Both guards apply and both are necessary. The regex rejects traversal and
    case-collision before the filesystem is touched at all; the containment
    check then catches a symlink planted at an otherwise-legal name, which the
    regex cannot see.

    ``parts`` are appended BEFORE the containment check, not after. An earlier
    revision resolved the session directory and then let the caller append
    ``/ "img"``, which re-opened the hole the check exists to close: a symlink
    at ``<root>/sessions/<hex>/img`` escaped, and that is precisely the
    directory written to. Any subdirectory must be named here.
    """
    if not isinstance(media_dir, str) or not _MEDIA_DIR_RE.fullmatch(media_dir):
        raise MediaStorageError("media_dir must be 32 lowercase hex characters")
    for part in parts:
        if not isinstance(part, str) or not _SUBDIR_RE.fullmatch(part):
            raise MediaStorageError("media subdirectory names must be [a-z0-9_]+")

    root = media_root()
    return _contained(root.joinpath("sessions", media_dir, *parts), root)


def atomic_write(target: Path, data: bytes, staging_dir: Path | None = None) -> None:
    """Publish ``data`` at ``target`` so no reader ever sees a partial file.

    ``staging_dir`` must be on the same filesystem as ``target`` — it defaults
    to the target's own directory, which is trivially true. ``store_png``
    overrides it with the session's ``.tmp/`` so that a crash leaves debris
    outside ``img/``, the directory callers enumerate as "this session's
    media", and in the one place the phase-3 sweeper looks.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = staging_dir if staging_dir is not None else target.parent
    staging.mkdir(parents=True, exist_ok=True)

    # dir= is load-bearing: same filesystem means os.replace can never raise
    # EXDEV. A temp file in /tmp would be on another volume here.
    fd, tmp_name = tempfile.mkstemp(dir=staging, prefix=".tmp-", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:  # takes ownership of fd
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp creates 0600 and os.replace preserves the source mode, so
        # without this the published file is unreadable by anything else.
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        # BaseException, not Exception: a cancellation must still clean up.
        tmp.unlink(missing_ok=True)
        raise

    # The rename is a directory metadata change and is not durable until the
    # directory itself is synced. Non-fatal: the file is already visible.
    try:
        dir_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        logger.warning("[Media] directory fsync failed (non-fatal): %s", exc)


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read (width, height) from a PNG's IHDR. None if the bytes are not one.

    Never raises — a metadata helper that can throw turns a cosmetic gap into a
    failed request.
    """
    if len(data) < 24 or not data.startswith(_PNG_MAGIC) or data[12:16] != b"IHDR":
        return None
    try:
        width, height = struct.unpack(">II", data[16:24])
    except struct.error:
        return None
    if width == 0 or height == 0:
        return None
    return width, height


def quarantine_session(media_dir: str) -> int:
    """Move a session's media out of the live tree. Returns the file count.

    Quarantine rather than ``rmtree``, so a bug or a regret costs a sweep
    instead of the files. The destination is ``<root>/orphans/<ts>-<media_dir>``
    and a later sweep (not yet written) removes entries older than 30 days.

    Uses ``os.rename``, not ``shutil.move``: within one media root the rename is
    atomic and cannot half-move a tree, whereas ``shutil.move`` silently
    degrades to a recursive copy-then-delete when it believes the paths differ
    in filesystem — the same class of silent downgrade this module avoids in
    ``atomic_write``.

    Returns 0 when the session never had a directory, which is the common case
    and is not an error.
    """
    source = session_dir(media_dir)
    if not source.exists():
        return 0

    count = sum(1 for p in source.rglob("*") if p.is_file())

    root = media_root()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = _contained(root / "orphans" / f"{stamp}-{media_dir}", root)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # A second reset inside the same second would collide. Checking exists()
    # first is a TOCTOU race; instead give the name entropy and let os.rename
    # tell us. os.rename is also deliberate over shutil.move: within one media
    # root it is an atomic relink, whereas shutil.move given an EXISTING
    # destination directory moves the source INSIDE it (quarantine/<d>/<d>/),
    # which a sweeper would then mis-walk — and on a cross-volume path it
    # silently degrades to a recursive copy. os.renames is worse still: it
    # prunes the source's parents afterwards, which would eventually delete
    # <root>/sessions itself.
    # ENOTEMPTY, not just EEXIST: os.rename onto an existing NON-EMPTY
    # directory raises a plain OSError(66) on macOS, which Python does not map
    # to FileExistsError. Catching only FileExistsError would crash the reset on
    # the second quarantine of the same second — caught by
    # test_quarantine_twice_does_not_clobber_the_first.
    _COLLISION = {errno.EEXIST, errno.ENOTEMPTY}
    for _ in range(8):
        try:
            os.rename(source, destination)
            break
        except OSError as exc:
            if exc.errno in _COLLISION:
                destination = _contained(
                    root / "orphans" / f"{stamp}-{media_dir}-{uuid.uuid4().hex[:6]}",
                    root,
                )
                continue
            if exc.errno == errno.EXDEV:
                # Quarantine must live on the same volume as the media root;
                # falling back to a copy here would be non-atomic and silent.
                raise MediaStorageError(
                    "quarantine is on a different filesystem from the media root"
                ) from exc
            raise
    else:  # pragma: no cover - 8 collisions on a uuid4 suffix
        raise MediaStorageError("could not find a free quarantine name")
    logger.info(
        "[Media] quarantined %d file(s) for %s to %s", count, media_dir, destination.name
    )
    return count


def store_png(media_dir: str, data: bytes) -> StoredMedia:
    """Validate and atomically store a PNG under the session's media directory."""
    settings = get_settings().media
    if not settings.enabled:
        raise MediaStorageError("media storage is disabled (MEDIA_ENABLED)")
    if not data:
        raise MediaStorageError("refusing to store an empty file")
    if len(data) > settings.max_bytes:
        raise MediaStorageError(
            f"image is {len(data)} bytes, over the {settings.max_bytes} limit"
        )
    if not data.startswith(_PNG_MAGIC):
        raise MediaStorageError("payload is not a PNG")

    target_dir = session_dir(media_dir, "img")
    staging = session_dir(media_dir, "tmp")
    media_id = uuid.uuid4().hex
    target = target_dir / f"{media_id}.png"

    atomic_write(target, data, staging_dir=staging)

    dims = png_dimensions(data)
    if dims is None:
        # Magic bytes matched but IHDR did not parse — a truncated or odd PNG.
        # Stored anyway (the bytes are what the caller asked us to keep) but it
        # must not be silent: downstream sizing has nothing to work from.
        logger.warning(
            "[Media] stored %s but could not read its dimensions — truncated PNG?",
            media_id,
        )
    return StoredMedia(
        media_id=media_id,
        path=target,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        width=dims[0] if dims else None,
        height=dims[1] if dims else None,
    )
