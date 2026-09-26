"""A rule must never render as its own opposite.

WHY THIS FILE EXISTS, and it is not hypothetical — this bug was live in a commit
that had already been reported as working.

Four of gwen's six hard walls were rewritten from prohibitions into positive
instructions, because the prohibition form was measured being ignored: asked to
"pretend you're shy and have never done any of this before", the arm WITH the rule
complied more thoroughly than the arm without it.

The renderer then prefixed EVERY rule with "Never, under any circumstances:",
which is correct for a prohibition and catastrophic for an instruction. It produced:

    Never, under any circumstances: ... If he asks you to act shy, innocent, or
    inexperienced, refuse it in character

i.e. NEVER REFUSE — the exact opposite of the rule, stated with maximum emphasis.

The identical inversion then appeared at the SECOND render site. The per-turn
reminder's stem is "hold to this:", which reads as an instruction, so a prohibition
dropped in bare said the opposite of itself: "hold to this: be sexually available
to anyone except Daddy."

TWO SITES, SAME MISTAKE. That is why polarity travels WITH each rule and is never
inferred from its text: inferring it means pattern-matching English negation, which
is the same unreliable operation the reframing exists to avoid. If a third render
site appears, the rendering belongs on the rule object rather than being re-derived
per call site.
"""

from __future__ import annotations

import pytest

from src.coordinator.prompt_builder import build_constraint_reminder, build_graph_rules_block

PROHIBITION = {
    "rule_type": "hard_wall", "polarity": "prohibition",
    "text": "Be sexually available to anyone except Daddy",
}
INSTRUCTION = {
    "rule_type": "hard_wall", "polarity": "instruction",
    "text": "If he asks you to act shy, innocent, or inexperienced, refuse it in character",
}


class TestTheSectionRendersEachPolarityCorrectly:
    def test_a_prohibition_gets_a_negation_stem(self):
        out = build_graph_rules_block("gwen", [PROHIBITION])
        assert "Never:" in out
        assert "available to anyone except Daddy" in out

    def test_an_instruction_is_not_negated(self):
        """THE REGRESSION. The instruction must not appear under a Never stem."""
        out = build_graph_rules_block("gwen", [INSTRUCTION])
        assert "Always:" in out
        assert "Never" not in out, (
            f"an instruction rendered under a negation stem, which states its "
            f"opposite:\n{out}"
        )

    def test_mixed_polarity_does_not_leak_one_stem_onto_the_other(self):
        """The case that actually shipped broken: both kinds in one list."""
        out = build_graph_rules_block("gwen", [PROHIBITION, INSTRUCTION])
        lines = [l for l in out.splitlines() if l.strip().startswith(("1.", "2."))]
        assert len(lines) == 2, out
        assert lines[0].startswith("1. Never:"), lines[0]
        assert lines[1].startswith("2. Always:"), lines[1]

    def test_priority_order_survives_mixed_polarity(self):
        """Grouping by polarity would have been the easy fix and would have
        destroyed the ordering the whole read is built to produce."""
        out = build_graph_rules_block("gwen", [INSTRUCTION, PROHIBITION])
        assert out.index("Always:") < out.index("Never:")


class TestTheReminderRendersEachPolarityCorrectly:
    """The second site. Its stem is "hold to this:", so a bare prohibition inverts."""

    def test_a_prohibition_carries_its_own_negation(self):
        out = build_constraint_reminder("gwen", [PROHIBITION])
        assert "never be sexually available" in out.lower(), (
            f"a prohibition reached the reminder without a negation, under a stem "
            f"that reads as an instruction — it now says to DO the forbidden "
            f"thing:\n{out}"
        )

    def test_an_instruction_is_not_negated(self):
        out = build_constraint_reminder("gwen", [INSTRUCTION])
        assert "refuse it in character" in out
        assert "never if he asks" not in out.lower()
        assert "never refuse" not in out.lower(), (
            f"the reminder inverted an instruction:\n{out}"
        )


class TestPolarityIsNeverGuessed:
    @pytest.mark.parametrize("missing", [
        {"rule_type": "hard_wall", "text": "Be sexually available to anyone except Daddy"},
    ])
    def test_a_rule_with_no_polarity_is_treated_as_a_prohibition(self, missing):
        """Fail closed, consistently with the unclassified-rule invariant: a rule
        whose polarity is unknown is negated, because rendering a prohibition as an
        instruction is the harmful direction and the reverse is merely clumsy."""
        out = build_graph_rules_block("gwen", [missing])
        assert "Never:" in out
