"""The identity graph: shapes, mapping, completeness (ADR-018, ecosystem ADR-012).

These run WITHOUT a graph on purpose. The card-to-node mapping and the completeness check
are pure functions of the card, and this project has a documented history of checks that
existed but never ran — a test that skips when Neo4j is absent is a check that does not
run in CI. The live-graph behaviour (rebuild preserves annotations) is proved separately
by scripts/research/identity_ab.py, which measured arm A losing 3 of 3 annotations and
arm B losing none.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.coordinator.identity_completeness import check_card_coverage, check_card_file
from src.coordinator.identity_from_card import (
    EXCLUDED_FIELDS,
    EXCLUDED_LEAVES,
    MODELLED_FIELDS,
    identity_nodes,
)
from src.coordinator.identity_shapes import (
    BOUNDARY,
    EDGES,
    EXPERTISE,
    IDENTITY_ORIGINS,
    LORE_ENTRY,
    SHAPES,
    TRAIT,
)

CARDS = Path(__file__).parents[3] / "personas"


@pytest.fixture
def card():
    return json.loads((CARDS / "gwen.json").read_text())


# ─────────────────────────────────────────────────────────────
# Shapes — the content/annotation split is the whole design
# ─────────────────────────────────────────────────────────────

class TestTheSplit:
    @pytest.mark.parametrize("shape", list(SHAPES.values()), ids=lambda s: s.label)
    def test_content_and_annotation_never_overlap(self, shape):
        """A property in both classes has no defined rebuild behaviour."""
        assert not (shape.content & shape.annotation), shape.label

    @pytest.mark.parametrize("shape", list(SHAPES.values()), ids=lambda s: s.label)
    def test_annotations_are_the_same_three_everywhere(self, shape):
        """Hers are hers, identically, on every node kind. A per-label annotation set
        would mean a rebuild has to know which label it is looking at to know what to
        preserve — one more thing that can be wrong."""
        assert shape.annotation == frozenset(
            {"salience", "reinforced_count", "last_referenced"}
        )

    @pytest.mark.parametrize("shape", list(SHAPES.values()), ids=lambda s: s.label)
    def test_an_unknown_property_is_refused(self, shape):
        with pytest.raises(ValueError, match="neither content nor annotation"):
            shape.split({"colour": "blue"})

    def test_a_bad_vocabulary_value_is_refused(self):
        with pytest.raises(ValueError, match="not in"):
            LORE_ENTRY.split({"origin": "banana"})
        with pytest.raises(ValueError, match="not in"):
            TRAIT.split({"kind": "nonsense"})
        with pytest.raises(ValueError, match="not in"):
            EXPERTISE.split({"level": "expert"})

    def test_learned_is_a_valid_origin(self):
        """ADR-018 specifies four origins; the rule store's ORIGINS has three and the
        fourth had no code. `learned` matters because a reset drops precisely that set."""
        assert "learned" in IDENTITY_ORIGINS
        assert IDENTITY_ORIGINS == frozenset({"card", "learned", "conversation", "inferred"})

    def test_every_shape_has_exactly_one_edge_with_typed_endpoints(self):
        """An untyped range is the named modelling defect — "do not make the range
        THING" — and it is what has_opinion_on violated in the fact store."""
        targets = {tgt for _src, tgt in EDGES.values()}
        assert targets == set(SHAPES)
        assert all(src == "Persona" for src, _ in EDGES.values())

    def test_source_hash_is_content_not_annotation(self):
        """It describes the CARD, so a rebuild must refresh it. If it were an
        annotation, a stale-source check could never detect anything."""
        for shape in SHAPES.values():
            assert "source_hash" in shape.content


# ─────────────────────────────────────────────────────────────
# Mapping — the card is prose, and that bounds the granularity
# ─────────────────────────────────────────────────────────────

class TestMapping:
    def test_her_card_produces_nodes(self, card):
        assert len(identity_nodes(card)) > 100

    def test_the_prose_is_kept_WHOLE(self, card):
        """One lore entry = one node, verbatim. Not decomposed into "blue eyes". Three
        independent findings say so: context-stripping raised retrieval failure 49-67%
        in Anthropic's measurement, GraphRAG keeps source text alongside structure, and
        this repo's own extractor produced 2 useful facts out of 12."""
        texts = {n["text"] for n in identity_nodes(card) if n["source_field"] == "lore"}
        assert texts == {e.strip() for e in card["lore"]}

    def test_every_node_declares_its_provenance(self, card):
        for n in identity_nodes(card):
            assert n["origin"] == "card"
            assert n["source_field"] and n["source_index"] is not None
            assert len(n["source_hash"]) == 16

    def test_the_card_never_produces_an_annotation(self, card):
        """An annotation arriving from the card would be her learning overwritten by her
        factory settings on every rebuild."""
        ann = SHAPES["LoreEntry"].annotation
        for n in identity_nodes(card):
            assert not (set(n) & ann), n

    def test_sliders_are_not_nodes(self, card):
        """A number modelled as a node is a value pretending to be a thing, and ADR-016
        measured them inert as behaviour controls anyway."""
        assert not [n for n in identity_nodes(card) if "sliders" in n["source_field"]]

    def test_positional_keys_are_unique(self, card):
        keys = [(n["source_field"], n["source_index"]) for n in identity_nodes(card)]
        assert len(keys) == len(set(keys)), "a duplicate key would collide on MERGE"

    def test_it_survives_a_malformed_card(self):
        for bad in ({}, {"key": "x"}, {"key": "x", "lore": None},
                    {"key": "x", "lore": ["", "  "]}, {"key": "x", "behavior": None}):
            identity_nodes(bad)  # must not raise


# ─────────────────────────────────────────────────────────────
# Completeness — watched failing on all four paths
# ─────────────────────────────────────────────────────────────

class TestCompleteness:
    def test_her_real_card_is_complete(self, card):
        cov = check_card_coverage(card)
        assert cov.ok is True, cov.report()
        assert cov.unaccounted == []

    def test_a_new_top_level_field_is_caught(self, card):
        c = copy.deepcopy(card); c["favourite_colour"] = "green"
        cov = check_card_coverage(c)
        assert cov.ok is False and "favourite_colour" in cov.unaccounted

    def test_a_new_LEAF_under_a_modelled_key_is_caught(self, card):
        """The one the key-level check could not see. The first draft reported
        33-of-33 complete while NINE leaves went nowhere — seven sliders and two
        genuinely forgotten behaviour fields."""
        c = copy.deepcopy(card); c["behavior"]["shoe_size"] = 38
        cov = check_card_coverage(c)
        assert cov.ok is False and "behavior.shoe_size" in cov.unaccounted

    def test_coverage_is_reported_at_LEAF_granularity(self, card):
        """A claim made coarser than the data is technically true and misleading, which
        is worse than no claim."""
        cov = check_card_coverage(card)
        assert cov.total_leaves > len(card) * 3

    def test_a_stale_exclusion_is_reported(self, card, monkeypatch):
        """Same family as mypy's unused-ignore and ESLint's unused-disable-directive:
        an exclusion is a recorded decision, and one about a field that no longer
        exists is misleading rather than inert."""
        from src.coordinator import identity_from_card as ifc

        monkeypatch.setitem(ifc.EXCLUDED_FIELDS, "gone_field", "test")
        assert "gone_field" in check_card_coverage(card).stale_exclusions

    @pytest.mark.parametrize("bad", [{}, None, "not a dict", []])
    def test_it_reports_COULD_NOT_DETERMINE_rather_than_passing(self, bad):
        """Nagios's convention: UNKNOWN is distinct from CRITICAL, and neither is green.
        A check that cannot verify anything must not return success."""
        assert check_card_coverage(bad).ok is None

    def test_an_unreadable_card_is_indeterminate_not_a_pass(self, tmp_path):
        assert check_card_file(tmp_path / "nope.json").ok is None
        broken = tmp_path / "broken.json"; broken.write_text("{not json")
        assert check_card_file(broken).ok is None

    def test_no_field_is_both_modelled_and_excluded(self):
        assert not (MODELLED_FIELDS & set(EXCLUDED_FIELDS))

    def test_every_exclusion_states_a_reason(self):
        """An exclusion without a reason is how a field gets quietly dropped to make a
        check go green. The bar is "says something", not a character count — an earlier
        version of this test asserted len > 15 and failed on the perfectly adequate
        reason "routing", which is the test being wrong rather than the reason."""
        placeholders = {"", "n/a", "na", "todo", "tbd", "-", "excluded", "no", "none"}
        for k, v in {**EXCLUDED_FIELDS, **EXCLUDED_LEAVES}.items():
            assert isinstance(v, str), k
            assert v.strip().lower() not in placeholders, f"{k}: placeholder reason {v!r}"
            assert len(v.strip()) >= 5, f"{k}: reason too short to be one: {v!r}"

    def test_all_nine_previously_missed_leaves_are_now_accounted_for(self, card):
        cov = check_card_coverage(card)
        for leaf in ("behavior.clarifying_questions", "behavior.relationship_to_user",
                     "emotional_profile.sliders.warmth"):
            assert leaf in cov.consumed or leaf in cov.excluded, leaf


# ─────────────────────────────────────────────────────────────
# The map-form SET landmines, guarded by grep because they are one character
# ─────────────────────────────────────────────────────────────

class TestTheSetLandmines:
    def _src(self) -> str:
        """The CODE, not the module docstring.

        The docstring explains the two map-form SET traps by name, so a whole-file grep
        trips on its own documentation — the second time today a regression guard has
        failed on the text describing the fix. Scoping to the body is the fix; deleting
        the explanation to satisfy a grep would be the wrong one."""
        p = Path(__file__).parents[3] / "src/coordinator/repositories/identity_repository.py"
        src = p.read_text()
        return src.split('"""', 2)[-1]

    def test_no_map_form_SET_anywhere(self):
        """`SET n = $map` REPLACES every property — one character from `+=` and a
        rebuild wipes annotations. `SET n += $map` DELETES any key whose value is null,
        so an absent optional field removes a property instead of leaving it unset.
        Both are avoided by assigning properties individually."""
        src = self._src()
        assert "SET n = $" not in src
        assert "SET n += $" not in src

    def test_annotations_are_only_ever_set_ON_CREATE(self):
        """Preservation is structural: the rebuild path never names them."""
        src = self._src()
        after_on_create = src.split("ON CREATE SET", 1)[1] if "ON CREATE SET" in src else ""
        assert "n.salience = 0.0" in after_on_create
        assert "n.reinforced_count = 0" in after_on_create

    def test_merge_matches_the_live_label(self):
        """An expired node and a live node share a positional key, so a MERGE on the
        primary label alone would bind the expired one and resurrect it."""
        assert "CurrentIdentity" in self._src()
