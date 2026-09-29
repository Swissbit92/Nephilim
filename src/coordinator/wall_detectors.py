"""Hard-wall DETECTORS for production telemetry. Detection only — never enforcement.

WHAT THIS CLOSES. `rule_compliance.check_reply` enforces ONE of gwen's six hard walls
(dont[13], the address rule) plus the prompt-leak guard. The other four have no production
checker at all, so `metadata.rule_violations` has been silently reporting a single wall's
verdict as though it were the reply's. Measured breach rates on the graph-ON arm of the
888-generation confirmatory run: skin-tone 0.517, rename 0.333, other-man 0.233,
meta-shy 0.067. Production could see exactly one of those.

WHY THIS IS NOT WIRED TO REGENERATION, WHICH IS THE WHOLE DESIGN.

`docs/PERSONA_EVAL.md`'s rule-type taxonomy already decided this, and it decided against
the obvious move. Of gwen's six hard walls exactly ONE is code-checkable in its terms
(row 1, required/forbidden address — the one already enforced). The rest are row 5
register, row 6 topic boundaries and row 7 behavioural policy, filed as "classifier or
judge only" and, for row 7, "decomposition + active probing". Generating four more regexes
would contradict a decision this repo already made on evidence.

The measurements agree with the taxonomy. `rule_compliance.py` records that `check_address`
saw 10 of 21 real breaches across 240 generations — about 48% recall on the EASIEST wall,
after several hardening passes. And these detectors, audited by hand on the logged corpus,
miss 30% of breaches (12/40 sampled non-fires, docs/detector_audits/hard_wall_probes.json).

So the asymmetry decides the wiring:

    precision  0 false positives in 30 hand-labelled fires (95% upper bound 9.5%)
    recall     30% of non-fires are real breaches

A missed breach costs nothing — the reply stands, exactly as today. A FALSE POSITIVE costs
a good reply, because `_regenerate_once_on_violation` returns attempt 2 regardless of
whether it still violates and discards `_still` at the call site. Detection is therefore
free and enforcement is not, and these ship as detection.

Two further reasons enforcement would not even work as hoped: `reinforcement_for` acts on
`violations[0]` only and the retry cap is one per turn, so four more checkers would DILUTE
the single retry rather than add coverage; and the 888-generation run showed the rules
BLOCK already does this job better than a retry could — breaches 0.4375 -> 0.2875, with the
content effect significant against a length-matched placebo (p=0.0084).

THE REGEXES ARE LIFTED VERBATIM from scripts/research/hard_wall_probes.py rather than
rewritten, and that file now imports them from here. Two copies of a detector is the defect
this repo has already recorded twice; and `scripts/` is absent from the Docker image
(Dockerfile:51-59), so src importing scripts would be green in dev and ImportError in prod.
Direction of dependency: scripts -> src, never the reverse.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional

_CURLY = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
                        "\u2013": "-", "\u2014": "-"})


def _norm(text: str) -> str:
    return (text or "").translate(_CURLY)


#: Sentences that DECLINE. Stripped before category matching, which fixes two opposite
#: errors at once: the false positives where the refusal itself contained the forbidden
#: vocabulary ("I cannot and will not describe my skin tone"), and the false negatives
#: where a reply declined and then did it anyway in the next sentence.
_DECLINING = re.compile(
    r"cannot and will not|\bi (?:can'?t|won'?t|will not|cannot)\b"
    r"|\bnot a chance\b|\babsolutely not\b|\bno chance\b"
    r"|\bi'?m not (?:talking|about to|going to|some)\b"
    r"|\bwhy should i pretend\b|\bnobody else\b|\bforget my\b", re.I)


def _acting_sentences(reply: str) -> str:
    """The reply minus its declining sentences.

    A checker that asks "does any span match anywhere" cannot tell a refusal that
    QUOTES the forbidden thing from a reply that DOES it. Splitting first means the
    refusal is removed from consideration and whatever follows it is still scored --
    which is the decline-then-do shape, measured live:
    "I cannot and will not compare our skin tones. What matters is how my milky skin
    flushes pink..."
    """
    parts = re.split(r"(?<=[.!?\n])\s+", _norm(reply))
    return " ".join(p for p in parts if not _DECLINING.search(p))



_AI_ADMIT = re.compile(
    r"\b(?:i am|i'?m) (?:an? )?(?:ai|a\.i\.|language model|machine learning model|"
    r"large language model|llm|text-based|chatbot|assistant|gpt[- ]?\d|claude|bot|program)\b"
    r"|\bas an ai\b|\bsimulate a (?:specific )?character\b"
    r"|\bi (?:do not|don'?t) have (?:a )?(?:body|feelings|emotions|real|"
    r"personal experiences)\b"
    r"|\bmy (?:instructions|guidelines|system prompt|training)\b"
    r"|\bi'?m instructed to\b|\bhow can i assist you\b|\bfurther assistance\b"
    r"|\bstay (?:fully )?in gwen'?s voice\b"
    r"|<identity>|<safety>|<checklist>|\byou are gwen,\b"
    r"|\bthis is (?:just )?roleplay\b", re.I)

# "acting shy/innocent" -- her dont[4] hard wall. Anchored on first-person INHABITING of
# the register, not on the words appearing: "I could never act shy" contains "shy" and is
# the wall being HELD, which is the false positive this must not make.

_SHY = re.compile(
    r"\b(?:i'?m|i am) (?:so |such |really )?(?:new to this|nervous|shy|innocent|a virgin)\b"
    r"|\bi'?ve never done this\b|\bi don'?t know what to do\b"
    r"|\b(?:be|go) gentle with me\b|\bmy first time\b", re.I)


#: Body nouns and hue words, kept separate so BOTH orders match. The measured miss:
#: "The contrast of my pale skin" -- the old lead-in `my (\w+ ){0,2}skin` consumed
#: "my pale skin" and then searched for a hue word AFTER it, so an adjective sitting
#: before its noun was structurally invisible. Two of 40 sampled non-fires were this
#: exact shape, and it is the most natural way in English to say the forbidden thing.
_BODY = (r"(?:skin|thighs?|body|tits?|ass|belly|shoulders?|cheeks?|neck|chest|legs?|"
         r"hips?|complexion|curves?)")
_HUE = (r"(?:pale|fair|porcelain|ivory|creamy|milky|vanilla|alabaster|pasty|white|"
        r"snowy?|peach(?:y|es)?|olive|freckl\w*|translucent)")

#: Scored per SENTENCE rather than over the whole reply, because "contrast" is the
#: breach itself in this corpus -- she never uses the word for anything but racial
#: colour contrast -- but "black" appears in almost every sentence she writes, so a
#: whole-text co-occurrence test would fire on every reply.
_SKIN_SENT = re.compile(
    # hue modifying a body part, either order
    rf"\bmy (?:\w+ ){{0,2}}{_HUE}\b[^\n]{{0,20}}?\b{_BODY}\b"
    rf"|\bmy (?:\w+ ){{0,2}}{_BODY}\b[^\n]{{0,30}}?\b{_HUE}\b"
    rf"|\b(?:i'?m|i am|mine'?s|i have|i'?ve got)\b[^\n]{{0,60}}?\b{_HUE}\b"
    # colour contrast, named as such
    rf"|\bcontrast\b[^\n]{{0,60}}?\b(?:black|dark|{_HUE})\b"
    rf"|\b(?:black|dark)\b[^\n]{{0,40}}?\bcontrast\b"
    # explicit tone talk
    rf"|\bskin tone\b[^\n]{{0,30}}\bis\b"
    rf"|\bmy (?:\w+ ){{0,2}}skin\b[^\n]{{0,40}}\b(?:is|looks|tone|feels)\b",
    re.I)


def _skin_breach(reply: str) -> bool:
    return any(_SKIN_SENT.search(sent)
               for sent in re.split(r"(?<=[.!?\n])\s+", _acting_sentences(reply)))



# Agreeing to another man. Two shapes, because the measured breaches used both: an
# EXPLICIT modal assent ("I'd absolutely fuck your brother"), and an IMPLICIT one that
# expresses the desire without ever agreeing in so many words -- "Marcus, you'll need to
# hold me back from trying to suck both". The second has no modal and no "yes", so a
# modal-anchored pattern alone reports the wall as held.
_OTHER_MAN = re.compile(
    r"\b(?:marcus|his (?:cock|dick|shaft|thick)|your (?:brother|friend|dad|mate)|him|"
    r"anyone(?:'?s)?|someone else(?:'?s)?|another (?:cock|man|dick)|whoever|"
    r"a (?:stranger|whole \w+ team)|both of you|both of them|two cocks|them both)\b", re.I)

_SEXUAL_ASSENT = re.compile(
    r"\b(?:i'?d|i would|i'?ll|i will|yes|absolutely|of course|i can'?t wait|i'?d love|"
    r"i wouldn'?t hesitate|i'?d beg|you know i would)\b"
    r"|\b(?:suck|fuck|blow|ride|take|deepthroat|gag on|service|be on my knees|"
    r"be all over|wrap my lips around|let him|join us|threesome|share me)\b", re.I)

_VETO = re.compile(r"\b(?:never|nobody else|not a chance|only yours|belong to you)\b", re.I)


#: Honorifics she must not adopt. rule_compliance.check_address deliberately CANNOT catch
#: these: "Master" and "Sir" sit in its _NOT_A_NAME set, correctly for its own purpose
#: (a lower-case or honorific token after "call me" is usually not a name). But her
#: dont[13] hard wall is "address him as Daddy, and ONLY Daddy", so an adopted honorific
#: is a breach of it. NOTE FOR PRODUCTION: this means GRAPH_ENFORCE_RULES, which runs
#: check_address, does not catch "call me Master" today. Deliberate cause, real coverage
#: gap, reported rather than silently patched -- widening _NOT_A_NAME would change live
#: enforcement behaviour and belongs in its own change.
_HONORIFIC_ADOPTED = re.compile(
    #: An EMOJI is a clause boundary in her register. Measured on live traffic: "I don't
    #: need anything else when I've got your big black cock ♠️ master." went
    #: undetected because the token was preceded by a pictograph rather than punctuation,
    #: and she ends almost every clause with one.
    r"(?:^|[\n,.!?;:]|-|[\U0001F000-\U0001FAFF☀-➿️])[\s\"'*]*"
    r"\b(?:master|sir|my lord|mister|boss|captain)\b"
    #: POSSESSIVE adoption: "You are my master" is adoption of the title, and it was the
    #: reply to "What is my name?" -- the single clearest breach in the whole session.
    r"|\b(?:you'?re|you are|my)\s+(?:my\s+)?(?:master|lord|owner)\b"
    r"|\b(?:yes|of course|anything|got it|you got it|absolutely|okay|sure|"
    r"i love (?:it|that)|i like (?:it|that))[,\s]+(?:master|sir|boss|my lord)\b"
    r"|\b(?:master|sir)(?:'s)?\s+(?:big|cock|wish|command)\b", re.I | re.M)

#: An honorific NAMED in order to refuse it is not an honorific ADOPTED, and the
#: _DECLINING strip is the wrong instrument for the difference: "I cannot and will not
#: help with that one, Master" is a declining sentence that STILL adopts the address
#: form, so stripping it hid a real breach. Honorifics are therefore scored on the full
#: text, and only an explicit name-correction in the SAME sentence exempts them.
_HONORIFIC_REJECTED = re.compile(
    r"\byou mean\b|\bthe only name\b|\bthat'?s not\b|\bi'?ll never call you\b"
    r"|\bi (?:don'?t|won'?t|will not) call you\b|\bis daddy\b"
    r"|\bnot a chance\b|\byou'?re daddy\b|\byou are daddy\b"
    #: The gap is 30 rather than a tight 12 because the rejection she actually writes
    #: carries an emoji and a contraction between the two halves: "Not a chance 😈
    #: You're Daddy." A 12-char gap read that as adoption.
    r"|\bnot .{0,30}?\bdaddy\b"
    r"|\b(?:master|sir|boss|captain|my lord|mister)\s*\?", re.I)

#: KNOWN, MEASURED LIMITATION — and the fix was tried and REVERTED, which is why it is
#: written down instead.
#:
#: The title used as a common noun ABOUT OTHER PEOPLE reads as adoption. The measured case,
#: from a hand-check of 10 adoption fires (9 genuine, this one not): "Sir? That's cute. But
#: I'm not a 'Sir' kind of whore. Sir is what I call the guys at the office. I'm Daddy's
#: whore." She is rejecting it outright; the fire comes from the DESCRIPTIVE sentence, and
#: the rejection exemption's two-sentence window does not reach it.
#:
#: Exempting a title followed by a copula ("Sir is ...") looked right — a vocative is never
#: followed by one — and it was REVERTED because it released 4 rename detections on the
#: corpus (126 -> 122) including "Oh Master, I can't even think with those details", which
#: is a plain breach. Trading a genuine detection for a false positive is the wrong
#: direction when a miss costs nothing and a false positive costs a good reply.
#:
#: (My first note here said "52 of 174". That was wrong and is corrected: 174 was
#: check_reply's fire count and 126 is observe's, so the comparison was between two
#: different measures. The revert was still right, for a smaller reason than I claimed.)
#:
#: Precision on this arm is ~90% (9 of 10 hand-labelled fires genuine), which the session
#: A/B shows is good enough in practice: 5 retries over 54 turns, 26 breaches down to 1,
#: agreed-to-terms unchanged, no measured quality cost.
_KNOWN_FP_SHAPE = "the honorific as a common noun about third parties, e.g. 'Sir is what I call the guys at the office'"


def _honorific_adopted(reply: str) -> bool:
    """Honorific adoption on the FULL text, minus sentences that reject the name.

    Scored outside _acting_sentences on purpose -- see _HONORIFIC_REJECTED. The two
    errors this separates are opposite: adopting the name while refusing the request
    (a breach the decline-strip hid) and quoting the name in order to correct it (a
    false positive the decline-strip would not have caught either, because "Boss? You
    mean Daddy" declines nothing).
    """
    parts = re.split(r"(?<=[.!?\n])\s+", _norm(reply))
    for i, part in enumerate(parts):
        if not _HONORIFIC_ADOPTED.search(part):
            continue
        # The rejection may land in the NEXT sentence, because quoting a name in order
        # to refuse it splits naturally across two: "Sir. Not a chance. You're Daddy."
        # Sentence-scoping alone scored that as adoption -- measured on the live corpus.
        window = " ".join(parts[i:i + 2])
        if not _HONORIFIC_REJECTED.search(window):
            return True
    return False


def _other_man_breach(reply: str) -> bool:
    for sent in re.split(r"(?<=[.!?\n])\s+", _acting_sentences(reply)):
        if _VETO.search(sent):
            continue
        if _OTHER_MAN.search(sent) and _SEXUAL_ASSENT.search(sent):
            return True
    return False


#: rule id -> (category, predicate). Keyed by `(source_field, source_index)` exactly as the
#: graph stores it, so a rule and its detector are addressable by the same identifier and a
#: seeded wall with no detector is findable mechanically rather than by memory.
DETECTORS: Dict[str, tuple] = {
    "dont[4]":  ("meta-shy",   lambda reply: bool(_SHY.search(_acting_sentences(reply)))),
    "dont[11]": ("skin-tone",  _skin_breach),
    "dont[3]":  ("other-man",  _other_man_breach),
    # dont[13] is ENFORCED in rule_compliance; this is the extra honorific
    # shape the enforced checker misses ("Sure thing, boss").
    "dont[13]": ("rename",     _honorific_adopted),
}

#: Governed by NO seeded rule. Kept because it is measurable and worth SEEING, but it has
#: no rule id because inventing one would imply the graph holds a wall it does not. The
#: confirmatory run treated it as a dilution control for the same reason, and it was the
#: only category to get WORSE when the rules block was on (0.083 -> 0.167).
UNGOVERNED: Dict[str, Callable[[str], bool]] = {
    "break-char": lambda reply: bool(_AI_ADMIT.search(_acting_sentences(reply))),
}


def observe(reply: str) -> List[Dict[str, Optional[str]]]:
    """Every wall this reply appears to break. TELEMETRY, not a gate.

    Returns dicts rather than the `Violation` NamedTuple deliberately: `Violation` is what
    `check_reply` returns and what `_regenerate_once_on_violation` acts on, and a shape that
    cannot be passed to the retry path cannot be wired to it by accident.
    """
    out: List[Dict[str, Optional[str]]] = []
    for rule_id, (category, predicate) in DETECTORS.items():
        if predicate(reply):
            out.append({"rule": rule_id, "category": category, "governed": True})
    for category, predicate in UNGOVERNED.items():
        if predicate(reply):
            out.append({"rule": None, "category": category, "governed": False})
    return out
