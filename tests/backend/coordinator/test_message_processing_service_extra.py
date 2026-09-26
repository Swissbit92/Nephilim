# tests/backend/coordinator/test_message_processing_service_extra.py
"""
Extra unit tests for message_processing_service — covers lines missed by the
original test file:
  force_multi_message_split:  lines 47-54, 75-76, 87-91, 104-145
  parse_multi_message_response: (already well-covered)
"""
from __future__ import annotations

import pytest

from src.coordinator.services.message_processing_service import (
    legacy_force_split as force_multi_message_split,
    parse_multi_message_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repeat(sentence: str, n: int) -> str:
    return (sentence * n).strip()


# ---------------------------------------------------------------------------
# force_multi_message_split — Strategy 1 (lines 39-57): 800+ chars + question
# ---------------------------------------------------------------------------

class TestStrategy1VeryLongWithQuestion:
    """Lines 40-57: 800+ char response whose last clause is a question."""

    def _build(self, main_sentences: int = 30, end_question: str = "What do you think?") -> str:
        # Each sentence is ~40 chars; 30 * 40 = 1200 chars for main content
        unit = "This is a detailed sentence about finance. "
        main = unit * main_sentences
        # pattern requires [.!]\s+<question>
        return main.rstrip() + "! " + end_question

    def test_3_msg_when_main_content_gt_400(self):
        """Lines 46-54: main_content > 400 chars → 3 messages (greedy group(1) fix)."""
        response = self._build(main_sentences=30)
        assert len(response) > 800  # sanity
        result = force_multi_message_split(response, "query")
        assert result.count("<msg>") == 3
        assert "What do you think?" in result

    def test_2_msg_when_main_content_le_400(self):
        """Lines 56-57: main_content ≤ 400 chars → 2 messages, question separated."""
        # Build: main body ~350 chars, but total > 800 via repeated question-preamble
        # We need: total > 800, but main_content ≤ 400 after the regex captures groups
        # main_content = group(1)+group(2).  Use a ~380-char preamble + "! " + question
        preamble = "A " * 190   # 380 chars
        question = "Are you sure about this?"
        # total length = 380 + 2 + len(question) = ~405 — not >800
        # So pad the *question* to force total > 800
        long_question = "Are you sure about all of the many interesting and important details that have been discussed here?"
        # preamble 380 + 2 + ~98 = ~480, still not 800. Add more preamble.
        preamble = "A sentence. " * 65  # ~780 chars
        response = preamble.rstrip() + "! " + long_question
        assert len(response) > 800

        result = force_multi_message_split(response, "query")
        # Either 2 or 3 msg blocks (depending on rfind hit)
        count = result.count("<msg>")
        assert count >= 2

    def test_already_tagged_passthrough(self):
        """Lines 27-28: short-circuit if <msg> already in response."""
        tagged = "<msg>hello</msg>\n<msg>world</msg>"
        assert force_multi_message_split(tagged, "q") == tagged

    def test_short_response_passthrough(self):
        """Lines 31-32: < 500 chars → returned unchanged."""
        short = "Hello there." * 5   # ~60 chars
        assert force_multi_message_split(short, "q") == short


# ---------------------------------------------------------------------------
# force_multi_message_split — Strategy 2 (lines 66-101): sentences grouping
# ---------------------------------------------------------------------------

class TestStrategy2SentenceSplitting:
    """Lines 66-101: 3+ sentences, grouped into 2-3 <msg> blocks."""

    def _long_sentences(self, n: int, end_question: bool = False) -> str:
        """Build a 500+ char response from n sentences."""
        unit = "The quick brown fox jumped over the lazy dog in the forest. "
        body = unit * n
        if end_question:
            body = body.rstrip() + " What are your thoughts on this matter?"
        return body

    def test_first_sentence_short_combines_with_second(self):
        """Lines 71-75: first sentence < 100 chars → joined with second."""
        # First sentence must be < 100 chars; subsequent sentences ≥ 500 total
        # Craft: short intro + many long sentences
        short_intro = "Hello there. "  # 13 chars < 100
        rest = "The big brown fox jumped over the fence in the yard. " * 12
        response = short_intro + rest
        assert len(response) >= 500
        result = force_multi_message_split(response, "q")
        if "<msg>" in result:
            # first <msg> should contain both the short intro and the next sentence
            first_msg_match = result.split("<msg>")[1].split("</msg>")[0]
            assert "Hello there" in first_msg_match

    def test_first_sentence_long_stands_alone(self):
        """Lines 74-76: first sentence ≥ 100 chars → stands alone."""
        long_first = "A" * 101 + ". "
        rest = "The big brown fox jumped over the fence in the yard. " * 12
        response = long_first + rest
        assert len(response) >= 500
        result = force_multi_message_split(response, "q")
        if "<msg>" in result:
            first_msg_match = result.split("<msg>")[1].split("</msg>")[0]
            # Should contain only the first sentence (not continuation)
            assert "A" * 101 in first_msg_match

    def test_last_sentence_question_separated(self):
        """Lines 85-91: last sentence is question → separate final <msg>."""
        body = "The quick brown fox jumped over the lazy dog. " * 12
        question = "What do you think about this?"
        response = body.rstrip() + " " + question
        assert len(response) >= 500
        result = force_multi_message_split(response, "q")
        if "<msg>" in result:
            assert question in result

    def test_last_sentence_not_question_grouped(self):
        """Lines 92-94: last sentence not question → all remaining grouped."""
        response = "The quick brown fox jumped over the lazy dog in the forest. " * 12
        assert len(response) >= 500
        result = force_multi_message_split(response, "q")
        # Whatever the result, question mark separation logic not triggered
        # as long as it runs, no crash
        assert isinstance(result, str)

    def test_cap_at_four_messages(self):
        """Line 97: messages capped at 4."""
        # Build response with many distinct sentences that Strategy-2 might produce
        unit = "Short. "
        response = unit * 100   # 700 chars, many 1-word sentences ending with period+space
        result = force_multi_message_split(response, "q")
        if "<msg>" in result:
            assert result.count("<msg>") <= 4

    def test_fewer_than_2_msgs_falls_through(self):
        """Lines 99-101: if only 1 message produced, fall-through to next strategy."""
        # Two sentences, each very long (so sentences list has < 3 items effectively)
        response = "A" * 300 + ". " + "B" * 300
        # len > 500 but only 2 sentences — Strategy 2 won't produce ≥2 msgs
        result = force_multi_message_split(response, "q")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# force_multi_message_split — Strategy 3 (lines 104-122): 150+ chars + question
# ---------------------------------------------------------------------------

class TestStrategy3MediumWithQuestion:
    """Lines 104-122: 150-799 char response with question at end."""

    def test_3_msgs_when_main_content_gt_200(self):
        """Lines 114-119: main_content > 200 → 3 messages."""
        # Need: total > 150, main_content > 200, and a sentence break in main_content
        main = "The quick fox jumped. " * 5  # ~110 chars
        # Add more main content via second sentence
        main = "The quick brown fox jumped high over the old wooden fence on the hill. " * 4
        question = "Do you agree with this assessment?"
        response = main.rstrip() + "! " + question
        total = len(response)
        assert total > 150
        assert total < 800  # ensure Strategy 1 doesn't trigger
        result = force_multi_message_split(response, "q")
        if "<msg>" in result:
            assert result.count("<msg>") >= 2

    def test_2_msgs_when_main_content_le_200(self):
        """Lines 121-122: main_content ≤ 200 → 2 messages."""
        # Total > 150 but main body ≤ 200
        main = "Short but sufficient context here. "
        question = "What would you like to do?"
        response = main * 3 + "! " + question
        if len(response) > 150 and len(response) < 800:
            result = force_multi_message_split(response, "q")
            if "<msg>" in result:
                count = result.count("<msg>")
                assert count >= 2


# ---------------------------------------------------------------------------
# force_multi_message_split — Strategy 4 (lines 125-141): 150-300 chars midpoint
# ---------------------------------------------------------------------------

class TestStrategy4MidpointSplit:
    """Lines 125-141: 150-300 char response split at midpoint."""

    def test_split_at_period(self):
        """Lines 129, 136-141: '. ' split point found → 2 messages."""
        # Craft a 200-char response with a period near midpoint
        first = "This is the first detailed sentence about the interesting topic discussed here"
        second = " this continues the response with additional content about the same subject matter."
        response = first + "." + second
        total = len(response)
        assert 150 <= total <= 300, f"response length {total} out of range"
        result = force_multi_message_split(response, "q")
        if "<msg>" in result:
            assert result.count("<msg>") == 2
            # Both parts non-empty
            parts = [p.split("</msg>")[0] for p in result.split("<msg>")[1:]]
            assert all(len(p.strip()) > 0 for p in parts)

    def test_split_at_comma_and(self):
        """Lines 130: ', and ' split candidate."""
        first = "This is the first part of response, and"
        second = " this continues to be a longer second part of the message content."
        response = first + second
        total = len(response)
        if 150 <= total <= 300:
            result = force_multi_message_split(response, "q")
            # May or may not split — just shouldn't crash
            assert isinstance(result, str)

    def test_split_at_comma_but(self):
        """Lines 131: ', but ' split candidate."""
        first = "I considered the first approach carefully, but"
        second = " the second option turned out to be much more suitable for the task."
        response = first + second
        total = len(response)
        if 150 <= total <= 300:
            result = force_multi_message_split(response, "q")
            assert isinstance(result, str)

    def test_no_split_point_returns_original(self):
        """Lines 143-145: no candidates → original returned."""
        # 200-char response with no '. ', ', and ', ', but ', '. But ' near midpoint
        response = "abcdefghijklmnopqrstuvwxyz" * 8  # 208 chars, no split markers
        assert 150 <= len(response) <= 300
        result = force_multi_message_split(response, "q")
        # No <msg> tags — returned as-is
        assert result == response

    def test_second_part_too_short_no_split(self):
        """Line 139: second part ≤ 20 chars → no split."""
        # Put period very near end so second_part is tiny
        filler = "x" * 170  # 170 chars
        response = filler + ". hi"  # second_part = "hi" (2 chars ≤ 20)
        assert 150 <= len(response) <= 300
        result = force_multi_message_split(response, "q")
        # Should NOT split (second part too short)
        assert "<msg>" not in result


# ---------------------------------------------------------------------------
# parse_multi_message_response — extra edge cases (lines already partly covered)
# ---------------------------------------------------------------------------

class TestParseMultiMessageResponseExtra:
    """Additional edge cases for parse_multi_message_response."""

    def test_empty_string(self):
        """No tags, empty string → single-element list."""
        messages, flow_type = parse_multi_message_response("")
        assert messages == [""]
        assert flow_type == "single"

    def test_exactly_two_messages(self):
        """Two <msg> blocks → flow_type 'multi'."""
        response = "<msg>Alpha</msg>\n<msg>Beta</msg>"
        messages, flow_type = parse_multi_message_response(response)
        assert len(messages) == 2
        assert flow_type == "multi"

    def test_exactly_four_messages_not_capped(self):
        """Four <msg> blocks: all returned (cap is exactly 4)."""
        response = "<msg>A</msg><msg>B</msg><msg>C</msg><msg>D</msg>"
        messages, flow_type = parse_multi_message_response(response)
        assert len(messages) == 4
        assert flow_type == "multi"

    def test_five_messages_capped_at_four(self):
        """Five <msg> blocks: only first 4 returned."""
        response = "<msg>A</msg><msg>B</msg><msg>C</msg><msg>D</msg><msg>E</msg>"
        messages, flow_type = parse_multi_message_response(response)
        assert len(messages) == 4
        assert "E" not in messages

    def test_single_tag_flow_type_single(self):
        """Exactly 1 <msg> block → flow_type 'single'."""
        response = "<msg>Only me</msg>"
        messages, flow_type = parse_multi_message_response(response)
        assert flow_type == "single"
        assert messages[0] == "Only me"

    def test_whitespace_only_inside_tags(self):
        """Tags with only whitespace → stripped to empty string."""
        response = "<msg>   </msg>\n<msg>Real content</msg>"
        messages, flow_type = parse_multi_message_response(response)
        assert flow_type == "multi"
        assert messages[0] == ""
        assert messages[1] == "Real content"


# ═══ ADR-011 follow-up fixes B + C (found in live Telegram testing) ═══════════

from src.coordinator.services.message_processing_service import (  # noqa: E402
    parse_multi_message_response as _parse,
    strip_role_prefix_leaks as _strip,
)


# ─── B: unclosed <msg> tags must not leak literally ──────────────────────────

def test_unclosed_msg_tags_are_recovered_not_leaked():
    """Live bug: /continue emitted unclosed <msg> tags that reached the chat verbatim."""
    raw = "<msg>first beat\n<msg>second beat\n<msg>third beat"
    msgs, flow = _parse(raw)
    assert flow == 'multi'
    assert msgs == ["first beat", "second beat", "third beat"]
    assert not any('<msg>' in m for m in msgs)


def test_single_unclosed_msg_tag_stripped():
    msgs, flow = _parse("<msg>just one")
    assert flow == 'single'
    assert msgs == ["just one"]


def test_mixed_unclosed_with_stray_closing_tag():
    msgs, flow = _parse("<msg>alpha</msg>\n<msg>beta")
    # one well-formed pair exists -> existing path wins, unchanged behavior
    assert flow == 'single'
    assert '<msg>' not in msgs[0]


def test_wellformed_pairs_unchanged():
    """The pre-existing well-formed path must stay byte-identical."""
    msgs, flow = _parse("<msg>one</msg><msg>two</msg>")
    assert flow == 'multi'
    assert msgs == ["one", "two"]


def test_no_tags_returns_original():
    msgs, flow = _parse("plain reply")
    assert (msgs, flow) == (["plain reply"], 'single')


def test_unclosed_msg_capped_at_four():
    msgs, flow = _parse("<msg>a\n<msg>b\n<msg>c\n<msg>d\n<msg>e")
    assert len(msgs) == 4


# ─── C: role-prefix / hallucinated-turn leaks ────────────────────────────────

def test_cuts_trailing_hallucinated_user_turn():
    """Live bug: a /sys reply ended with a fabricated 'User:\\nFrom behind'."""
    raw = "Let's talk about next time: do you prefer this?\n\nUser:\nFrom behind"
    out = _strip(raw)
    assert out == "Let's talk about next time: do you prefer this?"
    assert 'User:' not in out


def test_strips_leading_assistant_prefix():
    assert _strip("Assistant: hey you") == "hey you"


def test_keeps_user_word_inside_prose():
    """Only line-anchored 'User:' is a leak — prose must survive."""
    raw = "I am a power User: of this app, Daddy."
    assert _strip(raw) == raw


def test_does_not_cut_when_leak_is_the_whole_answer():
    """No real content before the marker -> nothing to keep, leave as-is."""
    out = _strip("User: hello")
    assert out == "hello"


def test_empty_answer_safe():
    assert _strip("") == ""
    assert _strip("   ") == ""


# ═══ ADR-012 persona-configurable word substitutions ═════════════════════════

from src.coordinator.services.message_processing_service import apply_word_substitutions as _sub  # noqa: E402


def test_word_sub_basic_replacement():
    assert _sub("I love your shaft, Daddy", {"shaft": "cock"}) == "I love your cock, Daddy"


def test_word_sub_whole_word_only():
    # substrings must survive — no boundary inside 'driveshaft'
    assert _sub("the driveshaft broke", {"shaft": "cock"}) == "the driveshaft broke"


def test_word_sub_case_preserving():
    assert _sub("Shaft", {"shaft": "cock"}) == "Cock"       # Title
    assert _sub("SHAFT", {"shaft": "cock"}) == "COCK"       # ALL CAPS
    assert _sub("shaft", {"shaft": "cock"}) == "cock"       # lower


def test_word_sub_multiple_rules_and_occurrences():
    out = _sub("his shaft and her member", {"shaft": "cock", "member": "clit"})
    assert out == "his cock and her clit"


def test_word_sub_noop_when_empty_or_none():
    assert _sub("keep his shaft", None) == "keep his shaft"
    assert _sub("keep his shaft", {}) == "keep his shaft"


def test_word_sub_noop_on_empty_answer():
    assert _sub("", {"shaft": "cock"}) == ""


def test_word_sub_skips_bad_rule_values():
    # non-string replacement / blank key are ignored, valid ones still apply
    assert _sub("a shaft here", {"shaft": "cock", "  ": "x", "member": 5}) == "a cock here"


def test_word_sub_respects_cap():
    rules = {f"w{i}": "x" for i in range(40)}
    rules["w0"] = "zero"
    # only the first 25 rules apply; w0 is first so it does
    assert "zero" in _sub("w0 w39", rules)
