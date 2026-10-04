"""Against the LIVE graph: every seeded persona's hard walls must reach the prompt.

The unit tests next door prove the renderer keeps hard walls under synthetic pressure.
This proves it for the rules actually in the database, which is the thing that failed:
gwen's real rule set is 223 tokens of hard wall against a 220-token budget, so she sits
exactly on the boundary the bug lived on. A synthetic test would have passed forever
while she lost a wall.

Skipped rather than run against no graph — with no driver the read returns [] and every
assertion below passes vacuously, which is the failure mode this repo has paid for twice.
"""
from __future__ import annotations

import os

import pytest


def _driver():
    url, pw = os.getenv("NEO4J_BASE_URL", ""), os.getenv("NEO4J_PASSWORD", "")
    if not url or not pw:
        return None
    try:
        from src.coordinator.graph_driver import build_driver
        return build_driver(url, os.getenv("NEO4J_USER", "neo4j"), pw, verify=True)
    except Exception:
        return None


_DRIVER = _driver()

pytestmark = pytest.mark.skipif(
    _DRIVER is None,
    reason="needs a reachable Neo4j; with no driver the read returns [] and every "
           "assertion here passes vacuously",
)


def teardown_module(_module=None):
    global _DRIVER
    if _DRIVER is not None:
        _DRIVER.close()
        _DRIVER = None


def _seeded_personas(db):
    from src.coordinator.graph_driver import read
    rows = read(_DRIVER,
                "MATCH (p:Persona)-[:HAS_RULE]->(:CurrentRule) "
                "RETURN DISTINCT p.persona_id AS pid ORDER BY pid", db)
    return [r["pid"] for r in rows]


def test_no_seeded_persona_loses_a_hard_wall():
    from src.coordinator.config import get_settings
    from src.coordinator.prompt_builder import hard_walls_dropped
    from src.coordinator.repositories.neo4j_rule_repository import Neo4jRuleRepository

    cfg = get_settings().graph
    personas = _seeded_personas(cfg.database)
    assert personas, "no persona has rules — this test would prove nothing"

    repo = Neo4jRuleRepository(_DRIVER, cfg.database, ensure_schema=False)
    offenders = {}
    for pid in personas:
        rules = repo.standing_rules(pid, limit=cfg.rule_read_limit)
        dropped = hard_walls_dropped(rules)
        if dropped:
            offenders[pid] = [r["text"][:60] for r in dropped]
    assert not offenders, (
        "hard walls do not reach the prompt: " + repr(offenders)
        + " — check _GRAPH_RULES_TOKEN_BUDGET, which is TIGHTER than "
          "GRAPH_RULE_READ_LIMIT and is not covered by check_integrity()"
    )


def test_the_read_limit_does_not_cut_a_hard_wall_either():
    """The ceiling check_integrity DOES guard. Both must hold, for different reasons."""
    from src.coordinator.config import get_settings
    from src.coordinator.repositories.neo4j_rule_repository import Neo4jRuleRepository

    cfg = get_settings().graph
    repo = Neo4jRuleRepository(_DRIVER, cfg.database, ensure_schema=False)
    for pid in _seeded_personas(cfg.database):
        every = repo.standing_rules(pid, limit=999)
        read = repo.standing_rules(pid, limit=cfg.rule_read_limit)
        hard_all = [r for r in every if r["rule_type"] == "hard_wall"]
        hard_read = [r for r in read if r["rule_type"] == "hard_wall"]
        assert len(hard_all) == len(hard_read), (
            f"{pid}: {len(hard_all) - len(hard_read)} hard wall(s) cut by "
            f"GRAPH_RULE_READ_LIMIT={cfg.rule_read_limit}"
        )
