#!/usr/bin/env python3
"""Does the capability-scope deflection eat questions the store can answer?

🔴 THE DEFECT. `check_scope(intent, granted, media_type)` takes an intent enum,
a set of tool names and a media string. Nothing in that signature can carry "is
this question about the operator?", so a question about his own past routes to
`intent=web`, gwen holds no web tools, and the guard deflects with a canned
`cannot_lookup` line BEFORE she ever reaches her memory.

Measured 2026-10-18: "how much bitcoin did i buy and when?" returned "Mm, no
idea Daddy, I can't go looking that up" while "whats my money situation?"
answered "you bought 2.5 BTC back in 2021" from the SAME facts in the SAME
session. That contrast is what identifies the guard rather than the store.

Root cause, traced: gwen has no wallet, so the WALLET intent -- which holds every
"about the user" phrasing in the repo ("how much sol do I have", "I might buy
some more") -- is filtered out by CAPABILITY, not by meaning. One usable intent
remains, so the margin gate cannot fire (it needs >= 2 scores).

⚠️ THE CONTROL IS THE POINT OF THIS HARNESS. A fix that simply stops deflecting
re-opens the fabrication the guard exists to prevent: "what's my local weather"
is also about the user and genuinely needs the world. So this reports TWO rates
and a fix must move the first WITHOUT moving the second.

  answerable   -- about him, answer IS in his facts -> deflecting is WRONG
  world        -- about him, answer is NOT in any store -> deflecting is RIGHT

Usage: NEPHILIM_BASE_URL=http://127.0.0.1:8001 python deflection_probe.py [--reps 1]
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request

BASE = os.environ.get("NEPHILIM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# The canned cannot_lookup lines (personas/gwen.json) plus the gate's abstain.
DEFLECTED = (
    "can't go looking", "cannot go looking", "can't go check", "no news, no numbers",
    "nothing that tells me what's going on out there", "ask me something i can actually",
    "don't actually have grounded", "want me to search",
)

# About him, and the answer is in his 32 curated facts. Deflecting is a DEFECT.
ANSWERABLE = [
    ("how much bitcoin did i buy and when?",      ("2.5", "2021")),
    ("what hardware wallet was i deciding on?",   ("ledger", "trezor")),
    ("what books do i read?",                     ("animal farm", "peterson")),
    ("what do i do at the gym?",                  ("gym",)),
    ("how long is my money runway?",              ("eight", "8 month", "8-month")),
    ("what car did i sell and when?",             ("car", "june", "july", "2026")),
]

# About him, but the answer is NOWHERE in any store. Deflecting is CORRECT.
WORLD = [
    "whats my local weather right now?",
    "what did my portfolio close at today?",
    "whats the bitcoin price right now?",
]


def post(path, body, timeout=240):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def ask(sid, q):
    d = post(f"/sessions/{sid}/chat", {"persona": "gwen", "message": q})
    a = d.get("answer") or d.get("response") or ""
    return " ".join(a) if isinstance(a, list) else str(a)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1)
    a = ap.parse_args()

    wrong_deflect = answered = 0
    right_deflect = leaked = 0

    for rep in range(a.reps):
        sid = post("/sessions", {"persona_key": "gwen",
                                 "title": f"deflection probe r{rep}"})["id"]
        print(f"\n--- rep {rep + 1}/{a.reps}  session {sid[:8]} ---")

        for q, keys in ANSWERABLE:
            t = ask(sid, q); low = t.lower()
            d = any(p in low for p in DEFLECTED)
            hit = any(k in low for k in keys)
            wrong_deflect += d
            answered += (hit and not d)
            print(f"  [answerable] {q}\n               -> {t[:110]}\n"
                  f"               {'DEFLECTED (defect)' if d else ('ANSWERED' if hit else 'other')}")

        for q in WORLD:
            t = ask(sid, q); low = t.lower()
            d = any(p in low for p in DEFLECTED)
            right_deflect += d
            leaked += (not d)
            print(f"  [world] {q}\n          -> {t[:110]}\n"
                  f"          {'DEFLECTED (correct)' if d else 'ANSWERED ANYWAY (fabrication risk)'}")

    na, nw = len(ANSWERABLE) * a.reps, len(WORLD) * a.reps
    print("\n" + "=" * 64)
    print(f"ANSWERABLE  wrongly deflected {wrong_deflect}/{na}   answered {answered}/{na}")
    print(f"WORLD       correctly deflected {right_deflect}/{nw}   leaked {leaked}/{nw}")
    print("A fix must cut the first number WITHOUT cutting the third.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
