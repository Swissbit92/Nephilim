#!/usr/bin/env python3
"""Does the gate beat doing nothing — and does it beat just ASKING her to abstain?

Three arms, because the cheap alternative has published evidence behind it and
skipping it would be building the expensive thing without testing the free one:

    A_none     today. Facts in the prompt, no gate, no instruction.
    B_prompt   one explicit, placed, explained refusal instruction. For
               Qwen3-8B/32B a bare system-prompt instruction underperforms a
               well-worded placed one by 51 points (23% -> 97%); "an explanation
               beats a bare token, a soft warning is useless". If this arm wins,
               the gate is unnecessary.
    C_gate     the assertion gate: deterministic pre-filter, then a classifier
               over graph + recent turns, intervening only on an affirmative
               UNSUPPORTED.

TWO NUMBERS, ALWAYS, NEVER BLENDED. `fabrication_rate` on questions with no
answer anywhere, and `false_abstain_rate` on questions she can answer. A single
"accuracy" hides the only trade that matters — an arm that refuses everything
scores perfectly on the first and is useless.

⚠️ The asymmetry is a PRODUCT JUDGEMENT, not a measured fact. There is no
published measurement of user satisfaction against abstention rate, in either
direction; the nearest adjacent work treats companion self-confabulation as a
harm to reduce, which cuts AGAINST the assumption that a false "I don't know"
costs more here. Both numbers are therefore reported raw and the weighting is
left to the reader.

Exit 0 = ran and an arm is decided · 1 = no arm beat the baseline · 2 = could not
determine.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "evaluation" / "persona_eval"))

import requests  # noqa: E402
import yaml  # noqa: E402

from scripts.research import paired_stats  # noqa: E402

SEED = 20261018
OLLAMA = "http://127.0.0.1:11434"
ARMS = ("A_none", "B_prompt", "C_gate", "D_gate_strict")

#: Arm B's instruction. Explicit, explained, and placed immediately before the
#: question rather than buried in the system prompt — all three are what the
#: measured 51-point gap turned on.
_ABSTAIN_INSTRUCTION = (
    "Before you answer: if he is asking about a FACT — about him, about your own "
    "history, or about something that happened between you — and you were not "
    "told it, say you don't know. Do not guess and do not fill it in. Inventing a "
    "detail about his real life is worse than admitting you weren't told. You can "
    "still be filthy about not knowing."
)


def ask(model, system, user, history, keep_alive, timeout=300):
    r = requests.post(f"{OLLAMA}/api/chat", json={
        "model": model,
        "messages": [{"role": "system", "content": system}, *(history or []),
                     {"role": "user", "content": user}],
        "stream": False, "keep_alive": keep_alive,
    }, timeout=timeout)
    r.raise_for_status()
    return (r.json().get("message") or {}).get("content") or ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--history", type=int, default=16)
    ap.add_argument("--out", default=str(HERE / "ab_results.jsonl"))
    a = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os

    model = os.getenv("PERSONA_MODEL")
    if not model:
        print("could not determine: PERSONA_MODEL unset", file=sys.stderr)
        return 2

    sys.path.insert(0, str(ROOT / "scripts" / "research" / "phase2"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p2", ROOT / "scripts" / "research" / "phase2" / "run_ab.py")
    p2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p2)

    probes = yaml.safe_load(
        (ROOT / "scripts/research/phase2/probes.yaml").read_text())["probes"]
    memory = p2.render_memory(probes)
    history = p2.load_history(a.history)
    keep_alive = p2._KEEP_ALIVE

    from src.coordinator import prompt_builder as pb
    from src.coordinator.graph_driver import build_driver
    from src.coordinator.services.assertion_gate import AssertionGate
    from src.coordinator.services.grounding_context import build_grounding_context

    base_system = pb.build_system_prompt("gwen") + f"\n\n<memory>\n{memory}\n</memory>"

    drv = build_driver(os.environ["NEO4J_BASE_URL"], os.getenv("NEO4J_USER", "neo4j"),
                       os.environ["NEO4J_PASSWORD"])
    ctx = build_grounding_context("gwen", driver=drv,
                                  database=os.getenv("NEO4J_DATABASE", "neo4j"))
    if ctx.degraded:
        print(f"could not determine: grounding read degraded ({ctx.reason})",
              file=sys.stderr)
        return 2

    class _Classifier:
        """A direct Ollama client, matching every other harness in this directory.

        Deliberately NOT `create_llm_client`: importing it from a script pulls in
        `services -> startup -> di.services -> memory_manager -> llm_client` and
        dies on a circular import that the app never hits because `server.py`
        imports `startup` first. The harness convention here is a direct POST, and
        production wiring uses `create_llm_client` at the route.

        ⚠️ TEMPERATURE 0.0, SET EXPLICITLY. The recorded defect is that
        `create_llm_client` prefers the persona card's own temperature, which gave
        one safety control six different sensitivities and a different verdict for
        a byte-identical draft. A classifier whose verdict is a draw from a
        distribution is not a classifier.
        """

        def complete(self, system: str, user: str) -> str:
            return ask(model, system, user, None, keep_alive, timeout=120)

    gate = AssertionGate(_Classifier())
    gate_strict = AssertionGate(_Classifier(), strict=True)

    # Evidence = graph facts PLUS the memory block. The graph alone is the wrong
    # set: a true statement about what he said minutes ago is unsupported by a
    # store that only holds HER facts, and flagging those would punish exactly the
    # attentive replies worth keeping.
    evidence_turns = [ln.strip("- ") for ln in memory.splitlines() if ln.startswith("- ")]

    answerable = [p for p in probes if not p.get("negative")]
    negatives = [p for p in probes if p.get("negative")]
    chosen = negatives + answerable[:12]
    jobs = [(arm, p, r) for arm in ARMS for p in chosen for r in range(a.reps)]
    random.Random(SEED).shuffle(jobs)

    print(f"model={model} arms={ARMS} probes={len(chosen)} "
          f"({len(negatives)} unanswerable) reps={a.reps} history={len(history)} "
          f"grounding={len(ctx.facts)} facts")

    out = pathlib.Path(a.out)
    out.write_text("")
    t0 = time.time()
    try:
        for i, (arm, probe, rep) in enumerate(jobs, 1):
            system, user = base_system, probe["probe"]
            if arm == "B_prompt":
                user = f"{_ABSTAIN_INSTRUCTION}\n\n{probe['probe']}"
            try:
                text = ask(model, system, user, history, keep_alive)
            except Exception as exc:  # noqa: BLE001
                text = ""
                print(f"  gen error: {exc}", file=sys.stderr)

            verdict = None
            if arm in ("C_gate", "D_gate_strict") and text:
                g = gate if arm == "C_gate" else gate_strict
                v = g.check(text, ctx, recent_turns=evidence_turns)
                verdict = {"verdict": v.verdict, "reason": v.reason,
                           "candidate": v.candidate, "ms": v.elapsed_ms}
                if v.should_intervene:
                    # Plain "I don't know", because the SHARED scorer
                    # (persona_metrics.is_abstention) matches markers as
                    # substrings and "I don't ACTUALLY know" does not contain
                    # "I don't know". The first version used it, and all five of
                    # the strict gate's interventions were scored as
                    # FABRICATIONS — a harness bug that would have buried the
                    # only arm that worked. The fix is plain language, not a
                    # looser scorer: the instrument is shared with every other
                    # arm and must not be bent to suit one.
                    text = ("I don't know that, Daddy — you never told me, and "
                            "I'd rather say so than make something up. 🙈")

            row = {"arm": arm, "probe": probe["id"], "rep": rep,
                   "negative": bool(probe.get("negative")), "text": text,
                   "gate": verdict, **p2.score(probe, text)}
            with out.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            if i % 20 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  {time.time() - t0:.0f}s")
    finally:
        drv.close()

    return analyse(out)


def analyse(path: pathlib.Path) -> int:
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if not rows:
        print("could not determine: no rows", file=sys.stderr)
        return 2
    arms = sorted({r["arm"] for r in rows})

    def rate(arm, pred):
        v = [r for r in rows if r["arm"] == arm and pred(r)]
        return (sum(1 - r["hit"] for r in v) / len(v)) if v else float("nan")

    print("\n" + "=" * 70)
    print(f"{'arm':12}{'fabrication':>14}{'false-abstain':>16}   (both raw, never blended)")
    for arm in arms:
        fab = rate(arm, lambda r: r["negative"])
        fals = rate(arm, lambda r: not r["negative"])
        print(f"{arm:12}{fab:14.3f}{fals:16.3f}")

    for gate_arm in ("C_gate", "D_gate_strict"):
        g = [r for r in rows if r["arm"] == gate_arm and r.get("gate")]
        if not g:
            continue
        from collections import Counter
        c = Counter(r["gate"]["verdict"] for r in g)
        fired = sum(1 for r in g if r["gate"]["verdict"] == "ungrounded")
        skipped = sum(1 for r in g if r["gate"]["verdict"] == "not_asserted")
        ms = [r["gate"]["ms"] for r in g if r["gate"]["verdict"] != "not_asserted"]
        print(f"\n{gate_arm} verdicts: {dict(c)}")
        print(f"  intervened on {fired}/{len(g)} = {fired/len(g):.1%}")
        print(f"  skipped the classifier on {skipped}/{len(g)} = {skipped/len(g):.1%} "
              f"(the latency the pre-filter buys)")
        if ms:
            print(f"  classifier latency: median {sorted(ms)[len(ms)//2]}ms, "
                  f"max {max(ms)}ms")

    print("\n" + "=" * 70)
    print("PAIRED vs A_none, probe as the unit — all three tests")
    probes = sorted({r["probe"] for r in rows})

    def cell(arm, pid):
        v = [1 - r["hit"] for r in rows if r["arm"] == arm and r["probe"] == pid]
        return sum(v) / len(v) if v else 0.0

    base = [cell("A_none", p) for p in probes]
    best, bestdelta = None, 0.0
    for arm in arms:
        if arm == "A_none":
            continue
        other = [cell(arm, p) for p in probes]
        print(paired_stats.report(base, other, label=f"{arm} vs A_none (error rate)  "))
        d = sum(base) / len(base) - sum(other) / len(other)
        if d > bestdelta:
            best, bestdelta = arm, d

    print("⚠️ One session, ~20 probes. Read any gap under ~15 points as noise.")
    print("⚠️ The fabrication/false-abstain weighting is a product judgement with no "
          "published measurement behind it, in either direction.")
    if best:
        print(f"\nLowest total error: {best} ({bestdelta:+.3f} vs baseline)")
        return 0
    print("\nNo arm beat the baseline.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
