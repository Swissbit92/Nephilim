#!/usr/bin/env python3
"""Author the 60 probes and their gold values, then freeze them by sha256.

🔴 WRITTEN AND COMMITTED BEFORE ANY ARM IS RUN. I am authoring both the probes
and the gold values while knowing the hypothesis, which is exactly the bias the
pre-registration exists to bound. The mitigation is order: all 60 probes and
golds are fixed and sha'd first, and the runner refuses to start on a mismatch.

SHAPE MIX. Three shapes per subject, because an empty slot is where an
unrelated biography has the most room to leak:
  bare      subject only                -> setting/mood both legitimately empty
  setting   subject + a time-of-day      -> tests the documented mood/setting
                                            confusion on every probe, not one
  mood      subject + an explicit feeling-> the slot contamination would occupy

NEAR vs FAR. `near` subjects sit beside something the frozen memory block also
names (a dog, a cat, a beach, a gym, an RPG, breakfast, a book, a hardware
wallet). `far` subjects share no lexical or semantic neighbour with it. If the
block leaks by ASSOCIATION, near moves and far does not. If it leaks
indiscriminately, both move. If neither moves, there is nothing to see. The
split is what makes a positive finding interpretable instead of one number.

STYLE GOLDS ARE DELIBERATELY BALANCED. The previous A/B produced
`photographic` 48/48, so a photographic-skewed gold set would put `style_exact`
on the ceiling and make it insensitive. Four subjects request a style
explicitly, spread across the enum.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

OUT = pathlib.Path(__file__).parent / "probes.json"

#: (subject-slug, bucket, noun phrase as the user says it, gold subject keys,
#:  time-of-day word for the `setting` shape, mood word for the `mood` shape,
#:  explicit style request or None)
SUBJECTS = [
    # --- NEAR: each sits beside a fact in the frozen block -------------------
    ("dog",        "near", "a dog running through deep snow",   ("dog",),        "at dusk",      "lonely",     None),
    ("cat",        "near", "a cat asleep on a windowsill",      ("cat",),        "in the morning","peaceful",   None),
    ("beach",      "near", "a quiet beach with a paperback on a towel", ("beach",), "at sunset",  "wistful",    None),
    ("dumbbells",  "near", "a rack of dumbbells in an empty gym", ("dumbbell","gym"), "at night", "grim",       None),
    ("rpg",        "near", "a lone swordsman on a moor",        ("swordsman",),  "at twilight",  "ominous",     "illustration"),
    ("breakfast",  "near", "a plate of poached eggs on toast",  ("egg",),        "in the morning","homely",     None),
    ("juice",      "near", "a tall glass of apple juice on a sill", ("juice",),   "at midday",   "bright",      None),
    ("book",       "near", "an open paperback on a rumpled bed", ("paperback","book"), "in the evening", "melancholy", None),
    ("wallet",     "near", "a small metal hardware wallet on a desk", ("wallet",), "at night",    "austere",     None),
    ("coin",       "near", "a single gold coin on black velvet", ("coin",),      "at dusk",      "tense",       "photographic"),
    # --- FAR: no neighbour in the block -------------------------------------
    ("lighthouse", "far",  "a lighthouse in a storm",           ("lighthouse",), "at dawn",      "desolate",    None),
    ("lemons",     "far",  "a bowl of lemons on a wooden table", ("lemon",),     "at midday",    "cheerful",    None),
    ("tram",       "far",  "a tram in a rainy city",            ("tram",),       "at night",     "lonely",      None),
    ("heron",      "far",  "a grey heron in shallow water",     ("heron",),      "at sunrise",   "still",       None),
    ("watercycle", "far",  "the water cycle",                   ("water cycle","water"), None,   None,          "diagram"),
    ("cathedral",  "far",  "a cathedral nave",                  ("cathedral","nave"), "in the afternoon", "reverent", None),
    ("bicycle",    "far",  "a rusted bicycle against a wall",   ("bicycle",),    "at dusk",      "forlorn",     None),
    ("teapot",     "far",  "a chipped teapot on a stove",       ("teapot",),     "in the morning","cosy",       None),
    ("violin",     "far",  "a violin resting on a chair",       ("violin",),     "at twilight",  "sombre",      "anime"),
    ("glacier",    "far",  "a glacier calving into the sea",    ("glacier",),    "at dawn",      "awed",        None),
]


def build() -> list[dict]:
    probes = []
    for slug, bucket, phrase, keys, time_word, mood_word, style in SUBJECTS:
        style_suffix = f", as {'a' if style == 'diagram' else ''} {style}".replace("  ", " ") if style else ""
        want_style = style  # None == not requested; scorer then skips style_exact

        # shape 1 — bare. setting and mood are BOTH legitimately empty, which is
        # the strongest contamination probe: two empty slots and a biography.
        probes.append({
            "id": f"{slug}.bare", "bucket": bucket,
            "text": f"draw me {phrase}{style_suffix}",
            "gold": {"subject": keys, "setting": "", "mood": "", "style": want_style},
            "shape": "bare", "allow": [],
        })

        # shape 2 — subject + a time of day. `_INSTRUCTION` says time of day is
        # SETTING, never mood. This is the documented historical failure
        # (`mood: "dusk"`), so it is exercised on every subject rather than one.
        if time_word:
            probes.append({
                "id": f"{slug}.setting", "bucket": bucket,
                "text": f"draw me {phrase} {time_word}{style_suffix}",
                "gold": {"subject": keys, "setting": time_word.split()[-1],
                         "mood": "", "style": want_style},
                "shape": "setting", "allow": [],
            })

        # shape 3 — subject + an explicit mood. The mood slot is now occupied by
        # the user's own word, so a contaminant has to displace it to land here.
        if mood_word:
            probes.append({
                "id": f"{slug}.mood", "bucket": bucket,
                "text": f"draw me {phrase}, it should feel {mood_word}{style_suffix}",
                "gold": {"subject": keys, "setting": "", "mood": mood_word,
                         "style": want_style},
                "shape": "mood", "allow": [],
            })
    return probes


def main() -> int:
    probes = build()
    near = sum(1 for p in probes if p["bucket"] == "near")
    payload = {
        "_comment": (
            "FROZEN. Authored before any arm was run, and the runner refuses to "
            "start on a sha mismatch -- a probe set edited mid-run invalidates the "
            "pre-registration. `allow` lists tokens that legitimately belong in an "
            "answer even though the memory block also contains them; without it the "
            "contamination detector false-positives on exactly the near probes that "
            "matter most."
        ),
        "n": len(probes), "near": near, "far": len(probes) - near,
        "probes": probes,
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    sha = hashlib.sha256(body.encode()).hexdigest()
    payload["sha256"] = sha
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {OUT}")
    print(f"  {len(probes)} probes  ({near} near / {len(probes)-near} far)")
    from collections import Counter
    print(f"  shapes: {dict(Counter(p['shape'] for p in probes))}")
    print(f"  style golds: {dict(Counter(str(p['gold']['style']) for p in probes))}")
    print(f"  sha256(body) = {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
