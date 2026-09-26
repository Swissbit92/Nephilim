#!/usr/bin/env python3
"""
Seed a persona's standing rules from her card into the Neo4j graph — ADR-014.

Purpose:
    A persona's card in git is her ORIGIN. This imports the rules it declares into
    the graph, which is a rebuildable projection of them. The card is only ever
    read here, never written — that is an invariant, not a convention, because the
    card is also the reset target and a system that writes to it destroys the only
    copy of the original.

    Rules are read from two files that must agree:
      personas/<key>.json                   the card, holding `dont` text
      personas/_rule_tiers/<key>.yaml       the tier overlay (hard_wall/soft_wall/dial)

    Only `layer: semantic` rules are seeded. The `layer: consumer` ones are
    rendering mechanics — emoji placement, grammatical person, vocabulary — which
    SEMANTIC_PLATFORM.md's layer table assigns to the consumer tier, with the right
    reason: "negating it yields a different design choice, not a falsehood." They
    stay in the card as template text and the graph never sees them.

Usage:
    python3 scripts/utils/seed_graph.py --persona gwen_dev --dry-run
    python3 scripts/utils/seed_graph.py --persona gwen_dev
    python3 scripts/utils/seed_graph.py --persona gwen_dev --verify-only
    python3 scripts/utils/seed_graph.py --all

Exit codes:
    0 = nothing needed doing, or everything applied and verified
    1 = one or more personas failed (the rest are still processed)
    2 = could not determine — no graph configured, or no graph reachable

WHY A SEPARATE SCRIPT AND NOT A STARTUP HOOK. Seeding is the one operation a
rebuild must never perform: [ADR-009] makes the pipeline a build with `append` and
`rebuild` entry points, and a rebuild that re-imported the card would overwrite
months of learned state with factory settings, silently, producing a persona who
behaves plausibly and has forgotten everything. Keeping the seed out of every
automatic path means the dangerous operation is not reachable from the routine one
— structural, rather than a rule someone has to remember.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# parents[2] from scripts/utils/ is the repo root. Anchored on __file__ rather
# than the cwd so a hand-run from anywhere resolves the same files as launchd.
REPO_ROOT = Path(__file__).resolve().parents[2]
PERSONAS_DIR = REPO_ROOT / "personas"
TIERS_DIR = PERSONAS_DIR / "_rule_tiers"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

# PRIORITY IS DERIVED FROM THE TIER, not authored per rule.
#
# This replaces the flat sketch (card dont 100 / card do 80 / operator 50 /
# inferred 10) that the operator rejected as too blunt: a single integer cannot
# express "this may be moved and that may not". The tier says what KIND of rule it
# is; the integer is only how the read orders them, and it is a function of the
# tier so the two can never disagree.
#
# The gaps are deliberate. Operator-given rules will land BETWEEN these bands
# (a lifted soft wall, a standing instruction) without renumbering anything.
TIER_PRIORITY: dict[str, int] = {
    "hard_wall": 100,
    "soft_wall": 50,
    "dial": 10,
}


class Seeder:
    """Reads cards, writes the graph. Never writes a card."""

    def __init__(self, repo: Any, dry_run: bool = False) -> None:
        self._repo = repo
        self._dry_run = dry_run

    # ---- reading the source of truth ------------------------------------

    def load_rules(self, persona_key: str) -> list[dict[str, Any]]:
        """Build seed rows from the card plus its tier overlay. Fails loudly.

        Every mismatch between the two files is an error rather than a skip. A
        silently skipped rule is a rule that does not reach the model, which is
        indistinguishable from the bug this whole slice exists to fix.
        """
        card_path = PERSONAS_DIR / f"{persona_key}.json"
        tier_path = TIERS_DIR / f"{persona_key}.yaml"
        if not card_path.exists():
            raise FileNotFoundError(f"no card at {card_path}")
        if not tier_path.exists():
            raise FileNotFoundError(
                f"no tier overlay at {tier_path}. Refusing to seed unclassified "
                f"rules: with no overlay every rule would default to hard_wall, "
                f"which fails closed correctly but silently makes the whole "
                f"persona immovable."
            )

        card = json.loads(card_path.read_text())
        tiers = yaml.safe_load(tier_path.read_text())

        declared = tiers.get("persona")
        if declared != persona_key:
            raise ValueError(
                f"{tier_path.name} declares persona: {declared!r} but is being "
                f"applied to {persona_key!r} — a copied overlay retargeted by "
                f"filename only would seed one persona's tiers onto another."
            )

        rows: list[dict[str, Any]] = []
        for entry in tiers.get("rules", []):
            if entry.get("layer") != "semantic":
                continue
            field, _, rest = entry["index"].partition("[")
            idx = int(rest.rstrip("]"))
            source = card.get(field) or []
            # 0-BASED, matching gwen_probes.json. See the note at the top of the
            # overlay: v1 of these files was 1-based and disagreed with the eval
            # harness by one.
            if not (0 <= idx < len(source)):
                raise IndexError(
                    f"{persona_key}: overlay references {entry['index']} but the "
                    f"card's `{field}` has {len(source)} entries. The overlay is "
                    f"keyed by POSITION, so a reordered or shortened card silently "
                    f"retargets tiers onto the wrong rules — which is why this is "
                    f"fatal rather than a warning."
                )
            text = source[idx]
            if text.strip() != entry["text"].strip():
                raise ValueError(
                    f"{persona_key} {entry['index']}: the card and the overlay "
                    f"disagree about the rule text.\n"
                    f"  card:    {text[:70]!r}\n"
                    f"  overlay: {entry['text'][:70]!r}\n"
                    f"The overlay is keyed by position, so this means the card was "
                    f"edited without re-classifying. Re-run the classification "
                    f"rather than seeding a tier that belongs to different words."
                )
            rule_type = entry["rule_type"]
            if rule_type not in TIER_PRIORITY:
                raise ValueError(f"unknown rule_type {rule_type!r} in {tier_path.name}")
            rows.append({
                "text": text,
                "source_field": field,
                "source_index": idx,
                "rule_type": rule_type,
                "origin": "card",
                "priority": TIER_PRIORITY[rule_type],
            })
        return rows

    # ---- the verbs ------------------------------------------------------

    def seed(self, persona_key: str) -> int:
        """Seed one persona. Returns 0 on success, 1 on failure."""
        try:
            rows = self.load_rules(persona_key)
        except Exception as e:
            logger.error("%s: %s", persona_key, e)
            return 1

        by_tier: dict[str, int] = {}
        for r in rows:
            by_tier[r["rule_type"]] = by_tier.get(r["rule_type"], 0) + 1
        logger.info("%s: %d semantic-layer rule(s) %s", persona_key, len(rows), by_tier)

        if self._dry_run:
            for r in rows:
                logger.info("  would seed %s[%d] p%-3d %-9s %s",
                            r["source_field"], r["source_index"], r["priority"],
                            r["rule_type"], r["text"][:60])
            return 0

        try:
            out = self._repo.seed_rules(persona_key, rows)
        except Exception as e:
            logger.error("%s: seed failed: %s", persona_key, e)
            return 1
        if out.get("reason"):
            logger.error("%s: %s", persona_key, out["reason"])
            return 1
        logger.info("%s: seeded %s", persona_key, out.get("seeded"))
        return self.verify(persona_key, expected=rows)

    def verify(self, persona_key: str, expected: Optional[list[dict]] = None) -> int:
        """Prove the seed worked. A write that raised nothing is not a write.

        This is the graph's version of the backup restore-check, and it exists for
        the same reason: `mongodump` exiting zero reports on the dump, not on
        whether the result reads back. Four assertions, each catching something the
        others cannot:
          * every expected rule is READABLE (round-trip, not write-count)
          * hard walls fit inside the read limit, or the limit silently drops them
          * integrity is clean (vocabulary, orphans, label drift)
          * the read is byte-identical twice, which is the ADR's actual promise
        """
        if expected is None:
            try:
                expected = self.load_rules(persona_key)
            except Exception as e:
                logger.error("%s: %s", persona_key, e)
                return 1

        got = self._repo.standing_rules(persona_key, limit=999)
        if len(got) != len(expected):
            logger.error("%s: wrote %d rule(s) but only %d read back",
                         persona_key, len(expected), len(got))
            return 1

        want_texts = {r["text"].strip() for r in expected}
        got_texts = {r["text"].strip() for r in got}
        if want_texts != got_texts:
            missing = want_texts - got_texts
            logger.error("%s: %d rule(s) did not round-trip, e.g. %r",
                         persona_key, len(missing), next(iter(missing))[:60])
            return 1

        # The limit the read actually uses at runtime, not the 999 above.
        from src.coordinator.config import get_settings
        limit = get_settings().graph.rule_read_limit
        hard = [r for r in got if r["rule_type"] == "hard_wall"]
        top = self._repo.standing_rules(persona_key, limit=limit)
        top_hard = [r for r in top if r["rule_type"] == "hard_wall"]
        if len(top_hard) != len(hard):
            logger.error(
                "%s: %d hard wall(s) exist but only %d fit in the read limit of "
                "%d — the rest are silently dropped every turn. Raise "
                "GRAPH_RULE_READ_LIMIT or reclassify.",
                persona_key, len(hard), len(top_hard), limit)
            return 1

        ig = self._repo.check_integrity()
        if not ig.get("clean"):
            logger.error("%s: integrity not clean: bad_vocab=%d drift=%d orphans=%d",
                         persona_key, len(ig.get("bad_vocabulary", [])),
                         len(ig.get("label_drift", [])), len(ig.get("orphans", [])))
            return 1

        a = [r["rule_id"] for r in self._repo.standing_rules(persona_key, limit=limit)]
        b = [r["rule_id"] for r in self._repo.standing_rules(persona_key, limit=limit)]
        if a != b:
            logger.error("%s: the read is NOT deterministic — two calls disagreed",
                         persona_key)
            return 1

        logger.info("%s: verified — %d rule(s), %d hard wall(s) all within the "
                    "limit of %d, integrity clean, read deterministic",
                    persona_key, len(got), len(hard), limit)
        return 0


def _known_personas() -> list[str]:
    """Personas that have a tier overlay. Without one there is nothing to seed."""
    return sorted(p.stem for p in TIERS_DIR.glob("*.yaml") if not p.stem.startswith("_"))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  seed_graph.py --persona gwen_dev --dry-run     show the plan, touch nothing
  seed_graph.py --persona gwen_dev               seed and verify
  seed_graph.py --persona gwen_dev --verify-only re-check an existing seed
  seed_graph.py --all                            every persona with an overlay

Exit codes:
  0  nothing needed doing, or applied and verified
  1  one or more personas failed (the rest are still processed)
  2  could not determine — no graph configured or reachable
""",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--persona", metavar="KEY", help="persona key, e.g. gwen_dev")
    g.add_argument("--all", action="store_true", help="every persona with a tier overlay")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be seeded and exit. Writes nothing.")
    ap.add_argument("--verify-only", action="store_true",
                    help="Re-verify an existing seed without writing.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    targets = _known_personas() if args.all else [args.persona]
    if not targets:
        logger.error("no personas with a tier overlay in %s", TIERS_DIR)
        sys.exit(2)

    repo = None
    if not args.dry_run:
        from src.coordinator.config import get_settings
        from src.coordinator.graph_driver import build_driver
        from src.coordinator.repositories.neo4j_rule_repository import Neo4jRuleRepository
        cfg = get_settings().graph
        if not cfg.enabled:
            logger.error("GRAPH_ENABLED is false — nothing to seed into. "
                         "Set it true in .env, or use --dry-run.")
            sys.exit(2)
        try:
            driver = build_driver(cfg.base_url, cfg.username, cfg.password,
                                  max_pool_size=cfg.max_pool_size, verify=True)
        except Exception as e:
            logger.error("cannot reach the graph: %s", e)
            sys.exit(2)
        repo = Neo4jRuleRepository(driver, cfg.database)

    seeder = Seeder(repo, dry_run=args.dry_run)
    exit_code = 0
    try:
        for key in targets:
            if args.verify_only:
                exit_code |= seeder.verify(key)
            else:
                exit_code |= seeder.seed(key)
    finally:
        # Driver 6.x does not close itself in __del__, and a leaked driver is
        # SILENT — ResourceWarning is ignored by default. A script that exits
        # without closing leaves sockets behind for as long as the process lives.
        if repo is not None and getattr(repo, "_driver", None) is not None:
            repo._driver.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
