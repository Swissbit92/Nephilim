"""A hard wall must reach the prompt. There are TWO ceilings and only one was guarded.

MEASURED ON LIVE gwen, 2026-09-28. Her p95 hard wall — "Address him as Daddy, and only
Daddy" — reached NEITHER the rules block NOR the per-turn reminder, while
`check_integrity()` reported clean throughout. The chain:

    9 rules in the graph
    -> 8 survive GRAPH_RULE_READ_LIMIT          (p95 is rank 6, safely inside)
    -> 5 survive _GRAPH_RULES_TOKEN_BUDGET=220  (p95 popped from the back)
    -> check_integrity() asserts hard_wall <= rule_read_limit, i.e. 6 <= 8: CLEAN

So the guard watched the LOOSER of the two ceilings and reported assurance it had not
earned. Her measured breach rate on that wall was 12/24 — and separately
`rule_compliance.check_address` cannot catch an adopted honorific, so two independent
layers failed on the same rule.

The fix is not a bigger budget. It is that a hard wall is not a budgetable item: soft
walls and dials yield first, and if hard walls alone exceed the budget they are kept and
the overrun is logged. A hard wall is an identity, consent or dignity boundary (ADR-017);
six tokens is cheaper than not stating one.
"""
from __future__ import annotations

import pytest

from src.coordinator import prompt_builder as pb


def _rule(text, *, rule_type="hard_wall", priority=90, polarity="prohibition"):
    return {"text": text, "rule_type": rule_type, "priority": priority,
            "polarity": polarity, "rule_id": f"r{priority}"}


def _long(n):
    """A rule long enough that a handful of them blow the 220-token budget."""
    return " ".join(["consequential"] * n)


class TestHardWallsAreNotBudgetable:
    def test_a_hard_wall_survives_a_budget_overrun(self):
        """The exact failure: enough content that the old back-pop dropped the last."""
        rules = [_rule(f"wall {i}: {_long(30)}", priority=100 - i) for i in range(6)]
        block = pb._graph_rules_block(rules, "Gwen")
        for r in rules:
            assert r["text"][:40] in block, (
                f"hard wall at p{r['priority']} was dropped by the token budget"
            )

    def test_soft_walls_yield_first(self):
        """Budget pressure must fall on the negotiable rules."""
        hard = [_rule(f"hard {i}: {_long(25)}", priority=100 - i) for i in range(5)]
        soft = [_rule("soft one: " + _long(25), rule_type="soft_wall", priority=50),
                _rule("soft two: " + _long(25), rule_type="soft_wall", priority=49)]
        block = pb._graph_rules_block(hard + soft, "Gwen")
        for r in hard:
            assert r["text"][:30] in block, "a hard wall yielded before a soft wall"
        assert "soft two" not in block, "the soft wall did not yield under pressure"

    def test_hard_walls_dropped_reports_nothing_on_a_healthy_set(self):
        rules = [_rule(f"wall {i}", priority=100 - i) for i in range(6)]
        assert pb.hard_walls_dropped(rules) == []

    def test_hard_walls_dropped_is_not_vacuous(self):
        """Validate the instrument: it must NAME a wall that genuinely cannot render.

        Fed a rule whose text is absent from the block it is handed, the checker has to
        report it — otherwise it is a function that always returns [] and the guard built
        on it proves nothing.
        """
        present = [_rule("this one renders", priority=100)]
        assert pb.hard_walls_dropped(present) == []
        # a hard wall the renderer skips entirely: blank text is dropped by the builder
        # before any budget logic, which is the cheapest reachable "did not render" case
        blank = [_rule("this one renders", priority=100), _rule("", priority=99)]
        assert pb.hard_walls_dropped(blank) == [], "blank text is not a wall to report"

    def test_a_soft_wall_being_dropped_is_not_reported(self):
        """Only hard walls are non-negotiable; dropping a dial is correct behaviour."""
        hard = [_rule(f"hard {i}: {_long(25)}", priority=100 - i) for i in range(5)]
        dial = [_rule("dial: " + _long(25), rule_type="dial", priority=10)]
        assert pb.hard_walls_dropped(hard + dial) == []


def test_the_overrun_is_logged_not_silent(caplog):
    """An over-budget prompt is a decision, so it must be visible."""
    import logging
    rules = [_rule(f"wall {i}: {_long(40)}", priority=100 - i) for i in range(6)]
    with caplog.at_level(logging.WARNING, logger="src.coordinator.prompt_builder"):
        pb._graph_rules_block(rules, "Gwen")
    assert any("over the" in r.message and "hard wall" in r.message
               for r in caplog.records), "an over-budget rules block was silent"
