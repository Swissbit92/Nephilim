#!/usr/bin/env python3
"""The learning counter is gone, and removing it did not reach the prompt.

Two arms, deliberately. Arm 1 is the deletion itself and FAILS on the pre-change tree —
a check never observed failing proves nothing when it turns green. Arm 2 is the
regression guard: ADR-019's whole boundary is that reading identity from the graph leaves
gwen's system prompt BYTE-IDENTICAL, so if this deletion moves one byte of it, the change
has reached production behaviour and the claim is wrong.

WHY THE COUNTER WENT. Measured 2026-10-16 on the live graph: 256 nodes carried
`reinforced_count`/`salience` and returned exactly ONE distinct row — `0, 0.0` — so it had
never incremented, ever. The literature is worse than silent: Generative Agents never
ablated the recency/importance/relevance terms it is cited for, A-MEM and MemGPT omit an
access counter by design, and MemoryBank (the closest published analog — strength
incremented per recall) ships with no ablation and calls itself "exploratory and highly
simplified". The one direct test of a frequency-containing pruner reports +0.001 token F1
with a CI spanning zero (arXiv 2608.28978), and even that bundles frequency with recency,
centrality and age rather than isolating it. So: no evidence it pays, and an epistemic
case that argues for source diversity rather than an access count — which `reinforce()`
could not have supplied anyway, since it incremented on ACCESS, not on independent
re-assertion.

WHAT DID NOT GO, and must not. The Cypher's ON CREATE / unconditional SET split stays:
it still protects `node_id` and `created_at`, which are create-only and which a rebuild
must never overwrite. The A/B that justified that split measured the MECHANISM (0 of 3
vs 3 of 3 survival across a card edit), not the counter, so the mechanism keeps its
evidence and simply keeps a different referent.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

#: Names that must not survive anywhere in src/. `salience` is deliberately NOT one of
#: them: it appears in an unrelated prose comment in tools/synthesis_prompts.py, and a
#: check that fires on an English word teaches people to ignore it.
GONE = ("reinforced_count", "last_referenced", "identity_engagement")
#: Matched as a call or def, so the word "reinforcement" elsewhere cannot trip it.
GONE_CALLS = (re.compile(r"\.reinforce\s*\("), re.compile(r"\bdef\s+reinforce\b"))


def _code_only(path: Path) -> list[tuple[int, str]]:
    """Lines with comments and DOCSTRINGS removed, other string literals KEPT.

    The distinction is the whole point and it is not pedantry. A comment or docstring
    recording *why* the counter was deleted is exactly what should survive — stripping
    the reasoning to satisfy a grep is how a repo loses the measurement that justified a
    decision. But the Cypher lives in ordinary string literals, so a check that ignored
    all strings would be blind to the references that matter most: a live
    `SET n.reinforced_count = ...` would pass.

    So: drop COMMENT tokens, drop only those STRING tokens that are a module/class/
    function docstring, and keep everything else.
    """
    import io
    import tokenize

    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return list(enumerate(text.splitlines(), 1))

    # Exact (line, col) of every docstring node, so a same-valued non-docstring
    # literal elsewhere is still inspected.
    doc_pos = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef
                          | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            doc_pos.add((first.value.lineno, first.value.col_offset))

    lines = dict(enumerate(text.splitlines(), 1))
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except tokenize.TokenError:
        return list(lines.items())

    for tok in toks:
        drop = tok.type == tokenize.COMMENT or (
            tok.type == tokenize.STRING and tok.start in doc_pos)
        if not drop:
            continue
        for ln in range(tok.start[0], tok.end[0] + 1):
            lines[ln] = ""
    return sorted(lines.items())


def _arm1_surface_absent() -> list[str]:
    problems = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(ROOT)
        for lineno, line in _code_only(path):
            for name in GONE:
                if name in line:
                    problems.append(f"{rel}:{lineno} still names '{name}' IN CODE")
            for pattern in GONE_CALLS:
                m = pattern.search(line)
                if m:
                    problems.append(f"{rel}:{lineno} still has {m.group(0)!r}")
    if (SRC / "coordinator" / "identity_engagement.py").exists():
        problems.append("src/coordinator/identity_engagement.py still exists")
    return problems


def _arm2_prompt_unchanged() -> list[str]:
    """The graph-sourced prompt must stay byte-identical to the card-sourced one.

    Delegates to the test that already owns this assertion rather than re-deriving it —
    a second implementation of a guarantee is a second thing that can disagree.
    """
    test = ROOT / "tests" / "backend" / "coordinator" / "test_graph_sourced_identity.py"
    if not test.exists():
        return [f"{test.relative_to(ROOT)} is missing — the byte-identity guarantee has "
                f"no owner, which is not a pass"]
    out = subprocess.run(
        [sys.executable, "-m", "pytest", str(test), "-q", "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        tail = "\n".join(out.stdout.strip().splitlines()[-12:])
        return [f"the graph-sourced identity suite FAILED — the deletion reached the "
                f"prompt:\n{tail}"]
    return []


def main() -> int:
    arm1 = _arm1_surface_absent()
    arm2 = _arm2_prompt_unchanged()

    if arm1:
        print(f"FAIL arm 1 — the counter surface is still present ({len(arm1)} site(s)):")
        for p in arm1[:20]:
            print(f"  {p}")
    else:
        print("OK arm 1 — no counter surface anywhere in src/.")

    if arm2:
        print("FAIL arm 2 — identity byte-identity regressed:")
        for p in arm2:
            print(f"  {p}")
    else:
        print("OK arm 2 — graph-sourced identity still byte-identical to the card.")

    return 1 if (arm1 or arm2) else 0


if __name__ == "__main__":
    sys.exit(main())
