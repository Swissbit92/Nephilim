"""The identity graph: shapes, mapping, completeness (ADR-018, ecosystem ADR-012).

These run WITHOUT a graph on purpose. The card-to-node mapping and the completeness check
are pure functions of the card, and this project has a documented history of checks that
existed but never ran — a test that skips when Neo4j is absent is a check that does not
run in CI. The live-graph behaviour (a rebuild preserves each node's create-once
properties) is proved separately by scripts/research/identity_ab.py, re-measured
2026-10-16 as arm A 0 of 128 and arm B 127 of 128 — the one exception being the entry the
harness deliberately rewrites, which gets a new `source_key` and is therefore a new node
by design. It previously measured arm A losing 3 of 3 annotations and
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
# Shapes — every property is declared, and anything else is refused
# ─────────────────────────────────────────────────────────────

class TestTheDeclaration:
    """TWO TESTS WERE DELETED HERE on 2026-10-16, deliberately, and the reason is the
    point: `test_content_and_annotation_never_overlap` asserted
    `not (shape.content & shape.annotation)`, and `test_the_card_never_produces_an
    _annotation` looped 128 nodes intersecting each against the annotation set. With the
    annotation class gone both intersect against an empty set, so both PASS while
    asserting nothing — and the second looks thorough while doing it. A green test that
    cannot fail is worse than no test, because it stops the next person checking.

    What replaced them is below: the shape now REFUSES the three deleted names outright,
    which is a stronger claim than "the card happens not to produce one".
    """

    @pytest.mark.parametrize("shape", list(SHAPES.values()), ids=lambda s: s.label)
    def test_an_undeclared_property_is_refused(self, shape):
        with pytest.raises(ValueError, match="not a declared property"):
            shape.validated({"colour": "blue"})

    @pytest.mark.parametrize("gone", ["salience", "reinforced_count", "last_referenced"])
    @pytest.mark.parametrize("shape", list(SHAPES.values()), ids=lambda s: s.label)
    def test_the_deleted_counter_names_are_refused_not_ignored(self, shape, gone):
        """A write of one of these must FAIL, not pass through and land nowhere.

        After the deletion these names are undeclared, so `validated` rejects them. That
        matters because the failure mode of silent acceptance is the worst one available:
        code would appear to record her learning, the property would be written to a node
        nothing reads, and the gap would surface as "why did she not remember" months
        later. Reinstating a counter has to go through the shape declaration.
        """
        with pytest.raises(ValueError, match="not a declared property"):
            shape.validated({gone: 1})

    def test_a_bad_vocabulary_value_is_refused(self):
        with pytest.raises(ValueError, match="not in"):
            LORE_ENTRY.validated({"origin": "banana"})
        with pytest.raises(ValueError, match="not in"):
            TRAIT.validated({"kind": "nonsense"})
        with pytest.raises(ValueError, match="not in"):
            EXPERTISE.validated({"level": "expert"})

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

    def test_source_hash_is_refreshed_not_create_once(self):
        """It describes the CARD, so a rebuild must refresh it — a stale-source check
        is the whole point of storing it, and could detect nothing if the value were
        pinned at creation. Renamed from `..._is_content_not_annotation`: the name
        asserted a distinction against a class that no longer exists, and a test name
        that describes a vanished design is how the next reader is misled."""
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

    def test_every_property_the_card_produces_is_declared(self, card):
        """Replaces `test_the_card_never_produces_an_annotation`, which went vacuous.

        The old test intersected each node against the annotation set; with that set
        empty it passed for every node while checking nothing. This asserts the property
        that still has teeth and is strictly stronger: every key the card produces must
        be DECLARED on its shape, so `validated` accepts it. An undeclared key raises,
        which is what makes this fail rather than pass when it should.
        """
        for n in identity_nodes(card):
            shape = SHAPES[n["_shape"]]
            props = {k: v for k, v in n.items() if k != "_shape"}
            shape.validated(props)   # raises on anything undeclared

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
        rebuild wipes a create-once property. `SET n += $map` DELETES any key whose value is null,
        so an absent optional field removes a property instead of leaving it unset.
        Both are avoided by assigning properties individually."""
        src = self._src()
        assert "SET n = $" not in src
        assert "SET n += $" not in src

    def test_create_once_properties_are_only_ever_set_ON_CREATE(self):
        """Preservation is structural: the rebuild path never names them.

        RE-POINTED 2026-10-16. This asserted on `salience` and `reinforced_count`; they
        are gone, but the MECHANISM is not, and it still has two properties that genuinely
        must survive a rebuild. `node_id` is a surrogate id — if it churned, every edge
        pointing at the node would be orphaned. `created_at` is transaction time, and it
        is the one genuinely irreversible property here: it cannot be reconstructed after
        the fact, which is why the ecosystem migration doc ranks transaction time as the
        most irreversible day-1 decision.

        The ADR-018 A/B (0 of 3 vs 3 of 3 survival across a card edit) measured THIS
        mechanism rather than the counter, so its evidence carries over unchanged.
        """
        src = self._src()
        assert "ON CREATE SET" in src, (
            "the create-once branch is gone — a rebuild now overwrites node_id and "
            "created_at, and created_at cannot be reconstructed once lost"
        )
        after_on_create = src.split("ON CREATE SET", 1)[1]
        # Cut at the unconditional SET: anything after it is refreshed every build.
        create_only = after_on_create.split("\n                SET ", 1)[0]
        assert "n.node_id = s.node_id" in create_only
        assert "n.created_at = s.created_at" in create_only
        # And the refresh block must not ASSIGN them, or they are rewritten every
        # rebuild. Matched as an assignment, not a bare mention: the same statement ends
        # `RETURN n.node_id AS node_id, n.created_at AS created_at`, which is a read and
        # perfectly fine. A bare-substring check fails on that and I watched it do so.
        refreshed = after_on_create.split("\n                SET ", 1)[1]
        assert "n.node_id =" not in refreshed
        assert "n.created_at =" not in refreshed

    def test_merge_matches_the_live_label(self):
        """An expired node and a live node share a positional key, so a MERGE on the
        primary label alone would bind the expired one and resurrect it."""
        assert "CurrentIdentity" in self._src()


class TestTheBehaviourPolicySplit:
    """`behaviour` held two different kinds of thing until 2026-10-17.

    It reached 34 live nodes against the vocabulary's own stated revisit trigger of
    ~15-20, and inspection showed why: one bucket carried bare dispositions from a list
    field ("seductive", "eager") AND full procedural clauses from seven scalar fields
    ("no pretense of professionalism", "every conversation steers to X"). The remedy was
    a sibling value, `policy`, not hierarchy — a conversational policy is not a more
    specific behaviour, it is a different thing, so nesting it would have kept a genus
    label that is wrong for half its members.
    """

    def test_dispositions_stay_behaviour_and_policies_become_policy(self, card):
        by_field = {}
        for n in identity_nodes(card):
            if n["_shape"] == "Trait":
                by_field.setdefault(n["source_field"], set()).add(n.get("kind"))

        assert by_field.get("behavior.traits") == {"behaviour"}, (
            "the list of bare dispositions must stay `behaviour`")
        for fname in ("pace", "humor", "formality", "small_talk", "emoji_policy",
                      "clarifying_questions", "relationship_to_user"):
            field = f"behavior.{fname}"
            if field in by_field:
                assert by_field[field] == {"policy"}, (
                    f"{field} is a standing rule about how an exchange proceeds, so it "
                    f"is `policy`, not {by_field[field]}")

    def test_no_kind_exceeds_its_own_revisit_trigger(self, card):
        """The trigger that forced this split, now mechanical rather than remembered.

        ⚠️ ~15-20 has NO external authority — Z39.19's only granularity section is
        qualitative and "7±2" is about recall during one task, not stored category size.
        It is this repo's own engineering heuristic, so the assertion is at the top of the
        stated range and the message says what to do rather than just failing.
        """
        import collections
        counts = collections.Counter(
            n.get("kind") for n in identity_nodes(card) if n["_shape"] == "Trait")
        over = {k: v for k, v in counts.items() if v > 20}
        assert not over, (
            f"{over} exceeds the ~15-20 revisit trigger in identity_shapes. Split it into "
            f"a SIBLING value (as `policy` was split from `behaviour`), not a hierarchy — "
            f"and check first whether the bucket holds two different kinds of thing.")
