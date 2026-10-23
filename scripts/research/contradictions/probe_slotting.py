#!/usr/bin/env python3
"""Can the local model put a free-text fact into a (subject, slot, value) shape?

This is the one thing that must be BUILT rather than wired, so it is measured
before anything is designed around it.

⚠️ A PATTERN-BASED SLOTTER IS ALREADY KNOWN TO FAIL HERE.
`scripts/research/facts/CURATION_RUBRIC.md` records three regex passes tried on
this exact store, each leaking for the same structural reason: "the subject of
an LLM-extracted sentence is not recoverable by pattern." So the question is
not whether a regex works — it is whether a 24B local model can do it
consistently enough to gate a destructive action (retiring a stored fact).

WHAT A SLOT IS, AND WHY IT IS NOT A PREDICATE.
The dormant `PREDICATE_VOCABULARY` has `has_pet` and `has_favorite`, and both
are MULTI-VALUED — correctly, because a person can have several pets and
several favourites. But "the name of THE DOG" admits one value, and "favourite
BAND" admits one. So the unit cardinality attaches to is finer than the
predicate: a `slot` is (predicate + qualifier), e.g. `has_pet:dog` and
`has_favorite:band`. Two facts conflict when they share a slot, the slot is
single-valued, and the values differ.

The gold set is built so this distinction is the ONLY thing that can separate
true contradictions from look-alikes:
  dog:Tulliver   vs dog:Wickham     -> same slot, different value -> CONFLICT
  dog:Wickham    vs cat:Pemberton   -> different slot             -> fine
  hobby Thursday vs hobby Friday    -> multi-valued slot          -> fine
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
GOLD = HERE / "fixtures" / "gold_2026-10-23.json"

_INSTRUCTION = (
    "You put a single stored fact about a person into a slot.\n"
    "A SLOT is what the fact is about, as `topic` or `topic:qualifier`.\n"
    "Use a qualifier whenever the topic could hold several different things:\n"
    "  'his dog is called Rex'        -> slot 'pet:dog',      value 'Rex'\n"
    "  'his cat is called Tom'        -> slot 'pet:cat',      value 'Tom'\n"
    "  'his favourite film is Alien'  -> slot 'favourite:film', value 'Alien'\n"
    "  'he plays squash on Mondays'   -> slot 'hobby',        value 'squash on Mondays'\n"
    "  'he lives in Geneva'           -> slot 'lives_in',     value 'Geneva'\n"
    "Also say whether the slot can hold only ONE thing at a time.\n"
    "A person has one name and one city, but many hobbies and many opinions.\n"
    "Return ONLY JSON: "
    '{"slot": "...", "value": "...", "single_valued": true|false}\n\n'
    "The fact: "
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "slot": {"type": "string"},
        "value": {"type": "string"},
        "single_valued": {"type": "boolean"},
    },
    "required": ["slot", "value", "single_valued"],
}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.coordinator.services.image_gen.extract import default_client

    client, model = default_client()
    if client is None or not model:
        print("REFUSING: no ollama client.", file=sys.stderr)
        return 2

    gold = json.loads(GOLD.read_text())
    keyed = [f for f in gold["facts"] if f.get("key")]
    print(f"model: {model}\nslotting {len(keyed)} keyed gold facts\n")

    out = {}
    for f in keyed:
        try:
            r = client.chat(model=model, format=_SCHEMA, stream=False,
                            messages=[{"role": "user",
                                       "content": _INSTRUCTION + f["text"]}],
                            options={"temperature": 0.0, "num_predict": 120})
            d = json.loads(r["message"]["content"])
        except Exception as exc:                                  # noqa: BLE001
            d = {"slot": f"<error {exc}>", "value": "", "single_valued": None}
        out[f["key"]] = d
        print(f"  {f['key']:<22} slot={str(d.get('slot'))[:26]:<28}"
              f" value={str(d.get('value'))[:22]:<24} single={d.get('single_valued')}")

    print("\n" + "=" * 78)
    print("DOES THE SLOT DECIDE THE LABELLED PAIRS?")
    print("=" * 78)
    ok = bad = 0
    for p in gold["pairs"]:
        a, b = out.get(p["a"], {}), out.get(p["b"], {})
        sa, sb = str(a.get("slot", "")).lower(), str(b.get("slot", "")).lower()
        same_slot = sa == sb and sa != ""
        single = bool(a.get("single_valued")) and bool(b.get("single_valued"))
        diff_val = str(a.get("value", "")).lower() != str(b.get("value", "")).lower()
        predicted = "contradiction" if (same_slot and single and diff_val) else "compatible"
        want = "contradiction" if p["label"] == "contradiction" else "compatible"
        hit = predicted == want
        ok += hit; bad += not hit
        print(f"  {'OK ' if hit else 'BAD'} {p['a']:<20} vs {p['b']:<20} "
              f"gold={p['label']:<14} -> {predicted:<14} "
              f"[slots {sa!r} / {sb!r}, single={single}]")
    print(f"\n  {ok} of {ok+bad} labelled pairs decided correctly")
    print("\n  ⚠️ `paraphrase` pairs count as `compatible` here on purpose: this probe")
    print("     only asks whether a SLOT separates contradiction from coexistence.")
    print("     Merging paraphrases is a separate decision and is not measured.")
    (HERE / "slotting_probe.json").write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
