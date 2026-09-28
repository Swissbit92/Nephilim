"""Reconstruct the card-shaped subset of a persona from the identity graph.

WHY THIS EXISTS, AND WHAT IT IS NOT. ADR-012 makes the graph the system of record and
demotes the card to an origin and a reset target. That claim needs a test, and the
obvious one -- "does the graph produce the same <identity> block as the card?" -- is
NOT AVAILABLE: <identity> is a paragraph written by an LLM at temperature 0.9
(cv_summarizer._make_cv_summary), so the same input yields different text on every
call and byte-equality is unachievable by construction. tests/backend/coordinator/
test_summaries_are_tracked.py exists precisely because that text cannot be regenerated.

So the equivalence boundary is pushed to BEFORE the LLM. This module rebuilds the
modelled subset of the card from the nodes, which is fully deterministic, and the
round-trip `card -> nodes -> card` can then be diffed exactly. That answers the
question ADR-012 actually raises -- "would dropping the card lose anything?" -- rather
than the question about prompt text, which no test can settle.

WHAT IT DELIBERATELY DOES NOT RECOVER. Only MODELLED_FIELDS. Everything in
EXCLUDED_FIELDS is out of the graph on purpose (capability grants stay in git per
ADR-011, asset paths and sampler settings are not identity), so a reconstruction
returning them would be inventing data. `round_trip_diff` therefore compares against
the modelled projection of the source card, never the whole card, and says so.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from .identity_from_card import MODELLED_FIELDS, SOURCE_FIELD_KIND


class UndeclaredSourceField(ValueError):
    """A node names a source_field with no declared container shape.

    Raised rather than guessed: a one-element list and a scalar both arrive as a single
    node at source_index 0, so inferring would silently turn ["x"] into "x".
    """


def _assign(target: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = target
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def card_from_nodes(nodes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """The modelled subset of a card, rebuilt from identity nodes.

    Ordering comes from `source_index`, not from the iteration order of `nodes`: the
    graph read is ordered by (source_field, source_index) but a caller may pass nodes
    in any order, and a list rebuilt in arrival order would be a different card.
    """
    buckets: Dict[str, List[Tuple[int, str]]] = {}
    for n in nodes:
        field = n.get("source_field")
        if not field:
            continue
        if field not in SOURCE_FIELD_KIND:
            raise UndeclaredSourceField(
                f"{field!r} has no entry in SOURCE_FIELD_KIND, so its container shape "
                "is unknown; add one next to the builder that emits it"
            )
        buckets.setdefault(field, []).append((int(n.get("source_index") or 0),
                                              n.get("text") or ""))

    out: Dict[str, Any] = {}
    for field, items in buckets.items():
        items.sort(key=lambda t: t[0])
        texts = [t for _, t in items]
        if SOURCE_FIELD_KIND[field] == "list":
            _assign(out, field, texts)
        else:
            if len(texts) != 1:
                raise UndeclaredSourceField(
                    f"{field!r} is declared scalar but has {len(texts)} nodes"
                )
            _assign(out, field, texts[0])
    return out


def modelled_projection(card: Dict[str, Any]) -> Dict[str, Any]:
    """The part of `card` the graph is responsible for -- the fair comparison target.

    Built from SOURCE_FIELD_KIND rather than from MODELLED_FIELDS wholesale, because a
    modelled top-level key can contain excluded leaves: `emotional_profile` is modelled
    but `emotional_profile.sliders` is deliberately not a node (ADR-016 measured the
    dials inert, and a number modelled as a node is a value pretending to be a thing).
    Comparing whole top-level keys would report those as losses.
    """
    out: Dict[str, Any] = {}
    for field in SOURCE_FIELD_KIND:
        cur: Any = card
        for part in field.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur is None:
            continue
        if isinstance(cur, list):
            cur = [x.strip() for x in cur if isinstance(x, str) and x.strip()]
            if not cur:
                continue
        elif isinstance(cur, str):
            cur = cur.strip()
            if not cur:
                continue
        _assign(out, field, cur)
    return out


def round_trip_diff(card: Dict[str, Any],
                    nodes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare the card's modelled projection against what the nodes rebuild to.

    Returns a report rather than a bool: "they differ" is not actionable, and the
    coverage-granularity lesson from identity_completeness applies here too -- a claim
    coarser than the data is technically true and misleading.
    """
    expected = modelled_projection(card)
    actual = card_from_nodes(nodes)

    def flat(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
        f: Dict[str, Any] = {}
        for k, v in d.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                f.update(flat(v, key + "."))
            else:
                f[key] = v
        return f

    e, a = flat(expected), flat(actual)
    missing = sorted(set(e) - set(a))
    extra = sorted(set(a) - set(e))
    changed = sorted(k for k in set(e) & set(a) if e[k] != a[k])
    return {
        "identical": not (missing or extra or changed),
        "fields_compared": len(e),
        "missing_from_graph": missing,
        "not_in_card": extra,
        "changed": changed,
        "excluded_by_design": sorted(MODELLED_FIELDS - {f.split(".")[0]
                                                        for f in SOURCE_FIELD_KIND}),
    }
