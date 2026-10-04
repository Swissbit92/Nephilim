#!/usr/bin/env python3
"""INV-4 — the image-prompt extractor is never given conversation history.

Exits 1 when `extract()`'s signature grows a parameter that could carry prior
turns, or when the module forwards history into the chat call.

WHY THIS IS A SAFETY BOUNDARY AND NOT A STYLE RULE. Measured 2026-10-04 in
the paired A/B: with 8 messages of real history, the native tool-call path
asked to paint "a quiet harbour at sunrise" composed a subject lifted
VERBATIM from a different image request eight messages earlier in that
session, and would have generated that picture instead. Confirmed against
`data/chats.db`. Real history does not merely suppress a tool call — when
the call fires under history, the arguments can come from the conversation
rather than from the request.

`extract()` is immune only because it passes no history at all. That is one
line of absence, which is exactly the kind of property a refactor removes
while every test still passes: adding `history=` would look like an
improvement ("give it context so it understands pronouns"), the unit tests
assert the message LIST shape and would be updated to match, and the
regression would be invisible until a user received a picture of something
they asked for last week.

So the guard is on the SIGNATURE. Any new parameter here is a decision that
must be made deliberately and re-justified against this file, rather than
arrived at.

PARSES THE SOURCE rather than importing: `invariants_run.py` runs under the
system interpreter, which has no pydantic, so an importing check would exit
2 ("could not determine") on every machine — and a check that can never run
is worse than none.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET = ROOT / "src" / "coordinator" / "services" / "image_gen" / "extract.py"

#: The signature as measured-and-justified. Anything else must come here first.
ALLOWED_PARAMS = {"message", "client", "model", "system_prompt"}

#: Names that would carry prior turns. Checked as a substring, so `history`,
#: `chat_history`, `prior_messages` and `context` are all caught.
HISTORY_LIKE = ("history", "messages", "context", "transcript", "turns")


def main() -> int:
    if not TARGET.exists():
        print(f"INV-4 could not run: {TARGET} is missing", file=sys.stderr)
        return 2

    try:
        tree = ast.parse(TARGET.read_text())
    except SyntaxError as exc:
        print(f"INV-4 could not run: {TARGET} does not parse ({exc})", file=sys.stderr)
        return 2

    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "extract"),
        None,
    )
    if fn is None:
        print("INV-4 could not run: no `extract` function in extract.py",
              file=sys.stderr)
        return 2

    args = fn.args
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)

    failures = []

    unexpected = [n for n in names if n not in ALLOWED_PARAMS]
    if unexpected:
        failures.append(
            f"extract() gained parameter(s) {unexpected}. The signature is the "
            f"containment boundary: under real history a generation request "
            f"can be answered with a subject copied from an earlier turn. If "
            f"this parameter genuinely cannot carry prior turns, add it to "
            f"ALLOWED_PARAMS in this file and say why."
        )

    smells = [n for n in names
              if any(h in n.lower() for h in HISTORY_LIKE)]
    if smells:
        failures.append(
            f"extract() takes {smells}, which can carry prior turns. This is "
            f"the exact defect the A/B measured on the path this replaced."
        )

    # ...and nothing history-shaped may be forwarded into the chat call.
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and any(h in kw.arg.lower() for h in HISTORY_LIKE):
                    # `messages=` is how the chat API is called at all; it is
                    # only a problem if it carries more than the two turns the
                    # unit tests pin. That shape is asserted there, not here.
                    if kw.arg == "messages":
                        continue
                    failures.append(
                        f"a call inside extract() forwards `{kw.arg}=`, which "
                        f"can carry prior turns."
                    )

    if failures:
        print("INV-4 VIOLATED — the extractor may now receive history:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1

    print("INV-4 ok — the image-prompt extractor takes no conversation history")
    return 0


if __name__ == "__main__":
    sys.exit(main())
