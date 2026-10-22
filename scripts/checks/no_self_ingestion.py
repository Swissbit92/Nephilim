#!/usr/bin/env python3
"""Her own words are never ingested as facts about the operator.

Scans every fact-extraction path for a renderer that puts a NON-USER turn's
CONTENT into the model's input. Position is fine — a redaction marker keeps the
conversational shape — but content is not.

🔴 WHY A SCRIPT AND NOT A TEST. There are two extraction paths and BOTH had this
defect; fixing one would have left the other open until `MEMORY_FACTS_ENABLED`
flipped. A third will be added by someone who has not read either test. The
transport invariant in this repo rests on the same argument: a constraint stated
once is least salient exactly when it is about to be broken, and the thing doing
the forgetting is the thing being asked to remember.

MEASURED 2026-10-21. She said "Eggs benedict and apple juice"; the extractor
read HER REPLY and wrote "User eats eggs benedict and apple juice for breakfast"
as a fact about him. After the self-facts section shipped she recited her own
appearance and it was filed under him — `Assistant's eyes are a deep ocean blue`
in the operator's fact list. 38 contaminated rows in ~20 minutes. The loop did
not exist before the recall frame made her recite stored facts at all.

Exit: 0 every path redacts · 1 a path renders assistant content · 2 could not
determine (NOT a pass).
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Every module that formats a transcript for a fact extractor. LISTED, not
#: globbed, for the same reason `CYPHER_SOURCES` is: adding an extraction path
#: should be a decision someone makes here. ⚠️ And the list has to be TRUE for
#: that sentence to mean anything — a path missing from it is invisible.
EXTRACTION_PATHS = (
    (ROOT / "src" / "coordinator" / "fact_extractor.py", "_format_messages"),
    (ROOT / "src" / "coordinator" / "triplet_extractor.py", "_format"),
)

#: The marker that proves position is kept without content.
MARKER_NAME = "_REDACTED_TURN"


def _renders_non_user_content(fn: ast.FunctionDef) -> tuple[bool, str]:
    """True when a non-user branch can append message CONTENT.

    The shape that is safe: a branch testing the role, whose body appends the
    marker and then `continue`s. The shape that is not: any append of a value
    derived from `content` that is reachable without a role test.
    """
    has_role_guard = False
    uses_marker = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            src = ast.unparse(node)
            if '"role"' in src or "'role'" in src:
                if "!=" in src and "user" in src:
                    has_role_guard = True
        if isinstance(node, ast.Name) and node.id == MARKER_NAME:
            uses_marker = True

    if not has_role_guard:
        return True, (
            "no `role != \"user\"` guard — every turn's content reaches the "
            "model input"
        )
    if not uses_marker:
        return True, (
            f"has a role guard but never appends {MARKER_NAME}; the turn's "
            "POSITION must be kept or a short user reply loses its antecedent"
        )
    return False, "redacts non-user content and keeps the position"


def main() -> int:
    failures: list[str] = []
    checked = 0

    for path, fn_name in EXTRACTION_PATHS:
        if not path.exists():
            print(f"could not determine: {path} does not exist", file=sys.stderr)
            return 2
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as e:
            print(f"could not determine: {path} does not parse: {e}", file=sys.stderr)
            return 2

        fns = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == fn_name]
        if not fns:
            print(f"could not determine: {path.name} has no {fn_name}()",
                  file=sys.stderr)
            return 2

        bad, why = _renders_non_user_content(fns[0])
        checked += 1
        rel = path.relative_to(ROOT)
        if bad:
            failures.append(f"{rel}::{fn_name} — {why}")
        else:
            print(f"  ok    {rel}::{fn_name} — {why}")

    if not checked:
        print("could not determine: no extraction path was checked", file=sys.stderr)
        return 2

    if failures:
        print(f"\n{len(failures)} extraction path(s) can ingest her own words "
              f"as facts about the operator:", file=sys.stderr)
        for f in failures:
            print(f"  FAIL  {f}", file=sys.stderr)
        print("\nMeasured consequence: 38 contaminated rows in ~20 minutes, "
              "including facts about HER filed under HIM.", file=sys.stderr)
        return 1

    print(f"\nOK: {checked} extraction path(s) redact non-user content.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
