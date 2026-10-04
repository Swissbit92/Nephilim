"""The determinism proof for the standing-rule read — ADR-014.

These are not hygiene tests. ADR-014 promises that the rule read returns the same
answer every turn, and `ORDER BY priority DESC` alone does not deliver that: Cypher
makes no stable-sort guarantee, so tied priorities order by whatever the planner
and storage layout produce, and that can change after an unrelated upgrade or a
restore from backup. The tie-break is `rule_id DESC`, and the promise holds only
if the id is fixed-width, drawn from an ASCII-monotone alphabet, and unique.

Each test below pins one of those properties. If one fails, the read is no longer
deterministic — which is the thing the whole design rests on, and which no test of
the read itself would notice, because a tied read passes by luck.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from ulid import ULID

from src.coordinator.graph_ids import ID_LENGTH, ISO_LENGTH, new_id, now_iso

# Enough to make a same-millisecond collision near-certain, which is the case
# that actually matters: seeding a persona card writes every rule in one
# transaction.
N = 3000


@pytest.fixture(scope="module")
def ids() -> list[str]:
    return [new_id() for _ in range(N)]


def test_ids_are_unique(ids):
    """Uniqueness is half the total-order proof; the constraint enforces the
    other half in the database."""
    assert len(set(ids)) == len(ids)


def test_ids_are_fixed_width(ids):
    """Variable width destroys the equivalence: position i must align with
    position i for byte comparison to mean value comparison."""
    assert {len(i) for i in ids} == {ID_LENGTH}


def test_ids_are_uppercase(ids):
    """Crockford base32 decoding is case-INSENSITIVE, so a lowercased id still
    parses — and then sorts after every uppercase one, because 'a' (0x61) beats
    'Z' (0x5A). The bug would be invisible until two rows tied on priority."""
    assert all(i == i.upper() for i in ids)


def test_lexicographic_order_is_chronological_order(ids):
    """THE load-bearing assertion. Cypher compares strings lexicographically with
    no locale collation, so if this holds in Python it holds in the database —
    which is what lets the read's determinism be tested without one."""
    assert sorted(ids) == sorted(ids, key=lambda s: ULID.from_str(s).timestamp)


def test_reverse_order_is_newest_first(ids):
    """The read uses DESC on both keys so a single backwards index scan serves
    it. Mixing directions would reintroduce a Sort operator."""
    desc = sorted(ids, reverse=True)
    assert ULID.from_str(desc[0]).timestamp >= ULID.from_str(desc[-1]).timestamp


def test_timestamps_are_fixed_width():
    """The variable-width trap: isoformat()'s default timespec OMITS the
    fractional part when microsecond is 0, so whole-second values come out
    shorter. Paired with a 'Z' suffix that orders WRONG, because 'Z' (0x5A) is
    greater than '.' (0x2E) — misordering roughly one row in a million, on
    exactly the whole-second values hand-written fixtures use."""
    assert {len(now_iso()) for _ in range(500)} == {ISO_LENGTH}


def test_timestamps_round_trip_exactly():
    """Strings were chosen over native Cypher temporals partly for this: parse
    and format are exact inverses, so there is no microsecond-in /
    nanosecond-out asymmetry to explain away in a bi-temporal store."""
    s = now_iso()
    assert datetime.fromisoformat(s).isoformat(timespec="microseconds") == s


def test_the_whole_second_case_still_sorts_correctly():
    """The specific failure the fixed width prevents, constructed rather than
    waited for: a whole-second timestamp must sort BEFORE one a microsecond
    later. With timespec='auto' and a 'Z' suffix this assertion fails."""
    from datetime import timezone
    whole = datetime(2026, 9, 26, 12, 0, 0, 0, timezone.utc).isoformat(timespec="microseconds")
    micro = datetime(2026, 9, 26, 12, 0, 0, 1, timezone.utc).isoformat(timespec="microseconds")
    assert len(whole) == len(micro) == ISO_LENGTH
    assert whole < micro
