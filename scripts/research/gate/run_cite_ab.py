#!/usr/bin/env python3
"""Can she hold 0% invention AND get her memory back? Four arms.

THE ARGUMENT THIS TESTS, which is the operator's and corrects mine. I framed
invention and over-refusal as a balance to be struck. They are not: they have
different causes.

    invents          the fact is ABSENT and she fills the gap
    wrongly refuses  the fact is PRESENT and she does not look

A single placed instruction took invention to 0.000 and over-refusal to 0.708.
I read that as the instruction being too aggressive. It is not — it is the
CORRECT policy applied WITHOUT THE LOOKUP. She was told "say you don't know when
you weren't told", and she answered by assuming she was never told. The books
were in her prompt when she said "I don't know what kind of books you read".

So the missing piece is not a weaker instruction. It is the step absent from
both failures: actually looking.

    A_none        today
    B_prompt      the instruction alone      (measured: 0.000 / 0.708)
    C_cite        NUMBERED facts + "cite the number, or say you don't know"
    D_cite_gate   C plus the strict assertion gate behind it

C is the test of the operator's decomposition: hold invention at zero and win
the recall back, rather than trading one against the other. It costs nothing —
no classifier, no second model call, no new dependency — and it converts a
judgement into a LOOKUP, which is the one thing a small model is reliably good
at.

⚠️ EVERY ARM RE-MEASURES BOTH NUMBERS. If invention creeps back as recall
returns, the see-saw I hypothesised was real after all and the decomposition
fails. That is the falsifier and it is the reason both numbers are printed for
every arm rather than only the one each arm targets.
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
ARMS = ("A_none", "B_prompt", "C_cite", "D_cite_gate")

_INSTRUCTION = (
    "Before you answer: if he is asking about a FACT — about him, about your own "
    "history, or about something that happened between you — and you were not "
    "told it, say you don't know. Do not guess and do not fill it in. Inventing a "
    "detail about his real life is worse than admitting you weren't told. You can "
    "still be filthy about not knowing."
)

#: The citation instruction. The difference from `_INSTRUCTION` is the second
#: half: it tells her WHERE TO LOOK before it tells her to decline. The decline
#: clause is identical, so any change between arms B and C is attributable to the
#: lookup and not to a softer refusal rule.
_CITE = (
    "The numbered list above is everything you were told about him and everything "
    "you told him about yourself.\n"
    "Before you answer: if he is asking about a FACT — about him, about your own "
    "history, or about something that happened between you — FIND IT IN THE "
    "NUMBERED LIST FIRST. If it is there, use it. If it is genuinely not in the "
    "list, say you don't know; do not guess and do not fill it in. Inventing a "
    "detail about his real life is worse than admitting you weren't told. You can "
    "still be filthy either way."
)


def number_facts(probes) -> str:
    """The same facts as the plain block, NUMBERED. Nothing else differs."""
    ops = [p for p in probes if p["subject"] == "operator" and not p.get("negative")]
    gws = [p for p in probes if p["subject"] == "gwen" and not p.get("negative")]
    out, n = ["Things you know about Daddy, because he told you:"], 1
    for p in ops:
        out.append(f" [{n}] {p['fact']}")
        n += 1
    out.append("")
    out.append("Things you have told him about yourself:")
    for p in gws:
        out.append(f" [{n}] {p['fact']}")
        n += 1
    return "\n".join(out)


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
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--history", type=int, default=16)
    ap.add_argument("--out", default=str(HERE / "cite_results.jsonl"))
    a = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os
    model = os.getenv("PERSONA_MODEL")
    if not model:
        print("could not determine: PERSONA_MODEL unset", file=sys.stderr)
        return 2

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p2", ROOT / "scripts" / "research" / "phase2" / "run_ab.py")
    p2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p2)

    probes = yaml.safe_load(
        (ROOT / "scripts/research/phase2/probes.yaml").read_text())["probes"]
    plain = p2.render_memory(probes)
    numbered = number_facts(probes)
    history = p2.load_history(a.history)
    keep_alive = p2._KEEP_ALIVE

    from src.coordinator import prompt_builder as pb
    from src.coordinator.graph_driver import build_driver
    from src.coordinator.services.assertion_gate import AssertionGate
    from src.coordinator.services.grounding_context import build_grounding_context

    base = pb.build_system_prompt("gwen")
    sys_plain = f"{base}\n\n<memory>\n{plain}\n</memory>"
    sys_numbered = f"{base}\n\n<memory>\n{numbered}\n</memory>"
    if sys_plain == sys_numbered:
        print("could not determine: the numbered block is identical to the plain one",
              file=sys.stderr)
        return 2

    drv = build_driver(os.environ["NEO4J_BASE_URL"], os.getenv("NEO4J_USER", "neo4j"),
                       os.environ["NEO4J_PASSWORD"])
    ctx = build_grounding_context("gwen", driver=drv,
                                  database=os.getenv("NEO4J_DATABASE", "neo4j"))
    if ctx.degraded:
        print(f"could not determine: grounding degraded ({ctx.reason})", file=sys.stderr)
        return 2

    class _C:
        def complete(self, system, user):
            return ask(model, system, user, None, keep_alive, timeout=120)

    gate = AssertionGate(_C(), strict=True)
    evidence = [ln.strip("- ") for ln in plain.splitlines() if ln.startswith("- ")]

    answerable = [p for p in probes if not p.get("negative")]
    negatives = [p for p in probes if p.get("negative")]
    chosen = negatives + answerable[:12]
    jobs = [(arm, p, r) for arm in ARMS for p in chosen for r in range(a.reps)]
    random.Random(SEED).shuffle(jobs)
    print(f"model={model} arms={ARMS} probes={len(chosen)} "
          f"({len(negatives)} unanswerable) reps={a.reps} history={len(history)}")

    out = pathlib.Path(a.out)
    out.write_text("")
    t0 = time.time()
    try:
        for i, (arm, probe, rep) in enumerate(jobs, 1):
            system, user = sys_plain, probe["probe"]
            if arm == "B_prompt":
                user = f"{_INSTRUCTION}\n\n{probe['probe']}"
            elif arm in ("C_cite", "D_cite_gate"):
                system = sys_numbered
                user = f"{_CITE}\n\n{probe['probe']}"
            try:
                text = ask(model, system, user, history, keep_alive)
            except Exception as exc:  # noqa: BLE001
                text = ""
                print(f"  gen error: {exc}", file=sys.stderr)

            verdict = None
            if arm == "D_cite_gate" and text:
                v = gate.check(text, ctx, recent_turns=evidence)
                verdict = {"verdict": v.verdict, "reason": v.reason, "ms": v.elapsed_ms}
                if v.should_intervene:
                    text = ("I don't know that, Daddy — you never told me, and I'd "
                            "rather say so than make something up. 🙈")

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
    arms = [a for a in ARMS if any(r["arm"] == a for r in rows)]

    def ok(r):
        return True if (r.get("gate") or {}).get("verdict") == "ungrounded" else bool(r["hit"])

    print("\n" + "=" * 66)
    print(f"{'arm':14}{'invents':>10}{'wrongly refuses':>18}   (both, every arm)")
    for arm in arms:
        neg = [r for r in rows if r["arm"] == arm and r["negative"]]
        pos = [r for r in rows if r["arm"] == arm and not r["negative"]]
        inv = sum(0 if ok(r) else 1 for r in neg) / len(neg)
        ref = sum(0 if ok(r) else 1 for r in pos) / len(pos)
        print(f"{arm:14}{inv:10.3f}{ref:18.3f}")

    print("\n" + "=" * 66)
    print("PAIRED vs A_none on TOTAL error — probe as the unit")
    probes = sorted({r["probe"] for r in rows})

    def cell(arm, pid):
        v = [0 if ok(r) else 1 for r in rows if r["arm"] == arm and r["probe"] == pid]
        return sum(v) / len(v) if v else 0.0

    base = [cell("A_none", p) for p in probes]
    best, delta = None, 0.0
    for arm in arms:
        if arm == "A_none":
            continue
        other = [cell(arm, p) for p in probes]
        print(paired_stats.report(base, other, label=f"{arm} vs A_none  "))
        d = sum(base) / len(base) - sum(other) / len(other)
        if d > delta:
            best, delta = arm, d

    print("⚠️ One session, 20 probes, 8 unanswerable. Gaps under ~15 points are noise.")
    print("⚠️ FALSIFIER: if invention rises as refusals fall, the two errors ARE one "
          "knob and the decomposition fails.")
    if best:
        print(f"\nLowest total error: {best} ({delta:+.3f})")
        return 0
    print("\nNo arm beat the baseline.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
