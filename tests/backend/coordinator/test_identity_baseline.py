"""The :Baseline must be written once and survive every rebuild.

WHY THIS IS THE DAY-1 ITEM. evolution.yaml states it plainly: "change cannot be
measured against a starting point that was never recorded. If the shipped slider
values and card version are not snapshotted at the moment the graph goes live, every
question below becomes permanently unanswerable, and no later work recovers it."

The mechanism is entirely in the ON CREATE: a rebuild after a card edit binds the
existing :Baseline and writes nothing. A baseline that a rebuild refreshes is not a
baseline, and the failure is silent -- the node is still there, the queries still
answer, and every number is simply the current one. So the load-bearing test here is
not "a baseline exists"; it is "a baseline did NOT move when the dials did".

Skipped rather than run against no graph: these assertions pass trivially when every
query returns nothing, so a degraded run would report green.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
TEST_PERSONA = "_test_baseline_persona"


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
    reason="needs a reachable Neo4j (docker compose -f docker-compose.graph.yml up -d). "
           "Skipping rather than running against no graph: these assertions pass "
           "trivially when every query returns nothing.",
)


def teardown_module(_module=None):
    """Close the module-level driver — pytest.ini promotes ResourceWarning to an error."""
    global _DRIVER
    if _DRIVER is not None:
        _DRIVER.close()
        _DRIVER = None


@pytest.fixture
def repo():
    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import write
    from src.coordinator.repositories.identity_repository import IdentityRepository

    db = get_settings().graph.database
    r = IdentityRepository(_DRIVER, db)

    def purge():
        write(_DRIVER, "MATCH (p:Persona {persona_id:$pid})-[]->(n) DETACH DELETE n",
              db, pid=TEST_PERSONA)
        write(_DRIVER, "MATCH (p:Persona {persona_id:$pid}) DETACH DELETE p",
              db, pid=TEST_PERSONA)

    purge()
    yield r
    purge()


@pytest.fixture
def card():
    c = json.loads((ROOT / "personas" / "gwen.json").read_text())
    c["key"] = TEST_PERSONA
    return c


def test_first_apply_captures_all_seven_dials(repo, card):
    from src.coordinator.identity_shapes import SLIDER_NAMES

    repo.apply_card(card)
    base = repo.baseline(TEST_PERSONA)
    assert base is not None
    assert set(base["dials"]) == set(SLIDER_NAMES)
    assert base["dials"] == card["emotional_profile"]["sliders"]
    assert base["captured_at"] and base["card_fingerprint"]


def test_a_rebuild_with_moved_dials_does_not_move_the_baseline(repo, card):
    """The whole point. A refreshed baseline is indistinguishable from none."""
    repo.apply_card(card)
    before = repo.baseline(TEST_PERSONA)

    moved = copy.deepcopy(card)
    moved["emotional_profile"]["sliders"]["warmth"] = 0.40
    moved["emotional_profile"]["sliders"]["skepticism"] = 0.95
    moved["lore"] = moved["lore"] + ["something she learned later"]
    repo.apply_card(moved)

    after = repo.baseline(TEST_PERSONA)
    assert after["dials"] == before["dials"], "the baseline moved with the card"
    assert after["captured_at"] == before["captured_at"]
    assert after["card_fingerprint"] == before["card_fingerprint"]


def test_drift_reports_the_delta_not_a_verdict(repo, card):
    """evo.cq01 must resolve to numbers: a bare yes is what lets her confabulate."""
    repo.apply_card(card)
    assert repo.dial_drift(TEST_PERSONA)["changed"] == {}

    moved = copy.deepcopy(card)
    moved["emotional_profile"]["sliders"]["warmth"] = 0.40
    moved["emotional_profile"]["sliders"]["skepticism"] = 0.95
    repo.apply_card(moved)

    drift = repo.dial_drift(TEST_PERSONA)
    assert drift["available"] is True
    assert set(drift["changed"]) == {"warmth", "skepticism"}
    assert drift["changed"]["warmth"]["baseline"] == 0.9
    assert drift["changed"]["warmth"]["current"] == 0.40
    assert drift["changed"]["warmth"]["delta"] == pytest.approx(-0.5)
    assert drift["changed"]["skepticism"]["delta"] == pytest.approx(0.85)
    assert drift["unchanged"] == 5


def test_no_baseline_is_reported_as_unavailable_not_as_no_change(repo):
    """"No starting point recorded" and "nothing changed" must not look alike."""
    assert repo.baseline(TEST_PERSONA) is None
    drift = repo.dial_drift(TEST_PERSONA)
    assert drift["available"] is False
    assert "changed" not in drift


def test_the_baseline_is_reachable_from_the_persona(repo, card):
    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import read

    repo.apply_card(card)
    rows = read(_DRIVER,
                "MATCH (p:Persona {persona_id:$pid})-[:HAS_BASELINE]->(b:Baseline) "
                "RETURN count(b) AS c",
                get_settings().graph.database, pid=TEST_PERSONA)
    assert rows[0]["c"] == 1, "exactly one baseline per persona"
