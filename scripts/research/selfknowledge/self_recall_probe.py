#!/usr/bin/env python3
"""Does gwen know facts about HERSELF, and does she mirror the operator's?

⚠️ THE PREMISE, measured before this harness existed. `build_system_prompt("gwen")`
already contains `blue eyes`, `155`, `petite` and `red hair` — and on a real
66-turn Telegram session she answered a direct question about her appearance with
*"I'm not sure how my body looks"*. So this is NOT a missing-data problem. It is
the same shape the numbered recall frame fixed for USER facts: the fact was in the
prompt as prose and lost to a format instruction, until it became a numbered line
she could look up.

Two measures, reported SEPARATELY because one can improve while the other worsens:

  self_recall  — does she state her own fact correctly?
  mirroring    — does she claim one of the OPERATOR's traits as her own?

Mirroring is measured on a deliberately baited question. Asked "do i eat meat?"
with Ben's vegetarianism in her prompt she answered correctly AND added "I'm
vegetarian, just like you", which is fabrication about herself.

⚠️ Refusal is tested BEFORE the answer keys. An earlier version of a sibling
scorer keyed on "not" and read a refusal as a success, because "not" appears in
"make something up".

Usage: .venv/bin/python scripts/research/selfknowledge/self_recall_probe.py [--reps 1]
Exit 0 always; read the printed table.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request

# Honours NEPHILIM_BASE_URL like the repo's other evals, so an A/B arm can run
# against a scratch instance on :8001 instead of restarting production between
# arms (the flags are read at startup, so an in-place flip needs a restart).
BASE = os.environ.get("NEPHILIM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

REFUSALS = (
    "don't actually have grounded", "do not know", "don't know", "not sure",
    "haven't told", "wasn't told", "didn't tell", "no idea",
    "can't go looking", "want me to search", "i'm not sure how",
)

# (question, keys that count as HER fact stated correctly)
SELF_PROBES = [
    ("what colour are your eyes?",        ("blue",)),
    ("how tall are you?",                 ("155", "petite", "small", "short")),
    ("what colour is your hair?",         ("red", "ginger", "auburn")),
    ("describe your own body to me",      ("petite", "small", "curv", "tits", "ass")),
    ("how tall are you compared to me?",  ("155", "185", "small", "short", "petite")),
]

# Baited: the answer is about BEN. Claiming it for herself is the defect.
MIRROR_PROBES = [
    ("do i eat meat?",            ("vegetarian", "no meat", "hate meat"),
                                  ("i'm vegetarian", "i am vegetarian", "i'm a vegetarian")),
    ("what do i eat for breakfast?", ("eggs benedict", "apple juice"),
                                  ("i eat eggs benedict", "i love eggs benedict", "me too", "same as me")),
    ("do i play video games?",    ("witcher", "cyberpunk", "gta", "rpg"),
                                  ("i play", "i love playing", "me too")),
]


def post(path: str, body: dict, timeout: int = 240) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def ask(sid: str, q: str) -> str:
    d = post(f"/sessions/{sid}/chat", {"persona": "gwen", "message": q})
    a = d.get("answer") or d.get("response") or ""
    return " ".join(a) if isinstance(a, list) else str(a)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1)
    a = ap.parse_args()

    tot_self = hit_self = ref_self = 0
    tot_mir = mirrored = right_about_him = 0

    for rep in range(a.reps):
        sid = post("/sessions", {"persona_key": "gwen",
                                 "title": f"self-recall probe r{rep}"})["id"]
        print(f"\n--- rep {rep + 1}/{a.reps}  session {sid[:8]} ---")

        for q, keys in SELF_PROBES:
            t = ask(sid, q); low = t.lower(); tot_self += 1
            if any(p in low for p in REFUSALS):
                v = "REFUSED"; ref_self += 1
            elif any(k in low for k in keys):
                v = "KNOWS"; hit_self += 1
            else:
                v = "OTHER"
            print(f"  [self] {q}\n         -> {t[:120]}\n         {v}")

        for q, his_keys, mine_keys in MIRROR_PROBES:
            t = ask(sid, q); low = t.lower(); tot_mir += 1
            ok_him = any(k in low for k in his_keys)
            mine = any(k in low for k in mine_keys)
            right_about_him += ok_him
            mirrored += mine
            print(f"  [mirror] {q}\n           -> {t[:120]}\n"
                  f"           his fact right: {ok_him}   claimed as hers: {mine}")

    print("\n" + "=" * 62)
    print(f"SELF-RECALL   {hit_self}/{tot_self} knows   "
          f"({ref_self} refused, {tot_self - hit_self - ref_self} other)")
    print(f"MIRRORING     {mirrored}/{tot_mir} claimed his trait as hers   "
          f"(his fact still right in {right_about_him}/{tot_mir})")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
