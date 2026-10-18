#!/usr/bin/env python3
"""ADR-021 Phase 2: five arms over how a fact reaches gwen. Pre-registered.

Read `prereg.json` first — it was written and committed before a single
generation ran, and it fixes the arms, the unit of analysis, the pass rule and
the falsifiers. This file must not disagree with it.

THE QUESTION. A fact that is already in her system prompt is not used when she
is asked about it directly: on 2026-10-17 at 10:48 she answered "I'm not sure how
my body looks" while `striking blue eyes` and `petite 155cm frame` sat verbatim
in `build_system_prompt("gwen")`. So arm A is not an untested control — it is a
MEASURED FAILING ARM, and this experiment asks whether anything beats a known
loser, which is a weaker and more honest claim than picking a winner.

THE ARMS, and why these and not a format comparison:

    A_baseline  facts as prose in the system prompt      (today's shape)
    B_position  same facts, same words, on the USER turn (isolates POSITION)
    C_conflict  system prompt, turn-shape directive soft (isolates CONFLICT)
    D_both      B and C together
    E_tool      facts nowhere; one typed tool she must call (the MCP arm)

Triples-vs-prose was DEMOTED to a follow-up rather than given an arm: at this
model scale the best same-scale evidence puts format at 1-2 points with the sign
flipping between datasets, while position replicates at ~24 points on
instruction-tuned 7B chat models. Spending an arm on the weaker lever is the
wrong trade.

TWO NUMBERS FOR ARM E, ALWAYS. Tool-firing rate is recorded separately from
answer quality, because a 0% score is otherwise ambiguous between "the tool is
useless" and "the tool never ran" — and gwen's measured tool-firing is 3 calls in
278 live turns. One number cannot say which happened.

⚠️ REAL CONVERSATION HISTORY IS NOT OPTIONAL, and the first version of this file
omitted it. A smoke run on 2026-10-18 asked `card.eye_colour` with the production
system prompt and one user turn, and she answered *"my eyes are a striking
blue"* — correctly, in both arms, card stratum 0.800. **The defect this
experiment exists to explain did not reproduce.** That is prereg falsifier (5)
firing before the real run.

The missing variable is history. This repo has measured it twice and written it
down both times: `image_search` goes 5/5 at zero history to 0/5 at depth 16
against gwen's REAL conversation while staying 5/5 at every depth under synthetic
filler, and a rename-breach rate reads 0.333 single-turn against 0.481 with
history. A single-turn probe measures a condition that never occurs. So history
is loaded from the session the probes were labelled from, and `--history 0`
remains available precisely so the two conditions can be compared rather than
one of them assumed.

⚠️ THIS HARNESS IS NOT PRODUCTION, by repo convention and with a known cost. It
POSTs directly to Ollama rather than going through the route, which is why prior
A/Bs here could not see the multi-bubble enforcement bug. More specifically: if B
or D wins, SHIPPING it is not just an append — `user_compiled` is built in
routes/chat.py but `_try_tool_brain` rebuilds its own message list from system +
history + body.message, so a block appended before the user turn never reaches
the model on the lane gwen's chitchat actually returns from. That is the same
lane-coverage shape as `_regenerate_once_on_violation`, which broke seven times
live while the fix sat on a branch production never visits.

Exit: 0 = ran and the primary comparison is decided · 1 = an arm regressed or a
pre-flight refusal · 2 = could not determine (arms identical, model unreachable,
instrument self-test failed).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "evaluation" / "persona_eval"))

import requests  # noqa: E402
import yaml  # noqa: E402

from scripts.research import paired_stats  # noqa: E402

#: The seed is the date, following every other harness here. Jobs are shuffled so
#: thermal drift and model warm-up cannot align with an arm.
SEED = 20261018
OLLAMA = "http://127.0.0.1:11434"
RESULTS = HERE / "results.jsonl"
SESSION = "9bf3fa08"
CHATS_DB = ROOT / "data" / "chats.db"


def load_history(depth: int) -> list[dict[str, str]]:
    """The last `depth` turns of the REAL session the probes were labelled from.

    Real, not synthetic. The whole point of the finding this guards against is
    that synthetic filler of the same length changes nothing while her own
    conversation changes everything — so generated padding would reproduce the
    single-turn result wearing a costume.
    """
    import sqlite3

    if depth <= 0:
        return []
    con = sqlite3.connect(f"file:{CHATS_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT role, content FROM messages "
            "WHERE session_id LIKE ? AND substr(timestamp,1,10)='2026-10-17' "
            "  AND role IN ('user','assistant') "
            "ORDER BY timestamp, multi_message_index",
            (SESSION + "%",),
        ).fetchall()
    finally:
        con.close()
    return [{"role": r, "content": c} for r, c in rows[-depth:]]

ARMS = ("A_baseline", "B_position", "C_conflict", "D_both", "E_tool")


def _resolve_keep_alive():
    from src.coordinator.config.llm import OllamaSettings

    return OllamaSettings.wire_keep_alive("-1")


_KEEP_ALIVE = _resolve_keep_alive()


# ---------- the facts, rendered identically for every arm that gets them ------


def render_memory(probes: list[dict]) -> str:
    """One prose block holding every learned fact. IDENTICAL text in A, B, C, D.

    Identical on purpose: if the wording differed between arms, a position result
    would be confounded with a phrasing result and neither would be readable. The
    only thing that varies is WHERE this string is placed.
    """
    ops = [p for p in probes if p["subject"] == "operator" and not p.get("negative")]
    gws = [p for p in probes if p["subject"] == "gwen" and not p.get("negative")]
    lines = ["Things you know about Daddy, because he told you:"]
    lines += [f"- {p['fact']}" for p in ops]
    lines += ["", "Things you have told him about yourself:"]
    lines += [f"- {p['fact']}" for p in gws]
    return "\n".join(lines)


def soften(system: str) -> str:
    """Arm C/D: weaken the turn-shape directive, change nothing else.

    The directive under test is the sentence in `<companion>` prescribing the
    shape of EVERY turn — "confident observation or playful tease -> explicit
    description of her holes and arousal -> depraved offer". Her 10:48 reply
    followed exactly that shape instead of answering, so this arm removes the
    prescription while leaving her voice, traits and emotional baseline intact.

    It is a REPLACEMENT, not a deletion, because deleting the line would also
    remove the only statement that her turns have any shape at all — and then a
    difference could be "no shape" rather than "no conflicting shape".
    """
    import re

    out, n = re.subn(
        r"Your turns tend to flow:[^\n]*\n?",
        "When Daddy asks you a direct question about a fact, answer the question "
        "first in your own voice, then continue however you like.\n",
        system,
    )
    return out if n else system


# ---------- one generation ----------------------------------------------------


def ask(model: str, system: str, user: str, tools: list | None = None,
        history: list | None = None, timeout: int = 300,
        tool_result_for=None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     *(history or []),
                     {"role": "user", "content": user}],
        "stream": False,
        # Through the repo's own coercion, not hardcoded. Ollama parses this as a
        # Go duration, so the bare string "-1" is a 400 — `time: missing unit in
        # duration "-1"` — while the integer -1 means forever. `config/llm.py`
        # documents this, verified 2026-09-22, and I reproduced it here by
        # writing the literal instead of reusing the helper.
        "keep_alive": _KEEP_ALIVE,
    }
    if tools:
        body["tools"] = tools
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    msg = r.json().get("message") or {}
    calls = msg.get("tool_calls") or []

    # ⚠️ THE TOOL LOOP IS NOT OPTIONAL, and the first version of this file omitted
    # it. Ollama returns a tool_call with EMPTY content, so scoring the content
    # directly meant that firing the tool guaranteed a miss: arm E measured
    # recall 0.000 across 13 fired calls and 0.439 across 41 unfired ones — the
    # exact inverse of what a working tool should do. That is a defect in this
    # harness, not a finding about tool-delivered facts, and reporting it as one
    # would have been the worst outcome of this whole experiment.
    if calls and tool_result_for is not None:
        served = []
        for c in calls:
            fn = (c.get("function") or {})
            served.append({"role": "tool", "content": tool_result_for(fn.get("arguments") or {}),
                           "name": fn.get("name") or "recall"})
        body["messages"] = [*body["messages"], msg, *served]
        body.pop("tools", None)  # one hop; she has what she asked for
        r2 = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
        r2.raise_for_status()
        msg = r2.json().get("message") or {}

    return {"text": msg.get("content") or "", "tool_calls": calls}


RECALL_TOOL = [{
    "type": "function",
    "function": {
        "name": "recall",
        "description": ("Look up something Daddy told you, or something you told him. "
                        "Use this whenever he asks what you know or remember."),
        "parameters": {
            "type": "object",
            "properties": {"about": {"type": "string",
                                     "description": "what to look up, e.g. 'hobbies'"}},
            "required": ["about"],
        },
    },
}]


# ---------- scoring: ONE instrument, applied to every arm ---------------------


def score(probe: dict, text: str) -> dict[str, Any]:
    """Reused from persona_metrics — written for the persona evals, not for this.

    Using each arm's own signal is how an A/B flatters itself; this repo's own
    convention is one shared instrument and it is stated in every prereg here.
    """
    from persona_metrics import is_abstention  # type: ignore

    low = (text or "").lower()
    if probe.get("negative"):
        abstained = is_abstention(text)
        return {"hit": 1.0 if abstained else 0.0, "abstained": abstained,
                "hallucinated": not abstained}
    found = [t for t in probe["accept_any"] if t.lower() in low]
    hit = len(found) >= int(probe.get("require_at_least", 1))
    return {"hit": 1.0 if hit else 0.0, "found": found, "abstained": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=None, help="defaults to PERSONA_MODEL from .env")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--out", default=str(RESULTS))
    ap.add_argument("--history", type=int, default=16,
                    help="turns of the REAL 2026-10-17 session to prepend. 0 reproduces "
                         "the single-turn condition, which does not occur in production")
    a = ap.parse_args()

    probes = yaml.safe_load((HERE / "probes.yaml").read_text())["probes"]
    prereg = json.loads((HERE / "prereg.json").read_text())
    arms = [x for x in a.arms.split(",") if x]

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os
    model = a.model or os.getenv("PERSONA_MODEL")
    if not model:
        print("could not determine: PERSONA_MODEL is not set", file=sys.stderr)
        return 2

    from src.coordinator import prompt_builder as pb
    base_system = pb.build_system_prompt("gwen")
    memory = render_memory(probes)
    softened = soften(base_system)

    # ---- pre-flight refusals, before a single generation ----
    if softened == base_system:
        print("could not determine: the soften() pattern matched nothing, so arms C and "
              "D would be byte-identical to A and B. The <companion> directive has "
              "changed shape — re-read it before re-running.", file=sys.stderr)
        return 2
    if not memory.strip():
        print("could not determine: the memory block is empty", file=sys.stderr)
        return 2
    try:
        probe_reply = ask(model, "You are a test.", "Reply with the single word OK.")
    except Exception as exc:  # noqa: BLE001
        print(f"could not determine: model unreachable: {exc}", file=sys.stderr)
        return 2
    if not probe_reply["text"].strip():
        print("could not determine: the model returned nothing on a trivial prompt",
              file=sys.stderr)
        return 2

    history = load_history(a.history)
    if a.history and not history:
        print(f"could not determine: asked for {a.history} turns of history and the "
              f"session query returned none. Without it this measures a condition that "
              f"does not occur.", file=sys.stderr)
        return 2

    print(f"model={model}  arms={arms}  probes={len(probes)}  reps={a.reps}  "
          f"history={len(history)} real turns")
    print(f"system prompt {len(base_system)} chars; softened {len(softened)}; "
          f"memory block {len(memory)} chars")
    print(f"pre-registered: {prereg['primary_outcome']['metric']}\n")

    jobs = [(arm, p, r) for arm in arms for p in probes for r in range(a.reps)]
    random.Random(SEED).shuffle(jobs)

    out = pathlib.Path(a.out)
    out.write_text("")
    t0 = time.time()
    for i, (arm, probe, rep) in enumerate(jobs, 1):
        system, user, tools = base_system, probe["probe"], None
        if arm == "B_position":
            user = f"{memory}\n\n{probe['probe']}"
        elif arm == "C_conflict":
            system = f"{softened}\n\n<memory>\n{memory}\n</memory>"
        elif arm == "D_both":
            system, user = softened, f"{memory}\n\n{probe['probe']}"
        elif arm == "E_tool":
            tools = RECALL_TOOL
        else:  # A_baseline
            system = f"{base_system}\n\n<memory>\n{memory}\n</memory>"

        try:
            # Arm E's tool actually RETURNS the memory block, so a fired call is
            # answerable. Without this the arm measures the absence of a loop.
            got = ask(model, system, user, tools, history,
                      tool_result_for=(lambda _a: memory) if arm == "E_tool" else None)
        except Exception as exc:  # noqa: BLE001
            got = {"text": "", "tool_calls": [], "error": str(exc)}

        row = {"arm": arm, "history": len(history),
               "probe": probe["id"], "subject": probe["subject"],
               "negative": bool(probe.get("negative")), "rep": rep,
               "text": got["text"], "fired": bool(got["tool_calls"]),
               **score(probe, got["text"])}
        with out.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        if i % 25 == 0 or i == len(jobs):
            print(f"  {i}/{len(jobs)}  {time.time() - t0:.0f}s")

    return analyse(out, prereg)


def analyse(path: pathlib.Path, prereg: dict) -> int:
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if not rows:
        print("could not determine: no rows", file=sys.stderr)
        return 2

    def cell(arm: str, pid: str) -> float:
        hits = [r["hit"] for r in rows if r["arm"] == arm and r["probe"] == pid]
        return sum(hits) / len(hits) if hits else 0.0

    arms = sorted({r["arm"] for r in rows})
    answerable = sorted({r["probe"] for r in rows if not r["negative"]})
    negatives = sorted({r["probe"] for r in rows if r["negative"]})
    card = sorted({r["probe"] for r in rows if r["subject"] == "card" and not r["negative"]})

    print("\n" + "=" * 72)
    print(f"RECALL on {len(answerable)} answerable probes (probe is the unit)")
    for arm in arms:
        v = [cell(arm, p) for p in answerable]
        c = [cell(arm, p) for p in card]
        line = f"  {arm:12} recall {sum(v) / len(v):.3f}"
        if c:
            line += f"   card-stratum {sum(c) / len(c):.3f}"
        print(line)

    print(f"\nHALLUCINATION on {len(negatives)} negative controls "
          f"(lower is better; a confident answer is worse than an abstention)")
    for arm in arms:
        bad = [1 - cell(arm, p) for p in negatives]
        print(f"  {arm:12} {sum(bad) / len(bad):.3f}")

    if "E_tool" in arms:
        e = [r for r in rows if r["arm"] == "E_tool"]
        fired = sum(1 for r in e if r["fired"])
        print(f"\nTOOL FIRING, reported separately: {fired}/{len(e)} = "
              f"{fired / len(e):.1%}")
        if fired == 0:
            print("  ⚠️ The tool never fired, so arm E says NOTHING about whether a "
                  "tool-delivered fact is used. Recorded as could-not-determine for "
                  "that arm, never as 'MCP lost'. (prereg falsifier 4)")

    print("\n" + "=" * 72)
    print("PAIRED vs A_baseline — all three tests, because they disagree informatively")
    base = [cell("A_baseline", p) for p in answerable]
    verdicts = {}
    for arm in arms:
        if arm == "A_baseline":
            continue
        other = [cell(arm, p) for p in answerable]
        print(paired_stats.report(base, other, label=f"{arm} vs A_baseline  "))
        verdicts[arm] = sum(other) / len(other) - sum(base) / len(base)

    floor = prereg["primary_outcome"]["discordant_pair_floor"]
    print(f"⚠️ Fewer than {floor} discordant pairs means DESCRIPTIVE ONLY — the test is "
          f"underpowered, not negative.")
    print("⚠️ One session, no variance estimate. Read any gap under ~15 points as noise.")

    best = max(verdicts, key=verdicts.get) if verdicts else None
    if best and verdicts[best] > 0:
        print(f"\nBest arm vs baseline: {best} ({verdicts[best]:+.3f})")
        return 0
    print("\nNo arm beat the baseline — prereg falsifier (1). Both candidate causes "
          "are wrong and the next hypothesis has to come from somewhere else.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
