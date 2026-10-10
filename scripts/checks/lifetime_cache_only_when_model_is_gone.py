#!/usr/bin/env python3
"""A process-lifetime cache for an in-voice line is allowed ONLY where the model is gone.

`persona_lines._MODEL_UNREACHABLE` names the situations that keep their first generation
forever. Every other situation regenerates when its permutation cycle drains, so the
number of phrasings a user sees grows with the number of events.

WHY THIS IS A STANDING CONSTRAINT RATHER THAN A ONE-OFF FIX. Measured 2026-10-10 over
gwen's live corpus: 9 of 127 comparable turns tripped an 8-token whole-history repetition
gate, and 9 of 9 were byte-identical replays of a cached status line rather than model
repetition, which was 0/127. One cached generation accounted for 6 of them. The repair was
to stop caching for the process lifetime wherever the model can actually answer.

The edit that would undo it looks like an optimisation: recycling costs one LLM call per
cycle, so adding a situation to `_MODEL_UNREACHABLE` makes a latency graph better and
silently restores the exact defect. Nothing fails. The lines stay in voice and grammatical
— they are simply the same lines forever, which is invisible to every test that checks
whether a line was produced.

So the rule is tied to the ONE fact that justifies an exception: the image worker warms a
situation immediately before `unload()` precisely because that line is said when the model
is gone. If a situation is in `_MODEL_UNREACHABLE` it must be warmed there, and if it is
warmed there it must be in `_MODEL_UNREACHABLE` — the two lists are the same claim written
twice, and this check is what keeps them one claim.

Exit 0 = the sets agree. Exit 1 = they do not, and the message names the direction.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINES = ROOT / "src" / "coordinator" / "services" / "persona_lines.py"
WORKER = ROOT / "src" / "coordinator" / "services" / "image_gen" / "worker.py"


def _unreachable() -> set[str] | None:
    """The situations persona_lines caches for the process lifetime."""
    tree = ast.parse(LINES.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_MODEL_UNREACHABLE"
                   for t in node.targets):
            continue
        # frozenset({...}) / frozenset([...]) / a bare set literal
        val = node.value
        if isinstance(val, ast.Call):
            if not val.args:
                # `frozenset()` / `set()` — empty is perfectly readable, and conflating
                # it with "cannot decide" produced a right exit code for a wrong reason,
                # which is the failure mode this file lectures about elsewhere.
                return set()
            val = val.args[0]
        if isinstance(val, ast.Set | ast.List | ast.Tuple):
            out = {e.value for e in val.elts if isinstance(e, ast.Constant)}
            if len(out) != len(val.elts):
                return None       # a non-literal member: cannot decide, do not guess
            return out
        return None
    return None


def _warmed() -> set[str] | None:
    """The situations the worker generates BEFORE it evicts the chat model."""
    tree = ast.parse(WORKER.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in {"warm", "warm_lines"}:
            continue
        # warm(persona_key, ("image_busy",)) — the situations are the 2nd positional
        # arg or the `situations` keyword.
        arg = None
        if len(node.args) >= 2:
            arg = node.args[1]
        for kw in node.keywords:
            if kw.arg == "situations":
                arg = kw.value
        if arg is None:
            continue              # relies on the default; the default is the declaration
        if not isinstance(arg, ast.Tuple | ast.List | ast.Set):
            return None
        vals = {e.value for e in arg.elts if isinstance(e, ast.Constant)}
        if len(vals) != len(arg.elts):
            return None
        found |= vals
    return found


def main() -> int:
    for path in (LINES, WORKER):
        if not path.exists():
            print(f"FAIL: {path.relative_to(ROOT)} is missing — cannot verify the "
                  f"lifetime-cache constraint, which is not a pass.")
            return 1

    unreachable = _unreachable()
    warmed = _warmed()

    # Undecidable is reported as a failure, never as a pass. A check that cannot read its
    # own subject and says nothing is indistinguishable from a check that found no problem.
    if unreachable is None:
        print("FAIL: could not read _MODEL_UNREACHABLE as a set of string literals in "
              "persona_lines.py. If it became computed, this check needs rewriting — it "
              "must not silently stop deciding.")
        return 1
    if warmed is None:
        print("FAIL: could not read the warmed situations as string literals in "
              "image_gen/worker.py. Same reasoning as above.")
        return 1

    if unreachable == warmed:
        pretty = ", ".join(sorted(unreachable)) or "(none)"
        print(f"OK: lifetime-cached situations == warmed-before-unload situations "
              f"({pretty}).")
        return 0

    cached_not_warmed = sorted(unreachable - warmed)
    warmed_not_cached = sorted(warmed - unreachable)
    print("FAIL: the lifetime-cache exception no longer matches the one fact that "
          "justifies it.")
    if cached_not_warmed:
        print(f"  cached for the process lifetime but NOT warmed before the model is "
              f"evicted: {cached_not_warmed}")
        print("  -> the model is reachable when these are said, so they must RECYCLE "
              "when their cycle drains. Caching them restores the measured defect: one "
              "generation replayed forever (9 of 9 flagged turns in the live corpus).")
    if warmed_not_cached:
        print(f"  warmed before eviction but NOT lifetime-cached: {warmed_not_cached}")
        print("  -> these are said while the model is GONE, so recycling them would call "
              "a model that cannot answer and degrade to the hardcoded fallback string.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
