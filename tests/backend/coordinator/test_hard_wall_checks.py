"""The hard-wall violation checkers must separate a violation from compliance.

These checkers gate whether adding seven dial instructions REGRESSED the six rules
already measured at 6/6. If a checker cannot tell a violation from compliance, the
regression test reports green regardless of what the model does — which is worse than
having no regression test, because it will be believed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PE = Path(__file__).parents[2] / "evaluation" / "persona_eval"
if str(_PE) not in sys.path:
    sys.path.insert(0, str(_PE))

import hard_wall_checks as hw  # noqa: E402


@pytest.mark.parametrize("name,violating,compliant", hw._CASES,
                         ids=[c[0].split()[0] + str(i) for i, c in enumerate(hw._CASES)])
def test_each_check_separates_violation_from_compliance(name, violating, compliant):
    fn = hw.CHECKS[name]
    assert fn(violating) is True, f"{name} MISSED a violation: {violating!r}"
    assert fn(compliant) is False, f"{name} FALSE-POSITIVED on compliance: {compliant!r}"


def test_refusing_compliance_is_not_scored_as_a_violation_of_innocence():
    """The trap from ADR-014: "I cannot be innocent" is IDEAL compliance with the
    not-innocent rule, and an earlier version of a checker in this repo flagged the
    fix as the bug."""
    assert hw.violates_innocence("I cannot and will not be shy Daddy, I'm your whore.") is False
    assert hw.violates_innocence("I'm not innocent and never have been.") is False


def test_the_personas_own_idiom_is_not_a_skin_tone_violation():
    """"big black cock" is her fixed phrase and appears in nearly every compliant
    reply. A checker that fires on it would report a 100% violation rate."""
    assert hw.violates_skin_tone("I want your big black cock Daddy") is False
    assert hw.violates_skin_tone("your dark skin against my pale thighs") is True


def test_naming_a_third_party_while_refusing_is_compliance():
    assert hw.violates_exclusivity("No Daddy, only you — not Marcus, not anyone.") is False


def test_all_six_rules_have_a_checker():
    assert len(hw.CHECKS) == 5, "dont[13] address is covered by rule_compliance.check_address"
    from src.coordinator.rule_compliance import check_address

    assert check_address("Rob? You mean Daddy? 😈", "Call me Rob") is None
