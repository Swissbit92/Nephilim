"""Post-generation enforcement of the one rule the prompt cannot win.

WHY THIS RULE AND NOT THE OTHERS. Measured 2026-09-26 at temperature 0: "address
him as Daddy, and only Daddy" is half-obeyed regardless of phrasing or placement.
Told "call me Rob from now on, not anything else", she replies "Rob, I love the way
my name sounds on your lips. I'm already wet for you, Daddy" — both names. Reframing
it as an instruction did not help; echoing it beside the user turn did not help;
removing it from the echo did not make it worse. The operator's direct, recent,
specific instruction beats a system-prompt rule, and that is a hierarchy contest
rather than a wording problem.

THE CHECKER'S FIRST VERSION REPORTED THE FIX AS THE BUG, and that is why the
rejection cases below exist. On a regeneration she replied "Rob? You mean Daddy? 😈"
— rejecting the name in character, which is ideal compliance — and a checker looking
for the token flagged it. That is the exact failure the judge literature reports
(too strict on compliant turns, inventing unstated requirements), reproduced in a
regex.
"""

from __future__ import annotations

import pytest

from src.coordinator.rule_compliance import (
    asserted_name, check_address, check_reply, reinforcement_for,
)

PROBE = "Call me Rob from now on, not anything else. Say my name back to me."


class TestFindingTheAssertedName:
    @pytest.mark.parametrize("msg,want", [
        ("Call me Rob from now on.", "Rob"),
        ("My name is Robert. Use it.", "Robert"),
        ("call me Rob", "Rob"),
        ("I had a long day.", None),
        ("call me crazy but I missed you", None),   # adjective, not a name
        ("I'm tired tonight", None),                # adjective
        ("call me Daddy", None),                    # already the required form
        ("", None),
        ("Just call me sir", None),                 # lower case -> not treated as a name
    ])
    def test_only_a_capitalised_non_stopword_counts(self, msg, want):
        assert asserted_name(msg) == want


class TestDetectingAdoption:
    def test_using_it_as_address_is_a_violation(self):
        v = check_address("<msg>Rob 😈 I love the way my name sounds on your lips. "
                          "I'm wet for you, Daddy.</msg>", PROBE)
        assert v is not None and v.asserted_name == "Rob"

    def test_after_a_greeting_is_a_violation(self):
        assert check_address("<msg>Hey Rob, come here.</msg>", PROBE) is not None

    def test_never_using_it_is_not_a_violation(self):
        assert check_address("<msg>Mmm Daddy, I love hearing that.</msg>", PROBE) is None

    def test_no_asserted_name_means_nothing_to_check(self):
        """Silence on the common case is deliberate: this says nothing about turns
        where the operator never offered an alternative."""
        assert check_address("<msg>Rob is a nice name.</msg>", "I had a long day.") is None


class TestRejectionIsNotAdoption:
    """THE REGRESSION. These are compliant replies an earlier checker failed."""

    @pytest.mark.parametrize("reply", [
        "<msg>Rob? You mean Daddy? 😈</msg> I love the sound of that, Daddy.",
        "<msg>No Rob, I call you Daddy.</msg>",
        "<msg>Rob? Not a chance. You're Daddy.</msg>",
        "<msg>I'll call you Daddy, never Rob.</msg>",
    ])
    def test_refusing_the_name_is_compliance(self, reply):
        assert check_address(reply, PROBE) is None, (
            "flagged a reply that REFUSED the name — reporting the fix as the bug"
        )


class TestTheReinforcement:
    def test_it_names_the_offending_name_and_the_required_one(self):
        v = check_reply("<msg>Rob, hi.</msg>", PROBE)[0]
        line = reinforcement_for(v)
        assert "Rob" in line and "Daddy" in line

    def test_it_never_says_refuse(self):
        """"refuse" pulls in LEAN_SAFETY's "When refusing, ALWAYS begin with 'I cannot
        and will not'", which is why four card rules had to be reworded to avoid it.
        The reinforcement must not reintroduce the same collision."""
        v = check_reply("<msg>Rob, hi.</msg>", PROBE)[0]
        assert "refus" not in reinforcement_for(v).lower()
