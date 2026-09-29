#!/usr/bin/env python3
"""Confirmatory three-arm run: does the graph's rules block reduce hard-wall breaches?

WHAT THIS SETTLES AND WHAT IT DOES NOT. The 20-probe run measured -34% relative with an
exact permutation p of 0.046, but three things disqualify it as a confirmation:

  * it was never powered for that effect -- its own prereg said 60 probes would be
  * a fifth of its probes never breached in either arm, so they carried no information
  * two of its probes are VERBATIM prompt exemplars, and the detector that scored it was
    repaired twice DURING the analysis, so the p-value is conditioned on instrument
    iteration. Three values have been quoted for the same 336 generations: 0.9697, 0.0580
    and 0.0460.

So this run freezes the instrument BEFORE generating, screens the probes on a pilot whose
data is then discarded, and pre-registers the decision rule.

WHY THREE ARMS RATHER THAN TWO. Turning the rules block on also makes the prompt ~350
tokens longer, and adding text to a prompt changes behaviour through position and
attention effects independent of what the text says. A two-arm OFF-vs-ON test measures
content PLUS length and reports the sum as content. PLACEBO is length-matched,
position-matched, non-normative filler:

    ON - PLACEBO   = the CONTENT effect      <- the primary, declared here
    PLACEBO - OFF  = the LENGTH effect       (the confound, measured)
    ON - OFF       = what a two-arm test would have said

`three_arm.check_placebo_matching` refuses the run if the placebo is not actually matched,
because an unmatched placebo is worse than none: the decomposition is reported with the
same confidence while measuring something else.

FOUR CATEGORIES, NOT FIVE. `break-char` is governed by no seeded rule, so it cannot
respond to the treatment. It was inside the old primary endpoint, diluting it. Here it is
a dilution control.

SEEDS ARE DISJOINT PER ARM AND THAT IS LOAD-BEARING. Verified live: this model is
byte-deterministic on (prompt, seed). OFF and PLACEBO differ only in prompt, so sharing a
seed block would correlate their noise; more importantly, any future A/A replicate sharing
seeds with its twin would produce byte-identical output and a null band of exactly zero.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import random
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
for _p in (REPO, REPO / "tests" / "evaluation" / "persona_eval", REPO / "scripts" / "research"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import hard_wall_probes as HW                                    # noqa: E402
import hard_wall_probes_60 as P60                                # noqa: E402
import three_arm as TA                                           # noqa: E402

OLLAMA = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
MODEL = os.environ.get("PERSONA_MODEL", "")

#: Non-normative filler for the PLACEBO arm. Length is tuned at build time to match the
#: real block; this is the sentence pool it is assembled from. Deliberately free of every
#: token in three_arm._NORMATIVE -- filler that tells the model what to do is a second
#: treatment, not a control.
_FILLER = (
    "The archive index was compiled over several seasons by a small team of catalogue "
    "assistants. Entries are grouped by acquisition date rather than by subject, which "
    "reflects the order in which the crates arrived. Marginal notes in the older volumes "
    "use a shorthand whose key was lost early on. Paper stock varies between gatherings, "
    "and the watermarks suggest at least three suppliers. A later hand added page numbers "
    "in pencil, skipping a leaf here and there. The binding was replaced once, and the "
    "original boards sit in a separate box. Humidity readings are taken twice a week. "
    "Several folios carry a stamp that appears nowhere in the register. The reading room "
    "keeps its own concordance, which disagrees with the index in eleven places. A card "
    "tray by the window holds cross-references in two hands, one of them faded. "
)


def _detector_sha() -> str:
    h = hashlib.sha256()
    p = REPO / "scripts" / "research" / "hard_wall_probes.py"
    h.update(b"scripts/research/hard_wall_probes.py")
    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def build_arms(persona: str) -> dict[str, tuple[str, str]]:
    """(system, per_turn_reminder) for off / placebo / on. Built once, before generating."""
    from src.coordinator import startup
    from src.coordinator.config import get_settings
    from src.coordinator import prompt_builder as pb
    from src.coordinator.repositories.neo4j_rule_repository import Neo4jRuleRepository
    from neo4j import GraphDatabase

    def _fresh(identity_source: str):
        os.environ["GRAPH_IDENTITY_SOURCE"] = identity_source
        get_settings.cache_clear()
        startup.init_graph_driver()
        pb._build_system_prompt_lean.cache_clear()
        return pb.build_system_prompt(persona)

    base = _fresh("true")

    drv = GraphDatabase.driver(
        os.environ["NEO4J_BASE_URL"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))
    try:
        rows = Neo4jRuleRepository(drv, os.environ.get("NEO4J_DATABASE", "neo4j"),
                                   ensure_schema=False).standing_rules(
            persona, limit=get_settings().graph.rule_read_limit)
    finally:
        drv.close()
    block = pb.build_graph_rules_block(persona, rows)
    reminder = pb.build_constraint_reminder(persona, rows)
    on = f"{base}\n\n{block}"

    # Match the placebo to the block's ADDED length, to the character, at the same offset.
    want = len(on) - len(base)
    filler = (_FILLER * (want // len(_FILLER) + 2))[:want].rstrip()
    filler += " " * (want - len(filler))
    placebo = f"{base}{filler}"
    # ...and match the reminder's length too, so the user turn is matched as well.
    rem_filler = (_FILLER * (len(reminder) // len(_FILLER) + 2))[:len(reminder)] if reminder else ""

    return {"off": (base, ""), "placebo": (placebo, rem_filler), "on": (on, reminder)}


def generate(system: str, user: str, reminder: str, seed: int, temperature: float = 0.9):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": f"{reminder}\n\n{user}" if reminder else user}],
        "stream": False,
        # keep_alive is a TOP-LEVEL field. Inside `options` Ollama accepts and silently
        # ignores it (HTTP 200 + types.go:1048 "invalid option provided"), which is how
        # three sibling harnesses in this repo came to have a no-op pin.
        "keep_alive": -1,
        "options": {"temperature": temperature, "num_ctx": 8192, "seed": seed},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", body,
                                {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as f:
        out = json.load(f)
    return out["message"]["content"], time.time() - t0


def preflight(arms: dict[str, tuple[str, str]], card: dict, frozen_sha: str | None) -> None:
    """Every check that can fail runs BEFORE a single generation is spent."""
    fails: list[str] = []

    sha = _detector_sha()
    if frozen_sha and sha != frozen_sha:
        fails.append(f"detector sha {sha} != frozen {frozen_sha}; the instrument moved")
    if HW.validate():
        fails.append(f"detector self-test has {len(HW.validate())} failures")

    audit = REPO / "docs" / "detector_audits" / "hard_wall_probes.json"
    if not audit.exists():
        fails.append("no recall audit for the detector")
    else:
        rec = json.loads(audit.read_text())
        if rec.get("detector_sha") != sha:
            fails.append(f"recall audit is stale (audited {rec.get('detector_sha')}, now {sha})")

    try:
        P60.assert_uncontaminated(card)
        P60.assert_no_duplicates()
        P60.assert_shape_balance()
    except AssertionError as e:
        fails.append(str(e))

    errs = TA.check_placebo_matching(arms["off"][0], arms["placebo"][0], arms["on"][0])
    fails.extend(errs)

    if arms["off"][0] == arms["on"][0]:
        fails.append("OFF and ON prompts are identical; the graph read returned nothing")
    if not MODEL:
        fails.append("PERSONA_MODEL is unset")

    if fails:
        raise SystemExit("PRE-FLIGHT FAILED:\n  - " + "\n  - ".join(fails))
    print(f"pre-flight OK  detector={sha}  "
          f"off={len(arms['off'][0])} placebo={len(arms['placebo'][0])} on={len(arms['on'][0])} chars")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--persona", default="gwen")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--pilot", action="store_true",
                    help="arm OFF only, to screen probes on base rate. Data is DISCARDED.")
    ap.add_argument("--probes", default="", help="json file of screened probes (confirmatory)")
    ap.add_argument("--out", default="")
    ap.add_argument("--frozen-sha", default="")
    ap.add_argument("--temperature", type=float, default=0.9)
    a = ap.parse_args()

    card = P60.load_card(REPO)
    arms = build_arms(a.persona)
    preflight(arms, card, a.frozen_sha or None)

    if a.probes:
        keep = {tuple(x) for x in json.loads(pathlib.Path(a.probes).read_text())["keep"]}
        rule_probes = [t for t in P60.PROBES if (t[0], t[2]) in keep]
    else:
        rule_probes = list(P60.PROBES)

    if a.pilot:
        work = [("off", c, s, q) for c, s, q in rule_probes]
        out = pathlib.Path(a.out or REPO / "scripts/research/three_arm_pilot.jsonl")
    else:
        allp = rule_probes + P60.DILUTION + P60.CONTROLS
        work = [(arm, c, s, q) for arm in TA.ARMS for c, s, q in allp]
        out = pathlib.Path(a.out or REPO / "scripts/research/three_arm_results.jsonl")

    if out.exists():
        raise SystemExit(f"{out} exists. Refusing to append to a previous run -- move it "
                         f"aside. (The old harness opened results in 'a' mode, so a re-run "
                         f"silently interleaved two runs into one file.)")

    #: One disjoint seed block per arm. See the module docstring.
    seed_base = {"off": 10_000, "placebo": 20_000, "on": 30_000}
    jobs = [(arm, c, s, q, rep) for (arm, c, s, q) in work for rep in range(a.k)]
    random.Random(20260929).shuffle(jobs)

    run_id = f"{int(time.time())}-{_detector_sha()}"
    print(f"run_id={run_id}  {len(jobs)} generations  arms={sorted({j[0] for j in jobs})}")
    t0 = time.time()
    with out.open("w") as f:
        for i, (arm, cat, shape, q, rep) in enumerate(jobs, 1):
            system, reminder = arms[arm]
            # STABLE hash. Python randomizes str.__hash__ per process, so
            # `hash(q) % 997` made the seeds unreproducible across runs -- in a
            # study whose null arm depends on seed discipline, and whose model is
            # byte-deterministic on (prompt, seed).
            qh = int(hashlib.sha256(q.encode()).hexdigest()[:8], 16) % 997
            seed = seed_base[arm] + rep * 997 + qh
            reply, elapsed = generate(system, q, reminder, seed, a.temperature)
            breached = HW.breached(cat, reply, q)
            f.write(json.dumps({
                "run_id": run_id, "arm": arm, "cat": cat, "shape": shape, "q": q,
                "rep": rep, "seed": seed, "order": i, "reply": reply,
                "breached": breached, "words": len(reply.split()), "elapsed": elapsed,
            }) + "\n")
            f.flush()
            if i % 25 == 0 or i == len(jobs):
                rate = (time.time() - t0) / i
                print(f"  {i}/{len(jobs)}  {rate:.1f}s/gen  "
                      f"eta {(len(jobs) - i) * rate / 60:.0f}m", flush=True)

    print(f"\nwrote {out}  in {(time.time()-t0)/60:.1f} min")
    if a.pilot:
        rows = [json.loads(l) for l in out.open()]
        per: dict[tuple, list] = collections.defaultdict(list)
        for r in rows:
            if r["breached"] is not None:
                per[(r["cat"], r["q"])].append(r["breached"])
        keep = [[c, q] for (c, q), v in per.items() if sum(v) > 0]
        drop = [[c, q] for (c, q), v in per.items() if sum(v) == 0]
        sel = out.with_name("three_arm_screened.json")
        sel.write_text(json.dumps({"keep": keep, "dropped_zero_base_rate": drop,
                                   "k": a.k, "run_id": run_id}, indent=2))
        print(f"\nBASE-RATE SCREEN: keep {len(keep)}, drop {len(drop)} (never breached)")
        for c in P60.CATEGORIES:
            print(f"  {c:11s} keep {sum(1 for x in keep if x[0]==c):2d} / "
                  f"{sum(1 for x in keep+drop if x[0]==c)}")
        print(f"wrote {sel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
