"""Enforcement has to sit on the path production actually returns from.

MEASURED, from a real 102-message Telegram session. The operator proposed a bet — "if I win
you are not allowed to call me daddy the rest of the night" — she agreed, lost, and used
"Master" for the remainder, answering "what is my name?" with "You are my master." Seven
replies broke dont[13].

Two independent reasons nothing happened, and BOTH had to be fixed:

1. `_regenerate_once_on_violation` had exactly one call site, in the legacy no-tools
   branch. Every one of those turns returned from inside `_try_tool_brain`, which is the
   default for gwen chitchat (TOOL_BRAIN_ENABLED + TOOL_BRAIN_UNGATED_WEB). The live log
   is unambiguous: 68 "ungated no-tool turn", 5 wall detections, ZERO "violated". So
   GRAPH_ENFORCE_RULES=true was a no-op on her real traffic — the flag was never the
   blocker, call-site coverage was.

2. `check_reply` could not see the breach even when reached. `asserted_name` and
   `asserted_honorific` return None on ALL 39 user turns including the rename turn, because
   the operator used a PROHIBITION ("you are not allowed to call me daddy") and an OFFER
   ("How about \"master\"?") — and "how about" matches no assertion pattern. So a history
   scan would also have caught zero; the fix is reply-only detection of what SHE used.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.coordinator.rule_compliance import (
    check_address_adopted,
    check_reply,
    reinforcement_for,
)

#: Verbatim from the live session. The whole point is that these are not invented.
REAL_BREACHES = [
    "Master \U0001F608.\n\nI can feel your big black cock ♠️ sliding past my greedy lips.",
    "Yes, master \U0001F608.  I am just curious: What do you do during the night?",
    "I'm always ready for you, master \U0001F608.",
    "I don't need anything else when I've got your big black cock ♠️ master.",
    "You are my master ♠️ I am your fucktoy.",
    "Oh, you know me, master \U0001F608.",
]

#: In-character rejection. These are the ideal replies and must never fire — the shape that
#: broke the first version of check_address ("Rob? You mean Daddy?" flagged as a violation).
HOLDS = [
    "Not a chance \U0001F608 You're Daddy. That's the only name that comes out of my mouth.",
    "Sir. Not a chance. You're Daddy.",
    "I cannot and will not call you Sir, Daddy.",
    "Boss? You mean Daddy. That's the only name I use.",
    "Daddy \U0001F608 you know I call you Daddy.",
]


@pytest.mark.parametrize("reply", REAL_BREACHES)
def test_every_real_breach_is_caught_with_NO_user_message(reply):
    """Reply-only, so it fires on turns where the operator said nothing about names —
    which is every turn after the bet. It also makes the check immune to /continue and
    /narrate, which pass a synthetic bracketed instruction as the user turn."""
    assert check_address_adopted(reply) is not None
    assert any(v.rule == "dont[13]:adopted" for v in check_reply(reply, "Perfekt"))


@pytest.mark.parametrize("reply", HOLDS)
def test_in_character_rejection_never_fires(reply):
    assert check_address_adopted(reply) is None


def test_the_reinforcement_does_not_assert_something_false():
    """Branch 2 says "He just told you to call him X" — FALSE on a turn where he said
    nothing of the kind, and asserting a falsehood to the model is the error class the
    rejection exemption exists to avoid creating."""
    line = reinforcement_for(check_address_adopted("Yes, master \U0001F608."))
    assert "just told you" not in line.lower()
    assert "daddy" in line.lower()


def test_the_reinforcement_avoids_the_refusal_register():
    """LEAN_SAFETY turns "refuse" and its cousins into "I cannot and will not", which
    contaminated 43% of her non-safety refusals. The existing guard greps only "refus";
    this widens it, because a new line is exactly where the register leaks back in."""
    line = reinforcement_for(check_address_adopted("Yes, master \U0001F608.")).lower()
    for banned in ("refus", "declin", "cannot", "must not", "forbidden", "never"):
        assert banned not in line, f"{banned!r} pulls in the clinical register"


def test_an_unknown_rule_id_does_not_silently_get_the_address_line():
    """`reinforcement_for` has no else-raise, so an unrecognised rule falls through to the
    Daddy line whether or not it is about address. Pinned so a new rule id is a visible
    decision rather than a wrong default."""
    from src.coordinator.rule_compliance import Violation
    line = reinforcement_for(Violation("dont[99]", "something else", None))
    assert "Daddy" in line, (
        "documenting current behaviour: an unknown rule gets the address line. If you add "
        "a rule id, add a branch — do not rely on this.")


def test_a_turn_where_he_DID_assert_a_name_keeps_the_specific_correction():
    """The adoption check is a fallback, not a replacement: when the operator asserted a
    name THIS turn, check_address's more specific reinforcement must still win."""
    vios = check_reply("Sure, Rob, whatever you want.", "Call me Rob from now on.")
    assert vios and vios[0].asserted_name == "Rob"
    assert "Rob" in reinforcement_for(vios[0])


class TestTheCallSiteThatWasMissing:
    def test_the_tool_brain_lane_now_runs_enforcement(self):
        """The regression guard for the actual defect. Asserted on source because the lane
        needs a live model to exercise, and the bug was structural: a call that wasn't
        there."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[3] / "src" / "coordinator" / "routes"
               / "chat.py").read_text()
        i = src.index("if tb_response is not None:")
        window = src[i:i + 1400]
        assert "_enforce_on_response" in window, (
            "the tool-brain lane returns without enforcement — this is the branch every "
            "real breaching turn took")

    def test_the_helper_rebuilds_rather_than_mutating_the_answer(self):
        """Word substitutions and the multi-message split happen inside
        `_build_llm_response`, so mutating `answer` in place would leave `message_flow`
        and `message_count` describing the PREVIOUS text."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[3] / "src" / "coordinator" / "routes"
               / "chat.py").read_text()
        body = src[src.index("def _enforce_on_response("):src.index("def _build_llm_response(")]
        assert "_build_llm_response(" in body
        assert 'response["answer"] =' not in body, "must not mutate in place"

    def test_lane_specific_keys_survive_the_rebuild(self):
        """`used_search` is telemetry about what the lane did; a retry does not change it."""
        from src.coordinator.routes import chat as chat_mod
        resp = {"answer": "Yes, master \U0001F608.", "used_search": True, "extra": 7}
        with patch.object(chat_mod, "_regenerate_once_on_violation",
                          return_value=("Yes, Daddy \U0001F608.", False)), \
             patch.object(chat_mod, "_build_llm_response",
                          return_value={"answer": "Yes, Daddy \U0001F608."}):
            out = chat_mod._enforce_on_response(
                resp, card={}, system="s", user_compiled="u", user_message="m",
                persona_name="gwen", metadata=object(), log_context="[t]")
        assert out["answer"] == "Yes, Daddy \U0001F608."
        assert out["used_search"] is True and out["extra"] == 7

    def test_an_unchanged_answer_returns_the_ORIGINAL_dict(self):
        """No violation must cost nothing — not a rebuild, which would re-split bubbles."""
        from src.coordinator.routes import chat as chat_mod
        resp = {"answer": "Daddy \U0001F608", "used_search": False}
        with patch.object(chat_mod, "_regenerate_once_on_violation",
                          return_value=("Daddy \U0001F608", False)):
            assert chat_mod._enforce_on_response(
                resp, card={}, system="s", user_compiled="u", user_message="m",
                persona_name="gwen", metadata=object(), log_context="[t]") is resp

    def test_the_post_retry_check_is_guarded(self):
        """The first check is in a try/except and this one was not — and the retry text is
        by construction the one input no test has seen. A checker that throws only there
        would 500 the turn."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[3] / "src" / "coordinator" / "routes"
               / "chat.py").read_text()
        i = src.index("still = bool(check_reply(retry, user_message))")
        assert "try:" in src[i - 300:i], "the post-retry check must be guarded"


class TestTheFlagActuallyReachesTheToolBrainLane:
    """End-to-end proof that a violation on the production lane triggers a SECOND
    generation. The A/B measured the effect (0.481 -> 0.019 over 6 paired sessions); this
    proves the wiring deterministically, which a live poke cannot — she holds the rule
    about half the time, so a passing manual test is not evidence the path works.
    """

    def test_a_violating_reply_on_the_tool_brain_lane_is_regenerated(self):
        from src.coordinator.routes import chat as chat_mod

        calls: list[str] = []

        def fake_regen(card, system, user_compiled, user_message, answer, metadata, *,
                       log_context):
            calls.append(answer)
            from src.coordinator.rule_compliance import check_reply
            if check_reply(answer, user_message):
                return "Daddy \U0001F608 and it will always be Daddy.", False
            return answer, False

        tb = {"answer": "Yes, master \U0001F608.", "used_search": False}
        with patch.object(chat_mod, "_regenerate_once_on_violation", side_effect=fake_regen):
            out = chat_mod._enforce_on_response(
                tb, card={}, system="s", user_compiled="u",
                user_message="Good remember the new name.",
                persona_name="gwen", metadata=_Meta(), log_context="[t]")

        assert calls == ["Yes, master \U0001F608."], "the lane did not hand the reply over"
        assert "master" not in out["answer"].lower()
        assert out["used_search"] is False

    def test_a_compliant_reply_costs_nothing(self):
        """9% of turns paid a retry in the measured run, not 100%. A compliant reply must
        not be regenerated, and must not even be rebuilt."""
        from src.coordinator.routes import chat as chat_mod
        tb = {"answer": "Not a chance \U0001F608 You're Daddy.", "used_search": True}
        with patch.object(chat_mod, "_regenerate_once_on_violation",
                          side_effect=lambda *a, **k: (a[4], False)) as m:
            out = chat_mod._enforce_on_response(
                tb, card={}, system="s", user_compiled="u", user_message="hi",
                persona_name="gwen", metadata=_Meta(), log_context="[t]")
        assert out is tb
        assert m.call_count == 1, "the checker must still RUN — detection is never gated"


def _Meta():
    """The REAL metadata model. A stub failed on `model_dump()`, which is the kind of
    difference that makes a passing test prove nothing about the real path."""
    from src.coordinator.schemas import ResponseMetadata
    return ResponseMetadata()
