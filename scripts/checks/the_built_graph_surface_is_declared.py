#!/usr/bin/env python3
"""Every label and rel in a Cypher literal appears in `graph_surface`, and vice versa.

`graph_surface.BUILT_LABELS`/`BUILT_RELS` is the operand C17 compares the question set
against. Half of it is derived from `identity_shapes` and cannot drift; the other half
is hand-written, because the rule and proposal paths build their Cypher as string
literals and nothing can enumerate those. A hand-written manifest that drifts is worse
than none: C17 would then pass by comparing the question set to a stale list, which is
the exact shape of a check that reports a clean run it never made.

So the manifest is re-derived here from the source on every invariant run, and the
check fails in BOTH directions — a new label in a Cypher string that nobody declared,
and a declared name no Cypher string mentions any more.

**Docstrings are excluded, and that is load-bearing.** `neo4j_rule_repository`'s module
docstring contains `(:Rule)-[:LEARNED_FROM]->(:Message)` as the shape a future write
path will take. A regex sweep reads that as built, and the first version of this check
reported `Message` and `LEARNED_FROM` as live schema — two names with no node, no edge
and no writer. Planned is not built, so the scan walks the AST and skips the string
literals Python treats as documentation.

Exit 0 = the manifest matches the source · 1 = it does not · 2 = could not determine.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "coordinator"

#: The files that build graph structure. Listed rather than globbed so that adding a
#: third writer is a decision someone makes here, not something that happens quietly.
CYPHER_SOURCES = (
    SRC / "repositories" / "identity_repository.py",
    SRC / "repositories" / "neo4j_rule_repository.py",
    SRC / "rule_proposals.py",
)

#: `(x:Label` / `(:Label` — a parenthesis is required, so a bare `:Label` inside prose
#: or a Python annotation cannot match.
_LABEL = re.compile(r"\(\s*[a-z0-9_]*((?::[A-Z][A-Za-z]+)+)")
#: `[:REL]->` / `-[r:REL]-` — the arrow or dash after the bracket is required, which is
#: what keeps a Python slice like `text[:_MAX_BYTES]` out of the relationship set.
_REL = re.compile(r"\[\s*[a-z0-9_]*:([A-Z][A-Z_]*)\s*\][-<>]")
#: `n:CurrentIdentity` in a REMOVE/SET clause, which has no parenthesis of its own.
_BARE_LABEL = re.compile(r"\b(?:REMOVE|SET)\s+[a-z0-9_]+((?::[A-Z][A-Za-z]+)+)")
#: `MERGE (n:{label}:CurrentIdentity ...)` — the f-string hole is filled from SHAPES at
#: runtime, so the literal carries `{label}` where a label would be. Dropped, not
#: guessed: the identity half of the manifest is derived from SHAPES already.
_FSTRING_HOLE = re.compile(r"\{[a-z_]+\}")


def _non_docstring_strings(path: pathlib.Path) -> list[str]:
    """Every string constant in the file except the ones Python reads as documentation."""
    tree = ast.parse(path.read_text())
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            # An f-string's literal halves arrive as separate Constant children, which
            # ast.walk reaches on its own. Nothing to add here.
            continue
    return out


def derive() -> tuple[set[str], set[str]]:
    labels: set[str] = set()
    rels: set[str] = set()
    for path in CYPHER_SOURCES:
        if not path.exists():
            raise FileNotFoundError(path)
        for text in _non_docstring_strings(path):
            text = _FSTRING_HOLE.sub("", text)
            for group in _LABEL.findall(text) + _BARE_LABEL.findall(text):
                labels |= {part for part in group.split(":") if part}
            rels |= set(_REL.findall(text))
    return labels, rels


def main() -> int:
    sys.path.insert(0, str(ROOT))
    try:
        from src.coordinator.graph_surface import BUILT_LABELS, BUILT_RELS
        derived_labels, derived_rels = derive()
    except Exception as exc:  # pragma: no cover - environment, not logic
        print(f"could not determine: {exc}")
        return 2

    if not derived_labels or not derived_rels:
        print("could not determine: the scan found no Cypher at all — "
              "the regexes or the source list are wrong, and an empty scan "
              "would otherwise agree with any manifest")
        return 2

    bad = []
    # Identity labels come from SHAPES via an f-string hole the scan cannot see, so only
    # the hand-written half is compared in the undeclared direction.
    for name in sorted(derived_labels - set(BUILT_LABELS)):
        bad.append(f"label {name!r} is built in Cypher but not declared in graph_surface "
                   f"— C17 would compare the question set against a stale list")
    for name in sorted(derived_rels - set(BUILT_RELS)):
        bad.append(f"rel {name!r} is built in Cypher but not declared in graph_surface")

    # The other direction: a declared name no longer built. Identity names live in
    # SHAPES/EDGES rather than in a literal, so they are exempt from this half.
    from src.coordinator.identity_shapes import BASELINE_EDGE, BASELINE_LABEL, EDGES, SHAPES
    derived_from_shapes = set(SHAPES) | {BASELINE_LABEL} | set(EDGES) | {BASELINE_EDGE}
    for name in sorted((set(BUILT_LABELS) - derived_labels) - derived_from_shapes):
        bad.append(f"label {name!r} is declared in graph_surface but no Cypher builds it "
                   f"— delete the entry or the dead writer")
    for name in sorted((set(BUILT_RELS) - derived_rels) - derived_from_shapes):
        bad.append(f"rel {name!r} is declared in graph_surface but no Cypher builds it")

    if bad:
        for line in bad:
            print(f"FAIL {line}")
        return 1

    print(f"OK: {len(BUILT_LABELS)} labels and {len(BUILT_RELS)} rels declared in "
          f"graph_surface, and the Cypher in {len(CYPHER_SOURCES)} files builds "
          f"exactly those.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
