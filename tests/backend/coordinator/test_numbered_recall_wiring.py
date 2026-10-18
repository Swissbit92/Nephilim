"""ADR-022 — the numbered recall frame at the injection seam.

The seam is a BRANCH, not an early return. The first version of this wiring
returned once it had set the numbered context, which silently dropped the
ADR-011 author's note and the summary-context block that follow it in
`_build_turn_prompt`. `test_author_note_survives_the_numbered_branch` is that
regression, and it fails against the returning version.
"""
from __future__ import annotations

import pytest

from coordinator.context_framing import frame_numbered_facts
from coordinator.user_profile import UserProfile


def _profile(facts=None, background=None, name="Ben") -> UserProfile:
    return UserProfile("user_test", {
        "name": name, "background": background or [], "preferences": {},
        "holdings": {}, "topics_discussed": {}, "facts": facts or [],
        "personas_met": [], "total_sessions": 1, "total_messages": 1,
        "first_interaction": None, "last_updated": None,
    })


class TestRecallFactExtraction:
    def test_background_comes_before_facts(self):
        p = _profile(facts=["f1"], background=["b1"])
        assert p.get_recall_facts() == ["b1", "f1"], (
            "background is the higher-signal store and the cap truncates from the end"
        )

    def test_deduplicates_case_insensitively(self):
        p = _profile(facts=["Is Vegetarian", "is vegetarian"], background=["IS VEGETARIAN"])
        assert p.get_recall_facts() == ["IS VEGETARIAN"]

    def test_cap_truncates_from_the_end(self):
        p = _profile(facts=[f"fact {i}" for i in range(50)])
        got = p.get_recall_facts(max_facts=5)
        assert got == [f"fact {i}" for i in range(5)]

    def test_empty_profile_recalls_nothing(self):
        assert _profile().get_recall_facts() == []

    def test_name_is_not_spent_on_a_numbered_slot(self):
        p = _profile(facts=["Prefers Celsius"], name="Ben")
        assert p.get_recall_facts() == ["Prefers Celsius"]
        assert not any("Ben" in f for f in p.get_recall_facts())

    def test_blank_and_whitespace_entries_are_dropped(self):
        p = _profile(facts=["  ", "", "real"], background=["\n"])
        assert p.get_recall_facts() == ["real"]


class TestFramingContract:
    def test_nothing_to_recall_injects_nothing(self):
        assert frame_numbered_facts("gwen", None, []) is None
        assert frame_numbered_facts("gwen", None, ["   "]) is None

    def test_facts_are_numbered_from_one(self):
        out = frame_numbered_facts("gwen", None, ["alpha", "beta"])
        assert " [1] alpha" in out and " [2] beta" in out

    def test_frame_tells_her_not_to_recite_the_numbers(self):
        out = frame_numbered_facts("gwen", None, ["alpha"])
        assert "Never recite the list" in out
        assert "<remembered>" in out and "</remembered>" in out


class TestSeamOrdering:
    """The flag ordering, asserted rather than assumed."""

    def test_numbered_requires_context_inject(self):
        from coordinator.config import get_settings
        f = type(get_settings().memory).model_fields
        assert f["recall_numbered_facts"].default is False
        assert f["context_inject_enabled"].default is False, (
            "both must default OFF so the dormant path stays byte-identical"
        )

    def test_the_prose_branches_are_guarded_on_not_numbered(self):
        """A grep-level pin: every prose branch must carry the guard.

        Asserting the FLAG rather than the call site is what let an earlier
        regression test in this repo stay green while the mechanism was removed,
        so this reads the source of the seam itself.
        """
        import inspect
        from coordinator.services import chat_session_service as css
        src = inspect.getsource(css._build_turn_prompt)
        # Count BRANCH STATEMENTS, not occurrences — the first draft of this test
        # counted the string and passed on a comment that happened to mention it.
        guarded = [ln.strip() for ln in src.splitlines()
                   if ln.strip().startswith(("if ", "elif ")) and "not _numbered" in ln]
        assert len(guarded) == 3, (
            "three prose branches (facts narrative, profile narrative, emotional "
            f"narrative) must each be guarded; found {len(guarded)}: {guarded}"
        )

    def test_author_note_survives_the_numbered_branch(self):
        """THE REGRESSION. An early return here drops ADR-011's author note.

        `_append_author_note` is documented "always injected when set", and the
        summary-context block sits after it. Both must remain reachable after
        the numbered frame is set, so the numbered code must not return.
        """
        import inspect
        from coordinator.services import chat_session_service as css
        src = inspect.getsource(css._build_turn_prompt)
        numbered_at = src.index("_recalled = ")
        note_at = src.index("_append_author_note")
        assert note_at > numbered_at, "author note must come after the numbered branch"
        between = src[numbered_at:note_at]
        assert "return" not in between, (
            "the numbered branch must not return — it would skip the ADR-011 "
            "author note and the summary-context block"
        )
