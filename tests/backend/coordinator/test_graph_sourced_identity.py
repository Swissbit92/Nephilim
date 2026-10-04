"""The graph may be the SOURCE of her identity — without becoming extra prompt text.

WHAT THIS PINS, AND WHY THE DISTINCTION IS THE WHOLE POINT.

ADR-012 makes the graph the system of record. That was not true while nothing read it:
dropping the database would have cost nothing, which is the opposite of a system of
record. `GRAPH_IDENTITY_SOURCE` makes it true.

ADR-018 separately CLOSED a different change that sounds the same — injecting
identity-node content into the prompt. It was measured: voice distinctiveness 0.804 with
injection off, 0.625 with it on, and three deliberate reframings at 0.708 / 0.542 / 0.500,
every one below the off-baseline. The ADR names "wiring a rich identity store into the
prompt the way the last attempt was wired" as the thing most likely to be regretted.

So the two changes must be told apart mechanically, not by intention, and that is what
`test_the_prompt_is_byte_identical_whichever_source_wins` is for. A sourcing change adds
zero tokens. The moment it stops being byte-identical it has become the closed change, and
that test fails.

The failure mode this suite exists to prevent, besides: `IdentityRepository.identity()`
has no try/except, unlike `Neo4jRuleRepository.standing_rules()` which documents
"Returns [] on a graph outage rather than raising". A naive call from the prompt path would
turn a Neo4j blip into a 500 on her turn.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _card(key: str = "gwen") -> dict:
    return json.loads((ROOT / "personas" / f"{key}.json").read_text())


# ── the pure overlay: no graph, no flag, no I/O ────────────────────────────────

def test_overlay_of_the_graphs_own_nodes_reproduces_the_card():
    """The sourcing claim, deterministically: graph content == card content.

    This is what makes the change a SOURCING change rather than a CONTENT change, and it
    holds only because identity_nodes and card_from_nodes are inverses.
    """
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_source import identity_overlay

    for path in sorted((ROOT / "personas").glob("*.json")):
        card = json.loads(path.read_text())
        merged = identity_overlay(card, identity_nodes(card))
        assert merged == card, f"{path.name}: overlay changed the card"


def test_a_modelled_list_is_replaced_not_appended():
    """Merging lists would duplicate every entry on every rebuild."""
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_source import identity_overlay

    card = _card()
    nodes = [n for n in identity_nodes(card) if n["source_field"] == "lore"][:2]
    merged = identity_overlay(card, nodes)
    assert len(merged["lore"]) == 2, "lore was appended to rather than replaced"


def test_an_unmodelled_sibling_key_survives_the_overlay():
    """behavior.traits is graph-owned; a sibling the graph does not model must remain."""
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_source import identity_overlay

    card = copy.deepcopy(_card())
    card["behavior"]["an_unmodelled_key"] = "must survive"
    merged = identity_overlay(card, identity_nodes(card))
    assert merged["behavior"]["an_unmodelled_key"] == "must survive"


# ── the guarded read: every failure falls back to the card ─────────────────────

def test_flag_off_is_a_no_op(monkeypatch):
    from src.coordinator import identity_source as isrc

    card = _card()
    out, source = isrc.overlay_from_graph(card, "gwen")
    assert source == isrc.SOURCE_CARD
    assert out is card, "the card object should pass straight through when the flag is off"


def test_a_graph_outage_falls_back_to_the_card_and_does_not_raise(monkeypatch):
    """The asymmetry with standing_rules() is the reason this suite exists."""
    from src.coordinator import identity_source as isrc

    class _Boom:
        def identity(self, *a, **k):
            raise RuntimeError("ServiceUnavailable (simulated)")

    monkeypatch.setattr(isrc, "_read_limit", lambda card: 256)
    monkeypatch.setattr(
        "src.coordinator.repositories.identity_repository.IdentityRepository",
        lambda *a, **k: _Boom(),
    )

    class _Cfg:
        enabled = True
        identity_source = True
        database = "neo4j"

    monkeypatch.setattr("src.coordinator.config.get_settings",
                        lambda: type("S", (), {"graph": _Cfg()})())
    monkeypatch.setattr("src.coordinator.startup.get_neo4j_driver", lambda: object())

    card = _card()
    out, source = isrc.overlay_from_graph(card, "gwen")
    assert source == isrc.SOURCE_CARD
    assert out == card


def test_an_empty_graph_falls_back_without_warning(monkeypatch):
    """No identity nodes is the normal state before seed_identity has run."""
    from src.coordinator import identity_source as isrc

    out, source = isrc.overlay_from_graph(_card(), "gwen")
    assert source == isrc.SOURCE_CARD


def test_a_node_with_an_undeclared_source_field_falls_back(monkeypatch):
    """card_from_nodes RAISES on an undeclared field rather than guessing; the read
    must absorb that rather than 500 the turn."""
    from src.coordinator import identity_source as isrc

    card = _card()
    bad = [{"source_field": "behavior.invented_by_a_future_builder",
            "source_index": 0, "text": "x"}]
    with pytest.raises(Exception):
        isrc.identity_overlay(card, bad)          # the pure function still raises

    class _Repo:
        def identity(self, *a, **k):
            return bad

    class _Cfg:
        enabled = True
        identity_source = True
        database = "neo4j"

    monkeypatch.setattr(isrc, "_read_limit", lambda c: 256)
    monkeypatch.setattr(
        "src.coordinator.repositories.identity_repository.IdentityRepository",
        lambda *a, **k: _Repo(),
    )
    monkeypatch.setattr("src.coordinator.config.get_settings",
                        lambda: type("S", (), {"graph": _Cfg()})())
    monkeypatch.setattr("src.coordinator.startup.get_neo4j_driver", lambda: object())

    out, source = isrc.overlay_from_graph(card, "gwen")   # but the READ absorbs it
    assert source == isrc.SOURCE_CARD and out == card


def test_the_read_limit_cannot_silently_truncate_this_persona(monkeypatch):
    """identity() applies LIMIT after ORDER BY source_field, so an over-limit persona
    loses whole alphabetically-late fields and still looks like a valid card."""
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_source import _read_limit

    card = _card()
    assert _read_limit(card) > len(identity_nodes(card))


# ── the boundary against the change ADR-018 closed ────────────────────────────

def test_no_chat_time_identity_write_path_exists():
    """The reason a graph read inside an lru_cache is acceptable *today*.

    A cached prompt can serve stale graph state — which is exactly why the RULES read was
    kept out of the cached builder. Identity differs only because ADR-018 defers the write
    path: "no write path from chat exists", so nothing mutates these nodes mid-session.

    That is a property of the current system, not a law. If this fails, an identity write
    became reachable from a request and the read must move out to the route the way
    build_graph_rules_block already is.
    """
    writers = ("apply_card", "reinforce")
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if path.name in ("identity_repository.py", "identity_source.py"):
            continue
        text = path.read_text()
        for w in writers:
            if f".{w}(" in text:
                offenders.append(f"{path.relative_to(ROOT)} calls .{w}()")
    assert not offenders, (
        "an identity WRITE is reachable from src/: " + "; ".join(offenders)
        + " — a cached prompt would now serve stale identity, so the graph read must "
          "move from _build_system_prompt_lean out to the route"
    )

# ── the floor: a store that fails by returning LESS, not by raising ────────────

class TestTheFloorAssertion:
    """A partial read produces a structurally valid card, which is the dangerous shape.

    Pinecone shipped this exact failure on 2026-06-18 — queries to infrequently-read
    namespaces "incorrectly returning empty results", a successful 200 with no rows. And
    `identity_overlay` REPLACES any field it is given, so a read carrying some of `lore`
    deletes the rest. Measured on gwen before the floor existed: a 111-of-128 read left
    her with 4 of 21 lore entries, built a normal prompt, and reported nothing.
    """

    def _nodes(self, card):
        from src.coordinator.identity_from_card import identity_nodes
        return identity_nodes(card)

    def test_a_full_read_is_accepted(self):
        from src.coordinator.identity_source import _passes_floor
        card = _card()
        ok, why = _passes_floor(card, self._nodes(card), 256)
        assert ok, why

    def test_a_read_at_the_limit_is_rejected_as_truncated(self):
        """LIMIT is applied AFTER ORDER BY source_field, so the tail is whole fields."""
        from src.coordinator.identity_source import _passes_floor
        card = _card()
        nodes = self._nodes(card)[:100]
        ok, why = _passes_floor(card, nodes, 100)
        assert not ok and "limit" in why

    def test_the_measured_silent_loss_case_is_rejected(self):
        """111 of 128 nodes clears the aggregate ratio; `lore` at 4 of 21 does not."""
        from src.coordinator.identity_source import _passes_floor
        card = _card()
        full = self._nodes(card)
        partial = ([n for n in full if n["source_field"] != "lore"]
                   + [n for n in full if n["source_field"] == "lore"][:4])
        ok, why = _passes_floor(card, partial, 256)
        assert not ok, "a read that guts one field was accepted"
        assert "lore" in why and "4 of 21" in why

    def test_a_field_absent_entirely_is_safe(self):
        """Absence keeps the card value; only partial PRESENCE deletes."""
        from src.coordinator.identity_source import _passes_floor
        card = _card()
        nodes = [n for n in self._nodes(card) if n["source_field"] != "lore"]
        ok, why = _passes_floor(card, nodes, 256)
        assert ok, why

    def test_a_legitimate_single_retirement_is_still_evolution(self):
        """The graph is authoritative — retiring one entry must not trip the floor."""
        from src.coordinator.identity_source import _passes_floor
        card = _card()
        full = self._nodes(card)
        lore = [n for n in full if n["source_field"] == "lore"]
        nodes = [n for n in full if n["source_field"] != "lore"] + lore[:-1]
        ok, why = _passes_floor(card, nodes, 256)
        assert ok, why

    def test_a_rejected_read_falls_back_to_the_card(self, monkeypatch):
        from src.coordinator import identity_source as isrc

        card = _card()
        gutted = ([n for n in self._nodes(card) if n["source_field"] != "lore"]
                  + [n for n in self._nodes(card) if n["source_field"] == "lore"][:4])

        class _Repo:
            def identity(self, *a, **k):
                return gutted

        class _Cfg:
            enabled = True
            identity_source = True
            database = "neo4j"

        monkeypatch.setattr(
            "src.coordinator.repositories.identity_repository.IdentityRepository",
            lambda *a, **k: _Repo())
        monkeypatch.setattr("src.coordinator.config.get_settings",
                            lambda: type("S", (), {"graph": _Cfg()})())
        monkeypatch.setattr("src.coordinator.startup.get_neo4j_driver", lambda: object())

        out, source = isrc.overlay_from_graph(card, "gwen")
        assert source == isrc.SOURCE_CARD
        assert len(out["lore"]) == len(card["lore"]), "the gutted read was applied"


def test_the_overlay_is_deterministic_across_repeated_reads():
    """A nondeterministic block silently destroys llama.cpp's prefix KV cache.

    ollama#18430 is a live 2026 case: tool schemas rendered in nondeterministic Go map
    order cut `cached n_tokens` from 443 to 5 on an unchanged 444-token prompt, and the
    only symptom was that everything got slower. `identity()` carries
    ORDER BY source_field, source_index for this reason; nothing else would tell us when
    that ordering stops being load-bearing.
    """
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_source import identity_overlay

    card = _card()
    nodes = identity_nodes(card)
    first = json.dumps(identity_overlay(card, nodes), sort_keys=True)
    for _ in range(5):
        assert json.dumps(identity_overlay(card, nodes), sort_keys=True) == first
    # and order-independence, since a driver is free to change row order
    shuffled = list(reversed(nodes))
    assert json.dumps(identity_overlay(card, shuffled), sort_keys=True) == first
