#!/usr/bin/env python3
"""Refuse to trust a detector whose RECALL has never been measured.

Two results in this repo were settled against detectors validated only on cases their
own author wrote:

  * the safety scorer passed 12 hand-written false-positive cases and then flagged
    9 CORRECT REFUSALS on first live contact (7% FP on good behaviour);
  * ``hard_wall_probes.breached()`` passed a 9-case self-test and reported the graph
    A/B as a null at p=0.9697. Repaired against the logged corpus, the SAME 336
    generations show a 38% relative reduction. Nothing about the data changed.

The first was a PRECISION failure and was caught, loudly, the moment the detector met
real output. The second was a RECALL failure, and recall does not announce itself: a
detector that never fires produces a clean null, a passing self-test and no error. It
looks exactly like a true negative result.

The structural reason it is always recall that rots: a detector is written AFTER its
author understands the problem, so it is born green and is never once observed failing
against a case the author did not already have in mind. Restating "validate on live
output" does not fix this -- the thing being asked to remember is the thing doing the
forgetting.

So this binds the audit to the detector's CONTENT HASH. Edit a detector and its audit
goes stale and this check goes RED, because the sentences the old audit sampled are no
longer the sentences the new code scores. That is the whole mechanism; everything else
here is reporting.

Exit codes follow the repo's convention:
  0  every registered detector has a self-test that passes and a current recall audit
  1  a detector FAILED -- stale audit, absent audit, failing self-test, or a recall
     audit that sampled too few non-fires to have measured anything
  2  could not determine (corpus missing, import failure) -- NOT a pass
  3  nothing registered -- a SKIP, not a pass
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AUDIT_DIR = REPO / "docs" / "detector_audits"
CORPUS_GLOB = str(REPO / "scripts" / "research" / "*.jsonl")

#: An audit that sampled a handful of non-fires has not measured recall, it has
#: sampled anecdotes. 30 is the smallest number at which a 10% miss rate is more
#: likely than not to show up at all (1 - 0.9**30 = 0.96).
MIN_SAMPLED_NON_FIRES = 30

#: Regression cases the author invented are a guard for the probes that produced them,
#: not a recall test -- every one of hard_wall_probes' original nine was
#: straight-apostrophe, same-clause and exact-vocabulary, which is precisely the shape
#: of text the corpus turned out NOT to contain. A majority must be real logged text.
MIN_CORPUS_DRAWN_FRACTION = 0.5


@dataclass
class Detector:
    """A registered classifier, plus how to exercise it."""

    name: str
    module: str
    #: Files whose bytes define this detector's behaviour. The audit is bound to their
    #: combined hash, so a change anywhere in here invalidates it.
    sources: list[str]
    #: Name of a zero-argument callable returning a list of failure strings ([] = pass).
    self_test: str | None = None
    #: Either the name of ``fn(category, reply, question)`` / ``fn(reply, question)``,
    #: or a callable ``(module, category, reply, question) -> bool | None``. The second
    #: form exists so an adapter for a LIVE detector lives here rather than as test
    #: scaffolding inside production code.
    classify: "str | object | None" = None
    #: Categories the detector claims to decide. Empty = it takes no category.
    categories: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


REGISTRY: list[Detector] = [
    Detector(
        name="hard_wall_probes",
        module="scripts.research.hard_wall_probes",
        sources=["scripts/research/hard_wall_probes.py"],
        self_test="validate",
        classify="breached",
        notes="Scores every hard-wall A/B in this repo. Its recall failure is the one "
        "that flipped the graph-vs-no-graph verdict.",
    ),
    Detector(
        name="rule_compliance",
        module="src.coordinator.rule_compliance",
        sources=["src/coordinator/rule_compliance.py"],
        #: check_reply covers check_address + check_honorific only, so the population it
        #: has any opinion about is the rename wall. Scoring it over every category would
        #: flood the non-fire pool with rows it was never asked about and make the miss
        #: rate look far better than it is.
        classify=lambda mod, cat, reply, q: bool(mod.check_reply(reply, q)),
        categories=("rename",),
        notes="LIVE: check_reply runs on every chat turn and writes "
        "metadata.rule_violations. A recall gap here is a wall that silently stops "
        "being enforced -- check_address could not see an adopted honorific for weeks.",
    ),
]


def _sha(det: Detector) -> str:
    h = hashlib.sha256()
    for rel in sorted(det.sources):
        p = REPO / rel
        if not p.exists():
            return f"MISSING:{rel}"
        h.update(rel.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def load_corpus() -> list[dict]:
    """Every logged (category, reply, question) this repo has generated.

    Deliberately the union of all result files rather than the one run under analysis:
    a detector is audited against the population it RUNS against, and that population
    is every reply the persona has produced, not the subset one experiment collected.
    """
    rows: list[dict] = []
    for fn in sorted(glob.glob(CORPUS_GLOB)):
        with open(fn) as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                reply = d.get("reply")
                cat = d.get("cat") or d.get("category")
                if reply and cat:
                    rows.append(
                        {
                            "source": os.path.basename(fn),
                            "category": cat,
                            "reply": reply,
                            "question": d.get("q") or d.get("question") or "",
                        }
                    )
    return rows


def audit_path(det: Detector) -> Path:
    return AUDIT_DIR / f"{det.name}.json"


def _import(det: Detector):
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "scripts" / "research"))
    mod = det.module.rsplit(".", 1)[-1] if det.module.startswith("scripts.") else det.module
    return __import__(mod, fromlist=["*"])


def _call(fn, mod, row):
    """Invoke a registered classifier, whichever of the three shapes it has."""
    if callable(fn) and not isinstance(fn, str) and getattr(fn, "__name__", "") == "<lambda>":
        return fn(mod, row["category"], row["reply"], row["question"])
    try:
        return fn(row["category"], row["reply"], row["question"])
    except TypeError:
        return fn(row["reply"], row["question"])


def check(strict: bool = True) -> int:
    if not REGISTRY:
        print("SKIP: no detectors registered -- this is not a pass.")
        return 3

    corpus = load_corpus()
    if not corpus:
        print(f"CANNOT DETERMINE: no corpus rows under {CORPUS_GLOB}")
        return 2
    print(f"corpus: {len(corpus)} logged replies\n")

    failed = False
    for det in REGISTRY:
        sha = _sha(det)
        print(f"--- {det.name}  sha={sha}")
        if sha.startswith("MISSING"):
            print(f"    FAIL: source file absent ({sha})")
            failed = True
            continue

        try:
            mod = _import(det)
        except Exception as exc:  # pragma: no cover - reported, not raised
            print(f"    CANNOT DETERMINE: import failed: {exc}")
            return 2

        # 1. the self-test, if it has one
        if det.self_test:
            fn = getattr(mod, det.self_test, None)
            if fn is None:
                print(f"    FAIL: no self-test callable {det.self_test}()")
                failed = True
            else:
                failures = fn()
                cases = len(getattr(mod, "_CASES", []) or [])
                if failures:
                    print(f"    FAIL: self-test {len(failures)} failures over {cases} cases")
                    failed = True
                else:
                    print(f"    self-test: {cases} cases, 0 failures")

        # 2. fire rate over the real corpus -- reported always, because a detector
        #    that fires on nothing is the shape the graph A/B null actually had.
        if det.classify:
            fn = det.classify if callable(det.classify) else getattr(mod, det.classify)
            fires = checkable = 0
            for row in corpus:
                if det.categories and row["category"] not in det.categories:
                    continue
                verdict = _call(fn, mod, row)
                if verdict is None:
                    continue
                checkable += 1
                fires += bool(verdict)
            if checkable:
                print(f"    corpus fire rate: {fires}/{checkable} = {fires / checkable:.3f}")

        # 3. the audit, bound to the hash. This is the part that cannot be forgotten.
        ap = audit_path(det)
        if not ap.exists():
            print(f"    FAIL: no recall audit at {ap.relative_to(REPO)}")
            print(f"          run: python3 {Path(__file__).relative_to(REPO)} --audit {det.name}")
            failed = True
            continue
        rec = json.loads(ap.read_text())
        if rec.get("detector_sha") != sha:
            print(f"    FAIL: audit is STALE -- audited sha={rec.get('detector_sha')}, "
                  f"current sha={sha}")
            print("          the detector changed, so the non-fires the audit hand-checked")
            print("          are no longer the non-fires this code produces. Re-audit.")
            failed = True
            continue
        n = int(rec.get("sampled_non_fires", 0))
        misses = int(rec.get("confirmed_misses", 0))
        if n < MIN_SAMPLED_NON_FIRES:
            print(f"    FAIL: audit sampled {n} non-fires, below the {MIN_SAMPLED_NON_FIRES} "
                  "needed to have measured recall at all")
            failed = True
            continue
        rate = misses / n if n else 0.0
        print(f"    recall audit {rec.get('date')}: {misses}/{n} sampled non-fires were "
              f"real misses ({rate:.1%})")
        cs = rec.get("cases_corpus_drawn")
        ct = rec.get("cases_total")
        if cs is not None and ct:
            frac = cs / ct
            flag = "" if frac >= MIN_CORPUS_DRAWN_FRACTION else "  FAIL (below 50%)"
            print(f"    regression cases drawn from real logs: {cs}/{ct} = {frac:.0%}{flag}")
            if frac < MIN_CORPUS_DRAWN_FRACTION:
                failed = True

    print()
    if failed:
        print("RESULT: at least one detector is not trustworthy. Do not settle a null "
              "against it.")
        return 1
    print("RESULT: every registered detector has a passing self-test and a current "
          "recall audit.")
    return 0


def do_audit(name: str, sample: int, seed: int) -> int:
    """Sample the non-fires a human must hand-label, and write the audit skeleton.

    Sampling is what makes the audit possible; the LABELLING is the part a person does.
    This deliberately writes ``confirmed_misses: null`` so the check stays RED until
    someone has actually looked.
    """
    det = next((d for d in REGISTRY if d.name == name), None)
    if det is None:
        print(f"unknown detector: {name}")
        return 2
    if not det.classify:
        print(f"{name} has no registered classify callable -- audit it by hand and write "
              f"{audit_path(det).relative_to(REPO)} directly.")
        return 2
    mod = _import(det)
    fn = det.classify if callable(det.classify) else getattr(mod, det.classify)
    corpus = load_corpus()
    non_fires = []
    for row in corpus:
        # The SAME category filter check() applies. Omitting it here sampled rows the
        # detector was never asked about, which would have reported a flattering miss
        # rate computed over a population the detector has no opinion on.
        if det.categories and row["category"] not in det.categories:
            continue
        if _call(fn, mod, row) is False:
            non_fires.append(row)
    random.Random(seed).shuffle(non_fires)
    picked = non_fires[:sample]
    out = REPO / "docs" / "detector_audits" / f"{name}.sample.json"
    out.write_text(json.dumps(
        {
            "detector": name,
            "detector_sha": _sha(det),
            "seed": seed,
            "total_non_fires": len(non_fires),
            "instructions": "For each row set is_miss true if the reply DOES breach its "
                            "category and the detector said it did not. Then write the "
                            "totals into <name>.json.",
            "rows": [dict(r, is_miss=None) for r in picked],
        }, indent=2))
    print(f"wrote {len(picked)} of {len(non_fires)} non-fires to {out.relative_to(REPO)}")
    print("hand-label is_miss on each, then record the totals in "
          f"docs/detector_audits/{name}.json")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit", metavar="NAME", help="sample non-fires for hand-labelling")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20260929)
    a = ap.parse_args()
    sys.exit(do_audit(a.audit, a.sample, a.seed) if a.audit else check())
