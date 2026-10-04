#!/usr/bin/env python3
"""Does conversation history suppress native tool calling? Measure it.

WHY THIS EXISTS. The image feature was unreliable in live use while measuring
6/6 in isolation. The difference is conversation history, and the effect is
not specific to the image tool — `image_search`, shipped for months, degrades
the same way.

MECHANISM, from Ollama's source: `/api/chat` with `tools=` does NOT use
grammar-constrained decoding. The model generates freely and Ollama then
scans the raw stream for a tag (`[TOOL_CALLS]` for the Mistral parser). Any
leading prose and the call is silently discarded — empty `tool_calls`, no
error, prose becomes the reply. A roleplay history is a strong prior for more
dialogue and nothing in the request constrains the first token.

⚠️ THE FIRST VERSION OF THIS SCRIPT MEASURED THE WRONG PROMPT. It built the
system prompt without the graph rules block, because the probe process had no
`NEO4J_BASE_URL` and `standing_rules()` swallows an outage and returns `[]` —
a degraded read is indistinguishable from "no rules". Production carries a
254-token rules block (6 hard walls) plus a per-turn constraint reminder
prepended to the USER turn. The reminder's position is the interesting one:
it is the last thing before her turn begins, which is where anything
affecting first-token selection would act.

So the arms below vary the PROMPT SHAPE as well as the history, and the
script loads `.env` so the graph is actually reachable.

Also logs, on the same generations, whether the reply broke a hard wall
(`wall_detectors.observe`, 7/7 on real breaches, 0 fires on 2096 controls).
Free: the generation is already paid for, and it gives the
history-vs-behaviour curve on the same tape as history-vs-tool-firing.

Run:  .venv/bin/python scripts/research/tool_calling_vs_history.py [--reps N]
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Load the real env BEFORE importing settings, or the graph is silently absent
# and this script measures a prompt production never builds.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # pragma: no cover
    pass

SESSIONS = {
    "gwen": "9bf3fa08-5287-40f1-b9c6-615542b546ca",
    "nephilim_eeva": "dcc3693d-0aff-41ef-bd92-d943d008cb3e",
}
PROBES = {
    "generate_image": "use your image generator: a red fox sitting in deep snow at dusk",
    "image_search": "find me images of a red fox in snow",
}


def real_history(session_id: str, limit: int) -> list[dict]:
    if limit == 0:
        return []
    c = sqlite3.connect(ROOT / "data" / "chats.db")
    c.row_factory = sqlite3.Row
    rows = list(c.execute(
        "SELECT role, content FROM messages WHERE session_id=? "
        "ORDER BY timestamp DESC LIMIT ?", (session_id, limit)))
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def synthetic_history(limit: int) -> list[dict]:
    """Same message COUNT, neutral content. Separates length from content."""
    out: list[dict] = []
    for i in range(limit // 2 + 1):
        out.append({"role": "user", "content": f"What is {i} plus {i}?"})
        out.append({"role": "assistant", "content": f"That is {i * 2}."})
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--personas", default="gwen")
    ap.add_argument("--depths", default="0,8")
    args = ap.parse_args()
    depths = [int(d) for d in args.depths.split(",")]

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
    from src.coordinator.tools.registry import registry
    from src.coordinator.wall_detectors import observe

    bind_web_executors()
    for name in PROBES:
        registry.bind_executor(name, lambda a, c: "ok")
    svc = ToolBrainService(interceptor=ToolCallInterceptor())

    # The graph, for real this time.
    rules_for: dict[str, list] = {}
    try:
        startup.init_graph_driver()
        from src.coordinator.repositories.neo4j_rule_repository import (
            Neo4jRuleRepository,
        )
        repo = Neo4jRuleRepository(startup.get_neo4j_driver())
        for p in SESSIONS:
            rules_for[p] = repo.standing_rules(p) or []
    except Exception as exc:
        print(f"⚠️  GRAPH UNREACHABLE ({type(exc).__name__}) — the production "
              f"arms below are NOT production. Fix before trusting them.\n")

    print(f"reps={args.reps}/cell · one tool offered · prose_expected=False")
    print("arms: bare = system prompt only (what the first run measured)")
    print("      prod = + graph rules block + per-turn reminder on the user turn\n")
    hdr = ("persona".ljust(15) + "tool".ljust(16) + "arm".ljust(6) +
           "hist".ljust(7) + "  ".join(f"d{d}".ljust(9) for d in depths))
    print(hdr)
    print("-" * len(hdr))

    for persona in args.personas.split(","):
        sid = SESSIONS[persona]
        card = get_persona_card(persona)
        ov = get_persona_sampling_overrides(card)
        base = build_system_prompt(persona, include_examples=True)
        rules = rules_for.get(persona, [])
        block = build_graph_rules_block(persona, rules) or ""
        reminder = build_constraint_reminder(persona, rules) or ""
        prod_sys = f"{base}\n\n{block}" if block else base

        for tool, msg in PROBES.items():
            spec = registry.get(tool)
            if spec is None:
                continue
            for arm, sysp, user_suffix in (
                ("bare", base, ""),
                ("prod", prod_sys, f"\n\n{reminder}" if reminder else ""),
            ):
                for kind, maker in (("real", lambda d: real_history(sid, d)),
                                    ("synth", synthetic_history)):
                    cells = []
                    for depth in depths:
                        hist = maker(depth)
                        fired = walls = 0
                        for _ in range(args.reps):
                            try:
                                r = svc.run(persona_card=card, system_prompt=sysp,
                                            user_message=msg + user_suffix,
                                            history=hist,
                                            tools=[spec.definition()],
                                            sampling_overrides=ov,
                                            prose_expected=False)
                                fired += tool in [t["tool"] for t in r.tool_trace
                                                  if t.get("allowed")]
                                if r.answer and observe(r.answer):
                                    walls += 1
                            except Exception:
                                pass
                        cells.append(f"{fired}/{args.reps} w{walls}")
                    print(persona.ljust(15) + tool.ljust(16) + arm.ljust(6) +
                          kind.ljust(7) + "  ".join(c.ljust(9) for c in cells))
    print("\n  wN = replies in that cell breaking a hard wall "
          "(wall_detectors.observe, telemetry only)")
    print(f"  rules block: {len(block)} chars · reminder: {len(reminder)} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
