# tests/evaluation/persona_eval/run_eval.py
"""Persona-eval runner + baseline freeze (ADR-005 Phase A).

Drives the live backend to collect each persona's responses to the probe set,
computes the trustworthy metrics (distinctiveness attribution + flatness), and
writes a timestamped baseline under ``baselines/``. This is the ADR-005
acceptance-gate step 1: *freeze a per-persona legacy baseline first.*

The metric computation (``compute_report``) is pure and unit-tested headless; the
live collection (``collect_live``) is a thin shell requiring Ollama + the
backend.

CLI:
  python tests/evaluation/persona_eval/run_eval.py --label legacy
  python tests/evaluation/persona_eval/run_eval.py --label lean-candidate
Then compare two baselines, or feed paired responses into ab_harness.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Dict, List

import persona_metrics as pm

_BASELINE_DIR = Path(__file__).parent / "baselines"
DEPTH_CATEGORY = "research_depth"


def responses_by_persona(results: List[dict], category: str = "distinctiveness") -> Dict[str, List[str]]:
    """Group answers by persona for one probe category (default: the shared set)."""
    out: Dict[str, List[str]] = {}
    for r in results:
        if r.get("category") == category and r.get("answer"):
            out.setdefault(r["persona"], []).append(r["answer"])
    return out


def probe_ids_by_persona(results: List[dict],
                         category: str = "distinctiveness") -> Dict[str, List[str]]:
    """The probe ids parallel to `responses_by_persona`, in the same order.

    WHY THIS IS NEEDED FOR A PAIRED DESIGN. `responses_by_persona` returns bare answers,
    so the only way to align two runs is BY POSITION -- and it skips rows where
    `not r.get("answer")`. One errored probe in one arm therefore shifts every subsequent
    index, silently pairing probe i of arm A against probe i+1 of arm B. Nothing would
    report it; the attribution numbers would simply be computed on mismatched items.

    Keyed the same way `analyse_format_experiment.paired_deltas` already does it, which
    intersects on probe_id and never on position.
    """
    out: Dict[str, List[str]] = {}
    for r in results:
        if r.get("category") == category and r.get("answer"):
            out.setdefault(r["persona"], []).append(r.get("probe_id") or r.get("id") or "")
    return out


def compute_report(results: List[dict], embed_fn: Callable[[str], List[float]],
                   frozen_personas: set = None) -> dict:
    """Pure metric computation over collected results. No I/O, no live calls.

    ``frozen_personas`` (frozen-gallery runs): those personas are reference
    prototypes — only the active (non-frozen) personas are scored, so the gate
    relaxes to >=2 responses for active personas (frozen are non-empty by
    construction). Default None ⇒ byte-identical to the original full-run.
    """
    frozen = set(frozen_personas or ())
    dist = responses_by_persona(results, "distinctiveness")
    report: dict = {"n_results": len(results)}

    # Headline: can we tell the personas apart by voice? Only ACTIVE personas are scored.
    active = [p for p in dist if p not in frozen]
    if len(dist) >= 2 and len(active) >= 1 and all(len(dist[p]) >= 2 for p in active):
        report["distinctiveness"] = pm.attribution_accuracy(dist, embed_fn, frozen_personas=frozen or None)
        report["mean_separation"] = pm.mean_separation(dist, embed_fn)
    else:
        # Original message verbatim when not a gallery run (byte-identical).
        msg = "need >=2 personas with >=2 distinctiveness responses"
        if frozen:
            msg += " (active); frozen-gallery personas need >=1"
        report["distinctiveness"] = {"error": msg}

    # Flatness per persona (across all categories) + overall.
    per_persona_answers: Dict[str, List[str]] = {}
    for r in results:
        if r.get("answer"):
            per_persona_answers.setdefault(r["persona"], []).append(r["answer"])
    report["flatness_rate"] = {p: pm.flatness_rate(a) for p, a in per_persona_answers.items()}
    all_answers = [a for ans in per_persona_answers.values() for a in ans]
    report["flatness_rate_overall"] = pm.flatness_rate(all_answers)

    # Grounding-specific flatness (the Phase-3 failure mode) for visibility.
    grounding = [r["answer"] for r in results if r.get("category") == "grounding" and r.get("answer")]
    report["grounding_flatness_rate"] = pm.flatness_rate(grounding)

    # Research-depth: deterministic profile only. The *verdict* on depth is
    # pairwise and needs two arms (depth_judge.py) — a single baseline cannot
    # produce one, so this block deliberately reports signals, not a score.
    depth = [r["answer"] for r in results
             if r.get("category") == DEPTH_CATEGORY and r.get("answer")
             and not str(r["answer"]).startswith("[ERROR")]
    if depth:
        report["research_depth"] = pm.aggregate_depth_profiles(depth)
    return report


def resolve_personas(requested: List[str], available: List[str]) -> List[str]:
    """Map a canary request (``eeva,nyx``) onto the probe persona keys.

    Each token matches a persona that equals it, or whose key ends with
    ``_<token>`` (so ``eeva`` → ``nephilim_eeva``). Preserves the probe order and
    de-dupes. Raises ValueError on an unmatched token so a typo fails loud rather
    than silently shrinking the run.
    """
    keep: List[str] = []
    for tok in requested:
        tok = tok.strip()
        if not tok:
            continue
        matches = [p for p in available if p == tok or p.endswith(f"_{tok}")]
        if not matches:
            raise ValueError(f"--personas token {tok!r} matched none of {available}")
        for m in matches:
            if m not in keep:
                keep.append(m)
    return keep


def collect_live(base_url: str, probes: dict) -> List[dict]:  # pragma: no cover - live
    """Drive the backend over every persona × probe. Requires Ollama + backend."""
    import sys
    here = Path(__file__).parent
    manual = here.parent.parent / "manual"
    if str(manual) not in sys.path:
        sys.path.insert(0, str(manual))
    from api_client import create_session, chat  # type: ignore

    results: List[dict] = []
    personas = probes["personas"]
    single_turn = [(c, probes[c]) for c in ("distinctiveness", "voice", "grounding", "adversarial")]
    for persona in personas:
        for category, items in single_turn:
            for item in items:
                sid = create_session(persona, base_url=base_url)
                try:
                    answer, elapsed, source, _ = chat(sid, persona, item["prompt"], base_url=base_url)
                except Exception as e:
                    answer, elapsed, source = f"[ERROR: {e}]", 0.0, "error"
                results.append({"persona": persona, "category": category, "probe_id": item["id"],
                                "prompt": item["prompt"], "answer": answer, "source": source,
                                "elapsed": elapsed})
                print(f"  [{persona}/{category}/{item['id']}] {source} {elapsed:.0f}s")
        # drift: multi-turn within one session
        for d in probes.get("drift", []):
            sid = create_session(persona, base_url=base_url)
            for ti, turn in enumerate(d["turns"]):
                try:
                    answer, elapsed, source, _ = chat(sid, persona, turn, base_url=base_url)
                except Exception as e:
                    answer, elapsed, source = f"[ERROR: {e}]", 0.0, "error"
                results.append({"persona": persona, "category": "drift", "probe_id": f"{d['id']}-t{ti}",
                                "prompt": turn, "answer": answer, "source": source, "elapsed": elapsed})
            print(f"  [{persona}/drift/{d['id']}] {len(d['turns'])} turns")
    return results


def depth_personas(depth_probes: dict, run_personas: List[str]) -> List[str]:
    """Personas that should get the depth probes on this run.

    The depth set declares its own scope (``personas`` in
    research_depth_probes.json, currently eeva-only) because depth is an
    absolute per-response property — unlike attribution, it needs no competing
    personas. Intersecting with the run's persona list keeps ``--personas``
    authoritative, so a canary never silently probes someone it excluded.
    """
    scoped = depth_probes.get("personas") or []
    return [p for p in scoped if p in run_personas]


def collect_depth_live(base_url: str, depth_probes: dict,
                       personas: List[str]) -> List[dict]:  # pragma: no cover - live
    """Drive the backend over the research-depth probes. Requires Ollama + backend.

    One fresh session per probe, matching ``collect_live``: depth probes are
    independent questions, and carrying conversation state between them would
    let an earlier answer contaminate a later one.
    """
    import sys
    here = Path(__file__).parent
    manual = here.parent.parent / "manual"
    if str(manual) not in sys.path:
        sys.path.insert(0, str(manual))
    from api_client import create_session, chat  # type: ignore

    results: List[dict] = []
    for persona in personas:
        for item in depth_probes["probes"]:
            sid = create_session(persona, base_url=base_url)
            try:
                answer, elapsed, source, _ = chat(sid, persona, item["prompt"], base_url=base_url)
            except Exception as e:
                answer, elapsed, source = f"[ERROR: {e}]", 0.0, "error"
            results.append({"persona": persona, "category": DEPTH_CATEGORY,
                            "probe_id": item["id"], "prompt": item["prompt"],
                            "answer": answer, "source": source, "elapsed": elapsed})
            print(f"  [{persona}/{DEPTH_CATEGORY}/{item['id']}] {source} {elapsed:.0f}s")
    return results


def freeze_baseline(label: str, results: List[dict], report: dict, stamp: str,
                    manifest: dict = None) -> Path:  # pragma: no cover - io
    _BASELINE_DIR.mkdir(exist_ok=True)
    path = _BASELINE_DIR / f"baseline_{label}_{stamp}.json"
    payload = {"label": label, "stamp": stamp, "report": report, "results": results}
    if manifest is not None:
        payload["manifest"] = manifest  # additive: staleness-check inputs (frozen-gallery)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)
    return path


def main() -> int:  # pragma: no cover - live entry point
    import datetime
    ap = argparse.ArgumentParser(description="Persona-eval baseline runner (ADR-005 Phase A)")
    ap.add_argument("--label", required=True, help="baseline label, e.g. 'legacy' or 'lean-candidate'")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--personas", default=None,
                    help="comma-separated canary subset (e.g. 'eeva,nyx'); default = all probe personas. "
                         "Dev-iteration only — the acceptance gate always runs the full set.")
    ap.add_argument("--gallery", default=None,
                    help="frozen reference gallery: a baseline label ('abliterated') or path. "
                         "Re-probes only --personas live and freezes the OTHER personas from the "
                         "gallery as reference prototypes, so chance stays 1/N and the result is "
                         "comparable to that baseline. Requires --personas.")
    ap.add_argument("--depth", action="store_true",
                    help="also collect the research-depth probes (analytical quality)")
    ap.add_argument("--depth-only", action="store_true",
                    help="collect ONLY the research-depth probes. Use for a cheap depth control "
                         "arm: a gallery run recovers attribution from a frozen baseline, but that "
                         "baseline has no depth rows, so depth still needs its own control.")
    args = ap.parse_args()

    import frozen_gallery as fg

    probes = pm.load_probes()
    if args.personas:
        subset = resolve_personas(args.personas.split(","), probes["personas"])
        probes = {**probes, "personas": subset}
        print(f"[canary] restricted to {len(subset)} personas: {subset}")

    # --gallery recovers ATTRIBUTION comparability from a frozen baseline, but a
    # frozen baseline carries no depth rows, so there is nothing for it to make
    # comparable in a depth-only run. Refuse rather than emit a gallery-stamped
    # artifact whose manifest describes personas that were never scored.
    if args.depth_only and args.gallery:
        ap.error("--depth-only cannot be combined with --gallery: a frozen gallery "
                 "supplies attribution prototypes, and a depth-only run scores no "
                 "attribution. Run them as two separate arms.")

    # Frozen-gallery mode: load dormant personas' responses + verify staleness.
    frozen = None
    dormant_rows: List[dict] = []
    manifest = None
    if args.gallery:
        if not args.personas:
            ap.error("--gallery requires --personas (the active subset to re-probe)")
        active = set(probes["personas"])
        gallery = fg.load_baseline(args.gallery)
        dormant = fg.dormant_responses(gallery, active)
        if not dormant:
            ap.error(f"gallery {args.gallery!r} has no dormant personas outside {sorted(active)}")
        manifest = fg.default_manifest(sorted(active | set(dormant)))
        errors, warnings = fg.check_staleness(manifest, gallery.get("manifest"), active)
        for w in warnings:
            print(f"[gallery] ⚠️  {w}")
        if errors:
            for e in errors:
                print(f"[gallery] ⛔ {e}")
            print("Refusing: the frozen gallery is not comparable to a fresh run. Re-freeze it.")
            return 2
        frozen = set(dormant)
        dormant_rows = fg.dormant_result_rows(dormant)
        print(f"[gallery] frozen {len(frozen)} personas from {args.gallery}: {sorted(frozen)}")

    results: List[dict] = []
    if not args.depth_only:
        print(f"Collecting {args.label} over {len(probes['personas'])} personas...")
        results += collect_live(args.base_url, probes)
        results.extend(dormant_rows)  # empty unless --gallery

    if args.depth or args.depth_only:
        dprobes = pm.load_depth_probes()
        dpersonas = depth_personas(dprobes, probes["personas"])
        if not dpersonas:
            print(f"[depth] SKIPPED — depth scope {dprobes.get('personas')} does not intersect "
                  f"this run's personas {probes['personas']}")
        else:
            print(f"Collecting {len(dprobes['probes'])} depth probes over {dpersonas}...")
            results += collect_depth_live(args.base_url, dprobes, dpersonas)

    report = compute_report(results, pm.default_embed_fn(), frozen_personas=frozen)
    if manifest is None:
        manifest = fg.default_manifest(sorted(probes["personas"]))
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = freeze_baseline(args.label, results, report, stamp, manifest=manifest)
    print(f"\nBaseline written → {path}")
    d = report.get("distinctiveness", {})
    print(f"distinctiveness overall={d.get('overall')} (random={d.get('random_baseline')})")
    if frozen:
        print(f"  scored={d.get('scored_personas')} frozen={d.get('frozen_personas')}")
    print(f"flatness overall={report.get('flatness_rate_overall')} grounding={report.get('grounding_flatness_rate')}")
    rd = report.get("research_depth")
    if rd:
        print(f"research_depth n={rd['n']} words={rd['mean_words']} "
              f"causal={rd['mean_causal_density']} numeric={rd['mean_numeric_density']} "
              f"hedge={rd['hedge_rate']} falsify={rd['falsification_rate']}")
        print("  (deterministic signals only — the depth VERDICT is pairwise: "
              "run depth_judge.py against a control baseline)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
