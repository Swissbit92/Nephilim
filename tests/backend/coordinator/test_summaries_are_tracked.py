"""The CV summaries must stay in git (ADR-016).

They look like a cache — derived from the persona cards, rebuilt on demand, stored
under a leading-underscore directory. They were ignored as one for months.

They are not a cache. The text is written by an LLM, so it is NOT reproducible: once
overwritten, the previous wording is gone. On 2026-09-27 a fingerprint change
regenerated EIGHT personas' <identity> paragraphs at boot and there was no history to
restore from. Gojo.json happened to be tracked and survived, which is exactly the
inconsistency that hid the risk.

These tests exist so the ignore rule cannot come back quietly.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[3]
SUMMARIES = REPO / "personas" / "_summaries"


def _tracked(pattern: str) -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", pattern], cwd=REPO, capture_output=True, text=True, check=False
    )
    return {Path(p).name for p in out.stdout.split() if p}


def test_every_summary_on_disk_is_tracked():
    """The guard that would have prevented the loss."""
    on_disk = {p.name for p in SUMMARIES.glob("*.json")}
    on_disk -= {"Gwen_alt.json"}  # deliberately ignored scratch variant
    untracked = sorted(on_disk - _tracked("personas/_summaries/*.json"))
    assert not untracked, (
        f"{untracked} exist on disk but are NOT in git. These are LLM-written and "
        "non-reproducible — once regenerated the previous text cannot be recovered."
    )


def test_the_ignore_rule_has_not_come_back():
    """Reads .gitignore directly, ON PURPOSE.

    The obvious implementation — `git check-ignore` on a summary — is a FALSE GREEN,
    watched passing with the rule re-added: git does not report a TRACKED file as
    ignored, so the check is masked by the very tracking it is meant to protect. It
    would only fire once the damage (untracking) had already happened.
    """
    rules = [
        ln.strip() for ln in (REPO / ".gitignore").read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    blanket = [r for r in rules if r.rstrip("/").lstrip("/") == "personas/_summaries"]
    assert not blanket, (
        f".gitignore has a blanket rule for the summaries directory: {blanket}. Combined "
        "with `git rm --cached` that reproduces the 2026-09-27 loss, where eight "
        "personas' LLM-written identity text was regenerated with no history to "
        "restore from."
    )


def test_the_lock_file_stays_ignored():
    """Tracking the summaries must NOT drag in the inter-process lock, which IS
    transient and would conflict on every concurrent boot."""
    ignored = subprocess.run(
        ["git", "check-ignore", "personas/_summaries/.lock"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert ignored.returncode == 0, "the lock file must stay ignored"


@pytest.mark.parametrize("f", sorted(SUMMARIES.glob("*.json")), ids=lambda p: p.name)
def test_each_summary_is_well_formed(f):
    """A tracked file that cannot be parsed is worse than an untracked one, because
    it will be trusted."""
    if f.name == "Gwen_alt.json":
        pytest.skip("deliberately ignored scratch variant")
    d = json.loads(f.read_text())
    assert set(d) >= {"key", "hash", "summary"}, f.name
    assert isinstance(d["summary"], str) and d["summary"].strip(), f.name
    assert len(d["hash"]) == 40, f"{f.name}: hash is not a sha1"
