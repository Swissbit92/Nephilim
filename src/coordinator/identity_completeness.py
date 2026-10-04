"""Is everything on her card actually in the graph? (competency question idn.cq04)

WHY THIS IS A CHECK AND NOT A COMMENT. Under ecosystem ADR-012 the graph is the system of
record for persona identity. So a card field nobody modelled does not degrade gracefully
— it disappears from who she is, and a rebuild makes the loss permanent with no error and
no second copy. The only defence is a mechanical three-way split where the third bucket
must be empty.

THE GRANULARITY IS THE WHOLE POINT, and getting it wrong is how this check lies. Her card
has 33 top-level keys and 244 leaves. A key-level claim — "33 of 33 accounted for" — is
technically true and practically misleading: the first draft of the mapper reported exactly
that while NINE leaves under a "modelled" key went nowhere, seven of them her sliders and
two of them genuinely forgotten (`behavior.relationship_to_user`,
`behavior.clarifying_questions`). A coverage metric coarser than the data is worse than no
metric, because it is believed.

So this check works at LEAF level, and reports three states rather than two — a check that
cannot run must say so instead of returning success, because "could not determine" is not
a pass.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, NamedTuple

from .identity_from_card import (
    EXCLUDED_FIELDS,
    EXCLUDED_LEAVES,
    MODELLED_FIELDS,
    identity_nodes,
)


class Coverage(NamedTuple):
    """Three states, never two. `ok` is None when the check could not run."""

    ok: bool | None
    reason: str
    total_leaves: int
    consumed: List[str]
    excluded: List[str]
    unaccounted: List[str]
    stale_exclusions: List[str]

    def report(self) -> str:
        if self.ok is None:
            return f"COULD NOT DETERMINE — {self.reason} (this is not a pass)"
        head = "PASS" if self.ok else "FAIL"
        lines = [
            f"{head} — {len(self.consumed)} consumed, {len(self.excluded)} excluded, "
            f"{len(self.unaccounted)} unaccounted of {self.total_leaves} leaves"
        ]
        if self.unaccounted:
            lines.append("  UNACCOUNTED (neither modelled nor excluded — this is the defect):")
            lines += [f"    {p}" for p in self.unaccounted]
        if self.stale_exclusions:
            lines.append("  STALE EXCLUSIONS (declared, but no longer in the card):")
            lines += [f"    {p}" for p in self.stale_exclusions]
        return "\n".join(lines)


def _leaves(obj: Any, prefix: str = "") -> Iterator[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _leaves(v, f"{prefix}[{i}]")
    else:
        yield prefix


def check_card_coverage(card: Dict[str, Any]) -> Coverage:
    """Every leaf of the card is consumed, explicitly excluded, or a defect."""
    if not isinstance(card, dict) or not card:
        return Coverage(None, "card is empty or not a mapping", 0, [], [], [], [])

    leaves = list(_leaves(card))
    nodes = identity_nodes(card)

    # A leaf is CONSUMED if a node claims its source_field, at its index for list leaves.
    consumed_fields = {n["source_field"] for n in nodes}
    consumed_indexed = {f"{n['source_field']}[{n['source_index']}]" for n in nodes}

    consumed: List[str] = []
    excluded: List[str] = []
    unaccounted: List[str] = []
    for leaf in leaves:
        top = leaf.split(".")[0].split("[")[0]
        base = leaf.rsplit("[", 1)[0] if leaf.endswith("]") else leaf
        if leaf in consumed_indexed or base in consumed_fields or leaf in consumed_fields:
            consumed.append(leaf)
        elif leaf in EXCLUDED_LEAVES or top in EXCLUDED_FIELDS:
            excluded.append(leaf)
        elif top in MODELLED_FIELDS:
            # Under a modelled key but nothing consumed it and nothing excused it. This
            # is the bucket that must stay empty — the one the key-level check missed.
            unaccounted.append(leaf)
        else:
            unaccounted.append(leaf)

    stale = sorted(
        [k for k in EXCLUDED_FIELDS if k not in card]
        + [k for k in EXCLUDED_LEAVES if k not in leaves]
    )
    return Coverage(
        ok=not unaccounted,
        reason="checked against the card in memory",
        total_leaves=len(leaves),
        consumed=sorted(consumed),
        excluded=sorted(excluded),
        unaccounted=sorted(unaccounted),
        stale_exclusions=stale,
    )


def check_card_file(path: str | Path) -> Coverage:
    p = Path(path)
    if not p.is_file():
        return Coverage(None, f"card not found at {p}", 0, [], [], [], [])
    try:
        card = json.loads(p.read_text())
    except Exception as e:  # noqa: BLE001 - a broken card must not look like a pass
        return Coverage(None, f"card at {p} is not valid JSON: {e}", 0, [], [], [], [])
    return check_card_coverage(card)


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "personas/gwen.json"
    cov = check_card_file(target)
    print(cov.report())
    raise SystemExit(0 if cov.ok else (1 if cov.ok is False else 2))
