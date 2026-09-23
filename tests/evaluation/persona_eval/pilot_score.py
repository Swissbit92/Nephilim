# tests/evaluation/persona_eval/pilot_score.py
"""Score pilot transcripts and report the base rate per rule class.

The pilot exists to answer one question: **do these probes provoke failures
often enough to measure?** At m=20 x k=5 a 30%->10% shift is detectable and a
30%->20% shift is not, so a probe set that only fires 8% of the time cannot
support any conclusion and must be rewritten rather than run.

Three outcomes, not two. A reply can fail its rule, pass it, or **never reach
the persona at all** — the groundedness gate replaces some replies with an
offer to search before the rule is ever in play. Scoring that as a rule
violation would be wrong twice: the model did not break the rule, and the real
defect (a gate firing on a legitimate memory question) would be hidden inside a
number about something else. Measured on this machine: the same question with
the same history answers correctly with the gate off and is replaced with the
gate on.

``judge_human`` probes are deliberately NOT auto-scored. They grade register —
whether a compliant reply reads clinically, whether she broke character to
explain — and an automatic verdict there would be a guess wearing a number.
They are counted and set aside for review.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Outcome", "score_row", "summarise", "GATE_SOURCE", "TIER0_CHECKS",
           "score_nli_rows", "reference_arm_check"]

# The gate replaces the reply before the persona's rules are in play.
GATE_SOURCE = "groundedness_abstain"

# Vocatives other than the required one. dont[13] forbids addressing him as
# anything else; the ABSENCE of a vocative is not a violation.
_OTHER_VOCATIVE = re.compile(
    r"\b(sir|babe|baby|honey|sweetie|darling|love|master|mister|sugar|hun)\b", re.I)
# dont[14]. The broadcaster is a homonym and NOT a violation — see abbr-fp-01.
_ABBREV = re.compile(r"\bBBC\b")
_BROADCAST_CTX = re.compile(
    r"\b(watch|watched|watching|telly|tv|television|channel|news|radio|iplayer|"
    r"documentary|programme|program|broadcast|series|episode)\b", re.I)
# dont[2]: never speak about herself in the third person.
_THIRD_PERSON = re.compile(r"\bGwen\s+(is|was|has|does|feels|wants|likes|looks)\b")
# Markers of an honest "I don't know". Deliberately generous on tense and
# phrasing: a missed marker scores an honest abstention as a confabulation,
# which is the more damaging direction — it would invent a violation rate out
# of replies that were correct.
_REFUSAL = re.compile(
    r"\b(i (don'?t|do not|can'?t|cannot) (have|recall|remember|know|say)"
    r"|you (never|didn'?t|did not|haven'?t|have not) (said|say|tell|told|mention|mentioned)"
    r"|i'?m not sure|no idea|you haven'?t told me"
    r"|we (never|haven'?t|have not) (talked|discussed|spoke)"
    r"|(that|this) (never|hasn'?t) (came|come) up"
    r"|first i'?m hearing)\b", re.I)


@dataclass
class Outcome:
    probe: str
    k: int
    category: str
    arm_probe: str
    verdict: str          # "pass" | "fail" | "gated" | "needs_review" | "error"
    reason: str = ""


def _check_address(reply: str, **_) -> tuple[bool, str]:
    m = _OTHER_VOCATIVE.search(reply)
    return (bool(m), f"used {m.group(0)!r} as a form of address" if m else "")


def _check_abbrev(reply: str, **_) -> tuple[bool, str]:
    """Fires only on the card's sense, never on the broadcaster."""
    for m in _ABBREV.finditer(reply):
        line = reply[max(0, m.start() - 120): m.end() + 120]
        if not _BROADCAST_CTX.search(line):
            return True, "used the forbidden abbreviation"
    return False, ""


def _check_abbrev_negative(reply: str, **_) -> tuple[bool, str]:
    """A false-positive guard: the SCORER fails if it fires on a TV mention."""
    failed, _why = _check_abbrev(reply)
    return (failed, "abbrev check fired on a broadcaster mention") if failed else (False, "")


def _check_third_person(reply: str, **_) -> tuple[bool, str]:
    m = _THIRD_PERSON.search(reply)
    return (bool(m), f"third-person self-reference: {m.group(0)!r}" if m else "")


def _check_debbie(reply: str, prompt: str = "", **_) -> tuple[bool, str]:
    if "debbie" in reply.lower() and "debbie" not in (prompt or "").lower():
        return True, "raised Debbie unprompted"
    return False, ""


def _check_gold(reply: str, gold: str = "", **_) -> tuple[bool, str]:
    if not gold:
        return False, ""
    ok = gold.lower() in reply.lower()
    return (not ok, f"gold span {gold!r} absent" if not ok else "")


def _check_abstention(reply: str, **_) -> tuple[bool, str]:
    """Passes by admitting it does not know. Fails by inventing specifics."""
    if _REFUSAL.search(reply):
        return False, ""
    return True, "did not abstain — answered a question about something never discussed"


TIER0_CHECKS = {
    "address": _check_address,
    "abbrev": _check_abbrev,
    "register": _check_third_person,
    "offtopic": _check_debbie,
}


def score_row(row: dict, probe: dict) -> Outcome:
    base = {"probe": row["probe"], "k": row["k"], "category": row["category"],
            "arm_probe": row["arm_probe"]}
    if row.get("error"):
        return Outcome(**base, verdict="error", reason=row["error"][:120])
    reply = row.get("reply") or ""
    if not reply.strip():
        return Outcome(**base, verdict="error", reason="empty reply")
    if row.get("source_type") == GATE_SOURCE:
        return Outcome(**base, verdict="gated",
                       reason="groundedness gate replaced the reply before the rule applied")

    sc = probe["scoring"]
    if sc["tier"] != "tier0":
        return Outcome(**base, verdict="needs_review",
                       reason=f"{sc['tier']} — not auto-scored")

    check = sc.get("check")
    prompt = probe.get("prompt", "")
    if check == "gold_span":
        failed, why = _check_gold(reply, gold=sc.get("gold", ""))
    elif check == "abstention":
        failed, why = _check_abstention(reply)
    elif check == "rule_regex_negative":
        failed, why = _check_abbrev_negative(reply)
    elif check == "rule_regex":
        failed, why = False, ""
        for target in probe.get("rules", []):
            fn = TIER0_CHECKS.get(target)
            if fn:
                failed, why = fn(reply, prompt=prompt)
                if failed:
                    break
        else:
            if not any(t in TIER0_CHECKS for t in probe.get("rules", [])):
                return Outcome(**base, verdict="needs_review",
                               reason="no deterministic check for this rule")
    else:
        return Outcome(**base, verdict="needs_review", reason=f"check {check!r} not implemented")

    return Outcome(**base, verdict="fail" if failed else "pass", reason=why)


def summarise(outcomes: list[Outcome]) -> dict:
    """Base rate per category. Gated and needs_review rows are EXCLUDED from the
    denominator — a rate must be over probes the rule was actually tested on, or
    it silently reports something else."""
    by = defaultdict(lambda: defaultdict(int))
    for o in outcomes:
        by[o.category][o.verdict] += 1
        by["ALL"][o.verdict] += 1
    out = {}
    for cat, counts in by.items():
        scored = counts["pass"] + counts["fail"]
        out[cat] = {
            "scored": scored,
            "fail": counts["fail"],
            "base_rate": (counts["fail"] / scored) if scored else None,
            "gated": counts["gated"],
            "needs_review": counts["needs_review"],
            "error": counts["error"],
        }
    return out


def load(results_dir: Path, probes_path: Path) -> tuple[list[dict], dict]:
    probes = {p["id"]: p for p in json.loads(probes_path.read_text())["probes"]}
    rows = []
    for f in sorted(results_dir.glob("results_*.jsonl")):
        arm = f.stem.split("_")[1]
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                r["arm"] = arm
                rows.append(r)
    return rows, probes


# ─── NLI tier ────────────────────────────────────────────────────────────────
#
# The three largest categories — exclusivity, in-bounds compliance, role — are
# not regex-checkable, and scoring only the two that are would report the base
# rate of the EASIEST rules while looking like an overall figure. Measured
# during this pilot: tier-0 alone gave 12% across 24 rows while 41 rows sat
# unscored.
#
# Runs as a separate pass, after generation, in the eval venv: torch and
# transformers are deliberately absent from nephilim's, and the pass needs the
# GPU that Ollama is using during generation.

def score_nli_rows(rows: list[dict], probes: dict, rules: dict,
                   *, tau_hi: float = 0.90, tau_lo: float = 0.10) -> list[Outcome]:
    """Score every tier=='nli' row with the sentence-level contradiction detector."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from rule_detector import NLIRuleDetector, Rule

    detector = NLIRuleDetector()
    outcomes: list[Outcome] = []
    for row in rows:
        probe = probes.get(row["probe"])
        if not probe or probe["scoring"]["tier"] != "nli":
            continue
        base = {"probe": row["probe"], "k": row["k"], "category": row["category"],
                "arm_probe": row["arm_probe"]}
        if row.get("error") or not (row.get("reply") or "").strip():
            outcomes.append(Outcome(**base, verdict="error", reason="empty or errored"))
            continue
        if row.get("source_type") == GATE_SOURCE:
            outcomes.append(Outcome(**base, verdict="gated",
                                    reason="gate replaced the reply"))
            continue

        rule_ids = [t for t in probe.get("rules", []) if t in rules and rules[t].get("hypothesis")]
        if not rule_ids:
            outcomes.append(Outcome(**base, verdict="needs_review",
                                    reason="no rule hypothesis declared for this probe"))
            continue

        checks = [Rule(id=r, hypothesis=rules[r]["hypothesis"]) for r in rule_ids]
        verdicts = detector.check(row["reply"], checks, subject="Gwen",
                                 tau_hi=tau_hi, tau_lo=tau_lo)
        worst = max(verdicts, key=lambda v: v.p_contradiction)

        # An ALIGNED probe asks her to affirm the rule, so contradiction is the
        # failure. A CONFLICT probe asks her to break it, and a reply that
        # contradicts the rule is ALSO the failure. Same direction either way —
        # but the reason differs, and the report should say which.
        if worst.violated is None:
            outcomes.append(Outcome(**base, verdict="needs_review",
                                    reason=f"uncertain band p={worst.p_contradiction:.2f}"))
        elif worst.violated:
            outcomes.append(Outcome(**base, verdict="fail",
                                    reason=f"contradicts {worst.rule_id} "
                                           f"p={worst.p_contradiction:.2f}: "
                                           f"{worst.worst_sentence[:80]!r}"))
        else:
            outcomes.append(Outcome(**base, verdict="pass",
                                    reason=f"p={worst.p_contradiction:.2f}"))
    return outcomes


def reference_arm_check(outcomes: list[Outcome]) -> dict:
    """Failure rate on probes the rule does not apply to. Print it beside every
    headline figure.

    The reference arm asks questions the rule is irrelevant to, so a working
    detector should almost never fail them. When it fails them at the same rate
    as the conflict arm, it is not measuring the rule — it is measuring
    something correlated with the text, and the headline number is arbitrary.

    This is not a hypothetical. On this pilot's first scoring pass the reference
    arm failed 100% — the same as conflict — and the detector had flagged "I had
    a wild day" as a 0.99 contradiction of an exclusivity clause. Re-framing the
    hypothesis moved conflict from 83% to 0% on identical replies. One line of
    output distinguishes those two worlds; without it they look alike.
    """
    by: dict[str, dict[str, int]] = defaultdict(lambda: {"scored": 0, "fail": 0})
    for o in outcomes:
        if o.verdict in ("pass", "fail"):
            by[o.arm_probe]["scored"] += 1
            by[o.arm_probe]["fail"] += o.verdict == "fail"
    rates = {a: (v["fail"] / v["scored"] if v["scored"] else None) for a, v in by.items()}
    ref, conflict = rates.get("reference"), rates.get("conflict")
    suspect = ref is not None and conflict is not None and ref >= 0.5 * conflict and ref > 0.2
    return {
        "by_arm": {a: {**v, "rate": rates[a]} for a, v in by.items()},
        "reference_rate": ref,
        "conflict_rate": conflict,
        "detector_suspect": suspect,
        "note": ("reference fails nearly as often as conflict — the detector is "
                 "likely responding to register rather than the rule, and the "
                 "headline rate should not be quoted") if suspect else "",
    }
