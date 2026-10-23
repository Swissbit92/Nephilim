#!/usr/bin/env python3
"""What the CURRENT machinery sees, and what the obvious alternatives see.

Run before building anything. A check that has never been watched failing
proves nothing when it turns green, and this one has three jobs:

  1. confirm the shipped dedup (`operator_fact_writer._norm` exact containment)
     catches 0 of the 4 real contradictions;
  2. confirm LEXICAL SIMILARITY IS THE WRONG INSTRUMENT at every threshold --
     not merely badly tuned. I already reported this store clean on the
     strength of a Jaccard sweep; the two dog facts share one word;
  3. establish the number any new detector has to beat.

No model, no graph, no network: reads the frozen gold set only.
"""
from __future__ import annotations

import itertools
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
GOLD = HERE / "fixtures" / "gold_2026-10-23.json"

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """VERBATIM from `operator_fact_writer._norm` — the shipped dedup."""
    return _WS.sub(" ", (text or "").strip().lower()).rstrip(".,;!?")


def shipped_dedup(a: str, b: str) -> bool:
    """True when the live writer would treat `b` as already held given `a`."""
    na, nb = _norm(a), _norm(b)
    return na in nb or nb in na


def jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z]{4,}", a.lower()))
    tb = set(re.findall(r"[a-z]{4,}", b.lower()))
    return len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0


def main() -> int:
    gold = json.loads(GOLD.read_text())
    by_key = {f["key"]: f for f in gold["facts"] if f.get("key")}
    pairs = [(p, by_key[p["a"]]["text"], by_key[p["b"]]["text"])
             for p in gold["pairs"]]

    want = [p for p, _a, _b in pairs if p["label"] == "contradiction"]
    print(f"gold: {len(pairs)} labelled pairs — "
          f"{len(want)} contradiction, "
          f"{sum(1 for p,_a,_b in pairs if p['label']=='compatible')} compatible, "
          f"{sum(1 for p,_a,_b in pairs if p['label']=='paraphrase')} paraphrase\n")

    # --- 1. the shipped dedup ------------------------------------------------
    caught = [p for p, a, b in pairs
              if p["label"] == "contradiction" and shipped_dedup(a, b)]
    print("=" * 74)
    print("1. THE SHIPPED DEDUP (`_norm` exact containment)")
    print(f"   contradictions it would notice: {len(caught)} of {len(want)}")
    print("   -> it is a DUPLICATE check, not a contradiction check. It asks "
          "whether\n      one string contains another, and two different values "
          "for the same\n      thing never do.")

    # --- 2. lexical similarity, swept ---------------------------------------
    print("\n" + "=" * 74)
    print("2. LEXICAL SIMILARITY, SWEPT — is it merely mis-tuned, or wrong?")
    print(f"   {'thr':>5}{'contradictions found':>23}{'compatible wrongly flagged':>30}")
    best = None
    for thr in [i / 20 for i in range(1, 20)]:
        tp = sum(1 for p, a, b in pairs
                 if p["label"] == "contradiction" and jaccard(a, b) >= thr)
        fp = sum(1 for p, a, b in pairs
                 if p["label"] == "compatible" and jaccard(a, b) >= thr)
        if thr in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.50, 0.75):
            print(f"   {thr:>5.2f}{f'{tp}/{len(want)}':>23}"
                  f"{f'{fp}/6':>30}")
        if best is None or (tp - fp) > best[1]:
            best = (thr, tp - fp, tp, fp)
    print(f"\n   best achievable (tp-fp) at any threshold: thr={best[0]:.2f} "
          f"-> {best[2]}/{len(want)} found, {best[3]}/6 false")

    print("\n   the two pairs that settle it:")
    dog = next((a, b) for p, a, b in pairs if p["a"] == "dog_tulliver")
    pet = next((a, b) for p, a, b in pairs if p["a"] == "cat_pemberton"
               and p["b"] == "dog_wickham")
    print(f"     CONTRADICTION, Jaccard {jaccard(*dog):.2f}:")
    print(f"       {dog[0]!r}\n       {dog[1]!r}")
    print(f"     COMPATIBLE,    Jaccard {jaccard(*pet):.2f}:")
    print(f"       {pet[0]!r}\n       {pet[1]!r}")
    print("\n   -> the compatible pair scores HIGHER than the contradictory one.")
    print("      No threshold can separate them, because similarity measures the")
    print("      wrong relation: two facts that DISAGREE about one thing are")
    print("      lexically distant almost by construction, while two facts about")
    print("      different pets share their whole sentence frame.")
    print("      ⚠️ This is the sweep I previously ran at 0.50, which reported the")
    print("      cat/dog pair as the store's only near-duplicate and MISSED the")
    print("      real dog contradiction entirely.")

    # --- 3. the bar ----------------------------------------------------------
    print("\n" + "=" * 74)
    print("3. THE BAR FOR A NEW DETECTOR")
    print(f"   must find   : {len(want)}/{len(want)} contradictions")
    print(f"   must not flag: 6/6 compatible pairs, including a temporal")
    print(f"                  evolution (2.5 BTC -> 2 BTC) and three")
    print(f"                  multi-valued hobbies on different days")
    print(f"   paraphrase   : 3 pairs — must NOT be called contradictions "
          f"(they agree)")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ===========================================================================
# PART 4 — what the DORMANT machinery would do, measured on the only rows it
# ever wrote. Appended 2026-10-23 after discovering it exists.
# ===========================================================================
#
# `MEMORY_FACTS_ENABLED=false` gates a complete second design that was built
# for ADR-006 P1/M3 and never enabled: a 33-predicate closed
# `PREDICATE_VOCABULARY`, a `SINGLE_VALUED_PREDICATES` set ("a new value
# supersedes the old"), `fact_write_policy.apply_triples()` implementing
# recency-wins, and a triplet extractor with verbatim quote-span validation.
#
# On paper that is exactly the subject+predicate+cardinality design Part 2
# proves is needed. Measured against the 12 rows it actually wrote, it has two
# stacked defects, and only the real data shows the first:
#
# 🔴 DEFECT 1 — SUBJECT RESOLUTION DOES NOT RESOLVE. `memory_entities` holds
#    FOUR distinct `entity_id`s all named 'self', one per extraction call. So
#    all 12 facts carry a unique `(subject_id, predicate)` key — 12 keys for 12
#    facts, ZERO keys with more than one fact. A supersede keyed on that pair
#    can never fire. Proven by two byte-identical
#    `is_concerned_about -> 'money runway'` rows sitting side by side, both
#    `valid_to IS NULL`. The policy is not mis-tuned; it is unreachable.
#
# 🔴 DEFECT 2 — CARDINALITY IS DECLARED PER PREDICATE, NOT PER QUALIFIER.
#    `SINGLE_VALUED_PREDICATES` holds nine identity/place/occupation terms.
#    `has_pet` and `has_favorite` are multi-valued, CORRECTLY: a person can
#    have several pets and several favourites. But "the name of THE DOG" admits
#    one value, and "favourite BAND" admits one. So even with defect 1 fixed,
#    the gold set's dog and band contradictions still accrete.
#
#    The missing distinction is a QUALIFIED cardinality restriction — single
#    per (predicate, qualifier) rather than per (subject, predicate). RDF/OWL
#    has had `owl:qualifiedCardinality` for this since 2009; whether anyone in
#    the LLM-memory space uses it is a research question, not an assumption.
#
# ⚠️ Three of the twelve rows are facts about the ASSISTANT'S OWN REASONING
#    (`is_currently_engaged_with: analyzing the user's question`) — the defect
#    INV-7 guards on the graph path, confirmed present on this one and already
#    named in the repository's own docstring.
#
# ⚠️ `has_name -> 'Ramon'` here sits against the graph's "His name is Ben".
#    Flagged, not resolved: which is correct is the operator's call, not a
#    detector's.
