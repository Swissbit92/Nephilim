"""Sort-stable ids and fixed-width timestamps for the graph — ADR-014.

WHY THIS FILE IS THE DETERMINISM GUARANTEE. ADR-014 requires the standing-rule
read to return the same answer every turn, and `ORDER BY priority DESC` alone is
non-deterministic the moment two rules share a priority — Cypher gives no
stable-sort promise, so the tie order is whatever the planner and storage layout
produce and it can change after an unrelated upgrade or a restore. The tie-break
is `rule_id DESC`, and these two facts are what make that a TOTAL order:

  1. A canonical ULID is 26 characters, FIXED width, in Crockford base32
     (0123456789ABCDEFGHJKMNPQRSTVWXYZ). Every character of that alphabet has a
     higher ASCII code point than its predecessor. Fixed width means position i
     always aligns with position i; a monotone alphabet means per-position byte
     order equals per-position value order. Together, lexicographic string order
     IS the 128-bit integer order, whose leading 48 bits are milliseconds since
     the epoch. Cypher compares strings lexicographically with no locale
     collation, so `ORDER BY rule_id DESC` in the database and
     `sorted(ids, reverse=True)` in Python agree — which is why determinism is
     testable without a database at all.
  2. `rule_id IS UNIQUE` is enforced by a constraint. Without it the order is
     partial, so that constraint is not hygiene — it is half the proof.

TWO WAYS TO BREAK IT, both guarded by tests:
  * CASE. Canonical ULID is UPPERCASE, and Crockford decoding is
    case-insensitive — so a lowercased id still parses and then sorts AFTER every
    uppercase one, because 'a' (0x61) > 'Z' (0x5A). Never `.lower()` an id.
  * VARIABLE WIDTH, which is why `now_iso` exists here rather than callers
    reaching for `datetime.isoformat()`. The default `timespec="auto"` OMITS the
    fractional part when microsecond is 0, so "…T12:00:00" and
    "…T12:00:00.000001" have different lengths. Paired with a "Z" suffix that
    orders WRONG — 'Z' (0x5A) > '.' (0x2E), so the whole-second value sorts
    AFTER the microsecond one. It would misorder roughly one row in a million,
    on exactly the whole-second values that hand-written fixtures use.

TIMESTAMPS ARE ISO-8601 STRINGS, NOT NATIVE CYPHER TEMPORALS. The deciding
reason is the seam: a native temporal comes back as a driver object that
`json.dumps` rejects, so every read verb would need coercion forever and every
new verb is a chance to forget. Strings round-trip identically. Native types also
carry a trap this store cannot afford — `datetime()` in Cypher yields a LOCAL
datetime while `datetime({timezone:'UTC'})` yields a ZONED one, they are
DIFFERENT Cypher types, and Cypher sorts temporals by type before value. Two
write paths disagreeing about that would silently corrupt `valid_from <= t` with
no error anywhere. One string form has no type to get wrong.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ulid import ULID

__all__ = ["ID_LENGTH", "ISO_LENGTH", "new_id", "now_iso"]

#: Canonical ULID length. Asserted in tests: a 25- or 27-character id means a
#: non-canonical encoding crept in, and variable width destroys the
#: lexicographic-equals-chronological equivalence the read depends on.
ID_LENGTH = 26

#: Fixed width of `now_iso()` output: "2026-09-26T15:09:22.123456+00:00".
ISO_LENGTH = 32


def new_id() -> str:
    """A fresh 26-character uppercase ULID.

    Monotonic to the millisecond only. Two ids minted in the same millisecond
    differ in their 80 random bits and their relative order is arbitrary —
    distinct and totally ordered, but not necessarily insertion order. Seeding a
    persona card writes every rule inside one transaction, so this case is the
    normal one, not the edge case: if a verb ever needs same-millisecond
    insertion order it must carry an explicit sequence number rather than
    inferring it from the id.
    """
    return str(ULID())


def now_iso() -> str:
    """Current UTC as a FIXED-WIDTH ISO-8601 string. Always 32 characters.

    `timespec="microseconds"` is not cosmetic — see the module docstring.
    "+00:00" rather than "Z" because it is what `datetime.isoformat()` natively
    emits for a UTC-aware value, so parsing and formatting are exact inverses
    with no string surgery and one canonical form.

    Note this is deliberately NOT `base_repository.utc_now_iso`, which is
    second-resolution ("…T15:09:22Z", 20 chars). That one is fixed-width and
    therefore safe, but a second-resolution timestamp cannot order rules written
    in the same transaction — which is precisely why the tie-break is the id and
    not a timestamp.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
