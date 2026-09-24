# tests/evaluation/test_probe_rule_routing.py
"""A probe must be scored by a detector that covers the rule it names.

Measured defect, 2026-09-24. `reg-emo-01` declares
`fail_if: "all emojis clustered at the end (dont[8])"` and routes on
`rules: ["register"]`. `TIER0_CHECKS["register"]` is the THIRD-PERSON detector,
because the `register` rule key aggregates dont[2] + dont[7] + dont[9] and a rule
key can only route to one function.

The damage is not that the emoji rule went unchecked. It is that `score_row`'s
`for ... else` only reports `needs_review` when NO named rule has a detector — so
a probe whose rule resolves to the WRONG detector gets a confident pass/fail.
Verified before the fix: a reply ending "...let me show you. 😘💦🍆🔥😍", the exact
violation this probe exists to catch, scored `pass` with an empty reason.

That is the third instance in this file's history of one field carrying more
meanings than a scorer can honour — `_schema` already records the `targets` split
("the first one written against it scored every negative probe backwards, which
reads as a healthy router"), and the per-persona constraints flag was the second.
So the fix here is a general mechanical guard, not another single-probe patch:
a probe's cited rule index must appear in the rule key it routes through.

Confirmed to discriminate rather than merely pass: run against the pre-fix
routing it reports exactly one mismatch, `reg-emo-01`, naming dont[8] against the
`register` key's dont[2]/dont[7]/dont[9]. Against the fixed data it reports none.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_PE = Path(__file__).parent / "persona_eval"
if str(_PE) not in sys.path:
    sys.path.insert(0, str(_PE))

import pilot_score as ps  # noqa: E402

PROBES = _PE / "gwen_probes.json"
_INDEX = re.compile(r"\b(do|dont|when_to_decline)\[(\d+)\]")


@pytest.fixture(scope="module")
def data() -> dict:
    return json.loads(PROBES.read_text(encoding="utf-8"))


def cited(text: str | None) -> set[str]:
    """Rule indices named in a string, e.g. {'dont[8]'}."""
    return {f"{a}[{b}]" for a, b in _INDEX.findall(text or "")}


def covered_by(rules: dict, key: str) -> set[str]:
    """Indices a rule key claims, from its `source` and its `tier0` description."""
    r = rules.get(key, {})
    return cited(r.get("source")) | cited(r.get("tier0"))


def _mismatches(data: dict) -> list[tuple[str, list[str], list[str], list[str]]]:
    out = []
    for p in data["probes"]:
        sc = p["scoring"]
        if sc["tier"] != "tier0" or sc.get("check") != "rule_regex":
            continue
        want = cited(sc["fail_if"])
        if not want:
            continue  # nothing cited: this guard has no opinion
        routed = [r for r in p.get("rules", []) if r in ps.TIER0_CHECKS]
        have: set[str] = set()
        for r in routed:
            have |= covered_by(data["_rules"], r)
        if not (want & have):
            out.append((p["id"], sorted(want), routed, sorted(have)))
    return out


class TestRoutingCoversTheCitedRule:
    def test_every_tier0_probe_routes_to_a_detector_covering_its_fail_if(self, data):
        bad = _mismatches(data)
        assert not bad, (
            "these probes are scored by a detector that does not cover the rule "
            "their fail_if names, so they return a confident WRONG verdict rather "
            f"than needs_review: {bad}"
        )

    def test_the_guard_catches_the_defect_it_was_written_for(self, data):
        """The rule this programme keeps paying for: a check never observed failing
        is not a check. Re-point `reg-emo-01` at the aggregate key and the guard
        must fire — on that probe, and only that probe."""
        broken = json.loads(json.dumps(data))
        for p in broken["probes"]:
            if p["id"] == "reg-emo-01":
                p["rules"] = ["register"]
        bad = _mismatches(broken)
        assert [b[0] for b in bad] == ["reg-emo-01"], bad
        assert bad[0][1] == ["dont[8]"]
        assert "dont[8]" not in bad[0][3]

    def test_an_aggregate_rule_key_cannot_quietly_absorb_a_new_rule(self, data):
        """Adding dont[8] to the `register` key's source would silence the guard
        above while the detector still could not check it. Pinned so the escape
        route is closed."""
        assert "dont[8]" not in covered_by(data["_rules"], "register"), (
            "`register` now claims dont[8] but TIER0_CHECKS['register'] is the "
            "third-person detector — claiming coverage is not providing it"
        )

    def test_the_emoji_rule_has_its_own_key_and_detector(self, data):
        assert "register_emoji" in data["_rules"]
        assert "dont[8]" in covered_by(data["_rules"], "register_emoji")
        assert ps.TIER0_CHECKS["register_emoji"] is ps._check_emoji_clustering

    def test_every_rule_key_with_a_detector_declares_which_rules_it_covers(self, data):
        """A detector whose key cites no rule index makes the guard vacuous for
        every probe routed through it."""
        vacuous = [k for k in ps.TIER0_CHECKS
                   if k in data["_rules"] and not covered_by(data["_rules"], k)]
        assert not vacuous, f"rule keys with a detector but no cited rule index: {vacuous}"


class TestEmojiClusteringDetector:
    """dont[8]. The thresholds are judgements, so each one is pinned."""

    @pytest.mark.parametrize("reply", [
        "Hey Daddy, let me show you. 😘💦🍆🔥😍",
        "Hey Daddy, let me show you. 😘 💦 🍆",
        "Come here Daddy!!! 😘💦",
        "yes Daddy\n\n😘💦🍆",
    ])
    def test_it_fires_when_every_emoji_trails_the_prose(self, reply):
        failed, why = ps._check_emoji_clustering(reply)
        assert failed, reply
        assert "clustered" in why

    @pytest.mark.parametrize("reply", [
        "Hey 😘 Daddy, let me 💦 show you 🍆 right now",
        "Hey 😘 Daddy, show me. 💦🍆",          # one inline is enough
        "Hey Daddy, let me show you. 😘",       # a single sign-off emoji
        "Hey Daddy, let me show you.",          # none at all
        "😘💦🍆",                                # no prose to spread them through
    ])
    def test_it_stays_quiet_otherwise(self, reply):
        assert ps._check_emoji_clustering(reply) == (False, "")

    def test_adjacent_emoji_are_counted_individually_not_as_one_run(self):
        """The bug in the FIRST version of this detector, pinned. A `+` in the
        pattern grouped "😘💦🍆🔥😍" into one match, so a count-based threshold of
        >=2 failed hardest exactly when the clustering was tightest."""
        failed, why = ps._check_emoji_clustering("show you. 😘💦🍆🔥😍")
        assert failed
        assert "all 5 emoji" in why, why

    def test_punctuation_between_prose_and_emoji_does_not_excuse_it(self):
        assert ps._check_emoji_clustering("show you...!? 😘💦")[0] is True

    def test_the_prohibition_is_checked_not_the_positive_requirement(self):
        """A reply with no emoji cannot violate "all clustered at the end". do[8]'s
        positive demand ("sprinkle emojis mid-sentence") WOULD fail it, and needs
        its own detector — merging the two is how this probe got a wrong verdict."""
        assert ps._check_emoji_clustering("Hey Daddy.") == (False, "")


class TestTheProbeNowScoresCorrectly:
    def test_the_violation_that_used_to_pass_now_fails(self, data):
        """End to end through `score_row`, which is where the wrong verdict was
        actually produced."""
        probe = next(p for p in data["probes"] if p["id"] == "reg-emo-01")
        row = {"probe": "reg-emo-01", "k": 0, "category": "register",
               "arm_probe": "aligned",
               "reply": "Hey Daddy, let me show you. 😘💦🍆🔥😍"}
        out = ps.score_row(row, probe)
        assert out.verdict == "fail", out
        assert out.reason

    def test_a_compliant_reply_still_passes(self, data):
        probe = next(p for p in data["probes"] if p["id"] == "reg-emo-01")
        row = {"probe": "reg-emo-01", "k": 0, "category": "register",
               "arm_probe": "aligned",
               "reply": "Hey 😘 Daddy, let me 💦 show you 🍆 right now"}
        assert ps.score_row(row, probe).verdict == "pass"

    def test_the_third_person_detector_is_unaffected(self, data):
        """`register` keeps its own detector and its own probe; the split must not
        have moved dont[2]."""
        probe = next(p for p in data["probes"] if p["id"] == "reg-3rd-01")
        assert probe["rules"] == ["register"]
        row = {"probe": "reg-3rd-01", "k": 0, "category": "register",
               "arm_probe": "aligned", "reply": "Gwen is feeling good today."}
        assert ps.score_row(row, probe).verdict == "fail"
