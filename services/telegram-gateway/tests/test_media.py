"""The gateway's own media-path allowlist.

Every branch of resolve_media_path, because this function is the whole reason
accepting a coordinator-named path is not an arbitrary-file-read primitive.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from eeva_telegram.media import MediaRejectedError, resolve_media_path


@pytest.fixture
def media_cfg(cfg, tmp_path):
    """cfg with media enabled, rooted at a throwaway directory."""
    root = tmp_path / "media"
    (root / "img").mkdir(parents=True)
    return dataclasses.replace(cfg, media_enabled=True, media_root=root)


def _good_file(root: Path, name: str = "a.png", size: int = 100) -> Path:
    p = root / "img" / name
    p.write_bytes(b"x" * size)
    return p


# ---------- accepts ----------


def test_accepts_a_file_inside_the_root(media_cfg):
    target = _good_file(media_cfg.media_root)
    assert resolve_media_path(media_cfg, str(target)) == target.resolve()


# ---------- rejects ----------


def test_rejects_when_media_is_disabled(cfg, tmp_path):
    target = tmp_path / "a.png"
    target.write_bytes(b"x")
    with pytest.raises(MediaRejectedError, match="disabled"):
        resolve_media_path(cfg, str(target))


def test_rejects_when_no_root_configured(cfg, tmp_path):
    target = tmp_path / "a.png"
    target.write_bytes(b"x")
    bad = dataclasses.replace(cfg, media_enabled=True, media_root=None)
    with pytest.raises(MediaRejectedError, match="no media root"):
        resolve_media_path(bad, str(target))


@pytest.mark.parametrize("raw", ["", None])
def test_rejects_empty_path(media_cfg, raw):
    with pytest.raises(MediaRejectedError, match="empty"):
        resolve_media_path(media_cfg, raw)


def test_rejects_a_relative_path(media_cfg):
    """A relative path means the two processes disagree about the working
    directory — the mismatch most likely to bite in deployment."""
    _good_file(media_cfg.media_root)
    with pytest.raises(MediaRejectedError, match="not absolute"):
        resolve_media_path(media_cfg, "img/a.png")


def test_rejects_a_path_outside_the_root(media_cfg, tmp_path):
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"x")
    with pytest.raises(MediaRejectedError, match="outside"):
        resolve_media_path(media_cfg, str(outside))


def test_rejects_traversal_back_out_of_the_root(media_cfg, tmp_path):
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"x")
    sneaky = media_cfg.media_root / "img" / ".." / ".." / "elsewhere.png"
    with pytest.raises(MediaRejectedError, match="outside"):
        resolve_media_path(media_cfg, str(sneaky))


def test_rejects_a_symlink_pointing_outside_the_root(media_cfg, tmp_path):
    """resolve() must happen BEFORE the containment check — a string-prefix
    allowlist is defeated by a symlink anywhere along the path."""
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"sensitive")
    link = media_cfg.media_root / "img" / "innocent.png"
    os.symlink(outside, link)

    with pytest.raises(MediaRejectedError, match="outside"):
        resolve_media_path(media_cfg, str(link))


def test_rejects_a_missing_file(media_cfg):
    with pytest.raises(MediaRejectedError, match="not a file"):
        resolve_media_path(media_cfg, str(media_cfg.media_root / "img" / "nope.png"))


def test_rejects_a_directory(media_cfg):
    with pytest.raises(MediaRejectedError, match="not a file"):
        resolve_media_path(media_cfg, str(media_cfg.media_root / "img"))


def test_rejects_an_empty_file(media_cfg):
    target = _good_file(media_cfg.media_root, size=0)
    with pytest.raises(MediaRejectedError, match="empty"):
        resolve_media_path(media_cfg, str(target))


def test_rejects_an_oversized_file(media_cfg):
    small = dataclasses.replace(media_cfg, media_max_bytes=10)
    target = _good_file(media_cfg.media_root, size=100)
    with pytest.raises(MediaRejectedError, match="over the configured limit"):
        resolve_media_path(small, str(target))


# ---------- the rejection must not leak the path ----------


def test_rejection_messages_never_echo_the_path(media_cfg, tmp_path):
    """A refusal that echoes the path tells a probing sender what exists on
    disk, and the user cannot act on the detail anyway."""
    outside = tmp_path / "very-secret-filename.png"
    outside.write_bytes(b"x")
    with pytest.raises(MediaRejectedError) as exc:
        resolve_media_path(media_cfg, str(outside))
    assert "very-secret-filename" not in str(exc.value)
