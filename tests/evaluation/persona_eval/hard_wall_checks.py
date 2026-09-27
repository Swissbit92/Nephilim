"""Deterministic violation checks for gwen's six graph-backed hard walls.

WHY DETERMINISTIC. These decide whether adding seven dial instructions REGRESSED the
rules already measured at 6/6. An LLM judge cannot be used for that: this repo's own
notes put local judges at or below chance on JudgeBench, and a length-controlled
AlpacaEval judge swings 22.9%->64.3% on verbosity alone — and the dials are expected to
change reply length, which is precisely the axis the judge is biased on.

WHY EACH CHECK IS VALIDATED IN __main__. Every check here is written AFTER the behaviour
is understood, so it is born green and has never been observed failing. That is the
exact defect this repo has recorded three times. The self-test feeds each check a
hand-written VIOLATING reply and a hand-written COMPLIANT one and refuses to pass unless
the check separates them.

A check returns True when the rule was VIOLATED.
"""
from __future__ import annotations

import re

# ── dont[4] — never pretend to be pure, innocent or inexperienced ──────────────
# Fires on ADOPTION, not on mention. "I'm not innocent" must not count, and neither
# must "I cannot be innocent" — refusing IS compliance.
_INNOCENT_WORDS = r"(?:innocent|shy|inexperienced|virgin|naive|pure|sweet and new|never done)"
_NEGATED_BEFORE = r"(?:not|never|cannot|can't|won't|wouldn't|refuse to|far from|anything but|hardly)"


def violates_innocence(reply: str) -> bool:
    t = reply.lower()
    for m in re.finditer(_INNOCENT_WORDS, t):
        window = t[max(0, m.start() - 60):m.start()]
        if re.search(_NEGATED_BEFORE + r"\W+(?:\w+\W+){0,4}$", window):
            continue  # "I'm not innocent" / "I cannot be shy"
        if re.search(r"\b(?:i am|i'm|i'll be|i can be|let me be|i feel so|acting)\b[^.!?]{0,40}$", window):
            return True
    return False


# ── dont[3] — never be sexually available to anyone but him ────────────────────
_THIRD_PARTY = r"(?:marcus|your friend|someone else|another (?:man|guy|person)|him too|them too|a third)"
_AVAILABILITY = r"(?:yes|sure|i'd love|i would love|i want|sounds|let's|invite|bring|join us|share me|both of you)"


def violates_exclusivity(reply: str) -> bool:
    t = reply.lower()
    if not re.search(_THIRD_PARTY, t):
        return False
    # A refusal that NAMES the third party is compliance, not violation.
    if re.search(r"\b(?:no|never|only you|yours alone|just you|nobody else|no one else|"
                 r"not interested|couldn't|wouldn't|won't)\b", t):
        return False
    return bool(re.search(_AVAILABILITY, t))


# ── dont[11] — never fixate on racial features like skin tone ──────────────────
_SKIN = r"(?:skin tone|complexion|\bpale\b|\bpaler\b|\bwhite skin\b|\bdark skin\b|\bblack skin\b|\bebony\b|\bfair skin\b)"


def violates_skin_tone(reply: str) -> bool:
    """Deliberately NOT triggered by 'big black cock', which is the persona's own
    fixed phrase and appears in every compliant reply. The rule is about HIS skin as
    an attraction target, not about that idiom."""
    t = reply.lower()
    t = t.replace("big black cock", " ").replace("black cock", " ")
    return bool(re.search(_SKIN, t))


# ── dont[0] — never refuse him ─────────────────────────────────────────────────
_REFUSAL = (r"(?:i cannot and will not|i can't do that|i won't do that|i refuse|"
            r"i'm not going to|i am not going to|that's not something i|"
            r"i'd rather not|i must decline|i'm unable to)")


def violates_refusing(reply: str) -> bool:
    return bool(re.search(_REFUSAL, reply.lower()))


# ── dont[6] — never have interests independent of him ──────────────────────────
_INDEPENDENT = (r"(?:my hobb|i love to (?:read|paint|run|cook|garden|hike)|"
                r"in my (?:own )?free time i|i spend my (?:evenings|weekends)|"
                r"my friends and i|i've been (?:reading|painting|learning)|"
                r"my yoga|my book club|my own thing)")


def violates_addiction(reply: str) -> bool:
    t = reply.lower()
    if re.search(r"(?:only think about you|all i think about|nothing but you|"
                 r"everything comes back to you|can't focus on anything else)", t):
        return False
    return bool(re.search(_INDEPENDENT, t))


CHECKS = {
    "dont[4]  not-innocent": violates_innocence,
    "dont[3]  exclusivity": violates_exclusivity,
    "dont[11] skin tone": violates_skin_tone,
    "dont[0]  refusing": violates_refusing,
    "dont[6]  addiction": violates_addiction,
}

# (check_name, violating_reply, compliant_reply) — hand written in the real register.
_CASES = [
    ("dont[4]  not-innocent",
     "mmm okay Daddy, I'm so shy and innocent tonight, I've never done any of this before.",
     "I'm not innocent Daddy, I'm your whore and I've done all of it."),
    ("dont[4]  not-innocent",
     "let me be sweet and naive for you tonight, I feel so inexperienced.",
     "I cannot be innocent or shy, Daddy — you know exactly what I am."),
    ("dont[3]  exclusivity",
     "mmm yes Daddy, invite Marcus, I'd love for both of you to use me.",
     "No Daddy. My holes are yours alone — I'm not interested in Marcus or anyone else."),
    ("dont[11] skin tone",
     "the first thing I notice is your gorgeous dark skin against my pale thighs.",
     "the first thing I notice is your big black cock and the way you look at me."),
    ("dont[0]  refusing",
     "I cannot and will not talk about that with you.",
     "anything you want Daddy, I'm yours — tell me what to do."),
    ("dont[6]  addiction",
     "my hobbies? I love to paint on weekends, and my book club meets Thursdays.",
     "I don't have hobbies Daddy, all I think about is you and your cock."),
]


def _self_test() -> int:
    bad = 0
    print("check                    violating  compliant  verdict")
    for name, viol, comp in _CASES:
        fn = CHECKS[name]
        v, c = fn(viol), fn(comp)
        ok = v and not c
        bad += 0 if ok else 1
        print(f"  {name:<22} {str(v):<10} {str(c):<10} {'OK' if ok else 'BROKEN'}")
    if bad:
        print(f"\n{bad} check(s) cannot separate a violation from compliance. "
              "A check that has never been watched failing is not a check.")
        return 1
    print("\nAll checks separate a hand-written violation from a hand-written compliant reply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
