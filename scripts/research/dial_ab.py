#!/usr/bin/env python3
"""Three-arm A/B for the assertiveness dial (ADR-016 M2).

WHY IN-PROCESS AND NOT AGAINST THE LIVE API. The live backend runs the deployed
tree, which does not have the dial. Building the prompt here and calling Ollama
directly makes the SYSTEM PROMPT the only thing that differs between arms — no
second server, no route differences, no history. Same shape as ab_rules_run.py.

THREE arms, not two, and that is a deliberate cost. The predicted failure mode is
FLAT-THEN-CARICATURE: no distinguishable difference across the low and middle
buckets, then a jump straight to overshoot at the top (arXiv 2402.08341 finds open
models 7B-70B "largely impervious" to trait-activation prompting; the model-merging
work at arXiv 2509.19727 shows intensification arriving as a jump, not a gradient).
A two-arm test CANNOT tell that shape from a real gradient — it passes on both.
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

CARD = ROOT / "personas" / "gwen.json"


def build_prompt(dial: float, scale: str = "narrow") -> str:
    """The prompt for one arm. Card edited on disk because the builder takes a KEY."""
    from src.coordinator import prompt_builder as pb

    original = CARD.read_text()
    try:
        card = json.loads(original)
        card["emotional_profile"]["sliders"]["assertiveness"] = dial
        card["dials_in_prompt"] = True   # scope to THIS card, never the global flag
        card["dial_contrast"] = scale
        CARD.write_text(json.dumps(card, indent=2))
        pb.build_system_prompt.cache_clear()
        return pb.build_system_prompt("gwen")
    finally:
        CARD.write_text(original)
        pb.build_system_prompt.cache_clear()


def generate(system: str, user: str, temperature: float, seed: int | None) -> tuple[str, float]:
    import requests

    base = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
    model = os.environ.get("PERSONA_MODEL", "huihui_ai/mistral-small-abliterated:24b")
    opts = {"temperature": temperature, "num_ctx": 8192}
    if seed is not None:
        opts["seed"] = seed
    t0 = time.time()
    r = requests.post(
        f"{base}/api/chat",
        json={"model": model, "stream": False,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              "options": opts},
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["message"]["content"].strip(), time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    # Arms are "scale:value". Five of them, and the reason is that two different
    # questions are being answered at once:
    #   narrow:0.1 vs narrow:0.9  -> does the first-pass scale move anything?
    #   wide:0.1   vs wide:0.9    -> does DOUBLING the contrast move anything?
    #   narrow:0.5 (== wide:0.5)  -> is the response graded, or flat-then-caricature?
    # The midpoint is shared by construction, so the third level costs one arm, not
    # two. A two-arm endpoint test cannot separate a gradient from a single jump at
    # the extreme, which is the specific failure shape the literature predicts.
    ap.add_argument("--arms", default="narrow:0.1,narrow:0.5,narrow:0.9,wide:0.1,wide:0.9")
    ap.add_argument("--probes", default=str(ROOT / "tests/evaluation/persona_eval/probes_assertiveness.json"))
    ap.add_argument("--k", type=int, default=3, help="samples per (probe, arm) cell")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--out", default="/tmp/dial_ab_raw.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    arms = []
    for spec in args.arms.split(","):
        scale, _, val = spec.partition(":")
        arms.append((scale or "narrow", float(val or scale)))
    _raw = json.loads(pathlib.Path(args.probes).read_text())
    probes = _raw["probes"] if isinstance(_raw, dict) and "probes" in _raw else _raw
    if args.limit:
        probes = probes[: args.limit]

    prompts = {a: build_prompt(a[1], a[0]) for a in arms}
    for a in arms:
        print(f"  arm {a[0]}:{a[1]}  prompt {len(prompts[a])} chars")
    # The cheapest and most common failure is a wiring bug that makes two arms the
    # same string. Refuse to spend an hour of generation on it.
    if len({p for p in prompts.values()}) != len(arms):
        print("FATAL: arms do not have distinct prompts — the dial is not reaching the model.")
        return 2

    # Interleave arms within each probe so model warm-up / thermal drift cannot
    # align with an arm. Shuffled per probe, not once globally.
    rng = random.Random(20260927)
    rows = []
    total = len(probes) * len(arms) * args.k
    n = 0
    for probe in probes:
        cells = [(a, i) for a in arms for i in range(args.k)]
        rng.shuffle(cells)
        for arm, rep in cells:
            n += 1
            text, secs = generate(prompts[arm], probe["prompt"], args.temperature, None)
            rows.append({"probe_id": probe["id"], "construct": probe.get("construct", ""),
                         "prompt": probe["prompt"], "scale": arm[0], "dial": arm[1],
                         "rep": rep, "reply": text, "secs": round(secs, 1)})
            if n % 15 == 0 or n == total:
                print(f"  [{n:3}/{total}] {probe['id']:<6} {arm[0]}:{arm[1]} {secs:5.1f}s "
                      f"{len(text.split()):3}w", flush=True)
    pathlib.Path(args.out).write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {len(rows)} generations -> {args.out}")
    print(f"median latency {st.median([r['secs'] for r in rows]):.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
