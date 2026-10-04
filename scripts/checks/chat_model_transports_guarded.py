#!/usr/bin/env python3
"""INV-1 — every chat-model transport goes through the arbiter guard.

Exits 1 and names the offenders when a module can cause Ollama to load the
CHAT model without importing `guard_chat_model`.

Why a script and not only a test: a test protects the code that existed when
it was written. This is about code that does not exist yet. There are FIVE
distinct transports to Ollama in this repo — httpx, langchain-ollama
constructed in two separate places, the `ollama` package, and raw probes — so
there is no single socket to guard, and the sixth will be added by someone who
never read the test. The rule is on the shape, and a script does not care how
long ago the shape was agreed.

Embeddings are out of scope on purpose: `bge-m3` is 0.63 GiB against the chat
model's 16-19 GiB and sits on the routing hot path, so gating it would cost a
reload on every routing decision to save about 1% of the memory.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "coordinator"

TRANSPORT = re.compile(r"OllamaLLM\s*\(|ollama\.Client\s*\(|/api/generate|/api/chat")

#: Modules that mention a transport but cannot load a model, with the reason.
ALLOWED = {
    "services/ollama_admin.py": "the evictor itself — guarding it would deadlock",
    "config/llm.py": "settings prose, no call",
    "ollama_utils.py": "/api/tags only; does not load a model",
    "server.py": "/api/version health probe",
}


def main() -> int:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        if TRANSPORT.search(text) and "guard_chat_model" not in text:
            offenders.append(rel)

    if offenders:
        print("INV-1 VIOLATED — these reach a chat-model transport unguarded:")
        for rel in offenders:
            print(f"  src/coordinator/{rel}")
        print()
        print("Add `guard_chat_model(\"<what>\")` at the call, or add the file to")
        print("ALLOWED in this script with a reason it cannot load a model.")
        return 1

    print(f"INV-1 ok — every chat-model transport is guarded "
          f"({len(ALLOWED)} documented exemptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
