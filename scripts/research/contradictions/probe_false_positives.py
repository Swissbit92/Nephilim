#!/usr/bin/env python3
"""If retirement were AUTOMATIC today, what would it retire?

🔴 THE SAFETY MEASUREMENT. A false positive here silently deletes a true
memory, and there is no way for the operator to notice: the fact simply stops
being injected and she stops knowing it. That asymmetry is the whole reason
this is measured before the policy is chosen, rather than after.

The store was cleaned today (45 -> 35 current, the 10 probe-made facts
retired), so the CORRECT answer on a clean store is ZERO retirements. Anything
this finds is a false positive waiting to fire the moment the flush writes a
fact that lands in the same slot.

Reads the live graph read-only and calls the model; writes nothing.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

#: DECLARED cardinality. Cardinality is a schema decision, not a per-fact
#: judgement -- the owl:FunctionalProperty / SHACL sh:maxCount tradition. The
#: model was asked for it and got it wrong on 18 of 19 facts (9/13 pairs);
#: declaring it instead scored 13/13.
#:
#: ⚠️ DRAFT, and deliberately CONSERVATIVE. Every entry here authorises a
#: silent deletion, so a slot belongs in this set only when two different
#: values genuinely cannot both be true at once. When in doubt, leave it out:
#: an omission costs a missed contradiction (visible -- she gives an
#: inconsistent answer) while a wrong inclusion costs a deleted true memory
#: (invisible).
SINGLE_VALUED_SLOTS = frozenset({
    "pet:dog", "pet:cat", "pet:tortoise", "pet:parrot", "pet:rabbit",
    "favourite:band", "favourite:film", "favourite:book", "favourite:food",
    "favourite:colour", "favourite:drink",
    "has_name", "has_age", "has_birthday", "lives_in", "works_as", "works_at",
})

_INSTRUCTION = (
    "You put a single stored fact about a person into a slot.\n"
    "A SLOT is what the fact is about, as `topic` or `topic:qualifier`.\n"
    "Use a qualifier whenever the topic could hold several different things:\n"
    "  'his dog is called Rex'        -> slot 'pet:dog',      value 'Rex'\n"
    "  'his cat is called Tom'        -> slot 'pet:cat',      value 'Tom'\n"
    "  'his favourite film is Alien'  -> slot 'favourite:film', value 'Alien'\n"
    "  'he plays squash on Mondays'   -> slot 'hobby',        value 'squash on Mondays'\n"
    "  'he lives in Geneva'           -> slot 'lives_in',     value 'Geneva'\n"
    "Return ONLY JSON: " '{"slot": "...", "value": "..."}\n\n'
    "The fact: "
)
_SCHEMA = {"type": "object",
           "properties": {"slot": {"type": "string"}, "value": {"type": "string"}},
           "required": ["slot", "value"]}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import build_driver
    from src.coordinator.repositories.operator_fact_repository import (
        OperatorFactRepository,
    )
    from src.coordinator.services.image_gen.extract import default_client

    client, model = default_client()
    if client is None or not model:
        print("REFUSING: no ollama client.", file=sys.stderr)
        return 2

    cfg = get_settings().graph
    op = get_settings().memory.single_operator_user_id
    drv = build_driver(cfg.base_url, cfg.username, cfg.password)
    try:
        rows = OperatorFactRepository(drv, cfg.database).current_facts(op, limit=1024)
    finally:
        drv.close()

    print(f"model : {model}")
    print(f"facts : {len(rows)} CURRENT facts for {op}")
    print(f"slots : {len(SINGLE_VALUED_SLOTS)} declared single-valued\n")

    slotted = []
    for r in rows:
        try:
            resp = client.chat(model=model, format=_SCHEMA, stream=False,
                               messages=[{"role": "user",
                                          "content": _INSTRUCTION + r["text"]}],
                               options={"temperature": 0.0, "num_predict": 100})
            d = json.loads(resp["message"]["content"])
        except Exception as exc:                                  # noqa: BLE001
            d = {"slot": "", "value": "", "error": repr(exc)}
        slotted.append({"fact_id": r["fact_id"], "text": r["text"],
                        "origin": r["origin"], "asserted_by": r["asserted_by"],
                        "slot": str(d.get("slot", "")).strip().lower(),
                        "value": str(d.get("value", "")).strip()})

    groups = collections.defaultdict(list)
    for s in slotted:
        if s["slot"]:
            groups[s["slot"]].append(s)

    print(f"{'slot':<28}{'n':>4}  single-valued?  values")
    print("-" * 92)
    for slot, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        sv = slot in SINGLE_VALUED_SLOTS
        mark = "SINGLE" if sv else "multi "
        vals = " | ".join(i["value"][:22] for i in items[:3])
        flag = "  <-- WOULD RETIRE" if (sv and len(items) > 1) else ""
        print(f"{slot:<28}{len(items):>4}  {mark}          {vals[:40]}{flag}")

    collisions = [(s, i) for s, i in groups.items()
                  if s in SINGLE_VALUED_SLOTS and len(i) > 1]
    print("\n" + "=" * 92)
    if not collisions:
        print("ZERO automatic retirements on the clean store. ✅")
        print("  Expected: the 10 probe facts were retired today, so no single-valued")
        print("  slot should hold two live values. A non-zero result here would be a")
        print("  false positive waiting to fire.")
    else:
        print(f"⚠️ {len(collisions)} single-valued slot(s) hold MORE THAN ONE live fact.")
        print("   Under an automatic policy each of these loses a fact RIGHT NOW:")
        for slot, items in collisions:
            print(f"\n   slot {slot!r}:")
            for i in items:
                print(f"     [{i['asserted_by']:<17}] {i['text'][:62]}")
            print("     -> newest wins; the rest are silently retired")
    print("=" * 92)

    # Curated facts are the ones a false positive would hurt most: they were
    # hand-labelled over 292 extracted candidates and cannot be re-derived.
    cur_in_single = [s for s in slotted
                     if s["asserted_by"] == "operator_curated"
                     and s["slot"] in SINGLE_VALUED_SLOTS]
    print(f"\ncurated facts sitting in a single-valued slot: {len(cur_in_single)}")
    for s in cur_in_single:
        print(f"  {s['slot']:<22} {s['text'][:56]}")
    print("  ^ each of these could be retired by a future conversational claim")
    print("    landing in the same slot. Whether that is allowed is a POLICY call.")

    (HERE / "false_positive_probe.json").write_text(json.dumps(slotted, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
