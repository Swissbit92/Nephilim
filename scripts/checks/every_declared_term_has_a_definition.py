#!/usr/bin/env python3
"""Every term in a controlled vocabulary carries a one-line definition.

WHY THIS IS A STANDING CONSTRAINT AND NOT A STYLE PREFERENCE. This repo enforces its
vocabularies strictly — an out-of-vocabulary value raises at the write path — but until
2026-10-17 not one of those terms said what it MEANT. `TRAIT_KINDS` holds `behaviour`,
`psychological`, `emotional`, `style` and `tic` with nothing distinguishing them, and 34
live nodes are tagged `behaviour` while 20 are tagged `psychological`. Whoever assigns the
next one — a person or a model — is guessing.

It is already measured that guessing is the failure mode. `has_opinion_on` in the fact
store was an unconstrained-range predicate and **half of everything the extractor ever
wrote landed in its two vaguest predicates** (identity_shapes.EDGES, citing Noy &
McGuinness on "do not make the range THING"). A vague term is not a cosmetic problem; it
is where the data goes to die.

⚠️ WHAT A DEFINITION IS FOR, AND WHAT IT IS NOT FOR. It is for the human reading the code
and for this check. It is NOT for stuffing into a prompt:

  * Adding identity content to her prompt is a CLOSED change, measured — voice
    distinctiveness 0.804 off, 0.625 on, and three reframings at 0.708 / 0.542 / 0.500.
  * The extractor prompt is capped by a different measurement: schema pruning beat the
    full schema at ~6x fewer tokens, and small models "degrade sharply with schema
    bloat". Showing 33 definitions would be exactly that bloat.

So definitions live here, in the vocabulary file, and reach a model only pruned to the
few terms a given call could plausibly use — if at all.

WHY NOT SKOS / RDFS / A FORMAL ONTOLOGY. Researched 2026-10-17 and deliberately declined:
those solve interoperability between parties who cannot talk to each other, at term counts
no one person can hold. Thesauri exist for 40k-400k terms; this is 33. The ecosystem's own
ontology research reached the same verdict one level up ("adopt none of the candidates
wholesale") and gates the formal apparatus behind a stated trigger — a second person or
consumer. This check is the cheap 10% that pays: a definition per term, enforced.

Exit 0 = every declared term is defined. Exit 1 = a term has no definition, and the
message names it. Exit 2 = could not determine, which is NOT a pass.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: (module, vocabulary constant, definitions constant). The definitions dict must cover
#: every member of the vocabulary. Adding a vocabulary here is how it becomes governed —
#: a new controlled vocabulary that is NOT listed is invisible to this check, so the
#: registry is itself part of the constraint and `--list` prints what is governed.
GOVERNED = [
    ("src/coordinator/identity_shapes.py", "IDENTITY_ORIGINS", "IDENTITY_ORIGIN_DEFINITIONS"),
    ("src/coordinator/identity_shapes.py", "LORE_KINDS", "LORE_KIND_DEFINITIONS"),
    ("src/coordinator/identity_shapes.py", "TRAIT_KINDS", "TRAIT_KIND_DEFINITIONS"),
    ("src/coordinator/identity_shapes.py", "BOUNDARY_KINDS", "BOUNDARY_KIND_DEFINITIONS"),
    ("src/coordinator/identity_shapes.py", "EXPERTISE_LEVELS", "EXPERTISE_LEVEL_DEFINITIONS"),
    ("src/coordinator/repositories/memory_fact_repository.py",
     "PREDICATE_VOCABULARY", "PREDICATE_DEFINITIONS"),
    ("src/coordinator/fact_slots.py", "SINGLE_VALUED_SLOTS", "SLOT_DEFINITIONS"),
    ("src/coordinator/repositories/neo4j_rule_repository.py", "RULE_TYPES", "RULE_TYPE_DEFINITIONS"),
    ("src/coordinator/repositories/neo4j_rule_repository.py", "POLARITIES", "POLARITY_DEFINITIONS"),
    ("src/coordinator/repositories/neo4j_rule_repository.py", "ORIGINS", "ORIGIN_DEFINITIONS"),
    ("src/coordinator/repositories/neo4j_rule_repository.py",
     "RULE_TYPE_SOURCES", "RULE_TYPE_SOURCE_DEFINITIONS"),
]

#: Frozensets in the governed files that are deliberately NOT value vocabularies. A REASON
#: IS REQUIRED — the same discipline `identity_from_card.EXCLUDED_FIELDS` uses, and for the
#: same stated reason: "excluded" without one is how something gets quietly dropped to make
#: a check go green. The audit below fails on any frozenset that is neither governed nor
#: listed here, which is what stops this registry from silently falling behind the code.
NOT_A_VALUE_VOCABULARY: dict[str, str] = {
    "_BASE_CONTENT": "a property-NAME allowlist, not a set of values a property may take. "
                     "Its members are documented individually where they are declared.",
    "SINGLE_VALUED_PREDICATES": "a SUBSET of PREDICATE_VOCABULARY marking recency-wins "
                                "behaviour. Its members are already defined by "
                                "PREDICATE_DEFINITIONS; a second dict would be a second "
                                "place to drift.",
    "_BENIGN_SCHEMA_RACE": "Neo4j error codes tolerated during concurrent schema creation. "
                           "Not domain terms.",
}

#: A definition shorter than this is not a definition, it is a restatement of the term.
MIN_DEFINITION_CHARS = 25


def _literal(path: Path, name: str):
    """The value of a module-level constant, read statically. No import, no side effects."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return None, f"cannot parse {path.name}: {exc}"
    for node in ast.walk(tree):
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        val = node.value
        # frozenset({...}) / set(...) — unwrap the call
        if isinstance(val, ast.Call) and val.args:
            val = val.args[0]
        try:
            return ast.literal_eval(val), None
        except (ValueError, TypeError):
            return None, f"{name} in {path.name} is not a literal — cannot check it"
    return None, None   # absent, which is the common failure and not an error to parse


def main() -> int:
    if "--list" in sys.argv:
        print("governed vocabularies:")
        for mod, vocab, defs in GOVERNED:
            print(f"  {vocab:<22} in {mod}  (definitions: {defs})")
        return 0

    # AUDIT FIRST: a vocabulary absent from GOVERNED is invisible to the rest of this
    # check, so the registry's own completeness is part of the constraint. This found two
    # real misses (ORIGINS, RULE_TYPE_SOURCES) on the first run after it was written.
    seen: set[str] = set()
    for mod in sorted({m for m, _, _ in GOVERNED}):
        path = ROOT / mod
        if not path.exists():
            continue
        for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*frozenset\(",
                             path.read_text(encoding="utf-8"), re.M):
            seen.add(m.group(1))
    governed = {v for _, v, _ in GOVERNED}
    ungoverned = sorted(seen - governed - set(NOT_A_VALUE_VOCABULARY))
    problems: list[str] = []
    for name in ungoverned:
        problems.append(f"frozenset '{name}' is neither in GOVERNED nor declared in "
                        f"NOT_A_VALUE_VOCABULARY — an ungoverned vocabulary is invisible "
                        f"to this check, so say which it is and why")
    undetermined: list[str] = []
    checked = defined = 0

    for mod, vocab_name, defs_name in GOVERNED:
        path = ROOT / mod
        if not path.exists():
            undetermined.append(f"{mod} is missing — cannot verify {vocab_name}")
            continue

        vocab, err = _literal(path, vocab_name)
        if err:
            undetermined.append(err)
            continue
        if vocab is None:
            undetermined.append(f"{vocab_name} not found in {mod} — renamed or removed?")
            continue

        defs, err = _literal(path, defs_name)
        if err:
            undetermined.append(err)
            continue
        if defs is None:
            problems.append(f"{mod}: {vocab_name} has {len(vocab)} term(s) and NO "
                            f"{defs_name} dict at all")
            checked += len(vocab)
            continue
        if not isinstance(defs, dict):
            undetermined.append(f"{defs_name} in {mod} is not a dict")
            continue

        for term in sorted(vocab):
            checked += 1
            text = defs.get(term)
            if not text or not str(text).strip():
                problems.append(f"{mod}: {vocab_name} term '{term}' has no definition")
            elif len(str(text).strip()) < MIN_DEFINITION_CHARS:
                problems.append(
                    f"{mod}: {vocab_name} term '{term}' has a {len(str(text).strip())}-char "
                    f"definition — under {MIN_DEFINITION_CHARS} it restates the term "
                    f"rather than defining it")
            else:
                defined += 1

        extra = sorted(set(defs) - set(vocab))
        if extra:
            problems.append(f"{mod}: {defs_name} defines {extra}, which is not in "
                            f"{vocab_name} — a definition for a term that does not exist "
                            f"is how a retired term looks alive")

    if undetermined:
        print("COULD NOT DETERMINE — this is not a pass:")
        for u in undetermined:
            print(f"  {u}")
        return 2

    if problems:
        print(f"FAIL: {len(problems)} undefined or under-defined term(s) "
              f"({defined}/{checked} defined):")
        for p in problems[:40]:
            print(f"  {p}")
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        return 1

    print(f"OK: all {checked} declared terms across {len(GOVERNED)} vocabularies carry a "
          f"definition of at least {MIN_DEFINITION_CHARS} chars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
