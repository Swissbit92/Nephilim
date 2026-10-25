#!/usr/bin/env python3
"""INV-13 — every chat-model transport passes the thinking control.

Exits 1 and names the offenders when a module opens a chat-model transport
without threading `OLLAMA_REASONING` into it.

WHY THIS IS A SCRIPT AND NOT ONLY A TEST, and why it is a sibling of INV-1
rather than part of it: INV-1 asks whether a transport is *arbiter-guarded* —
a memory question. This asks whether it passes `think` — a correctness
question. They happen to enumerate the same five transports, so the regex is
shared deliberately; if a sixth is added, both checks should fail at once.

WHAT WENT WRONG, so the next person does not re-derive it. `think` is a
TOP-LEVEL key on `/api/generate` and `/api/chat`, never a sampler option.
Ollama validates the `options` dict against a fixed list and answers HTTP 200
with a WARN log for anything it does not recognise. So a `think` placed inside
`options` is accepted, ignored, and indistinguishable from working — and that
is precisely how this control sat dead on the HTTP transport while reading as
fully configured in `.env`.

The failure it prevents is the worst shape available: not an exception, not a
refusal, but an EMPTY STRING. A reasoning model spends the entire
`num_predict` budget on its thinking stream and returns `content == ""`.
Nothing raises. The refusal detector downstream then scores the empty string.
Measured on `gemma4:26b` through the live tool-brain path, 2026-10-25:

    think omitted   10 of 12 replies EMPTY   (thinking median 1462 chars)
    think=False      0 of 12 replies EMPTY   (thinking 0 chars)

Embeddings are out of scope for the same reason as INV-1: an embedder has no
thinking stream.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "coordinator"

#: Deliberately identical to INV-1's regex — see the module docstring.
TRANSPORT = re.compile(r"OllamaLLM\s*\(|ollama\.Client\s*\(|/api/generate|/api/chat")

#: Any of these at the call site counts as threading the control through.
THREADED = re.compile(r"wire_think|\breasoning\b|\bthink\b")

#: Modules that mention a transport but cannot be served by a reasoning model,
#: with the reason. A thinking stream is the hazard; no model load, no hazard.
ALLOWED = {
    "services/ollama_admin.py": "evictor — posts keep_alive=0, never generates",
    "config/llm.py": "settings prose, no call",
    "ollama_utils.py": "/api/tags only; does not load a model",
    "server.py": "/api/version health probe",
    "services/ollama_think.py": "the control itself",
}


def main() -> int:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        if TRANSPORT.search(text) and not THREADED.search(text):
            offenders.append(rel)

    if offenders:
        print("INV-13 VIOLATED — these open a chat transport without passing `think`:")
        for rel in offenders:
            print(f"  src/coordinator/{rel}")
        print()
        print("A reasoning model on this path returns an EMPTY STRING, not an error.")
        print("Thread it through: `**wire_think(get_settings().ollama.reasoning)` for an")
        print("`ollama` client call, or `reasoning=cfg.reasoning` for OllamaLLM. If the")
        print("module genuinely cannot load a generating model, add it to ALLOWED with")
        print("the reason.")
        return 1

    print(f"INV-13 ok — every chat-model transport passes the thinking control "
          f"({len(ALLOWED)} documented exemptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
