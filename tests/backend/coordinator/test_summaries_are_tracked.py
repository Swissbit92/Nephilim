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


# ─────────────────────────────────────────────────────────────
# Prompt-control flags must not touch the identity fingerprint.
# ─────────────────────────────────────────────────────────────

class TestPromptFlagsDoNotDriftTheIdentity:
    """Second drift, 2026-09-28. The ADR-016 A/B harness writes `dials_in_prompt` and
    `dial_contrast` onto the card to build an arm's prompt. Those keys were inside the
    CV fingerprint, so building an arm regenerated <identity> through the LLM and saved
    it under a hash derived from the temporarily-modified card. The harness restored the
    card in its `finally`; it could not restore the summary.

    Only tracking the summaries in git made this visible — it surfaced as a one-file
    diff on the next boot. That is the tracking decision paying for itself on day one.
    """

    @pytest.fixture
    def card(self):
        import json as _json

        return _json.loads((REPO / "personas" / "gwen.json").read_text())

    @pytest.mark.parametrize("key,value", [
        ("dials_in_prompt", True),
        ("dial_contrast", "wide"),
        ("constraints_in_prompt", True),
    ])
    def test_a_prompt_control_flag_does_not_move_the_fingerprint(self, card, key, value):
        import copy

        from src.coordinator.cv_summarizer import _fingerprint

        modified = copy.deepcopy(card)
        modified[key] = value
        assert _fingerprint(modified) == _fingerprint(card), (
            f"{key} is inside the CV fingerprint. Setting it regenerates <identity> "
            "through the LLM — the 2026-09-28 drift."
        )

    def test_a_real_card_edit_still_moves_the_fingerprint(self, card):
        """The exclude set must stay NARROW. Excluding too much would keep a stale
        summary after a genuine card change, which is the opposite failure."""
        import copy

        from src.coordinator.cv_summarizer import _fingerprint

        for path, value in [(("emotional_profile", "baseline"), "completely different"),
                            (("behavior", "traits"), ["utterly different"])]:
            modified = copy.deepcopy(card)
            d = modified
            for k in path[:-1]:
                d = d.setdefault(k, {})
            d[path[-1]] = value
            assert _fingerprint(modified) != _fingerprint(card), path

    def test_the_legacy_fingerprint_keeps_its_original_exclude_set(self, card):
        """Adoption recognises the PRE-2026-09-27 scheme. Widening the legacy exclude
        set would stop it matching those cached summaries and defeat adoption."""
        import copy

        from src.coordinator.cv_summarizer import _legacy_fingerprint

        modified = copy.deepcopy(card)
        modified["dials_in_prompt"] = True
        assert _legacy_fingerprint(modified) != _legacy_fingerprint(card)
