#!/usr/bin/env python3
"""Does conversation history suppress native tool calling? Measure it.

WHY THIS EXISTS. The image feature was unreliable in live use while measuring
6/6 in isolation. The difference turned out to be conversation history, and
the effect is not specific to the image tool — `image_search`, which has
shipped for months, degrades the same way. Before changing the tool path this
needs measuring properly rather than from four spot checks.

THE MECHANISM, from Ollama's source: `/api/chat` with `tools=` does NOT use
grammar-constrained decoding. The model generates freely and Ollama then
scans the raw stream for a tag — `[TOOL_CALLS]` for the Mistral parser. Any
leading prose and the call is silently discarded: empty `tool_calls`, no
error, the prose becomes the reply. A roleplay history primes prose.

Run:  .venv/bin/python scripts/research/tool_calling_vs_history.py [--reps N]

Output is a table of firing rate by (persona, tool, history depth), plus the
real-history vs synthetic-history contrast — because if SYNTHETIC history
does not reproduce it, the cause is the content of the conversation and not
its length, which points at a different fix.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SESSIONS = {
    "gwen": "9bf3fa08-5287-40f1-b9c6-615542b546ca",
    "nephilim_eeva": "dcc3693d-0aff-41ef-bd92-d943d008cb3e",
}
PROBES = {
    "generate_image": "use your image generator: a red fox sitting in deep snow at dusk",
    "image_search": "find me images of a red fox in snow",
}
DEPTHS = (0, 2, 4, 8, 16)


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
    out = []
    for i in range(limit // 2):
        out.append({"role": "user", "content": f"What is {i} plus {i}?"})
        out.append({"role": "assistant", "content": f"That is {i * 2}."})
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=6)
    args = ap.parse_args()

    from src.coordinator.config import get_persona_sampling_overrides
    from src.coordinator.persona_memory import build_system_prompt, get_persona_card
    from src.coordinator.services.tool_brain_service import ToolBrainService
    from src.coordinator.services.tool_interceptor import ToolCallInterceptor
    from src.coordinator.tools import registrations  # noqa: F401
    from src.coordinator.tools.executor_bindings import bind_web_executors
    from src.coordinator.tools.registry import registry

    bind_web_executors()
    for name in PROBES:
        registry.bind_executor(name, lambda a, c: "ok")
    svc = ToolBrainService(interceptor=ToolCallInterceptor())

    print(f"reps={args.reps} per cell; one tool offered; prose_expected=False\n")
    header = "persona".ljust(16) + "tool".ljust(17) + "hist " + \
        "  ".join(f"d{d:<3}" for d in DEPTHS)
    print(header)
    print("-" * len(header))

    for persona, sid in SESSIONS.items():
        card = get_persona_card(persona)
        sysp = build_system_prompt(persona, include_examples=True)
        ov = get_persona_sampling_overrides(card)
        for tool, msg in PROBES.items():
            spec = registry.get(tool)
            if spec is None:
                continue
            for kind, maker in (("real", lambda d: real_history(sid, d)),
                                ("synth", synthetic_history)):
                cells = []
                for depth in DEPTHS:
                    hist = maker(depth)
                    fired = 0
                    for _ in range(args.reps):
                        try:
                            r = svc.run(persona_card=card, system_prompt=sysp,
                                        user_message=msg, history=hist,
                                        tools=[spec.definition()],
                                        sampling_overrides=ov,
                                        prose_expected=False)
                            fired += tool in [t["tool"] for t in r.tool_trace
                                              if t.get("allowed")]
                        except Exception:
                            pass
                    cells.append(f"{fired}/{args.reps}")
                print(persona.ljust(16) + tool.ljust(17) + kind.ljust(5) +
                      "  ".join(c.ljust(4) for c in cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
