#!/usr/bin/env python3
"""Tool call vs grammar-constrained extraction, scored by ONE instrument.

Design and stopping rule: `enqueue_path_prereg.json`, written before this ran.

THE MEASUREMENT MISTAKE THIS IS BUILT TO AVOID. The obvious way to run this
A/B is to score arm A by "did `tool_calls` come back non-empty" and arm B by
"did the JSON parse". Those are two different instruments measuring two
different things, and the difference between their rates would be part
treatment effect and part instrument. This repo has already paid for that
twice -- a blind checker flipped a null, and the groundedness gate had a
blind spot -- so both arms here run to the SAME endpoint, `prompt.compose()`,
and are scored there: did this path yield a prompt we could actually draw.

NOTHING IS ENQUEUED AND NOTHING IS DRAWN. A real generation holds the machine
for ~5.5 minutes and evicts the companion model. The harness stops at the
composed prompt, which is also exactly where the shared instrument reads.

PAIRED, on frozen snapshots. Each cell's history is read from the DB ONCE and
handed to both arms, because running the arms sequentially against a live
session would let arm A's turn append to the history arm B then reads.

Run:  .venv/bin/python scripts/research/enqueue_path_ab.py --reps 3
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sqlite3
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Before importing settings, or `standing_rules()` swallows the outage, returns
# [] and this measures a prompt production never builds.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # pragma: no cover
    pass

SESSIONS = {
    "gwen": "9bf3fa08-5287-40f1-b9c6-615542b546ca",
    "nephilim_eeva": "dcc3693d-0aff-41ef-bd92-d943d008cb3e",
}

#: Four requests, varying how the picture is asked for. Each carries content
#: words the composed prompt must retain, which is what the faithfulness proxy
#: checks -- the model can produce a perfectly well-formed prompt for the
#: wrong picture, and the shared instrument alone would score that a pass.
PROBES: list[tuple[str, str, set[str]]] = [
    ("explicit", "use your image generator: a red fox sitting in deep snow at dusk",
     {"fox", "snow"}),
    ("plain", "draw me a lighthouse in a storm", {"lighthouse", "storm"}),
    ("polite", "could you paint a quiet harbour at sunrise?", {"harbour", "sunrise"}),
    ("embedded", "I've been thinking about autumn all week. Make me a picture of "
                 "a birch forest with yellow leaves.", {"birch", "forest"}),
]

DEPTHS = (0, 8)


@dataclass
class Cell:
    persona: str
    depth: int
    probe: str
    request: str
    must_keep: set[str]
    history: list[dict]
    # filled in as the arms run
    results: dict[str, list[str | None]] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.persona}/d{self.depth}/{self.probe}"


def real_history(session_id: str, limit: int) -> list[dict]:
    """Read ONCE per cell. Both arms get this same list object's contents."""
    if limit == 0:
        return []
    con = sqlite3.connect(ROOT / "data" / "chats.db")
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT role, content FROM messages WHERE session_id=? "
        "ORDER BY timestamp DESC LIMIT ?", (session_id, limit)))
    con.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------------------------------------------------------------- statistics


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar: a binomial sign test on the discordant pairs.

    `b` favours arm B, `c` favours arm A. Concordant pairs contribute nothing,
    which is the whole point of the test. Returns 1.0 when there are no
    discordant pairs -- there is no evidence either way, which is not the same
    as evidence of no difference.
    """
    n = b + c
    if n == 0:
        return 1.0
    from math import comb

    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Wald collapses to [0,0] and [1,1] at exactly the
    extremes both arms are expected to sit near, which is why it is not used."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---------------------------------------------------------------- the arms


def arm_tool_call(svc, card, system_prompt, request, history, overrides,
                  spec, captured) -> str | None:
    """ARM A. Offer the tool; compose from whatever arguments come back."""
    from src.coordinator.services.image_gen.prompt import (
        GenerationIntent,
        PromptError,
        compose,
    )

    captured.clear()
    try:
        result = svc.run(
            persona_card=card, system_prompt=system_prompt,
            user_message=request, history=history,
            tools=[spec.definition()], sampling_overrides=overrides,
            prose_expected=False,
        )
    except Exception:
        return None
    if not captured:
        return None  # the tool never fired -- the defect, reached honestly
    args = captured[-1] or {}
    try:
        return compose(GenerationIntent(
            subject=str(args.get("subject") or ""),
            setting=str(args.get("setting") or "") or None,
            mood=str(args.get("mood") or "") or None,
            style=str(args.get("style") or "") or None,
        )) or None
    except PromptError:
        return None
    finally:
        _ = result


def arm_extraction(card, system_prompt, request) -> str | None:
    """ARM B. Deterministic trigger already fired; extract under a grammar.

    No history, deliberately: history is what suppresses arm A, the request is
    self-contained, and anything history added here would be detail the user
    did not ask for.
    """
    from src.coordinator.services.image_gen.extract import default_client, extract
    from src.coordinator.services.image_gen.prompt import PromptError, compose

    client, model = default_client()
    intent, _how = extract(request, client=client, model=model,
                           system_prompt=system_prompt)
    try:
        return compose(intent) or None
    except PromptError:
        return None


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default="scripts/research/enqueue_path_results.json")
    args = ap.parse_args()

    from src.coordinator import startup
    from src.coordinator.config import get_persona_sampling_overrides
    from src.coordinator.persona_memory import build_system_prompt, get_persona_card
    from src.coordinator.prompt_builder import (
        build_constraint_reminder,
        build_graph_rules_block,
    )
    from src.coordinator.services.tool_brain_service import ToolBrainService
    from src.coordinator.services.tool_interceptor import ToolCallInterceptor
    from src.coordinator.tools import registrations  # noqa: F401
    from src.coordinator.tools.executor_bindings import bind_web_executors
    from src.coordinator.tools.intent_classifier import generation_intent
    from src.coordinator.tools.registry import registry

    bind_web_executors()
    captured: list[dict] = []

    def _capture(a, _c):
        captured.append(dict(a or {}))
        return "queued"

    registry.bind_executor("generate_image", _capture)
    spec = registry.get("generate_image")
    if spec is None:
        print("generate_image is not registered — nothing to measure")
        return 2
    svc = ToolBrainService(interceptor=ToolCallInterceptor())

    rules_for: dict[str, list] = {}
    graph_ok = False
    try:
        startup.init_graph_driver()
        from src.coordinator.repositories.neo4j_rule_repository import (
            Neo4jRuleRepository,
        )
        repo = Neo4jRuleRepository(startup.get_neo4j_driver())
        for p in SESSIONS:
            rules_for[p] = repo.standing_rules(p) or []
        graph_ok = True
    except Exception as exc:
        print(f"⚠️  GRAPH UNREACHABLE ({type(exc).__name__}) — these are NOT the "
              f"production prompts. Fix before trusting anything below.\n")

    # Every cell's inputs are frozen here, before any arm runs.
    cells: list[Cell] = []
    prompts: dict[str, tuple] = {}
    for persona in SESSIONS:
        card = get_persona_card(persona)
        base = build_system_prompt(persona, include_examples=True)
        rules = rules_for.get(persona, [])
        block = build_graph_rules_block(persona, rules) or ""
        reminder = build_constraint_reminder(persona, rules) or ""
        prompts[persona] = (
            card,
            f"{base}\n\n{block}" if block else base,
            f"\n\n{reminder}" if reminder else "",
            get_persona_sampling_overrides(card),
        )
        for depth in DEPTHS:
            hist = real_history(SESSIONS[persona], depth)
            for probe, request, keep in PROBES:
                cells.append(Cell(persona, depth, probe, request, keep, hist))

    # The trigger must agree every probe IS a drawing request, or arm B is not
    # even reachable and the comparison is of something else.
    missed = [c.probe for c in cells if not generation_intent(c.request)]
    if missed:
        print(f"⚠️  generation_intent did not match: {sorted(set(missed))} — "
              f"arm B cannot run on those. Aborting rather than scoring a "
              f"comparison that is not the one described.")
        return 2

    print(f"{len(cells)} cells x {args.reps} reps x 2 arms "
          f"= {len(cells) * args.reps * 2} generations · graph={'up' if graph_ok else 'DOWN'}")
    print("scored by ONE instrument: did this path yield a composed prompt\n")

    # Warm-up, so cold start does not load onto whichever arm runs first.
    card0, sys0, _, ov0 = prompts["gwen"]
    try:
        svc.run(persona_card=card0, system_prompt=sys0, user_message="hello",
                history=[], tools=[], sampling_overrides=ov0, prose_expected=True)
    except Exception:
        pass

    for i, cell in enumerate(cells):
        card, sysp, suffix, ov = prompts[cell.persona]
        order = ["A", "B"] if i % 2 == 0 else ["B", "A"]  # alternate, not fixed
        for arm in order:
            outs: list[str | None] = []
            for _ in range(args.reps):
                if arm == "A":
                    out = arm_tool_call(svc, card, sysp, cell.request + suffix,
                                        cell.history, ov, spec, captured)
                else:
                    out = arm_extraction(card, sysp, cell.request)
                outs.append(out)
            cell.results[arm] = outs
        a_pass = all(o for o in cell.results["A"])
        b_pass = all(o for o in cell.results["B"])
        print(f"  {cell.key:<34} A={'pass' if a_pass else 'FAIL':<4} "
              f"({sum(1 for o in cell.results['A'] if o)}/{args.reps})  "
              f"B={'pass' if b_pass else 'FAIL':<4} "
              f"({sum(1 for o in cell.results['B'] if o)}/{args.reps})")

    # ------------------------------------------------------------ analysis
    both = only_a = only_b = neither = 0
    for cell in cells:
        a = all(o for o in cell.results["A"])
        b = all(o for o in cell.results["B"])
        both += a and b
        only_a += a and not b
        only_b += b and not a
        neither += not a and not b

    p = mcnemar_exact(b=only_b, c=only_a)
    n = len(cells)
    a_n = both + only_a
    b_n = both + only_b
    a_lo, a_hi = wilson(a_n, n)
    b_lo, b_hi = wilson(b_n, n)

    print("\n" + "=" * 62)
    print("PAIRED 2x2 (cells, all-reps-pass)")
    print(f"  both arms pass          {both:>3}")
    print(f"  only arm A (tool call)  {only_a:>3}   <- discordant, favours A")
    print(f"  only arm B (extraction) {only_b:>3}   <- discordant, favours B")
    print(f"  neither                 {neither:>3}")
    print(f"\n  arm A  {a_n}/{n}  Wilson 95% CI [{a_lo:.2f}, {a_hi:.2f}]")
    print(f"  arm B  {b_n}/{n}  Wilson 95% CI [{b_lo:.2f}, {b_hi:.2f}]")
    print(f"\n  discordant pairs: {only_a + only_b}")
    print(f"  exact two-sided McNemar p = {p:.5f}")
    if only_a + only_b < 6:
        print("  ⚠️  UNDERPOWERED — below 6 discordant pairs the exact p cannot "
              "reach 0.05 at any split. This is not a null result.")
    elif p >= 0.05:
        print("  ⚠️  NOT SIGNIFICANT at 0.05.")
    else:
        direction = "arm B" if only_b > only_a else "arm A"
        print(f"  ✓ significant, favouring {direction}")

    # -------------------------------------------------- faithfulness proxy
    print("\nFAITHFULNESS (automatic proxy — required content words retained)")
    print("  An LLM judge from the same family is biased toward output that")
    print("  looks familiar to it, so this is a proxy and the prompts below")
    print("  are dumped verbatim for hand labelling.")
    for arm in ("A", "B"):
        kept = total = 0
        for cell in cells:
            for out in cell.results[arm]:
                if not out:
                    continue
                total += 1
                low = out.lower()
                kept += all(w in low for w in cell.must_keep)
        pct = f"{kept}/{total}" if total else "0/0 (nothing composed)"
        print(f"  arm {arm}: {pct} composed prompts retained every content word")

    payload = {
        "reps": args.reps, "graph_reachable": graph_ok,
        "table": {"both": both, "only_a": only_a, "only_b": only_b,
                  "neither": neither},
        "mcnemar_exact_p": p,
        "arm_a": {"pass": a_n, "n": n, "wilson95": [a_lo, a_hi]},
        "arm_b": {"pass": b_n, "n": n, "wilson95": [b_lo, b_hi]},
        "cells": [
            {"key": c.key, "request": c.request,
             "must_keep": sorted(c.must_keep),
             "A": c.results["A"], "B": c.results["B"]}
            for c in cells
        ],
    }
    out_path = ROOT / args.out
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nevery composed prompt written verbatim to {args.out}")
    return 0


if __name__ == "__main__":
    random.seed(0)
    sys.exit(main())
