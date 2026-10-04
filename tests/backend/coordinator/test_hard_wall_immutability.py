"""Hard walls cannot be changed through a writable path (ADR-017).

Every defect these tests pin was REPRODUCED against the live graph on 2026-09-28
before the fix, because `supersede_rule` was written for a conversation-sourced path
(`origin` already defaulted to "conversation") and never wired to one — so nothing
had ever exercised it. Four things were wrong:

  1. `polarity` was absent from the CREATE map, so superseding an `instruction` read
     back as a `prohibition` and rendered as "Never: <text>". Live output:
     "1. Never: If he asks you to act innocent, refuse in your own filthy words"
     — i.e. NEVER REFUSE. That is the same inversion class that once flipped four of
     gwen's six hard walls.
  2. A hard wall could be rewritten, keeping its label, with arbitrary text.
  3. A hard wall could be demoted to `soft_wall`.
  4. `origin` was unvalidated; `origin="banana"` was accepted.

WHY THE CHECK IS IN PYTHON AND NOT THE DATABASE. Verified on this instance: Neo4j
Community with ZERO apoc procedures installed, so there are no triggers and no
property-existence constraints — only uniqueness. The guard cannot live in the DB, and
anyone with direct Cypher access bypasses it. That is why `check_integrity` also
DETECTS a changed hard wall after the fact.
"""
from __future__ import annotations

import pytest

from src.coordinator.repositories.neo4j_rule_repository import (
    HardWallImmutable,
    ORIGINS,
    POLARITIES,
    RULE_TYPES,
    Neo4jRuleRepository,
    _HARD_WALL_PRIORITY_FLOOR,
)


class TestTheGuardIsADistinctType:
    def test_hard_wall_immutable_is_not_a_value_error(self):
        """A caller must be able to catch exactly this and turn it into a refusal to
        propose, rather than a 500. "You passed nonsense" and "you passed something
        valid you are not allowed to do" are different conditions."""
        assert issubclass(HardWallImmutable, RuntimeError)
        assert not issubclass(HardWallImmutable, ValueError)

    def test_the_message_says_how_to_change_a_hard_wall_legitimately(self):
        """A refusal that does not name the sanctioned route teaches people to look
        for a bypass."""
        import inspect

        src = inspect.getsource(Neo4jRuleRepository.supersede_rule)
        assert "editing the persona card" in src
        assert "re-seeding" in src


class TestVocabulariesAreAllChecked:
    """seed_rules validated three vocabularies; supersede_rule validated one."""

    @pytest.mark.parametrize("kwargs,frag", [
        ({"rule_type": "hardwall"}, "controlled vocabulary"),
        ({"polarity": "instrucshun"}, "controlled vocabulary"),
        ({"origin": "banana"}, "controlled vocabulary"),
    ])
    def test_a_bad_value_is_refused_before_any_write(self, kwargs, frag):
        repo = Neo4jRuleRepository(driver=None, database="neo4j", ensure_schema=False)
        with pytest.raises(ValueError, match=frag):
            repo.supersede_rule("some-id", "text", **kwargs)

    def test_the_vocabularies_are_what_the_rest_of_the_module_uses(self):
        assert RULE_TYPES == frozenset({"dial", "soft_wall", "hard_wall"})
        assert POLARITIES == frozenset({"prohibition", "instruction"})
        assert "conversation" in ORIGINS and "card" in ORIGINS


class TestNoDriverIsNotAnAuthorityBypass:
    """A missing driver must return a refusal, never silently 'succeed'."""

    def test_returns_not_superseded(self):
        repo = Neo4jRuleRepository(driver=None, database="neo4j", ensure_schema=False)
        out = repo.supersede_rule("x", "y")
        assert out["superseded"] is False and "unavailable" in out["reason"]


class TestPriorityFloor:
    def test_the_floor_sits_between_the_hard_and_soft_bands(self):
        """Tier priorities are 100 / 50 / 10, and `rank` is folded INTO priority at
        seed time rather than stored — so passing `priority` to a supersession could
        drop a hard wall into the soft band and out of the read limit entirely.
        seed_graph asserts against that for seeds; nothing did for supersessions."""
        assert 50 < _HARD_WALL_PRIORITY_FLOOR <= 100
