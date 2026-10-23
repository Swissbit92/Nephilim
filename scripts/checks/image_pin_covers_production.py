#!/usr/bin/env python3
"""INV-8 — the live image-extract pin exercises the prompt production builds.

Exits 1 when every `extract()` call in `tests/integration/test_image_extract_live.py`
omits `system_prompt`, or when that file reads operator facts from the live graph
instead of a frozen fixture.

WHY THIS IS A STANDING CONSTRAINT AND NOT A ONE-OFF FIX.

`extract()` is the grammar-constrained image-argument extractor, and
`extract.py:43-47` records the live file as a behaviour PIN on its field
separation, passing 8/8. Every call in it omitted `system_prompt`, which
defaults to `""`.

Production has not passed `""` since `routes/chat.py:876` began splicing
`extra_system_context` into `system`. Six hops, all live for gwen by default:

    chat_session_service.py:741  state.extra_system_context = _framed
    chat_session_service.py:869  -> ChatBody
    routes/chat.py:876           system = system + extra_system_context
    routes/chat.py:990           _try_tool_brain(system=system, ...)
    routes/chat.py:521           direct.queue_generation(system_prompt=system)
    image_gen/direct.py:69       extract(..., system_prompt=system_prompt)
    image_gen/extract.py:198     messages.append({"role": "system", ...})

So the pin measured a condition production had left behind, and was blind to
any regression the injected block causes.

⚠️ THE GAP WIDENS ON ITS OWN. The block is assembled from the operator's stored
facts, so it GROWS every time she learns something: 710 tokens, then 794, then
833 observed on three different days with no code change. The trim bound is
2000. An untested divergence that enlarges itself is exactly the shape a
one-off fix does not hold.

⚠️ INV-4 DOES NOT COVER THIS. It guards the extractor against conversation
HISTORY and asserts on the signature. The system prompt is a different channel
and nothing watched it. Two channels, one guarded.

⚠️ AND THE FROZEN-FIXTURE HALF MATTERS TOO. A pin that reads the live graph
cannot distinguish a model regression from a store that grew, which is how the
original measurement became uncomparable. The check therefore also refuses a
live `current_facts(` read inside the pin.

PARSES THE SOURCE rather than importing, like every other check here:
`invariants_run.py` runs under the system interpreter, which has no pydantic,
so an importing check would exit 2 ("could not determine") on every machine --
and a check that can never run is worse than none.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PIN = ROOT / "tests" / "integration" / "test_image_extract_live.py"


def main() -> int:
    if not PIN.exists():
        print(f"INV-8 COULD NOT DETERMINE: {PIN} is missing", file=sys.stderr)
        return 2

    src = PIN.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        print(f"INV-8 COULD NOT DETERMINE: {PIN.name} does not parse: {exc}",
              file=sys.stderr)
        return 2

    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else "")
        if name == "extract":
            calls.append(node)

    if not calls:
        # Guard the guard. An empty list would make the assertion below pass
        # vacuously, which is the blind-spot class this repo has recorded.
        print("INV-8 COULD NOT DETERMINE: no extract() calls found in "
              f"{PIN.name} -- this check can no longer see what it guards",
              file=sys.stderr)
        return 2

    with_sysp = [c for c in calls
                 if any(kw.arg == "system_prompt" for kw in c.keywords)]
    if not with_sysp:
        print(f"INV-8 VIOLATED: all {len(calls)} extract() calls in "
              f"{PIN.name} omit `system_prompt`, so it defaults to \"\". "
              "Production passes the whole persona system prompt including the "
              "~833-token numbered recall block. The pin is measuring a "
              "condition production left behind.", file=sys.stderr)
        return 1

    if "current_facts(" in src:
        print(f"INV-8 VIOLATED: {PIN.name} reads operator facts from the live "
              "graph. Freeze the block into a fixture: the live block grew "
              "710 -> 794 -> 833 tokens while nothing in the extractor "
              "changed, so a live read cannot tell a model regression from a "
              "store that grew.", file=sys.stderr)
        return 1

    print(f"OK: {len(with_sysp)} of {len(calls)} extract() call(s) in "
          f"{PIN.name} pass a production system_prompt, from a frozen fixture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
