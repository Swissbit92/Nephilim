#!/usr/bin/env python3
"""Apply a persona card's identity to the graph. Idempotent; --dry-run by default.

WHY THIS IS A SCRIPT AND NOT A ONE-OFF. The first apply for a persona captures its
:Baseline, and every property there is written under ON CREATE — so the dial values live
in the card at that moment become the permanent starting point for
"have you changed since we met?" (evo.cq01). A rebuild never refreshes it, by design. That
makes the first run of this a one-way step, and a one-way step should be reviewable,
repeatable and refuse to run against a card that is mid-experiment.

Identity NODES are regenerable — apply_card is build and rebuild both, and content is
refreshed from the card while her annotations (salience, reinforced_count,
last_referenced) survive, per ADR-018's A/B. Only the baseline is one-way.

Usage:
    scripts/utils/seed_identity.py --persona gwen              # dry run, prints the plan
    scripts/utils/seed_identity.py --persona gwen --apply      # writes
    scripts/utils/seed_identity.py --all --apply
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _card_is_clean(path: pathlib.Path) -> tuple[bool, str]:
    """Refuse a card with uncommitted edits.

    The dial A/B harnesses write `personas/gwen.json` on disk to build an arm and restore
    it in a `finally`. If one died mid-run, the card on disk is an EXPERIMENTAL arm — and
    seeding then would freeze an experimental dial value as her permanent baseline. git is
    the cheapest available check for "is this the shipped card".
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", str(path.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"could not ask git: {exc}"
    if out.returncode != 0:
        return False, f"git status failed: {out.stderr.strip()}"
    if out.stdout.strip():
        return False, f"uncommitted changes: {out.stdout.strip()}"
    return True, "clean"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", help="card key, e.g. gwen")
    ap.add_argument("--all", action="store_true", help="every card in personas/")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="seed even if the card has uncommitted edits (NOT for a first "
                         "apply — the baseline would freeze an experimental value)")
    a = ap.parse_args()
    if not (a.persona or a.all):
        ap.error("pass --persona KEY or --all")

    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import build_driver, read
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.repositories.identity_repository import IdentityRepository

    cfg = get_settings().graph
    if not cfg.enabled:
        print("REFUSING: GRAPH_ENABLED is false — nothing would be written.")
        return 2
    drv = build_driver(cfg.base_url, cfg.username, cfg.password)
    repo = IdentityRepository(drv, cfg.database)

    paths = (sorted((ROOT / "personas").glob("*.json")) if a.all
             else [ROOT / "personas" / f"{a.persona}.json"])
    rc = 0
    try:
        for path in paths:
            if not path.exists():
                print(f"MISSING: {path}")
                rc = 1
                continue
            card = json.loads(path.read_text())
            pid = card.get("key") or path.stem
            clean, why = _card_is_clean(path)
            existing = repo.baseline(pid)
            first_apply = existing is None

            print(f"\n=== {pid} ({path.name}) ===")
            print(f"  nodes the card implies : {len(identity_nodes(card))}")
            print(f"  card state             : {why}")
            print(f"  baseline               : "
                  f"{'NONE — this apply CREATES it (one-way)' if first_apply else 'exists, captured ' + str(existing['captured_at'])}")
            if first_apply:
                dials = (card.get("emotional_profile") or {}).get("sliders") or {}
                print(f"  dials to freeze        : {dials}")

            if first_apply and not clean and not a.allow_dirty:
                print("  REFUSED: first apply on a dirty card would freeze an "
                      "experimental dial value as her permanent baseline. Commit or "
                      "restore the card, or pass --allow-dirty if you are certain.")
                rc = 1
                continue

            if not a.apply:
                print("  DRY RUN — nothing written. Re-run with --apply.")
                continue

            res = repo.apply_card(card)
            print(f"  applied                : {res}")
            live = read(drv,
                        "MATCH (p:Persona {persona_id:$pid})-[]->(n:CurrentIdentity) "
                        "RETURN count(n) AS c", cfg.database, pid=pid)[0]["c"]
            print(f"  live CurrentIdentity   : {live}")
            b = repo.baseline(pid)
            print(f"  baseline now           : {b['captured_at'] if b else 'MISSING'}")
    finally:
        drv.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
