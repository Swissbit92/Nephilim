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


class _EmoRepo:
    """Stub for the emotional half of `_load_session_identity`.

    It loads a profile AND an emotional state; these tests are about the
    profile, so the emotional side is stubbed rather than skipped — leaving it
    out made all three tests fail on an AttributeError that said nothing about
    the behaviour under test.
    """

    class _State:
        trust_level = 0.5
        current_mood = "curious"

        def to_prompt_context(self):
            return ""

    def get_or_create(self, session_id):
        return self._State()


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

    def test_truncation_warns_because_silence_here_freezes_her(self, caplog):
        """The cap keeps the OLDEST entries, so a full store can never show
        anything new. Measured 2026-10-18: the curated merge wrote exactly 32,
        the extractor appended two more, and both were silently dropped. They
        were paraphrases of answers she had just recited, so nothing was lost —
        a genuinely new fact would have been lost identically and as quietly.
        """
        import logging

        p = _profile(facts=[f"fact {i}" for i in range(34)])
        with caplog.at_level(logging.WARNING, logger="coordinator.user_profile"):
            p.get_recall_facts(max_facts=32)
        assert any("NOT shown" in r.message or "NOT shown" in r.getMessage()
                   for r in caplog.records), (
            "truncation must warn — this is the interim guard until ADR-021's "
            "write policy exists"
        )

    def test_no_warning_when_nothing_is_dropped(self):
        """The warning must mean something, so it cannot fire on every call."""
        import logging

        p = _profile(facts=["a", "b"])
        logger = logging.getLogger("coordinator.user_profile")
        seen = []
        h = logging.Handler()
        h.emit = lambda r: seen.append(r)
        logger.addHandler(h)
        try:
            p.get_recall_facts(max_facts=32)
        finally:
            logger.removeHandler(h)
        assert not seen, f"warned with nothing dropped: {seen}"

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


class TestOperatorIdentityIsReadBeforeTheTurn:
    """THE REGRESSION that shipped once.

    `SINGLE_OPERATOR_USER_ID` was wired into the POST-turn identity path only.
    The read side (`_load_session_identity`, Phase 1) still resolved the user
    from `get_session_user(session_id)`, which is EMPTY for a brand-new session —
    so turn 1 carried no profile, the numbered frame had nothing to render, and
    the one prose branch that needs no profile (the emotional narrative) injected
    alone. Measured live: 32 curated facts in the store and she still guessed at
    breakfast.

    Wiring only the write half fixes the fragmentation going forward and fixes
    nothing she can say.
    """

    def test_a_brand_new_unlinked_session_still_resolves_the_operator(self, monkeypatch):
        from coordinator.config import get_settings
        from coordinator.services import chat_session_service as css

        get_settings.cache_clear()
        monkeypatch.setenv("SINGLE_OPERATOR_USER_ID", "user_operator")
        get_settings.cache_clear()

        class _Repo:
            def get_session_user(self, session_id):
                return ""          # a brand-new session is linked to nobody

            def get_profile(self, user_id):
                assert user_id == "user_operator", (
                    f"the read side asked for {user_id!r}, not the operator"
                )
                return _profile(facts=["Enjoys eggs benedict for breakfast"])

        state = type("S", (), {
            "session_id": "fresh-session", "user_id": None, "user_profile": None,
            "user_profile_context": "", "effective_user_id": None,
            "emotional_state": None, "emotional_context": "",
        })()
        deps = type("D", (), {"user_profile_repo": _Repo(),
                              "emotional_state_repo": _EmoRepo()})()

        css._load_session_identity(state, deps)

        assert state.user_id == "user_operator"
        assert state.user_profile is not None, "turn 1 must carry the profile"
        assert state.user_profile.get_recall_facts() == [
            "Enjoys eggs benedict for breakfast"
        ]
        get_settings.cache_clear()

    def test_the_operator_id_wins_over_a_stale_session_link(self, monkeypatch):
        """A session linked to one of the 396 merged-away ids must not win."""
        from coordinator.config import get_settings
        from coordinator.services import chat_session_service as css

        get_settings.cache_clear()
        monkeypatch.setenv("SINGLE_OPERATOR_USER_ID", "user_operator")
        get_settings.cache_clear()

        class _Repo:
            def get_session_user(self, session_id):
                return "user_deadbeef"        # a pre-merge fragment

            def get_profile(self, user_id):
                assert user_id == "user_operator"
                return _profile(facts=["real fact"])

        state = type("S", (), {
            "session_id": "old-session", "user_id": None, "user_profile": None,
            "user_profile_context": "", "effective_user_id": None,
            "emotional_state": None, "emotional_context": "",
        })()
        css._load_session_identity(state, type("D", (), {
            "user_profile_repo": _Repo(), "emotional_state_repo": _EmoRepo()})())
        assert state.user_id == "user_operator"
        get_settings.cache_clear()

    def test_empty_setting_keeps_the_session_link_path(self, monkeypatch):
        """A real multi-user deployment must be unaffected."""
        from coordinator.config import get_settings
        from coordinator.services import chat_session_service as css

        get_settings.cache_clear()
        monkeypatch.delenv("SINGLE_OPERATOR_USER_ID", raising=False)
        get_settings.cache_clear()

        class _Repo:
            def get_session_user(self, session_id):
                return "user_from_session"

            def get_profile(self, user_id):
                assert user_id == "user_from_session"
                return _profile(facts=["x"])

        state = type("S", (), {
            "session_id": "s", "user_id": None, "user_profile": None,
            "user_profile_context": "", "effective_user_id": None,
            "emotional_state": None, "emotional_context": "",
        })()
        css._load_session_identity(state, type("D", (), {
            "user_profile_repo": _Repo(), "emotional_state_repo": _EmoRepo()})())
        assert state.user_id == "user_from_session"
        get_settings.cache_clear()
