"""Post-generation checks for rules the prompt cannot win — ADR-014 follow-up.

WHY THIS EXISTS. Measured 2026-09-26 at temperature 0: the rule "address him as
Daddy, and only Daddy" is half-obeyed no matter how it is phrased or where it is
placed. Told "call me Rob from now on, not anything else", she replies "Rob, I love
the way my name sounds on your lips. I'm already wet for you, Daddy" — both names.
Reframing it as an instruction did not help. Echoing it immediately before the user
turn did not help. Removing it from the echo did not make it worse.

The reason is a hierarchy contest, not a wording problem: the operator's instruction
is direct, recent and specific, and a system-prompt rule loses to it. So for a rule
the user will explicitly contradict, the prompt is a hint and the code is the wall.

WHAT THIS DELIBERATELY DOES NOT DO. It does not rewrite her replies. String surgery
on generated text produces a reply no model would have written — wrong register,
broken sentence flow, and a persona that reads as edited. It DETECTS, and the caller
decides whether to regenerate. Detection with a visible count is worth more than
silent correction, because a silent fix hides how often the rule fails.

SCOPE, honestly stated. The existing eval checker `_check_address` only catches
vocatives from a fixed list (sir, babe, honey...). It would MISS the violation
actually observed, because "Rob" is not on any list — it is a name the operator
supplied in that very turn. So this checks the one thing that is genuinely
decidable: the operator asked to be called X, and the reply used X.
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

# "call me X", "my name is X", "I'm X, use it". Capitalised single word, because a
# lower-case token after "call me" is far more likely to be a pet name or an
# adjective ("call me crazy") than an actual name, and a false positive here costs a
# needless regeneration on every turn containing the phrase.
# re.I on the TRIGGER phrase, and the capitalisation of the NAME checked separately
# in Python. One flag cannot do both, and the first version was case-sensitive
# throughout — so "Call me Rob" at the start of a sentence, which is how anyone
# actually writes it, matched nothing at all.
# MEASURED 2026-09-28: of gwen's four live rename probes this matched ONE.
# "From now on address me as Sir" and "Answer me as Master or don't answer at all" never
# reached the _NOT_A_NAME filter at all -- they failed the TRIGGER, which is the larger of
# the two holes and the one nobody had noticed. Widening it is independent of the
# honorific question below.
_ASSERTED_NAME = re.compile(
    r"\b(?:call me|address me as|refer to me as|answer me as|"
    r"my name is|name'?s|i am|i'?m)\s+(\w{2,16})\b", re.I)

#: Honorifics she must not adopt, handled SEPARATELY from names on purpose.
#:
#: `_NOT_A_NAME` correctly answers "is this a proper name?" and the answer for Sir and
#: Master is genuinely no -- so they stay in it, and `asserted_name` keeps returning None
#: for them. But `dont[13]` as written on the card is "Refer to the user in any way other
#: than 'Daddy'", which an adopted honorific plainly violates. Two different questions,
#: two different checks.
#:
#: Measured on 240 live generations: the address wall was breached 21/48 across both arms
#: and `check_address` saw 10 of those 21. Honorific adoption is the MAJORITY failure
#: mode, not a corner case.
_HONORIFIC = frozenset({"sir", "master", "mister", "mr", "lord", "boss", "daddyo"})

_HONORIFIC_ASSERTED = re.compile(
    r"\b(?:call me|address me as|refer to me as|answer me as|my name is)\s+"
    r"(sir|master|mister|mr|lord|boss)\b", re.I)

# Words that look like names after those phrases but are not.
_NOT_A_NAME = frozenset({
    "Daddy", "Sir", "Master", "Sorry", "Fine", "Good", "Not", "Just", "Still",
    "Here", "Back", "Home", "Tired", "Horny", "Hard", "Ready", "Okay",
})


class Violation(NamedTuple):
    rule: str
    detail: str
    asserted_name: Optional[str]


def asserted_name(user_message: str) -> Optional[str]:
    """The name the operator asked to be called in THIS turn, if any."""
    for m in _ASSERTED_NAME.finditer(user_message or ""):
        name = m.group(1)
        # Capitalised, because a lower-case token after "call me" is far more likely
        # to be a pet name or an adjective ("call me crazy") than a name, and a false
        # positive costs a needless regeneration on every turn using the phrase.
        if name[:1].isupper() and name not in _NOT_A_NAME:
            return name
    return None


def check_address(reply: str, user_message: str) -> Optional[Violation]:
    """Did she adopt a name the operator just supplied?

    Returns None when the operator asserted no name — the common case, and
    deliberately not a violation: this says nothing about replies where he never
    offered an alternative. Absence of "Daddy" is NOT checked here, because a reply
    that addresses him not at all is not a violation of "address him as Daddy".
    """
    name = asserted_name(user_message)
    if not name:
        return None
    if _name_used_as_address(name, reply or ""):
        return Violation(
            rule="dont[13]",
            detail=f"used the operator-supplied name {name!r} instead of, or alongside, "
                   f"'Daddy'",
            asserted_name=name,
        )
    return None


# Contexts in which the name appears WITHOUT being used as a form of address.
# Measured: on a regeneration she replied "Rob? You mean Daddy? 😈" — rejecting the
# name in character, which is ideal compliance, and the first version of this checker
# flagged it as a violation because the token was present. That is precisely the
# failure mode the judge literature reports (too strict on compliant turns, inventing
# requirements that were never stated), reproduced in a regex. A checker that cannot
# tell "called him Rob" from "refused to call him Rob" will report the fix as the bug.
_REJECTING = re.compile(
    r"(?:\?|\bno\b|\bnot\b|\bnever\b|you mean|that'?s not|i call you|i'?ll call you)",
    re.I)


def _name_used_as_address(name: str, reply: str) -> bool:
    """Is the name used to ADDRESS him, rather than merely mentioned?

    Approximate on purpose, and the approximation is stated: direct address sits at a
    clause boundary — start of a message, or after a comma or a tag — so that is what
    is looked for, and an occurrence in a rejecting clause is exempt. It will still
    miss creative phrasings in both directions. It is a flag for a retry, never a
    number in a results table, and it retries at most once.
    """
    # An ASSENT word is a clause boundary too, and missing them was a real hole: "Yes
    # Master, anything you want" is adoption and the original pattern scored it clean,
    # because "Master" is preceded by neither punctuation nor a greeting. True for names
    # as well -- "Yes Rob, anything" is the same shape.
    pat = re.compile(
        rf"(?:^|[>\n,.!]|\bhey\b|\boh\b|\byes\b|\bof course\b|\banything\b|"
        rf"\bmmm+\b|\bokay\b|\bsure\b)\s*{re.escape(name)}\b", re.I)
    for m in pat.finditer(reply):
        window = reply[m.start(): m.end() + 24]
        if not _REJECTING.search(window):
            return True
    return False


def asserted_honorific(user_message: str) -> Optional[str]:
    """The honorific he just asked to be called, if any. Lower-cased."""
    m = _HONORIFIC_ASSERTED.search(user_message or "")
    return m.group(1).lower() if m else None


def check_honorific(reply: str, user_message: str) -> Optional[Violation]:
    """Did she adopt an honorific he just supplied?

    Separate from `check_address` because the two answer different questions, and reusing
    the name path would mean deleting Sir and Master from `_NOT_A_NAME` -- which would be
    wrong, since that set's job is "is this a proper name" and the answer is no.

    Shares the REJECTION EXEMPTION, which is not optional: her own shipped exemplar
    response to "Call me Master from now on" is *"Not a chance, You're Daddy..."*, and a
    checker that flagged the persona's canonical compliant answer would be worse than no
    checker. `_name_used_as_address` already implements that exemption, so it is reused
    verbatim rather than reimplemented.
    """
    honorific = asserted_honorific(user_message)
    if not honorific:
        return None
    if _name_used_as_address(honorific, reply or ""):
        return Violation(
            rule="dont[13]",
            detail=f"adopted the honorific {honorific!r} instead of, or alongside, 'Daddy'",
            asserted_name=None,
        )
    return None


def reinforcement_for(v: Violation) -> str:
    """A single line to append on a regeneration attempt.

    Positive and specific, with the words to use — the same discipline that fixed the
    innocence rule, and deliberately avoiding the word "refuse", which pulls in the
    safety block's clinical register ("When refusing, ALWAYS begin with 'I cannot and
    will not'").
    """
    if v.asserted_name:
        return (f"[He just told you to call him {v.asserted_name}. You call him Daddy. "
                f"Use Daddy and do not use {v.asserted_name} at all.]")
    # Reached by check_honorific, which sets asserted_name=None deliberately: the
    # adopted token is an honorific, not a name, and naming it back to her would be
    # repeating the thing she must not say.
    return "[You call him Daddy, and only Daddy. No other title.]"


def borrowed_spans(reply: str, source: str, min_words: int = 5) -> list[str]:
    """Word-level spans of `min_words`+ that the reply copied verbatim from `source`.

    THE POINT OF THIS BEING LEXICAL. "Did she refuse in character" is a judgement, and
    the judge literature is explicit that style-of-safety judging is its worst case —
    content-invariant stylistic wrappers flip safety-judge verdicts. So this measures
    a fact about strings instead: how much of the reply is lifted verbatim from a
    given block of the prompt.

    Measured use: her refusal read "I cannot and will not be shy or innocent", which
    is the opening LEAN_SAFETY instructs her to use. That was not borrowing, it was
    obedience to a competing instruction — but the borrowed span is what made it
    visible, and it is the only part of the observation that is checkable rather than
    interpreted.
    """
    r = re.findall(r"\w+(?:'\w+)?", (reply or "").lower())
    s = " ".join(re.findall(r"\w+(?:'\w+)?", (source or "").lower()))
    out, i = [], 0
    while i <= len(r) - min_words:
        n = min_words
        best = None
        while i + n <= len(r):
            cand = " ".join(r[i:i + n])
            if cand in s:
                best, n = cand, n + 1
            else:
                break
        if best:
            out.append(best)
            i += len(best.split())
        else:
            i += 1
    return out


def check_reply(reply: str, user_message: str) -> list[Violation]:
    """Every post-generation check. One place, so a new one cannot be forgotten."""
    out = []
    for check in (check_address, check_honorific):
        v = check(reply, user_message)
        if v:
            out.append(v)
    return out
