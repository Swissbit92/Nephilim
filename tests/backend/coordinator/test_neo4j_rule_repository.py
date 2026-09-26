"""Cypher correctness for the standing-rule store — ADR-014.

WHY THESE ARE GUARDED BY A PROBE AND NOT testcontainers. This repo has zero
testcontainers precedent, pins its test dependencies deliberately, and runs CI
headless — so a container-per-session fixture would be a new dependency, a new
convention, and would skip in CI anyway. Instead this follows
test_capability_scope.py: a module-level probe and a loud skip reason.

The hermetic half of that pattern — the same contract pinned in a module that
runs everywhere — arrives with the render layer, which is where the selection
logic that can be tested without a database lives. Until then this module is the
only coverage of the Cypher, and that is stated here rather than implied.

AND WHY THE SKIP MUST BE LOUD. Several assertions here are of the form "the
answer is stable" or "integrity is clean", and those PASS against a dead graph
that returns nothing at all. A test that cannot detect the outage it is affected
by reports green — so it must skip rather than run degraded. That is the exact
failure test_capability_scope.py documents, and the reason its docstring is worth
re-reading before editing this file.

WHAT IS HERE vs HERMETIC: Cypher semantics — does MERGE dedupe, does the ordering
hold, does supersession set both clocks, is the read byte-identical. None of that
can be unit-tested, because there is no in-process Cypher engine for Python worth
betting integrity on.
"""

from __future__ import annotations

import os

import pytest

from src.coordinator.repositories.neo4j_rule_repository import (
    DEFAULT_RULE_TYPE,
    Neo4jRuleRepository,
)

_SKIP_REASON = (
    "needs Neo4j 5.26.31 on the URL in NEO4J_BASE_URL "
    "(docker compose -f docker-compose.graph.yml up -d). Skipping rather than "
    "running against no graph: most assertions in this module pass trivially "
    "when every query returns nothing, so a degraded run would report green."
)

TEST_PERSONA = "_test_rules_persona"


def _driver():
    """Build a driver from the environment, or None. Never raises.

    Asks for a connection the same way production does rather than probing a
    port, so 'available' has one definition. A second, drifting definition of
    availability is the defect class this repo has already paid for.
    """
    url, pw = os.getenv("NEO4J_BASE_URL", ""), os.getenv("NEO4J_PASSWORD", "")
    if not url or not pw:
        return None
    try:
        from src.coordinator.graph_driver import build_driver
        d = build_driver(url, os.getenv("NEO4J_USER", "neo4j"), pw, verify=True)
        return d
    except Exception:
        return None


_DRIVER = _driver()


def teardown_module(_module=None):
    """Close the module-level driver.

    Not optional, and not politeness: driver 6.x removed implicit close from
    __del__, so a leaked driver holds its pooled connections and background threads
    for the life of the process. pytest.ini promotes ResourceWarning to an error
    precisely so this shows up — and it did, on this very file, which is why this
    function exists.
    """
    global _DRIVER
    if _DRIVER is not None:
        _DRIVER.close()
        _DRIVER = None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DRIVER is None, reason=_SKIP_REASON),
]


@pytest.fixture()
def repo():
    r = Neo4jRuleRepository(_DRIVER, os.getenv("NEO4J_DATABASE", "neo4j"))
    yield r
    from src.coordinator.graph_driver import write
    write(_DRIVER,
          "MATCH (p:Persona {persona_id:$p})-[:HAS_RULE]->(r:Rule) DETACH DELETE r, p",
          os.getenv("NEO4J_DATABASE", "neo4j"), p=TEST_PERSONA)


def _rules(n=3):
    return [
        {"text": f"rule {i}", "source_field": "dont", "source_index": i,
         "rule_type": "hard_wall" if i == 1 else "soft_wall",
         "priority": 100 if i == 1 else 50}
        for i in range(1, n + 1)
    ]


class TestSeedingIsIdempotent:
    def test_reseeding_does_not_duplicate(self, repo):
        """The MERGE key is (persona_id, source_field, source_index), not the
        text. Keying on text would make an edit create a second rule and orphan
        the first, leaving the graph holding both."""
        repo.seed_rules(TEST_PERSONA, _rules())
        repo.seed_rules(TEST_PERSONA, _rules())
        repo.seed_rules(TEST_PERSONA, _rules())
        assert len(repo.standing_rules(TEST_PERSONA, limit=99)) == 3

    def test_an_edited_rule_updates_in_place(self, repo):
        """Documents the accepted limit rather than hiding it: a card edit is an
        UPDATE, so the old wording is overwritten rather than superseded. That is
        tolerable only because the card is hand-edited and version-controlled, so
        git holds the history. It would NOT be tolerable for an operator-given
        rule, which is why add/supersede is a different method."""
        repo.seed_rules(TEST_PERSONA, _rules())
        edited = _rules()
        edited[0]["text"] = "rule 1, reworded"
        repo.seed_rules(TEST_PERSONA, edited)
        got = repo.standing_rules(TEST_PERSONA, limit=99)
        assert len(got) == 3
        assert any(r["text"] == "rule 1, reworded" for r in got)
        assert not any(r["text"] == "rule 1" for r in got)

    def test_an_unknown_rule_type_is_refused(self, repo):
        """Community enforces uniqueness and nothing else — no value-domain
        constraint exists in ANY edition. So this ValueError is the only thing
        preventing a rule_type the read would silently coalesce into a hard wall
        forever, with no indication it was a typo."""
        bad = _rules(1)
        bad[0]["rule_type"] = "hardwall"
        with pytest.raises(ValueError, match="controlled vocabulary"):
            repo.seed_rules(TEST_PERSONA, bad)


class TestTheReadIsDeterministic:
    def test_same_question_same_answer(self, repo):
        """ADR-014's central promise. Two calls must be byte-identical."""
        repo.seed_rules(TEST_PERSONA, _rules(6))
        a = [r["rule_id"] for r in repo.standing_rules(TEST_PERSONA, limit=4)]
        b = [r["rule_id"] for r in repo.standing_rules(TEST_PERSONA, limit=4)]
        assert a == b and len(a) == 4

    def test_ties_on_priority_are_broken_stably(self, repo):
        """The assertion that would pass by LUCK without the rule_id tie-break.
        These five rules all share a priority, so `ORDER BY priority DESC` alone
        leaves their order to the planner — and Cypher gives no stable-sort
        guarantee, so it can change after an unrelated upgrade or a restore."""
        tied = [{"text": f"tied {i}", "source_field": "dont", "source_index": i,
                 "rule_type": "soft_wall", "priority": 50} for i in range(1, 6)]
        repo.seed_rules(TEST_PERSONA, tied)
        runs = [[r["rule_id"] for r in repo.standing_rules(TEST_PERSONA, limit=5)]
                for _ in range(5)]
        assert all(r == runs[0] for r in runs), "tied priorities ordered unstably"
        assert runs[0] == sorted(runs[0], reverse=True), "not ordered by rule_id DESC"

    def test_highest_priority_wins_the_limit(self, repo):
        """A limit that dropped hard walls in favour of dials would be worse than
        no limit at all."""
        repo.seed_rules(TEST_PERSONA, _rules(6))
        got = repo.standing_rules(TEST_PERSONA, limit=1)
        assert len(got) == 1 and got[0]["rule_type"] == "hard_wall"


class TestSupersession:
    def test_superseding_sets_both_clocks_and_keeps_the_old_row(self, repo):
        """A supersession asserts TWO things: expired_at says the system stopped
        BELIEVING the row, valid_to says the rule stopped APPLYING. Conflating
        them makes 'what did I believe before X' unanswerable."""
        repo.seed_rules(TEST_PERSONA, _rules(1))
        old = repo.standing_rules(TEST_PERSONA)[0]
        out = repo.supersede_rule(old["rule_id"], "the replacement")
        assert out["superseded"]

        live = repo.standing_rules(TEST_PERSONA, limit=99)
        assert len(live) == 1 and live[0]["text"] == "the replacement"

        chain = repo.history(out["new_rule_id"])
        assert len(chain) == 2, "the superseded row was destroyed, not retired"
        prev = chain[1]
        assert prev["text"] == "rule 1"
        assert prev["expired_at"] is not None, "stopped believing it — not recorded"
        assert prev["valid_to"] is not None, "stopped applying — not recorded"

    def test_a_superseded_rule_cannot_be_superseded_again(self, repo):
        repo.seed_rules(TEST_PERSONA, _rules(1))
        old = repo.standing_rules(TEST_PERSONA)[0]["rule_id"]
        repo.supersede_rule(old, "v2")
        assert repo.supersede_rule(old, "v3")["superseded"] is False


class TestIntegrity:
    def test_a_clean_graph_reports_clean(self, repo):
        repo.seed_rules(TEST_PERSONA, _rules(3))
        assert repo.check_integrity()["clean"] is True

    def test_label_drift_is_detected(self, repo):
        """:CurrentRule is DERIVED state and nothing in the database keeps it
        honest — Community cannot enforce it. This is the check that will
        actually fire one day, so it is watched failing here rather than
        trusted."""
        from src.coordinator.graph_driver import write
        repo.seed_rules(TEST_PERSONA, _rules(2))
        rid = repo.standing_rules(TEST_PERSONA)[0]["rule_id"]
        # expire the row WITHOUT dropping the label — precisely the drift
        write(_DRIVER, "MATCH (r:Rule {rule_id:$id}) SET r.expired_at = '2020-01-01T00:00:00.000000+00:00'",
              os.getenv("NEO4J_DATABASE", "neo4j"), id=rid)
        drift = repo.check_integrity()["label_drift"]
        assert any(d["rule_id"] == rid for d in drift), "drift went undetected"
        assert repo.check_integrity()["clean"] is False
