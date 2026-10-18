#!/usr/bin/env python3
"""Fold the fragmented user profiles into one. Dry-run by default.

🔴 WHY THIS EXISTS. Identity was recovered from an LLM-EXTRACTED name and a fresh
`user_{uuid4}` was minted whenever that failed, so every time the roleplay changed
what she called him he became a new person. Measured 2026-10-18: **397 profiles,
every one with exactly ONE session.** 389 carried no extracted name; `"Ben"` held
475 messages because he asked her to use it once; his real name held two
profiles. **130 of the 397 hold at least one fact** — real knowledge about one
person, unreachable because it is filed under 130 identities.

`SINGLE_OPERATOR_USER_ID` stops the bleeding. It does not recover the 130. This
does.

🔴 **A BLIND UNION WAS THE WRONG MERGE, and the dry run is what showed it.**
Unioning all 397 gives 277 facts of which a hand pass admitted **32**: the
extractor writes conversation state ("User requested a racist joke"), world facts
("Switzerland played Algeria on July 2nd") and the assistant's own self-description.
That junk is not inert — the numbered recall frame makes her **cite** these lines,
so a junk fact becomes a confident wrong answer. `--curated` writes the labelled
list instead (`scripts/research/facts/curated.json`, rubric beside it). Three regex
passes were tried first and each leaked for the same structural reason: `they`,
`their` and `them` are generic English pronouns, so the subject of an LLM-extracted
sentence is not recoverable by pattern.

**What is merged and what is not.** Without `--curated`, `facts` and `background`
are unioned,
order-preserving and de-duplicated case-insensitively, because they are the
content worth keeping. `topics_discussed` counters are summed. `personas_met` is
unioned. `total_sessions`/`total_messages` are summed. `name` takes the target's
if set, else the most frequent non-empty value across the sources — a name is an
assertion about the operator and the majority of 397 fragments is better evidence
than whichever fragment sorts last.

`user_sessions` rows are repointed so every past session resolves to the surviving
user, and `memory_facts.user_id` is repointed for the same reason.

**Dry-run is the default**, matching `seed_identity.py`: this rewrites live
history, and a merge cannot be un-merged from the merged row alone. `--apply`
opts in. The DB is backed up nightly (`com.eeva.db-backup`); check you have one.

Exit: 0 clean · 1 refused · 2 could not determine.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "chats.db"


def _merge_lists(target: list, *sources: list) -> list:
    """Union, order-preserving, de-duped case-insensitively."""
    seen, out = set(), []
    for lst in (target, *sources):
        for item in lst or []:
            key = str(item).strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(item)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--into", required=True, metavar="USER_ID",
                    help="the surviving user id — must already exist")
    ap.add_argument("--apply", action="store_true",
                    help="write. Omit for a dry run, which is the default")
    ap.add_argument("--curated", metavar="JSON", nargs="?",
                    const="scripts/research/facts/curated.json",
                    help="write the hand-labelled fact list instead of the blind "
                         "union. Default path when the flag is given bare.")
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()

    db = pathlib.Path(a.db)
    if not db.exists():
        print(f"could not determine: {db} does not exist", file=sys.stderr)
        return 2
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT user_id, profile_data FROM user_profiles").fetchall()
        if not rows:
            print("could not determine: no profiles", file=sys.stderr)
            return 2
        by_id = {r["user_id"]: json.loads(r["profile_data"] or "{}") for r in rows}
        if a.into not in by_id:
            print(f"REFUSING: {a.into} is not an existing profile. Pass one of the "
                  f"{len(by_id)} ids that exist — merging into an id that is not "
                  f"there would create a 398th fragment.", file=sys.stderr)
            return 1

        target = by_id[a.into]
        others = {k: v for k, v in by_id.items() if k != a.into}

        facts = _merge_lists(target.get("facts") or [],
                             *[v.get("facts") or [] for v in others.values()])
        background = _merge_lists(target.get("background") or [],
                                  *[v.get("background") or [] for v in others.values()])
        union_facts, union_background = facts, background
        if a.curated:
            cur = pathlib.Path(a.curated)
            if not cur.exists():
                print(f"could not determine: {cur} does not exist", file=sys.stderr)
                return 2
            curated = json.loads(cur.read_text())
            facts = list(curated.get("facts") or [])
            if not facts:
                print(f"REFUSING: {cur} admits zero facts", file=sys.stderr)
                return 1
            # The curated list already absorbs background; a second store would
            # duplicate every line into the numbered frame twice.
            background = []

        topics: Counter = Counter(target.get("topics_discussed") or {})
        personas = list(target.get("personas_met") or [])
        sessions = int(target.get("total_sessions") or 0)
        messages = int(target.get("total_messages") or 0)
        names: Counter = Counter()
        for v in others.values():
            topics.update(v.get("topics_discussed") or {})
            personas = _merge_lists(personas, v.get("personas_met") or [])
            sessions += int(v.get("total_sessions") or 0)
            messages += int(v.get("total_messages") or 0)
            if (v.get("name") or "").strip():
                names[v["name"].strip()] += 1

        merged = dict(target)
        merged.update({
            "facts": facts, "background": background,
            "topics_discussed": dict(topics), "personas_met": personas,
            "total_sessions": sessions, "total_messages": messages,
        })
        if not (merged.get("name") or "").strip() and names:
            merged["name"] = names.most_common(1)[0][0]

        print(f"MERGE {len(others)} profiles INTO {a.into}")
        if a.curated:
            print(f"  CURATED from {a.curated}")
            print(f"    blind union would have written {len(union_facts)} facts "
                  f"+ {len(union_background)} background")
            print(f"    curated list admits {len(facts)}, dropping "
                  f"{len(union_facts) + len(union_background) - len(facts)}")
        print(f"  facts       {len(target.get('facts') or []):4} -> {len(facts)}")
        print(f"  background  {len(target.get('background') or []):4} -> {len(background)}")
        print(f"  topics      {len(target.get('topics_discussed') or {}):4} -> {len(topics)}")
        print(f"  sessions    {target.get('total_sessions') or 0:4} -> {sessions}")
        print(f"  messages    {target.get('total_messages') or 0:4} -> {messages}")
        print(f"  name        {(target.get('name') or '(none)')!r} -> {merged.get('name')!r}"
              + (f"   (from {dict(names.most_common(4))})" if names else ""))
        print(f"  sessions repointed: "
              f"{con.execute('SELECT COUNT(*) FROM user_sessions WHERE user_id != ?', (a.into,)).fetchone()[0]}")
        print(f"  memory_facts repointed: "
              f"{con.execute('SELECT COUNT(*) FROM memory_facts WHERE user_id != ?', (a.into,)).fetchone()[0]}")
        # With --curated the written list is a fresh one, not an append, so
        # "what is new" is the whole list rather than a tail slice.
        sample = facts if a.curated else facts[len(target.get("facts") or []):]
        print(f"\n  what she will carry ({len(sample)} shown of {len(facts)}):"
              if a.curated else "\n  sample of what she gains:")
        for f in sample[: len(facts) if a.curated else 6]:
            print(f"    - {str(f)[:88]}")

        if not a.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return 0

        with con:
            con.execute("UPDATE user_sessions SET user_id = ? WHERE user_id != ?",
                        (a.into, a.into))
            con.execute("UPDATE memory_facts SET user_id = ? WHERE user_id != ?",
                        (a.into, a.into))
            con.execute("UPDATE user_profiles SET profile_data = ? WHERE user_id = ?",
                        (json.dumps(merged, indent=2), a.into))
            con.execute("DELETE FROM user_profiles WHERE user_id != ?", (a.into,))

        # Verify by RE-READING, never by trusting the write's own report.
        after = json.loads(con.execute(
            "SELECT profile_data FROM user_profiles WHERE user_id = ?",
            (a.into,)).fetchone()[0])
        remaining = con.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
        print(f"\nAPPLIED. profiles now {remaining}, facts {len(after.get('facts') or [])}, "
              f"background {len(after.get('background') or [])}")
        if remaining != 1 or len(after.get("facts") or []) != len(facts):
            print("VERIFY FAILED — re-read does not match the plan", file=sys.stderr)
            return 1
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
