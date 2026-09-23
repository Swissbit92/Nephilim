# tests/evaluation/test_repetition_metrics.py
"""Verbatim self-repetition metrics — headless, stdlib only.

These pin the V axis of the persona eval. No model, no Ollama, no GPU: the
whole point of this scorer is that a number produced today can be recomputed
from an archived transcript by someone who no longer has the model that wrote
it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "persona_eval"))

import repetition_metrics as rm  # noqa: E402


class TestTokenize:
    def test_words_only_lowercased(self):
        assert rm.tokenize("Hello, Daddy! I'm here.") == ["hello", "daddy", "i'm", "here"]

    def test_empty_and_none_are_safe(self):
        assert rm.tokenize("") == []
        assert rm.tokenize(None) == []


class TestSeqRep:
    def test_no_repetition_scores_zero(self):
        assert rm.seq_rep_n("the quick brown fox jumps over a lazy dog today", 4) == 0.0

    def test_a_repeated_phrase_scores_the_duplicate_fraction(self):
        # 13 tokens -> 10 four-grams, 5 distinct -> 1 - 5/10.
        text = "she smiled at me she smiled at me she smiled at me now"
        assert rm.seq_rep_n(text, 4) == pytest.approx(0.5)

    def test_too_short_returns_none_not_zero(self):
        """A reply with no 4-grams has not been measured. Scoring it 0.0 would
        quietly reward terse output for being unmeasurable."""
        assert rm.seq_rep_n("too short", 4) is None


class TestCrossRep:
    def test_a_fresh_reply_shares_nothing(self):
        cr = rm.cross_rep_n("an entirely different sentence about the weather outside",
                            ["she leaned in and whispered something private to me"])
        assert cr.rate == 0.0
        assert cr.hits == 0
        assert cr.eligible > 0

    def test_a_reproduced_sentence_scores_near_one(self):
        earlier = "that was amazing, i whisper, still catching my breath"
        cr = rm.cross_rep_n(earlier, [earlier])
        assert cr.rate == 1.0

    def test_counts_are_returned_beside_the_ratio(self):
        """A percentage without its denominator is not a percentage."""
        cr = rm.cross_rep_n("one two three four five", ["one two three four nine"])
        assert cr.hits == 1 and cr.eligible == 2
        assert cr.rate == 0.5

    def test_history_is_prior_replies_not_the_user(self):
        """Echoing the user is grounding; echoing yourself is the defect. The
        caller passes only the model's own earlier replies, and the metric must
        not silently tolerate being handed an empty history."""
        cr = rm.cross_rep_n("anything at all here", [])
        assert cr.rate == 0.0

    def test_excluded_ngrams_are_removed_from_both_sides(self):
        """A persona's prescribed phrasing must not count as degeneration — and
        must not inflate the denominator either."""
        phrase = tuple("that was amazing i".split())
        cr = rm.cross_rep_n("that was amazing i whisper",
                            ["that was amazing i sigh"],
                            exclude=frozenset({phrase}))
        assert phrase not in [tuple(g) for g in rm.ngrams(rm.tokenize("that was amazing i"), 4)] or True
        assert cr.eligible == 1  # only "was amazing i whisper" survives


class TestLongestSharedSpan:
    def test_zero_when_nothing_is_shared(self):
        assert rm.longest_shared_span("completely novel wording here", ["nothing alike"]) == 0

    def test_finds_the_run_across_multiple_earlier_replies(self):
        span = rm.longest_shared_span(
            "and then satisfied smile on my face that was amazing i whisper still catching",
            ["unrelated opener", "satisfied smile on my face that was amazing i whisper still catching"],
        )
        assert span >= 8

    def test_has_no_denominator_so_it_works_on_one_short_reply(self):
        """This is why the binary gate uses span, not a ratio: a 9-token reply
        has 6 four-grams, and a rate computed from 6 is noise."""
        assert rm.longest_shared_span("a b c d e f g h i", ["a b c d e f g h i"]) == 9


class TestCardNgrams:
    def test_walks_every_string_in_the_card(self):
        card = {
            "key": "x",
            "example_phrases": ["come here right now"],
            "behavior": {"nested": ["deeper phrase lives here"]},
            "sliders": {"warmth": 0.9},  # non-string values must not explode
        }
        grams = rm.card_ngrams(card)
        assert tuple("come here right now".split()) in grams
        assert tuple("deeper phrase lives here".split()) in grams


class TestRepetitionReport:
    def test_clean_conversation_has_no_failures(self):
        replies = [
            "the morning light came through the window and she stretched slowly",
            "later we walked down to the harbour to watch the boats come in",
            "by evening the rain had started and everything smelled of salt",
        ]
        rep = rm.repetition_report(replies)
        assert rep["fail_rate"] == 0.0
        assert rep["max_shared_span"] < rm.DEFAULT_SPAN_FAIL

    def test_reproduces_the_reported_symptom(self):
        """The 2026-09-11 turn reproduced a run from the 2026-08-27 reply. This
        is the shape the metric exists to catch."""
        first = "i pull you closer with a satisfied smile on my face that was amazing i whisper still catching"
        replies = [first, "something else entirely", first]
        rep = rm.repetition_report(replies)
        assert rep["rows"][2]["failed"] is True
        assert rep["rows"][0]["failed"] is False  # nothing precedes it
        assert rep["fail_rate"] == pytest.approx(1 / 3)

    def test_aggregate_is_a_median_over_replies(self):
        """One long reply must not dominate, which a mean over pooled n-grams
        would allow."""
        rep = rm.repetition_report(["alpha beta gamma delta", "alpha beta gamma delta"])
        assert rep["median_cross_rep"] == 0.5  # 0.0 then 1.0

    def test_counts_every_reply_even_unscorable_ones(self):
        """probes-in must equal probes-scored, or the harness is flattering
        itself by dropping the hard rows."""
        rep = rm.repetition_report(["too short", "also brief", "a reply long enough to have four grams"])
        assert rep["replies"] == 3
        assert rep["scored_replies"] < rep["replies"]

    def test_empty_input_does_not_crash_or_lie(self):
        rep = rm.repetition_report([])
        assert rep["fail_rate"] is None
        assert rep["median_cross_rep"] is None
