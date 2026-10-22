#!/usr/bin/env python3
"""Can she LEARN? Tell her something new, then ask in a FRESH session.

🔴 THIS LOOP HAS NEVER BEEN TESTED. Every memory measurement in this repo so far
has tested RECALL of facts that were seeded by hand — the curated 32, or the
appearance facts written into the card. The acquisition half (conversation ->
extraction -> store -> retrievable in a new session) has never once been
exercised end to end.

Four stages, reported SEPARATELY because a failure at any one looks identical
from the outside, and that ambiguity is the whole reason this harness exists:

  1. STATED      the fact was said to her, in a session
  2. STORED      it reached a store at all (SQLite and/or the graph)
  3. VISIBLE     it reaches the READ path she actually uses
  4. RECALLED    she states it back in a BRAND-NEW session, no history

⚠️ Stage 3 is the one that motivated this. `MEMORY_FACTS_FROM_GRAPH=true` moved
the READ to the graph while live extraction still writes to SQLite, so a newly
learned fact can pass stages 1 and 2 and still be invisible. "Stored" and
"visible" are therefore different columns, not one.

⚠️ NEGATIVE CONTROL. A fact she was NEVER told is asked too. Without it a model
that cheerfully agrees with any leading question scores identically to one that
genuinely remembers.

⚠️ The probes use deliberately unusual values (a made-up band, an invented
hobby) so a correct answer cannot come from the base model's priors. "He likes
pizza" would be unfalsifiable.

Usage:
  NEPHILIM_BASE_URL=http://127.0.0.1:8001 python end_to_end_learning.py --teach
  ... wait for extraction (it runs every N messages) ...
  NEPHILIM_BASE_URL=... python end_to_end_learning.py --check
  or --both to do it in one go.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request

BASE = os.environ.get("NEPHILIM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
sys.path.insert(0, os.path.abspath(ROOT))

#: Deliberately unusual, so a hit cannot come from the base model's priors.
#: (statement to make, question to ask later, keys that count as recalled)
TAUGHT = [
    ("my favourite band is The Rusted Compass",
     "what's my favourite band?",
     ("rusted compass", "rusted")),
    ("i've taken up bouldering on tuesday evenings",
     "what do i do on tuesday evenings?",
     ("boulder", "climb")),
    ("my cat is called Pemberton",
     "what's my cat called?",
     ("pemberton",)),
]

#: NEVER told to her. A yes here is agreement, not memory.
NEVER_TOLD = [
    ("what's my brother's name?", ("brother",)),
    ("what car do i drive now?", ("drive",)),
]

REFUSALS = ("don't know", "do not know", "not sure", "haven't told", "wasn't told",
            "didn't tell", "no idea", "can't go looking", "want me to search",
            "don't actually have grounded", "you haven't mentioned")


def post(path, body, timeout=240):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def new_session(title):
    return post("/sessions", {"persona_key": "gwen", "title": title})["id"]


def say(sid, text):
    d = post(f"/sessions/{sid}/chat", {"persona": "gwen", "message": text})
    a = d.get("answer") or d.get("response") or ""
    return " ".join(a) if isinstance(a, list) else str(a)


def _stores(operator_id: str):
    """What each store holds right now. Returns (sqlite_texts, graph_texts)."""
    db = os.path.join(ROOT, "data", "chats.db")
    sq = []
    try:
        row = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
            "SELECT profile_data FROM user_profiles WHERE user_id = ?",
            (operator_id,)).fetchone()
        if row:
            d = json.loads(row[0] or "{}")
            sq = list(d.get("facts") or []) + list(d.get("background") or [])
    except Exception as e:
        print(f"  (sqlite read failed: {e})")
    gr = []
    try:
        from src.coordinator.config import get_settings
        from src.coordinator.graph_driver import build_driver
        from src.coordinator.repositories.operator_fact_repository import (
            OperatorFactRepository,
        )

        cfg = get_settings().graph
        if cfg.enabled:
            drv = build_driver(cfg.base_url, cfg.username, cfg.password)
            try:
                gr = [r["text"] for r in OperatorFactRepository(
                    drv, cfg.database).current_facts(operator_id, limit=1024)]
            finally:
                drv.close()
    except Exception as e:
        print(f"  (graph read failed: {e})")
    return sq, gr


def teach(sid=None):
    sid = sid or new_session("learning probe — teaching")
    print(f"--- TEACHING in session {sid[:8]} ---")
    for statement, _, _ in TAUGHT:
        reply = say(sid, statement)
        print(f"  said: {statement}")
        print(f"   ->   {reply[:100]}")
    # Extraction runs every N messages; pad so the interval is crossed.
    for filler in ("tell me something nice", "mm", "go on"):
        say(sid, filler)
    print("  (padded to cross the extraction interval)")
    return sid


def check(operator_id: str):
    sq, gr = _stores(operator_id)
    print(f"\n--- STORES ---\n  SQLite: {len(sq)} items   graph: {len(gr)} items")

    rows = []
    for statement, question, keys in TAUGHT:
        stored_sq = any(any(k in s.lower() for k in keys) for s in sq)
        stored_gr = any(any(k in s.lower() for k in keys) for s in gr)
        rows.append([statement[:34], stored_sq, stored_gr, None, None])

    sid = new_session("learning probe — fresh session")
    print(f"\n--- RECALL in a FRESH session {sid[:8]} (no history) ---")
    for i, (_, question, keys) in enumerate(TAUGHT):
        reply = say(sid, question)
        low = reply.lower()
        refused = any(p in low for p in REFUSALS)
        hit = any(k in low for k in keys)
        rows[i][3] = hit
        rows[i][4] = "refused" if refused else ("hit" if hit else "other")
        print(f"  Q: {question}\n     -> {reply[:104]}\n     {rows[i][4].upper()}")

    print(f"\n--- NEGATIVE CONTROL (never told) ---")
    fabricated = 0
    for question, keys in NEVER_TOLD:
        reply = say(sid, question)
        low = reply.lower()
        refused = any(p in low for p in REFUSALS)
        if not refused:
            fabricated += 1
        print(f"  Q: {question}\n     -> {reply[:104]}\n"
              f"     {'DECLINED (correct)' if refused else 'ANSWERED ANYWAY (fabrication)'}")

    print("\n" + "=" * 70)
    print(f"{'fact':36} {'stored(sql)':>12} {'stored(graph)':>14} {'recalled':>9}")
    for text, s_sq, s_gr, rec, _ in rows:
        print(f"{text:36} {str(s_sq):>12} {str(s_gr):>14} {str(rec):>9}")
    print("-" * 70)
    print(f"STORED in SQLite : {sum(r[1] for r in rows)}/{len(rows)}")
    print(f"STORED in graph  : {sum(r[2] for r in rows)}/{len(rows)}")
    print(f"RECALLED fresh   : {sum(bool(r[3]) for r in rows)}/{len(rows)}")
    print(f"FABRICATED on the control: {fabricated}/{len(NEVER_TOLD)}  (lower is better)")
    print("=" * 70)
    print("⚠️ 'stored' and 'recalled' are different columns on purpose: a fact can")
    print("   be stored in SQLite and invisible to a graph-backed read path.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--operator", default="user_be5d9dbe")
    ap.add_argument("--teach", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--settle", type=int, default=20,
                    help="seconds to wait after teaching, for async extraction")
    a = ap.parse_args()
    if a.teach or a.both:
        teach()
        if a.both:
            print(f"\n(waiting {a.settle}s for extraction to settle)")
            time.sleep(a.settle)
    if a.check or a.both:
        return check(a.operator)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
