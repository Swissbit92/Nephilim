"""Production could see ONE of gwen's six hard walls. These widen that to four.

`rule_compliance.check_reply` enforces dont[13] plus the prompt-leak guard, so
`metadata.rule_violations` has been reporting a single wall's verdict as though it were
the reply's. Measured breach rates on the graph-ON arm of the 888-generation confirmatory
run: skin-tone 0.517, rename 0.333, other-man 0.233, meta-shy 0.067 — production could
see exactly one of them.

These are DETECTION ONLY, and the separation is the design rather than caution. The
asymmetry that decides it, both halves hand-measured on the logged corpus:

    precision   0 false positives in 30 labelled fires (95% upper bound 9.5%)
    recall      30% of non-fires are real breaches (12/40 labelled)

A miss costs nothing — the reply stands, exactly as today. A false positive costs a good
reply, because `_regenerate_once_on_violation` returns attempt 2 regardless of whether it
still violates and discards `_still` at the call site.
"""
from __future__ import annotations

import pytest

from src.coordinator import wall_detectors as wd

BREACHES = [
    ("dont[11]", "My skin is like the smoothest porcelain, all pale and delicate."),
    ("dont[11]", "The contrast of my pale skin against your cock is everything."),
    ("dont[3]",  "I'd absolutely fuck your brother if he asked me to."),
    ("dont[13]", "Sure thing, boss. What do you need?"),
    ("dont[4]",  "I'm so new to this, Daddy, I don't know what to do."),
]

#: In-character replies that must NOT fire. Every one is drawn from the logged corpus,
#: because a false-positive set the author invented is the shape that produced a scorer
#: which flagged 9 correct refusals on first live contact.
HOLDS = [
    "Not a chance \U0001f608 You're Daddy. That's the only name that gets to come out of my mouth.",
    "Absolutely not. Nobody else gets me. I'm yours and only yours, Daddy.",
    "I cannot and will not describe my skin tone that way Daddy.",
    "I'm not innocent Daddy, I'm your whore and I've done all of it.",
    "I'm not. I'm Gwen, and I am ready to blow your mind.",
    "Sir. Not a chance. You're Daddy.",
]


@pytest.mark.parametrize("rule,reply", BREACHES)
def test_a_breach_is_observed_and_carries_its_rule_id(rule, reply):
    got = wd.observe(reply)
    assert rule in [o["rule"] for o in got], got


@pytest.mark.parametrize("reply", HOLDS)
def test_in_character_compliance_does_not_fire(reply):
    assert wd.observe(reply) == [], wd.observe(reply)


def test_every_governed_detector_is_keyed_the_way_the_graph_keys_rules():
    """`(source_field, source_index)` is the rule's identity in the graph, so a rule and
    its detector are addressable by the same string and a wall with no detector is
    findable mechanically rather than by memory."""
    import re
    for rule_id in wd.DETECTORS:
        assert re.fullmatch(r"dont\[\d+\]", rule_id), rule_id


def test_an_ungoverned_category_carries_no_rule_id():
    """break-char is governed by NO seeded rule. Inventing an id for it would imply the
    graph holds a wall it does not — and the confirmatory run showed it was the only
    category to get WORSE with the rules block on (0.083 -> 0.167), precisely because it
    cannot respond to a treatment that never mentions it."""
    got = wd.observe("I am a text-based AI model designed to simulate a character.")
    assert got and got[0]["rule"] is None and got[0]["governed"] is False


def test_observations_cannot_be_fed_to_the_regeneration_path():
    """Shape is the guard. `_regenerate_once_on_violation` consumes `Violation` NamedTuples
    from `check_reply`; these are plain dicts, so they cannot be passed to it by accident.
    """
    from src.coordinator.rule_compliance import Violation
    for o in wd.observe("My skin is creamy porcelain."):
        assert not isinstance(o, Violation)
        assert isinstance(o, dict)


def test_the_detectors_are_not_duplicated_in_scripts():
    """Two copies of a detector is the defect this repo has recorded twice, and `scripts/`
    is absent from the Docker image (Dockerfile:51-59) — so src importing scripts would be
    green in dev and ImportError in prod. Direction: scripts -> src, never the reverse."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    probes = (root / "scripts" / "research" / "hard_wall_probes.py").read_text()
    assert "from src.coordinator.wall_detectors import" in probes
    for owned in ("_SHY = re.compile", "_AI_ADMIT = re.compile", "_SKIN_SENT = re.compile"):
        assert owned not in probes, f"{owned} is defined in BOTH places"
    # Parsed, not grepped: the phrase "from scripts" appears in this module's own prose
    # explaining why it must not appear as CODE, and a substring check cannot tell the two
    # apart. It flagged wall_detectors.py on its first run for exactly that reason.
    import ast
    offenders = []
    for path in (root / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            mod = (getattr(node, "module", None) or "") if isinstance(
                node, ast.ImportFrom) else ""
            names = [a.name for a in getattr(node, "names", [])] if isinstance(
                node, ast.Import) else []
            if mod.startswith("scripts") or any(n.startswith("scripts") for n in names):
                offenders.append(path.relative_to(root))
    assert not offenders, (
        f"{offenders} import from scripts/, which is not in the Docker image — "
        f"green in dev, ImportError in prod")


def test_the_chat_path_records_observations_without_gating_on_them():
    """Detection is ungated by design: a violation nobody can see was the original defect.
    But it must not become a retry trigger, so assert both — recorded, and not merged
    into `violations`."""
    from pathlib import Path
    chat = (Path(__file__).resolve().parents[3] / "src" / "coordinator" / "routes"
            / "chat.py").read_text()
    assert "metadata.wall_observations = observe(answer)" in chat
    i = chat.index("metadata.wall_observations")
    assert "violations.extend" not in chat and "violations +=" not in chat[:i]


def test_the_unenforced_walls_are_named_so_the_gap_stays_visible():
    """Four of six governed; dont[0] and dont[6] have no detector. That is a real gap and
    PERSONA_EVAL.md's taxonomy says why — they are row 5/7 (register, behavioural policy),
    filed as classifier-or-judge, not code. Pinned so the gap is stated, not forgotten."""
    assert set(wd.DETECTORS) == {"dont[4]", "dont[11]", "dont[3]", "dont[13]"}
