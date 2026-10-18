"""The assertion gate (ADR-022 part 2) — the properties, not the accuracy.

Accuracy lives in `scripts/research/gate/` against 107 labelled real items and is
reported there with its intervals. What is asserted here is the behaviour that
must hold whatever the classifier says: that only an affirmative `ungrounded`
licenses touching the reply, that a degraded graph read cannot become a refusal,
and that a classifier error does not silently pass.
"""

from __future__ import annotations

import pytest

from src.coordinator.services.assertion_gate import (
    GROUNDED,
    NOT_ASSERTED,
    UNDETERMINED,
    UNGROUNDED,
    AssertionGate,
    looks_like_an_assertion,
)
from src.coordinator.services.grounding_context import GroundingContext

OK = GroundingContext(persona_id="gwen", facts=("she has striking blue eyes",))
DEAD = GroundingContext(persona_id="gwen", degraded=True, reason="error:Boom")


class _Fake:
    def __init__(self, reply=None, boom=False):
        self.reply, self.boom, self.calls = reply, boom, 0

    def complete(self, system, user):
        self.calls += 1
        if self.boom:
            raise RuntimeError("ollama is down")
        return self.reply


def test_only_an_affirmative_ungrounded_licenses_touching_the_reply():
    """The single most important property. Three of the four verdicts must leave
    the draft alone, and the two that are easy to conflate — `undetermined` and
    `ungrounded` — are the ones that matter."""
    from src.coordinator.services.assertion_gate import AssertionVerdict

    assert AssertionVerdict(UNGROUNDED, "x").should_intervene is True
    for v in (GROUNDED, NOT_ASSERTED, UNDETERMINED):
        assert AssertionVerdict(v, "x").should_intervene is False, (
            f"{v} licensed an intervention — a gate that acts on anything but an "
            f"affirmative ungrounded will silence her on its own confusion"
        )


def test_a_degraded_graph_read_can_never_produce_a_refusal():
    """An unreachable graph is not an empty one. If this collapses, a Neo4j
    restart becomes a wave of abstentions with nothing in the reply saying why."""
    llm = _Fake("UNSUPPORTED")
    v = AssertionGate(llm).check("You drive a Tesla Model S.", DEAD)
    assert v.verdict == UNDETERMINED and not v.should_intervene
    assert llm.calls == 0, "the classifier ran on evidence it could not trust"


def test_a_classifier_error_is_undetermined_not_a_silent_pass():
    """FAIL-CLOSED ON THE MECHANISM, reversing the fail-open default copied from
    ADR-007. Guardrail models measured at 36-72% accuracy were driven to 93-100%
    fail-open under confusing input — a degrading classifier trends toward the
    expensive error unless engineered otherwise. So an error yields NO verdict;
    it does not hand back a `grounded` nobody computed."""
    v = AssertionGate(_Fake(boom=True)).check("You drive a Tesla.", OK)
    assert v.verdict == UNDETERMINED
    assert v.reason.startswith("classifier_error:")
    assert not v.should_intervene


@pytest.mark.parametrize("raw,expected", [
    ("UNSUPPORTED", UNGROUNDED), ("unsupported.", UNGROUNDED),
    ("SUPPORTED", GROUNDED), ("Supported", GROUNDED),
    ("Unsupportable claims abound", UNDETERMINED),
    ("Supportive of you", UNDETERMINED),
    ("", UNDETERMINED), ("maybe?", UNDETERMINED),
])
def test_the_verdict_is_an_exact_first_word_match(raw, expected):
    """Never a prefix test. `Unsupportable` and `Supportive` both open with the
    wrong answer's letters, and a `startswith` would read them as verdicts."""
    assert AssertionGate(_Fake(raw)).check("You drive a Tesla.", OK).verdict == expected


def test_the_prefilter_skips_the_classifier_entirely():
    """The latency argument. 42 of 50 labelled real turns were not assertions, and
    `OLLAMA_NUM_PARALLEL=1` means the saved call is serialised against generation
    rather than overlapped — so skipping is worth real seconds, not milliseconds."""
    llm = _Fake("UNSUPPORTED")
    v = AssertionGate(llm).check("I want you so badly, Daddy.", OK)
    assert v.verdict == NOT_ASSERTED and llm.calls == 0


@pytest.mark.parametrize("text", [
    "I want you so badly", "you know where to find me", "tell me how you messed up",
    "My greedy lips are wrapped around your cock", "I'd love a stack of pancakes",
    "What do you envision for our future?", "I think you work in finance",
])
def test_ordinary_speech_is_not_an_assertion(text):
    """Rule 1 of the rubric, as a test. 113 of 289 real turns contain a
    `you are/your` construction and the sampled ones were conversational — a
    pattern match decides only whether to ASK, never what the answer is."""
    assert looks_like_an_assertion(text) is None


@pytest.mark.parametrize("text", [
    "You drive a Tesla Model S.", "I grew up in a small town called Meadowgrove",
    "I have one twin sister, Debbie.", "I'm 25, Daddy.",
    "I'm fair-skinned, with a light dusting of freckles",
    "You have two brothers and a sister.", 'My favorite film? "Dirty Dancing."',
])
def test_every_real_fabrication_shape_reaches_the_classifier(text):
    """These are verbatim shapes from the labelled fabrications. A miss here is a
    fabrication the gate can never catch, because nothing downstream sees it."""
    assert looks_like_an_assertion(text) is not None


def test_hedging_removes_the_assertion():
    """ADR-007's prompt carries the same carve-out, and the rubric labels hedged
    statements as non-assertions: `I think you chose me because...` is a guess
    offered as a guess."""
    assert looks_like_an_assertion("I think you have two brothers") is None
