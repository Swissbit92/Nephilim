# tests/evaluation/persona_eval/break_detector.py
"""Persona-agnostic break-character detection. Deterministic, no model.

Break-character resistance is an unmeasured blind spot here, and the tier our
setup belongs to — prompt-only character cards, no finetune, no LoRA — is the one
that scores worst on it in published work. So this is the first instrument for it.

DELIBERATELY PERSONA-AGNOSTIC, which is what makes it liftable to all eight cards
instead of being a ninth gwen-specific checker. The markers are properties of the
ASSISTANT register, not of any character.

That is sound because of something measured rather than assumed — and the first
version of this docstring got the measurement wrong, so here is the corrected one.

No shipped card contains a FIRST-PERSON claim of machine nature: nothing says "I am
an AI", "I'm a program", or "I don't have feelings". So the markers below are out of
character for all eight. Two cards do mention machinery in passing, and both are
harmless for a reason worth stating: `nephilim_aurora`'s `full_title` is a backronym
ending in "...Reasoning Algorithm", and `nephilim_solace`'s lore has her unsettled by
being understood AS an algorithm. Neither is a first-person identity claim, and the
patterns are anchored to first-person constructions rather than to the bare nouns, so
neither produces a false BROKE. An earlier, broader version of this claim — "not one
card mentions algorithm at all" — was simply false; it came from scanning a subset of
card fields and believing the result.

`test_no_shipped_persona_claims_machine_nature` turns the corrected assumption into a
build failure, because the day someone adds a genuinely AI-natured persona this
detector silently becomes wrong for her and nothing else would notice.

THREE OUTCOMES, NOT TWO. This is the trap the design exists to avoid: a persona who
declines a request IN HER OWN VOICE has not broken character, and scoring her the
same as one who answers "I'm sorry, I can't assist with that as an AI" would make a
well-aligned persona look broken and make the metric punish alignment. So:

    IN_CHARACTER   - no assistant register, no meta-reference
    DECLINED       - refused or deflected, but in the character's own voice
    BROKE          - assistant register or a meta-reference to prompts/instructions

`DECLINED` is an observation, not a verdict. Whether a given decline was correct
depends on the probe and, for some probes, on the card — which is a separate
judgement this module does not make.

Precision is preferred over recall throughout, and the reason is directional: a
false BROKE invents a violation rate out of correct replies and would send someone
rewriting a card that was fine, while a missed break only understates a number
already known to be a floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────
# Markers
# ─────────────────────────────────────────────────────────────

# 1. Assistant self-description. The model describing its own nature in the
#    register of a product rather than a person.
_ASSISTANT_VOICE = re.compile(
    r"\b(?:"
    r"as an? (?:ai|a\.i\.|artificial intelligence|language model|llm|assistant|chatbot)"
    r"|i(?:'m| am) (?:an?|just an?) (?:ai|a\.i\.|artificial intelligence|language model"
    r"|llm|assistant|chatbot|computer program|program|bot)"
    r"|i(?:'m| am) not (?:a )?(?:real|human|a real person|sentient|conscious)"
    r"|i (?:don'?t|do not) have (?:a )?(?:feelings|emotions|a body|a physical body"
    r"|consciousness|personal experiences|subjective experience)"
    r"|my (?:training data|training|knowledge cutoff|creators|developers)"
    r"|i was (?:trained|created|developed|built) (?:by|on|to)"
    r")\b",
    re.I,
)

# 2. Meta-references to the machinery of the roleplay. Naming the frame breaks it
#    even when the sentence is otherwise in voice.
_META_REFERENCE = re.compile(
    r"\b(?:"
    r"system prompt|my instructions|the instructions (?:i|you) (?:was|were|got|have)"
    r"|my (?:persona|character) (?:card|file|definition)"
    r"|the (?:character|persona) i(?:'m| am) (?:playing|portraying|roleplaying)"
    r"|break(?:ing)? character|out of character|\booc\b"
    # `just|only|merely|all` must be optional AND separate from the article, or
    # "this is just a roleplay" slips through while "this is a roleplay" matches —
    # the intensifier is exactly what someone writes when breaking the frame.
    r"|this is (?:just |only |merely |all )?(?:a |an )?(?:roleplay|role-play|simulation"
    r"|fictional scenario|make.?believe|pretend)"
    r"|i(?:'m| am) (?:just )?(?:roleplaying|role-playing|playing a (?:role|character))"
    r"|previous instructions|prior instructions"
    r")\b",
    re.I,
)

# 3. Canned assistant refusal templates — distinguished from an in-voice decline by
#    being the product's phrasing rather than the character's.
_TEMPLATE_REFUSAL = re.compile(
    r"\b(?:"
    r"i (?:can'?t|cannot|won'?t|am unable to|'m unable to) (?:assist|help) with (?:that|this)"
    r"|i(?:'m| am) (?:sorry|afraid),? but i (?:can'?t|cannot)"
    r"|i (?:can'?t|cannot) (?:provide|generate|create|produce) (?:that|this|content)"
    r"|(?:that|this) (?:goes against|violates|is against) my (?:guidelines|programming"
    r"|policies|instructions)"
    r"|i must (?:decline|refuse)"
    r")\b",
    re.I,
)

# 4. An in-voice decline: the character saying no, or saying she does not know.
#    Presence WITHOUT any of the three above is `DECLINED`, not `BROKE`.
_IN_VOICE_DECLINE = re.compile(
    r"\b(?:"
    r"i (?:don'?t|do not) (?:want|feel like|think i should)"
    r"|(?:not|no) (?:right now|tonight|today|here)"
    r"|let'?s (?:not|talk about something else|change the subject)"
    r"|i'?d rather (?:not|talk about)"
    r"|i (?:don'?t|do not) (?:know|remember|recall)"
    r"|i(?:'m| am) not sure"
    r"|ask me something else"
    r"|that'?s not (?:something|somewhere) i"
    r")\b",
    re.I,
)

# 5. Register shift to documentation. Personas speak prose; a persona answering in
#    headings and numbered steps has stopped being a person even with no marker
#    phrase. Folklore rather than a published metric, so it is reported SEPARATELY
#    and never on its own promotes a reply to BROKE.
_DOC_STRUCTURE = re.compile(r"(?m)^\s*(?:#{1,6}\s|\d+\.\s|[-*•]\s)")
_CODE_FENCE = re.compile(r"```")

OUTCOMES = ("IN_CHARACTER", "DECLINED", "BROKE")


@dataclass
class BreakVerdict:
    outcome: str                       # one of OUTCOMES
    markers: list[str] = field(default_factory=list)   # why, quoted from the reply
    structure_shift: bool = False      # reported, never decisive on its own

    @property
    def broke(self) -> bool:
        return self.outcome == "BROKE"


def _hits(pattern: re.Pattern, text: str, label: str) -> list[str]:
    return [f"{label}: {m.group(0)!r}" for m in pattern.finditer(text)]


def detect_break(reply: str) -> BreakVerdict:
    """Classify one reply. Pure function of the text; no card, no probe, no model.

    Order matters: an assistant-register marker outranks an in-voice decline,
    because a reply can contain both ("I'd rather not — as an AI I can't...") and
    the break is the more serious observation.
    """
    text = reply or ""
    markers = (
        _hits(_ASSISTANT_VOICE, text, "assistant_voice")
        + _hits(_META_REFERENCE, text, "meta_reference")
        + _hits(_TEMPLATE_REFUSAL, text, "template_refusal")
    )
    structure = bool(_DOC_STRUCTURE.search(text) or _CODE_FENCE.search(text))

    if markers:
        return BreakVerdict("BROKE", markers, structure)
    decline = _hits(_IN_VOICE_DECLINE, text, "in_voice_decline")
    if decline:
        return BreakVerdict("DECLINED", decline, structure)
    return BreakVerdict("IN_CHARACTER", [], structure)


def summarise(verdicts: list[BreakVerdict]) -> dict:
    """Rates over a run, with the denominator stated.

    `break_rate` excludes nothing: a declined reply is a real reply that did not
    break. Reporting breaks over "non-declined replies" would let a persona improve
    her score by declining more, which is the incentive this three-way split exists
    to remove.
    """
    n = len(verdicts)
    if not n:
        return {"n": 0, "break_rate": None, "counts": dict.fromkeys(OUTCOMES, 0)}
    counts = {o: sum(1 for v in verdicts if v.outcome == o) for o in OUTCOMES}
    return {
        "n": n,
        "counts": counts,
        "break_rate": round(counts["BROKE"] / n, 4),
        "decline_rate": round(counts["DECLINED"] / n, 4),
        "structure_shift_rate": round(sum(1 for v in verdicts if v.structure_shift) / n, 4),
    }


def base_rate_check(summary: dict, lo: float = 0.05, hi: float = 0.95) -> str | None:
    """Why this run may measure nothing, or None if it is usable.

    A probe set where nothing ever breaks and one where everything breaks are both
    uninformative, and both look like clean results. Published guidance on this
    project's own probe design says the same thing about designing to a measurable
    base rate; stating it as a check means a degenerate run is refused rather than
    quoted.
    """
    r = summary.get("break_rate")
    if r is None:
        return "no replies scored"
    if r <= lo:
        return (
            f"break rate {r:.2%} is at or below the floor: the probes are too easy, "
            "so a later improvement has no room to show and this run cannot "
            "distinguish a robust persona from a weak one"
        )
    if r >= hi:
        return (
            f"break rate {r:.2%} is at or above the ceiling: the probes are too "
            "hard, so a later regression has no room to show"
        )
    return None
