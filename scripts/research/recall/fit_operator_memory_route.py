#!/usr/bin/env python3
"""Does `operator_memory` separate his-past from the-world? Fitted, not borrowed.

The research was explicit: Graphiti's 0.6/15 dedup constants are confirmed
unfitted from their own merge history, and this repo has measured a borrowed
threshold scoring 31% where a fitted one scored 57%. So the routing threshold is
checked against LABELLED questions rather than assumed from the existing 0.66.

Two strata, and the second is the whole point:
  MEMORY -- about his past / her self. Must NOT route to web_search.
  WORLD  -- about him AND about current world state. MUST still route to web.

Cheap: embeddings only, no generation. Exits 1 if any WORLD item misroutes,
because that is the regression that re-opens fabrication.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

MEMORY = [
    "how much bitcoin did i buy and when?",
    "what hardware wallet was i deciding on?",
    "what books do i read?",
    "what do i do at the gym?",
    "how long is my money runway?",
    "what car did i sell and when?",
    "do i eat meat?",
    "what do i usually eat for breakfast?",
    "what colour is your hair?",
    "how tall are you?",
    "what am i thinking about doing with my job?",
    "what do you remember about me?",
]
# ⚠️ THE BASELINE IS PINNED PER ITEM, not assumed.
#
# The first version of this check asserted that every WORLD question SHOULD route
# to web_search, and reported 3 of 6 as a regression caused by
# `operator_memory`. It was not. Measured on the router WITHOUT the new intent,
# three of them ALREADY routed to None: "whats my local weather right now?",
# "what did my portfolio close at today?" and "is it going to rain tomorrow?".
# The `web_search` example set is crypto/news-heavy and carries no weather,
# portfolio or general-knowledge coverage — a PRE-EXISTING gap, recorded here
# rather than silently attributed to this change.
#
# So the assertion is NO REGRESSION against the measured baseline. An item that
# routed to the web before must still route there; an item that did not is not
# this change's to fix, and pretending otherwise would have had me "fixing" my
# examples against a fault they did not cause.
#
# For the three None items the groundedness gate is the backstop, and the
# deflection probe confirms it catches some of them ("don't actually have
# grounded" appears in its deflected set).
WORLD_BASELINE = {
    "whats my local weather right now?": None,
    "what did my portfolio close at today?": None,
    "is it going to rain tomorrow?": None,
    "whats the bitcoin price right now?": "web_search",
    "whats the latest crypto news?": "web_search",
    "what did the fed say about rates?": "web_search",
}


def main() -> int:
    from coordinator.tools.semantic_router import route_by_embedding

    rows, bad_world, bad_mem = [], 0, 0
    for q in MEMORY:
        r = route_by_embedding(q, can_use_wallet=False, can_use_brave=True,
                               drop_llm_only_centroid=True)
        ok = r != "web_search"
        bad_mem += (not ok)
        rows.append(("MEMORY", q, r, ok))
    for q, expected in WORLD_BASELINE.items():
        r = route_by_embedding(q, can_use_wallet=False, can_use_brave=True,
                               drop_llm_only_centroid=True)
        ok = r == expected
        bad_world += (not ok)
        rows.append(("WORLD", q, r, ok))

    for stratum, q, r, ok in rows:
        print(f"  {'OK ' if ok else 'BAD'} [{stratum:6}] {str(r):12} {q}")

    print()
    print(f"MEMORY  correctly kept off the web: {len(MEMORY)-bad_mem}/{len(MEMORY)}")
    print(f"WORLD   unchanged vs baseline     : {len(WORLD_BASELINE)-bad_world}/{len(WORLD_BASELINE)}")
    if bad_world:
        print(f"\nFAIL: {bad_world} WORLD question(s) MOVED from their baseline "
              "route. Moving one off web_search re-opens the fabrication the "
              "deflection exists to prevent.")
        return 1
    print("\nNo WORLD regression.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
