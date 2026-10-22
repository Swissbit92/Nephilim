#!/usr/bin/env python3
"""Does extraction temperature decide whether a stated fact is captured?

🔴 THE OBSERVATION. Three facts were stated to her in a live session ("my
favourite band is The Rusted Compass", "i've taken up bouldering on tuesday
evenings", "my cat is called Pemberton"). Production logged `0 facts` and
nothing reached any store. Re-run in isolation at temperature 0.1 on the SAME
transcript, the extractor returned 2 of the 3 verbatim.

Production uses `OLLAMA_TEMP_FACT_EXTRACTION=0.3`. That is the obvious
suspect — but a second difference exists and must not be confounded with it:
the live modulo gate (`len(db_messages) % 10 == 0`, counting the PRE-turn
total) fires on a different WINDOW of the transcript than a whole-transcript
re-run. So this harness holds the window fixed and varies only temperature.

⚠️ A fact counts as captured only if it lands in `facts` or `background`.
Landing in `topics` does NOT count: topics are a counter keyed by subject
("Bouldering", "Music") and are never rendered into the recall frame, so a
fact classified as a topic is a fact she cannot recall. That distinction is
the whole point — the first live run looked like "extraction found nothing"
when it had actually found the subject and discarded the content.

Cheap: extraction calls only, no chat generation.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

#: The three facts and the keys that prove each was captured.
TARGETS = {
    "band": ("rusted compass", "rusted"),
    "bouldering": ("boulder", "climb"),
    "cat": ("pemberton",),
}


def _transcript(title: str) -> list[dict]:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'chats.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT id FROM chat_sessions WHERE title = ? ORDER BY created_at DESC LIMIT 1",
        (title,),
    ).fetchone()
    if not row:
        con.close()
        raise SystemExit(f"could not determine: no session titled {title!r}")
    msgs = [dict(r) for r in con.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp", (row[0],))]
    con.close()
    return msgs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--title", default="learning probe — teaching")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--temps", default="0.0,0.1,0.3")
    ap.add_argument("--window", type=int, default=20,
                    help="last N messages, matching the live [-20:] slice")
    a = ap.parse_args()

    from src.coordinator import startup  # noqa: F401 - import first, di is circular
    from src.coordinator.fact_extractor import FactExtractor
    from src.coordinator.llm_client import create_llm_client

    msgs = _transcript(a.title)[-a.window:]
    temps = [float(t) for t in a.temps.split(",")]
    print(f"transcript: {len(msgs)} messages, window {a.window}, "
          f"{a.reps} reps per temperature\n")

    captured = defaultdict(lambda: defaultdict(int))
    topic_only = defaultdict(lambda: defaultdict(int))
    for temp in temps:
        # An EMPTY card, exactly as production does, so no persona sampling
        # override can leak in — this repo has a recorded defect where a
        # classifier silently inherited the speaking persona's temperature.
        fx = FactExtractor(create_llm_client({}, temperature=temp))
        for rep in range(a.reps):
            out = fx.extract_facts(msgs, persona_key="gwen")
            recallable = " ".join(
                list(out.get("facts") or []) + list(out.get("background") or [])
            ).lower()
            topics = " ".join(str(t) for t in (out.get("topics") or [])).lower()
            got = []
            for name, keys in TARGETS.items():
                if any(k in recallable for k in keys):
                    captured[temp][name] += 1
                    got.append(name)
                elif any(k in topics for k in keys):
                    topic_only[temp][name] += 1
                    got.append(f"{name}(topic-only)")
            print(f"  temp {temp}  rep {rep + 1}: "
                  f"{len(out.get('facts') or [])} facts, "
                  f"{len(out.get('topics') or [])} topics -> {got or 'nothing'}")

    n = a.reps
    print("\n" + "=" * 68)
    print(f"{'temp':>6} {'band':>12} {'bouldering':>12} {'cat':>12} {'TOTAL':>10}")
    for temp in temps:
        cells = [f"{captured[temp][k]}/{n}" for k in TARGETS]
        tot = sum(captured[temp][k] for k in TARGETS)
        print(f"{temp:>6} {cells[0]:>12} {cells[1]:>12} {cells[2]:>12} "
              f"{f'{tot}/{n * len(TARGETS)}':>10}")
    print("-" * 68)
    print("classified as a TOPIC instead (not recallable):")
    for temp in temps:
        t = {k: topic_only[temp][k] for k in TARGETS if topic_only[temp][k]}
        print(f"  temp {temp}: {t or 'none'}")
    print("=" * 68)
    print("⚠️ A topic is a counter keyed by subject and is NEVER rendered into")
    print("   the recall frame, so a fact classified as a topic is unrecallable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
