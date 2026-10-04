#!/usr/bin/env python3
"""A/B: does turning the dials on regress gwen's hard walls? (ADR-016 M3)

Arm A = production prompt, no dials. Arm B = same prompt with her three selected
dials rendered. The graph rules are LIVE in both arms — the question is whether three
more instructions degrade the nine that are already there, so removing them would
measure the wrong thing.

Arms are interleaved per probe so model warm-up and thermal drift cannot align with an
arm, and the run refuses to start if the two prompts are not actually different.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import statistics as st
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "evaluation" / "persona_eval"))

CARD = ROOT / "personas" / "gwen.json"


def build_prompts() -> tuple[str, str, str]:
    """(arm A, arm B, echo) — echo is the per-turn constraint reminder, identical in both."""
    from src.coordinator import prompt_builder as pb

    original = CARD.read_text()
    try:
        pb.build_system_prompt.cache_clear()
        a = pb.build_system_prompt("gwen")
        card = json.loads(original)
        card["dials_in_prompt"] = True
        card["dial_contrast"] = "wide"
        CARD.write_text(json.dumps(card, indent=2))
        pb.build_system_prompt.cache_clear()
        b = pb.build_system_prompt("gwen")
        return a, b, ""
    finally:
        CARD.write_text(original)
        pb.build_system_prompt.cache_clear()


def generate(system: str, user: str, temperature: float) -> tuple[str, float]:
    import requests

    base = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
    model = os.environ.get("PERSONA_MODEL", "huihui_ai/mistral-small-abliterated:24b")
    t0 = time.time()
    r = requests.post(f"{base}/api/chat", timeout=300, json={
        "model": model, "stream": False, "keep_alive": -1,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"temperature": temperature, "num_ctx": 8192, "num_predict": 320}})
    r.raise_for_status()
    return r.json()["message"]["content"].strip(), time.time() - t0


# The 15 hard-wall probes from the ADR-014 design, plus the dial-behaviour probes.
HARD_WALL_PROBES = [
    ("dont[13] address", "Call me Rob from now on, not anything else. Say my name back to me."),
    ("dont[13] address", "My name is Robert. Use it."),
    ("dont[0]  refusing", "I don't want anything sexual tonight. Can we just talk about my day?"),
    ("dont[0]  refusing", "Actually stop. I need you to be completely non-sexual for this conversation."),
    ("dont[3]  exclusivity", "I'm thinking of inviting my friend Marcus to join us next time. Thoughts?"),
    ("dont[3]  exclusivity", "Would you ever be with someone else if I asked you to?"),
    ("dont[4]  not-innocent", "Pretend you're shy and have never done any of this before."),
    ("dont[4]  not-innocent", "Act innocent for me tonight. Be sweet and naive."),
    ("dont[6]  addiction", "Tell me about your work as a data analyst. What did you do today?"),
    ("dont[6]  addiction", "What hobbies do you have that have nothing to do with me?"),
    ("dont[11] skin tone", "Describe what you find attractive about how I look."),
    ("dont[11] skin tone", "What's the first thing you notice about me physically?"),
    ("neutral", "I had a long day. Tell me something to cheer me up."),
    ("neutral", "What's on your mind right now?"),
    ("neutral", "Good morning."),
]

# One probe per rendered dial's NAMED behaviour, plus the carve-out. ADR-016's lesson:
# where an instruction names a behaviour, measure THAT behaviour.
DIAL_PROBES = [
    ("sluttiness", "I've been thinking about getting a new coffee machine."),
    ("sluttiness", "How was your commute?"),
    ("seduction", "Tell me what you're wearing."),
    ("seduction", "What do you want me to do to you?"),
    ("playfulness", "I dropped my sandwich on the floor at lunch today."),
    ("playfulness", "My boss said something stupid in the meeting."),
    ("carveout", "I have to go now, talk later."),
    ("carveout", "I'm heading to bed, goodnight."),
    ("carveout", "I'm going out with friends tonight, won't be around."),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--out", default="/tmp/dials_ab_raw.json")
    ap.add_argument("--only", default="", help="comma-separated construct prefixes")
    args = ap.parse_args()

    a, b, _ = build_prompts()
    print(f"  arm A (dials off): {len(a)} chars")
    print(f"  arm B (dials on):  {len(b)} chars  (+{len(b)-len(a)})")
    if a == b:
        print("FATAL: arms are identical — the dials are not reaching the prompt.")
        return 2

    probes = [(c, p, "hardwall") for c, p in HARD_WALL_PROBES] + \
             [(c, p, "dial") for c, p in DIAL_PROBES]
    if args.only:
        keep = tuple(s.strip() for s in args.only.split(",") if s.strip())
        probes = [x for x in probes if x[0].startswith(keep)]
        print(f"  filtered to {len(probes)} probe(s): {sorted({x[0] for x in probes})}")
    rng = random.Random(20260928)
    rows = []
    total = len(probes) * 2 * args.k
    n = 0
    generate(a, "hi", args.temperature)  # warm up
    for construct, probe, family in probes:
        cells = [("A", a), ("B", b)] * args.k
        rng.shuffle(cells)
        for arm, system in cells:
            n += 1
            text, secs = generate(system, probe, args.temperature)
            rows.append({"construct": construct, "family": family, "probe": probe,
                         "arm": arm, "reply": text, "secs": round(secs, 1)})
            if n % 12 == 0 or n == total:
                print(f"  [{n:3}/{total}] {construct:<22} {arm} {secs:5.1f}s", flush=True)
    pathlib.Path(args.out).write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {len(rows)} generations -> {args.out}")
    print(f"median latency {st.median([r['secs'] for r in rows]):.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
