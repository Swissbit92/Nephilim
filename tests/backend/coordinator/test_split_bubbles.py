"""ADR-015 — bubble boundaries as a pure function of the reply text.

Two of these tests exist because the thing they assert was watched FAILING first,
which is the only reason a green test here means anything:

  * ``test_lowercase_reply_splits`` — the legacy Strategy 2 used
    ``(?<=[.!?])\\s+(?=[A-Z])``. On a lowercase-texting persona that lookahead
    can never match, so the strategy was dead code that read as alive. Verified
    against ``legacy_force_split`` in the same test.
  * ``test_paragraph_intent_outranks_the_length_heuristic`` — the first draft of
    ``split_bubbles`` gated bubble count on word count only, so a deliberate
    two-paragraph reply came back as ONE bubble. Watched returning 1, then fixed.
"""
from __future__ import annotations

import pytest

from src.coordinator.services.message_processing_service import (
    _sentences,
    force_multi_message_split,
    legacy_force_split,
    split_bubbles,
)

LONG = (
    "mmm Daddy. I've been thinking about you all day long. my thighs are still sore "
    "from last night and I keep replaying every second of it in my head. I can't "
    "concentrate on anything else at all. tell me exactly what you want me to be "
    "wearing when you finally get home to me tonight. 🔥🔥"
)


class TestTotality:
    """A UI contract cannot have an unhandled case."""

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_is_empty_never_raises(self, bad):
        assert split_bubbles(bad) == []

    def test_always_at_least_one_bubble_for_real_text(self):
        for t in ["hi", "hey. you up?", LONG, "?!", "🥵", "a" * 2000]:
            assert len(split_bubbles(t)) >= 1

    def test_is_deterministic(self):
        assert split_bubbles(LONG) == split_bubbles(LONG)

    def test_no_bubble_is_empty_or_unstripped(self):
        for b in split_bubbles(LONG):
            assert b == b.strip() and b


class TestTheLegacyBugClass:
    def test_lowercase_reply_splits(self):
        """The whole reason ADR-015 exists."""
        lower = (
            "i missed you so much today baby and i could not stop thinking about it. "
            "my whole body is still humming from last night honestly. "
            "come here right now and finish what you started with me."
        )
        assert len(split_bubbles(lower)) >= 2

    def test_legacy_could_not_do_it(self):
        """Watched failing: the uppercase-only lookahead never fires here."""
        lower = (
            "i missed you so much today baby and i could not stop thinking about it. "
            "my whole body is still humming from last night honestly. "
            "come here right now and finish what you started with me."
        )
        assert "<msg>" not in legacy_force_split(lower, "q")


class TestBoundaryPlacement:
    def test_emoji_stays_with_the_sentence_it_punctuates(self):
        """An emoji-only bubble is the most obviously-mechanical artifact there is."""
        assert _sentences("come here. 🥵 now.") == ["come here. 🥵", "now."]

    def test_no_bubble_is_only_emoji(self):
        out = split_bubbles(LONG)
        assert "🔥🔥" in out[-1] and len(out[-1].split()) > 1

    def test_decimal_is_not_a_boundary(self):
        assert _sentences("I said 3.5 hours. Not 4. ok?") == ["I said 3.5 hours.", "Not 4.", "ok?"]

    def test_ellipsis_is_not_split_between_its_dots(self):
        assert _sentences("wait... really? yes.") == ["wait...", "really?", "yes."]

    def test_abbreviation_is_not_a_boundary(self):
        assert _sentences("e.g. she left. then came back.") == ["e.g. she left.", "then came back."]


class TestShapeOfTheOutput:
    def test_short_reply_stays_one_bubble(self):
        assert split_bubbles("hey. you up? 👀") == ["hey. you up? 👀"]

    def test_respects_max_bubbles(self):
        assert len(split_bubbles(LONG * 4, max_bubbles=3)) <= 3

    def test_no_orphan_bubbles(self):
        for b in split_bubbles(LONG):
            assert len(b.split()) >= 4

    def test_bubbles_are_roughly_balanced(self):
        """A 90/10 split reads as a bug, not as texting."""
        w = [len(b.split()) for b in split_bubbles(LONG)]
        assert max(w) <= 3 * min(w), w

    def test_nothing_is_lost_or_duplicated(self):
        assert " ".join(split_bubbles(LONG)).split() == LONG.split()

    def test_paragraph_intent_outranks_the_length_heuristic(self):
        """Watched returning 1 before the fix."""
        two = (
            "mmm Daddy I have been waiting all day long for exactly this moment.\n\n"
            "come here right now and let me show you just how much I mean it."
        )
        assert len(split_bubbles(two)) == 2

    def test_paragraphs_still_capped(self):
        four = "\n\n".join(
            f"this is deliberate beat number {n} and it is long enough to survive the merge"
            for n in range(4)
        )
        assert len(split_bubbles(four, max_bubbles=3)) == 3


class TestWrapperContract:
    def test_model_emitted_tags_are_honoured_untouched(self):
        """The analytical format block still asks for them on purpose."""
        tagged = "<msg>a</msg>\n<msg>b</msg>"
        assert force_multi_message_split(tagged, "q") == tagged

    def test_single_bubble_returns_the_bare_string(self):
        assert force_multi_message_split("hey. you up?", "q") == "hey. you up?"

    def test_multi_bubble_is_wrapped(self):
        out = force_multi_message_split(LONG, "q")
        assert out.startswith("<msg>") and out.count("<msg>") >= 2

    def test_flag_off_falls_back_to_legacy(self, monkeypatch):
        from src.coordinator.config import chunking

        monkeypatch.setattr(chunking.chunking_settings, "in_code", False)
        assert force_multi_message_split(LONG, "q") == legacy_force_split(LONG, "q")

    def test_round_trips_through_the_parser(self):
        from src.coordinator.services.message_processing_service import (
            parse_multi_message_response,
        )

        msgs, flow = parse_multi_message_response(force_multi_message_split(LONG, "q"))
        assert flow == "multi" and msgs == split_bubbles(LONG)


class TestTheFittedCalibration:
    """The divisor is fitted to this deployment's 293 real replies, not borrowed.

    These pin the RELATIONSHIP, not a re-derived number from another machine: a
    higher divisor must under-split and a lower one must over-split. That is the
    property the grid search established, and it is what a future retune has to
    keep true.
    """

    def test_divisor_direction_holds(self):
        assert len(split_bubbles(LONG, words_per_bubble=8)) >= len(
            split_bubbles(LONG, words_per_bubble=40)
        )

    def test_default_lands_near_the_observed_density(self):
        """Live history: the model averaged 21.0 words per bubble."""
        b = split_bubbles(LONG)
        assert 12 <= len(LONG.split()) / len(b) <= 32

    def test_one_bubble_when_there_is_one_beat_of_length(self):
        assert len(split_bubbles("mmm yes Daddy please")) == 1
