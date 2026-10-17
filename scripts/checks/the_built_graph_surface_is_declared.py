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
#: writer is a decision someone makes here, not something that happens quietly — and the
#: list has to be TRUE for that sentence to mean anything. `scripts/research/identity_ab.py`
#: is on it because it writes raw Cypher against the live graph; it was missed on the first
#: pass, and the claim that a new writer is a decision made here was therefore false for it.
CYPHER_SOURCES = (
    SRC / "repositories" / "identity_repository.py",
    SRC / "repositories" / "neo4j_rule_repository.py",
    SRC / "rule_proposals.py",
    ROOT / "scripts" / "research" / "identity_ab.py",
)

#: `(x:Label` / `(:Label` — a parenthesis is required, so a bare `:Label` inside prose
#: or a Python annotation cannot match.
#:
#: ⚠️ **A LEADING UNDERSCORE IS PART OF THE PATTERN, and leaving it out hid a live label.**
#: The first version required `[A-Z]` immediately after the colon, which made
#: `:_PropertyKeyRegistry` — created from `ensure_schema()`, reached from `routes/chat.py`
#: — structurally invisible. Not a one-off miss: a whole CLASS of label the scan could
#: never see, in the check written specifically to make that impossible. Neo4j permits a
#: leading underscore, so the pattern must too. Lowercase is still excluded, because the
#: variable position in `(x:Label` is lowercase and admitting it would match `(a:b` in any
#: prose string.
_LABEL = re.compile(r"\(\s*[a-z0-9_]*((?::[A-Z_][A-Za-z0-9_]*)+)")
#: `[:REL]->` / `-[r:REL]-` — the arrow or dash after the bracket is required, which is
#: what keeps a Python slice like `text[:_MAX_BYTES]` out of the relationship set.
_REL = re.compile(r"\[\s*[a-z0-9_]*:([A-Z_][A-Z0-9_]*)\s*\][-<>]")
#: `n:CurrentIdentity` in a REMOVE/SET clause, which has no parenthesis of its own.
_BARE_LABEL = re.compile(r"\b(?:REMOVE|SET)\s+[a-z0-9_]+((?::[A-Z_][A-Za-z0-9_]*)+)")
#: `MERGE (n:{label}:CurrentIdentity ...)` — the f-string hole is filled from SHAPES at
#: runtime, so the literal carries `{label}` where a label would be.
#:
#: ⚠️ **Replaced with a sentinel, NOT deleted.** Deleting it leaves `MERGE (n::Current…`,
#: a double colon no label pattern can parse, so the whole clause matched nothing and
#: `CurrentIdentity` was only ever seen via incidental `MATCH`/`REMOVE` references
#: elsewhere in the same file. A secondary label introduced ONLY through this idiom would
#: have been invisible. The sentinel keeps the clause parseable and is dropped from the
#: results, since the identity half of the manifest comes from SHAPES anyway.
_FSTRING_HOLE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_.\[\]']*\}")
_HOLE_SENTINEL = "FstringHolePlaceholder"


def _non_docstring_strings(path: pathlib.Path) -> list[str]:
    """Every string constant in the file except the ones Python reads as documentation.

    **An f-string is REASSEMBLED, not walked as pieces.** This is the subtle half. Python
    parses `f"MERGE (n:{label}:CurrentIdentity ...)"` into a `JoinedStr` whose literal
    halves are SEPARATE `Constant` nodes — `"MERGE (n:"` and `":CurrentIdentity ..."` —
    so a scan that walks constants individually never sees one clause. The second half
    begins at a colon with no parenthesis in front of it and matches nothing.

    The consequence was not hypothetical: `:CurrentIdentity` is written ONLY through this
    idiom at `identity_repository.py:198`, and the first version of this check detected it
    purely by accident, via unrelated `MATCH`/`REMOVE` references elsewhere in the same
    file. A secondary label introduced only this way would have been invisible — in the
    check whose entire job is to make that impossible. So each `JoinedStr` is rebuilt into
    one string with `_HOLE_SENTINEL` standing in for every interpolation, and its own
    `Constant` children are then skipped so they are not also counted as fragments.
    """
    tree = ast.parse(path.read_text())

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    out: list[str] = []
    inside_fstring: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for piece in node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append(piece.value)
                    inside_fstring.add(id(piece))
                else:
                    parts.append(_HOLE_SENTINEL)
                    # A nested FormattedValue can itself hold string constants (format
                    # specs, dict keys); none of them are Cypher.
                    for inner in ast.walk(piece):
                        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                            inside_fstring.add(id(inner))
            out.append("".join(parts))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and id(node) not in inside_fstring:
                out.append(node.value)
    return out


def derive() -> tuple[set[str], set[str]]:
    labels: set[str] = set()
    rels: set[str] = set()
    for path in CYPHER_SOURCES:
        if not path.exists():
            raise FileNotFoundError(path)
        for text in _non_docstring_strings(path):
            text = _FSTRING_HOLE.sub(_HOLE_SENTINEL, text)
            for group in _LABEL.findall(text) + _BARE_LABEL.findall(text):
                # The sentinel is label-SHAPED on purpose (anything else would make
                # `(n:<hole>:Real` unparseable again), so it can sit flush against a real
                # name when the interpolation is adjacent — `(n:CurrentIdentity{expr})`
                # reassembles as one token. Strip it as a substring, not by equality.
                labels |= {part for part in
                           (seg.replace(_HOLE_SENTINEL, "") for seg in group.split(":"))
                           if part}
            rels |= {r.replace(_HOLE_SENTINEL.upper(), "") or r
                     for r in _REL.findall(text)}
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
