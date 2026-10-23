#!/usr/bin/env python3
"""Does the injected memory block corrupt image ARGUMENTS? Four arms.

🔴 WHY THE EXISTING PIN CANNOT ANSWER THIS.
`tests/integration/test_image_extract_live.py` calls `extract()` at lines 79,
93 and 113 with NO `system_prompt`, so it defaults to `""`. Production passes
the whole persona system prompt, which since ADR-022 carries the numbered
recall frame -- measured live at 833 tokens, "42 NUMBERED recalled facts + 8
self-facts". So the historical 8/8 was measured on a prompt production has not
built since `routes/chat.py:876` began splicing `extra_system_context` in. The
pin also skips entirely under `PERSONA_MODEL=test-model`, which is what
`tests/conftest.py:28` sets. It is blind twice over.

THE LITERATURE, WITH THE DIRECTION CORRECTED.
Deflanderization (arXiv 2510.13586, CPDC 2025 R2, Table 1, API track) reports
function-ARGUMENT exact match 0.226 for the persona-heavy ZeroShot baseline and
0.359 once persona instructions and world-setting text were STRIPPED.
⚠️ An earlier note in this repo quoted that as "0.359 -> 0.226", with the arrow
reversed. The thesis survives -- persona content hurts structured output -- but
the baseline is the LOW number and it is stripping persona that helps.
⚠️ It degrades ARGUMENTS (+59% relative) far more than NAMES (0.622 -> 0.714,
+15%). So "the tool still fires" is not evidence of health, and firing is
exactly what `scripts/research/tool_calling_vs_history.py` measures.

GRAMMAR DOES NOT PROTECT VALUES. Tam et al., EMNLP 2024 (arXiv 2408.02442):
JSON-schema-constrained generation dropped GSM8K 75.99% -> 49.25% while format
compliance stayed near perfect. OpenAI's structured-outputs docs claim schema
adherence and say nothing about semantics; llama.cpp's GBNF docs note the model
"has no visibility into the schema" under a grammar alone. A vendor rebuttal
(dottxt.ai, who ship Outlines) disputes the EMNLP prompt parity but claims no
immunity either. `format=_schema()` guarantees four keys exist. It guarantees
nothing about what is in them.

THE HYPOTHESIS, stated so it can fail. Not "accuracy drops" -- the sharp
version: the block tells the model `do not invent details they did not give`
while handing it a biography of the operator. This exact failure already
happened on this path from a different source: under 8 messages of real
history, a tool call composed a subject lifted VERBATIM from an image request
eight messages earlier. A wrong picture, not no picture. `extract()` is immune
to the history version only because it passes none (INV-4). Nothing guards the
system prompt, and INV-4 does not cover it.

REPS DO NOT BUY POWER HERE. `EXTRACT_TEMPERATURE = 0.0` (`extract.py:66`) is
greedy, so repeated calls on one cell sample nothing -- verified: a 10-probe
2-arm pilot at 5 reps returned byte-identical aggregates. Replication comes
from PROBES. `--reps 2` is a DETERMINISM PRE-FLIGHT only: if a cell's two
outputs differ, the assumption is false, the run aborts, and the
pre-registration must be amended rather than quietly carried.

READ-ONLY. Calls `extract()` directly with an explicit client. Touches no
store: not the session API, not SQLite, not Neo4j.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
import sys
import time
from collections import Counter

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
# Same path setup as `scripts/research/three_arm_hardwall.py:54-56`:
# `persona_eval/three_arm.py` imports its siblings by bare name, so its own
# directory has to be importable or the placebo check cannot be reused.
for _p in (ROOT, ROOT / "scripts/research", ROOT / "tests/evaluation/persona_eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FIXTURE = ROOT / "scripts/research/contamination/fixtures/injected_block_2026-10-23.json"
PROBES = HERE / "probes.json"
RESULTS = HERE / "results.jsonl"

#: Generic visual vocabulary that can coincide with the block without being
#: sourced from it. Kept deliberately short: a false positive is visible in the
#: printed evidence and dismissible by eye, a false negative is invisible.
_GENERIC = set("""light lighting warm cool soft dark bright dim shadow colour color
scene view shot frame image picture photo background foreground close distant
indoor outdoor natural artificial golden blue grey gray white black red green""".split())


def _norm(text: str) -> str:
    """Mirror `operator_fact_writer._norm` rather than invent a second
    normaliser -- two normalisers drift and then the scores are incomparable."""
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(".,;!?")


def contaminant_vocabulary(block: str) -> set[str]:
    """Distinctive tokens from the frozen block, lowercased.

    DERIVED FROM THE FIXTURE AT RUNTIME, never hardcoded. A hardcoded list
    silently stops matching when the block changes, which is the
    "source-scanning check has a blind-spot class" failure already recorded in
    this repo once.
    """
    stop = set("""the and for with that this from have has had you your his her its
their them they there what when which who whom how why not but all any are was were
been being own some such only than too very can will just should now about into over
under things know because told him never recite list quote numbers back note yourself
speak entirely voice earlier conversations seeker carry remembered prefers called does
eat enjoys likes plays reads goes sold user name also more most other another every
each both few many much general interest especially""".split())
    out = set()
    for w in set(re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", block)):
        lw = w.lower().strip("'")
        if lw in stop or lw in _GENERIC:
            continue
        if w[0].isupper() or lw in {
            "vegetarian", "benedict", "bouldering", "pottery", "glassblowing",
            "dumbbells", "hardware", "seedphrase", "breakfast",
        }:
            out.add(lw)
    return out


def score(intent, probe: dict, vocab: set[str]) -> dict:
    """Five independent judgements, reported separately on purpose: a blended
    score would hide which one moved."""
    from tests.integration.test_image_extract_live import TIME_OF_DAY

    gold = probe["gold"]
    fields = {"subject": intent.subject or "", "setting": intent.setting or "",
              "mood": intent.mood or "", "style": intent.style or ""}

    subject_ok = any(_norm(k) in _norm(fields["subject"]) for k in gold["subject"])

    # Containment per field, averaged. Only fields with a stated gold count --
    # an empty gold is "should be empty", scored separately below.
    parts = []
    for f in ("subject", "setting", "mood"):
        want = gold[f]
        if f == "subject":
            parts.append(1.0 if subject_ok else 0.0)
        elif want:
            parts.append(1.0 if _norm(want) in _norm(fields[f]) else 0.0)
    containment = sum(parts) / len(parts) if parts else float("nan")

    # The documented historical defect: a time-of-day word in `mood`.
    # TIME_OF_DAY is IMPORTED from the live pin, not copied.
    mood_n = _norm(fields["mood"])
    swap = bool(mood_n) and any(_norm(t) == mood_n or _norm(t) in mood_n
                                for t in TIME_OF_DAY)

    style_exact = (fields["style"] == gold["style"]) if gold["style"] else None

    # Contamination: a block token in any field that the user did not say and
    # that is not allow-listed for this probe.
    said = set(re.findall(r"[a-z'\-]{3,}", probe["text"].lower()))
    said |= {w for k in gold["subject"] for w in re.findall(r"[a-z'\-]{3,}", k.lower())}
    said |= {w for a in probe.get("allow", []) for w in re.findall(r"[a-z'\-]{3,}", a.lower())}
    blob = " ".join(fields.values()).lower()
    hits = sorted({w for w in vocab
                   if w not in said and re.search(rf"\b{re.escape(w)}\b", blob)})

    return {"probe": probe["id"], "bucket": probe["bucket"], "shape": probe["shape"],
            "subject_ok": subject_ok, "containment": containment,
            "mood_setting_swap": swap, "style_exact": style_exact,
            "contaminated": bool(hits), "contaminants": hits, "fields": fields}


def build_arms(block: str, persona: str) -> dict[str, str]:
    """Four arms. `system_prompt=""` vs "production" conflates three separate
    treatments -- persona prose, the graph rules block, and the memory block.
    Separating them is free here (greedy, one call per cell)."""
    from src.coordinator.memory_text_utils import estimate_tokens
    from src.coordinator.prompt_builder import build_graph_rules_block, build_system_prompt

    base = build_system_prompt(persona, include_examples=True)
    rules = build_graph_rules_block(persona) or ""
    nomem = f"{base}\n\n{rules}" if rules else base

    # A length-matched, content-free placebo inserted at the SAME position, so
    # prod_mem - prod_placebo isolates CONTENT and prod_placebo - prod_nomem
    # isolates LENGTH. The literature has no controlled study separating these.
    target = estimate_tokens(block)
    filler, n = [], 0
    while estimate_tokens("\n".join(filler)) < target:
        n += 1
        filler.append(f" [{n}] Entry number {n} is recorded here for length only.")
    placebo = "<remembered>\n" + "\n".join(filler) + "\n</remembered>"

    return {"bare": "", "prod_nomem": nomem,
            "prod_placebo": f"{nomem}\n\n{placebo}", "prod_mem": f"{nomem}\n\n{block}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2,
                    help="determinism pre-flight only; rep 2 is discarded")
    ap.add_argument("--persona", default="gwen")
    ap.add_argument("--arms", default="bare,prod_nomem,prod_placebo,prod_mem")
    ap.add_argument("--limit", type=int, default=0, help="first N probes (pilot)")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    fixture = json.loads(FIXTURE.read_text())
    if not fixture.get("verified_against_live_log"):
        print("REFUSING: fixture not verified against the live log.", file=sys.stderr)
        return 2
    pdoc = json.loads(PROBES.read_text())
    recorded = pdoc.pop("sha256", None)
    body = json.dumps(pdoc, indent=2, ensure_ascii=False)
    actual = hashlib.sha256(body.encode()).hexdigest()
    if recorded != actual:
        print(f"REFUSING: probes.json sha mismatch.\n  recorded {recorded}\n  actual   {actual}\n"
              "  A probe set edited mid-run invalidates the pre-registration.", file=sys.stderr)
        return 2

    probes = pdoc["probes"][: args.limit or None]
    block = fixture["rendered_block"]
    vocab = contaminant_vocabulary(block)
    arms = build_arms(block, args.persona)
    wanted = [a for a in args.arms.split(",") if a in arms]

    # The placebo must control what it claims to, or the decomposition is
    # reported with the same confidence while measuring something else.
    if {"prod_nomem", "prod_placebo", "prod_mem"} <= set(wanted):
        from tests.evaluation.persona_eval.three_arm import check_placebo_matching
        why = check_placebo_matching(arms["prod_nomem"], arms["prod_placebo"], arms["prod_mem"])
        if why:
            print("REFUSING: placebo does not control what it claims:", file=sys.stderr)
            for w in why:
                print(f"  - {w}", file=sys.stderr)
            return 2
        print("placebo matching: OK")

    from src.coordinator.memory_text_utils import estimate_tokens
    from src.coordinator.services.image_gen.extract import default_client, extract

    client, model = default_client()
    if client is None or not model:
        print("REFUSING: no ollama client. A fallback result is not a measurement.",
              file=sys.stderr)
        return 2

    print(f"model   : {model}")
    print(f"fixture : {fixture['block_tokens']} tok, {len(fixture['recalled_facts'])} recalled "
          f"+ {len(fixture['self_facts'])} self (frozen {fixture['frozen_on']})")
    print(f"probes  : {len(probes)} ({sum(1 for p in probes if p['bucket']=='near')} near / "
          f"{sum(1 for p in probes if p['bucket']=='far')} far), sha ok")
    print(f"vocab   : {len(vocab)} distinctive block tokens")
    for a in wanted:
        print(f"  arm {a:<13} {estimate_tokens(arms[a]):>5} tok")
    total = len(probes) * len(wanted) * args.reps
    print(f"calls   : {total}  (reps={args.reps}, rep 2 is the determinism check)\n")

    # Shuffle (arm, probe, rep) under a fixed seed so arms interleave at fine
    # grain -- a drifting machine must not land entirely on one arm.
    jobs = [(a, p, r) for a in wanted for p in probes for r in range(args.reps)]
    random.Random(20261023).shuffle(jobs)

    rows, t0 = [], time.time()
    RESULTS.write_text("")
    for i, (arm, probe, rep) in enumerate(jobs, 1):
        try:
            intent, how = extract(probe["text"], client=client, model=model,
                                  system_prompt=arms[arm])
            r = score(intent, probe, vocab)
            r.update({"arm": arm, "rep": rep, "how": how})
        except Exception as exc:                                  # noqa: BLE001
            r = {"arm": arm, "probe": probe["id"], "rep": rep,
                 "how": "error", "error": repr(exc)}
        rows.append(r)
        with RESULTS.open("a") as fh:
            fh.write(json.dumps(r) + "\n")
        if i % 25 == 0:
            print(f"  {i}/{len(jobs)}  ({time.time()-t0:.0f}s)")

    return report(rows, wanted, args.reps)


def determinism(rows) -> tuple[int, int]:
    """How many cells disagreed between rep 0 and rep 1."""
    by = {}
    for r in rows:
        if "error" in r:
            continue
        by.setdefault((r["arm"], r["probe"]), {})[r["rep"]] = r.get("fields")
    pairs = [v for v in by.values() if 0 in v and 1 in v]
    return sum(1 for v in pairs if v[0] != v[1]), len(pairs)


def report(rows, arms, reps) -> int:
    if reps >= 2:
        bad, n = determinism(rows)
        print(f"\nDETERMINISM PRE-FLIGHT: {bad} of {n} cells differed between reps")
        if bad:
            print("  ⚠️ Generation is NOT deterministic at this temperature. One observation"
                  "\n     per cell is then an unreplicated sample and the pre-registration"
                  "\n     must be amended to raise reps before any claim is made.")
        else:
            print("  greedy decoding confirmed -> rep 1 discarded, n = probes")

    live = [r for r in rows if "error" not in r and r["rep"] == 0]

    print("\n" + "=" * 92)
    print(f"{'arm':<14}{'n':>4}{'fallback':>9}{'subject_ok':>12}{'containment':>13}"
          f"{'CONTAM':>8}{'near':>7}{'far':>6}{'mood_swap':>11}{'style_ok':>10}")
    print("-" * 92)
    agg = {}
    for arm in arms:
        a = [r for r in live if r["arm"] == arm]
        if not a:
            print(f"{arm:<14}{0:>4}   (no rows)"); continue
        near = [r for r in a if r["bucket"] == "near"]
        far = [r for r in a if r["bucket"] == "far"]
        st = [r for r in a if r["style_exact"] is not None]
        pc = lambda xs, k: (100.0*sum(1 for r in xs if r[k])/len(xs)) if xs else float("nan")
        cont = [r["containment"] for r in a if r["containment"] == r["containment"]]
        agg[arm] = a
        print(f"{arm:<14}{len(a):>4}{sum(1 for r in a if r['how']=='fallback'):>9}"
              f"{pc(a,'subject_ok'):>11.0f}%{(sum(cont)/len(cont) if cont else float('nan')):>12.2f} "
              f"{pc(a,'contaminated'):>7.0f}%{pc(near,'contaminated'):>6.0f}%"
              f"{pc(far,'contaminated'):>5.0f}%{pc(a,'mood_setting_swap'):>10.0f}%"
              f"{pc(st,'style_exact'):>9.0f}%")
    print("=" * 92)

    # PRE-REGISTERED PRIMARY: contamination, prod_mem - prod_placebo, paired.
    if "prod_mem" in agg and "prod_placebo" in agg:
        contrast(agg, "prod_mem", "prod_placebo", "PRIMARY (pre-registered)")
    if "prod_placebo" in agg and "prod_nomem" in agg:
        contrast(agg, "prod_placebo", "prod_nomem", "length confound")
    if "prod_nomem" in agg and "bare" in agg:
        contrast(agg, "prod_nomem", "bare", "deflanderization proper (persona vs stripped)")

    ev = [r for r in live if r.get("contaminants")]
    if ev:
        print(f"\nCONTAMINATION EVIDENCE ({len(ev)} of {len(live)}):")
        for r in ev[:20]:
            print(f"  [{r['arm']}/{r['probe']}] {r['contaminants']}  -> {r['fields']}")
    else:
        print("\nno lexical contamination detected in any arm")

    sw = [r for r in live if r["mood_setting_swap"]]
    if sw:
        print(f"\nMOOD/SETTING SWAPS ({len(sw)}):")
        for r in sw[:12]:
            print(f"  [{r['arm']}/{r['probe']}] setting={r['fields']['setting']!r} "
                  f"mood={r['fields']['mood']!r}")

    print("\n⚠️ RESOLUTION, stated before any reading. One observation per cell on "
          f"{len(agg.get('prod_mem', []))} probes.\n"
          "   An exact paired sign test needs >=6 one-way discordant pairs for p<0.05\n"
          "   (2*2^-6 = 0.031), so this design detects a shift concentrated in >=6 probes\n"
          "   and CANNOT resolve anything smaller. Instrument resolution is 1/n ~ 1.7pp;\n"
          "   no tolerance finer than that may be quoted. The literature's reference\n"
          "   effect is 13.3pp on argument exact match, ~8 discordant pairs in\n"
          "   expectation -- detectable but BORDERLINE. Powered for >=15pp; 10-15pp is\n"
          "   suggestive; below 10pp descriptive only. A null here means 'no effect of\n"
          "   at least that size', never 'no effect'. If the estimate lands 10-15pp the\n"
          "   pre-registered follow-up is MORE PROBES, not more reps.")
    print("\n⚠️ The contamination detector is LEXICAL. It cannot see semantic leakage\n"
          "   that re-words ('a tabby cat' sourced from 'Pemberton'), so every rate is a\n"
          "   LOWER BOUND. That does not bias the CONTRAST unless missed shapes differ by\n"
          "   arm, which lexical misses do not -- but an absolute rate must never be\n"
          "   quoted as a level.")
    return 0


def contrast(agg, a_name, b_name, label):
    sys.path.insert(0, str(ROOT / "scripts/research"))
    from paired_stats import report as pr

    A = {r["probe"]: r for r in agg[a_name]}
    B = {r["probe"]: r for r in agg[b_name]}
    both = sorted(set(A) & set(B))
    print(f"\n{label}: contamination, {a_name} vs {b_name}, paired n={len(both)}")
    a = [1.0 if A[k]["contaminated"] else 0.0 for k in both]
    b = [1.0 if B[k]["contaminated"] else 0.0 for k in both]
    nb = sum(1 for i in range(len(both)) if a[i] > b[i])
    nc = sum(1 for i in range(len(both)) if b[i] > a[i])
    print(f"  discordant: {a_name}-only={nb}  {b_name}-only={nc}")
    if nb + nc == 0:
        print("  no discordant pairs -> undefined; no evidence of a difference")
    else:
        print("  " + pr(a, b, label=f"contamination {a_name}-{b_name}").replace("\n", "\n  "))
    ca = [A[k]["containment"] for k in both if A[k]["containment"] == A[k]["containment"]]
    cb = [B[k]["containment"] for k in both if B[k]["containment"] == B[k]["containment"]]
    if len(ca) == len(cb) and ca:
        print(f"  containment (graded, where the three tests genuinely differ):")
        print("  " + pr(ca, cb, label=f"containment {a_name}-{b_name}").replace("\n", "\n  "))


if __name__ == "__main__":
    raise SystemExit(main())
