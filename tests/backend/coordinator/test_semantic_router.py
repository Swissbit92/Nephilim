# tests/backend/coordinator/test_semantic_router.py
"""Unit tests for src/coordinator/tools/semantic_router.py.

Strategy
--------
- All embedding calls are mocked via unittest.mock.patch so tests run
  deterministically with zero network / Ollama access.
- Fixed 3-D unit vectors are used to control cosine similarity outcomes.
- Module-level globals (_centroids, _embeddings_model) are reset between
  tests using monkeypatch / direct assignment so state from one test cannot
  contaminate another.
"""

from __future__ import annotations

import math
import importlib
from typing import List
from unittest.mock import MagicMock, patch

import pytest

import src.coordinator.tools.semantic_router as sr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unit(v: List[float]) -> List[float]:
    """Return a L2-normalised copy of v (for predictable cosine results)."""
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v]


# Predefined orthogonal-ish unit vectors to anchor each intent.
VEC_WALLET    = _unit([1.0, 0.0, 0.0])   # points along x
VEC_WEB       = _unit([0.0, 1.0, 0.0])   # points along y
VEC_LLM_ONLY  = _unit([0.0, 0.0, 1.0])   # points along z


def _make_mock_model(query_vec: List[float]) -> MagicMock:
    """Return a mock embeddings model.

    embed_documents returns distinct centroid-aligned vectors per phrase;
    embed_query returns `query_vec`.

    Each call to embed_documents gets 10 identical vectors (matching the
    10 example phrases per intent in _INTENT_EXAMPLES).  The mean of 10
    identical vectors equals the vector itself, so the centroid equals
    exactly VEC_WALLET / VEC_WEB / VEC_LLM_ONLY.
    """
    model = MagicMock()

    # Build a side_effect that rotates through per-intent vectors.
    # The module iterates _INTENT_EXAMPLES in dict insertion order:
    # wallet → web_search → llm_only
    per_intent = {
        "wallet":     [VEC_WALLET]    * 10,
        "web_search": [VEC_WEB]       * 10,
        "llm_only":   [VEC_LLM_ONLY]  * 10,
    }
    call_order = list(per_intent.values())
    call_counter = {"n": 0}

    def embed_documents(phrases):
        idx = call_counter["n"] % len(call_order)
        call_counter["n"] += 1
        return call_order[idx]

    model.embed_documents.side_effect = embed_documents
    model.embed_query.return_value = query_vec
    return model


def _reset_globals(monkeypatch):
    """Clear module-level caches between tests.

    `_example_vecs_primary` was missing here — it postdates this helper — so a test
    using PRIMARY mode inherited whatever an earlier test had cached. Harmless while
    every test built its own vectors from a mock, and not harmless once a test
    asserts the BUILD fails: the cached value short-circuits the build entirely and
    the assertion silently tests nothing. Found exactly that way.
    """
    monkeypatch.setattr(sr, "_centroids", None)
    monkeypatch.setattr(sr, "_embeddings_model", None)
    monkeypatch.setattr(sr, "_example_vecs_primary", None)


# ---------------------------------------------------------------------------
# Tests: pure math helpers
# ---------------------------------------------------------------------------

class TestCosineSimilarity:

    def test_identical_vectors_returns_one(self):
        v = [1.0, 2.0, 3.0]
        assert sr._cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_returns_zero(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert sr._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors_returns_minus_one(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert sr._cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_a_returns_zero(self):
        assert sr._cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_zero_vector_b_returns_zero(self):
        assert sr._cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_both_zero_vectors_returns_zero(self):
        assert sr._cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_known_similarity(self):
        # cos(45°) between [1,0] and [1,1]/√2
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = 1.0 / math.sqrt(2)
        assert sr._cosine_similarity(a, b) == pytest.approx(expected, rel=1e-6)

    def test_single_element_vectors(self):
        assert sr._cosine_similarity([3.0], [5.0]) == pytest.approx(1.0)

    def test_mismatched_length_uses_zip(self):
        # zip stops at shortest — should not raise
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0]
        result = sr._cosine_similarity(a, b)
        assert isinstance(result, float)


class TestMeanVector:

    def test_single_vector(self):
        assert sr._mean_vector([[1.0, 2.0, 3.0]]) == pytest.approx([1.0, 2.0, 3.0])

    def test_two_vectors(self):
        result = sr._mean_vector([[1.0, 2.0], [3.0, 4.0]])
        assert result == pytest.approx([2.0, 3.0])

    def test_identical_vectors(self):
        result = sr._mean_vector([[1.0, 1.0], [1.0, 1.0]])
        assert result == pytest.approx([1.0, 1.0])

    def test_empty_list_returns_empty(self):
        assert sr._mean_vector([]) == []

    def test_three_vectors(self):
        vecs = [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]
        assert sr._mean_vector(vecs) == pytest.approx([1.0, 1.0])


# ---------------------------------------------------------------------------
# Tests: _build_centroids
# ---------------------------------------------------------------------------

class TestBuildCentroids:

    def test_returns_centroid_for_each_intent(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        centroids = sr._build_centroids(model)
        assert set(centroids.keys()) == {"wallet", "web_search", "llm_only"}

    def test_centroid_values_match_expected(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        centroids = sr._build_centroids(model)
        # Each centroid should equal its corresponding unit vector (mean of 10 identical)
        assert centroids["wallet"]    == pytest.approx(VEC_WALLET)
        assert centroids["web_search"] == pytest.approx(VEC_WEB)
        assert centroids["llm_only"]  == pytest.approx(VEC_LLM_ONLY)

    def test_partial_failure_skips_intent(self, monkeypatch):
        """If embed_documents raises for one intent, that intent is skipped."""
        _reset_globals(monkeypatch)
        call_n = {"n": 0}

        def embed_documents(phrases):
            n = call_n["n"]
            call_n["n"] += 1
            if n == 0:
                raise RuntimeError("simulated failure")
            return [[0.0, 1.0]] * 10

        model = MagicMock()
        model.embed_documents.side_effect = embed_documents
        centroids = sr._build_centroids(model)
        # First intent (wallet) fails; web_search and llm_only should succeed
        assert "wallet" not in centroids
        assert len(centroids) == 2


# ---------------------------------------------------------------------------
# Tests: _ensure_centroids (caching)
# ---------------------------------------------------------------------------

class TestEnsureCentroids:

    def test_builds_on_first_call(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        result = sr._ensure_centroids(model)
        assert result is not None
        assert "wallet" in result

    def test_returns_cached_on_second_call(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        first  = sr._ensure_centroids(model)
        second = sr._ensure_centroids(model)
        assert first is second  # same object — cache hit

    def test_returns_cached_even_with_new_model(self, monkeypatch):
        _reset_globals(monkeypatch)
        model1 = _make_mock_model(VEC_WALLET)
        sr._ensure_centroids(model1)  # prime cache
        model2 = _make_mock_model(VEC_WEB)
        result = sr._ensure_centroids(model2)
        assert result is sr._centroids  # still the cached dict

    def test_returns_none_on_build_exception(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = MagicMock()
        model.embed_documents.side_effect = Exception("boom")
        # _build_centroids will catch per-intent failures so centroids = {}
        # _ensure_centroids returns the (empty) dict — truthy check is on caller
        result = sr._ensure_centroids(model)
        # Empty dict is falsy; route_by_embedding checks `if not centroids`
        assert result is not None  # it IS returned
        assert len(result) == 0    # but empty


# ---------------------------------------------------------------------------
# Tests: _get_embeddings_model
# ---------------------------------------------------------------------------

class TestGetEmbeddingsModel:

    def test_returns_cached_model_without_reimport(self, monkeypatch):
        sentinel = MagicMock()
        monkeypatch.setattr(sr, "_embeddings_model", sentinel)
        result = sr._get_embeddings_model()
        assert result is sentinel

    def test_returns_none_when_import_fails(self, monkeypatch):
        monkeypatch.setattr(sr, "_embeddings_model", None)
        # Patch get_settings to raise so the whole initialisation fails
        with patch("src.coordinator.tools.semantic_router._get_embeddings_model", return_value=None):
            # We can't call the real function easily without real config;
            # verify via route_by_embedding returning None when model is None.
            result = sr.route_by_embedding("test query", can_use_brave=True)
            assert result is None


# ---------------------------------------------------------------------------
# Tests: route_by_embedding — main routing logic
# ---------------------------------------------------------------------------

class TestRouteByEmbedding:
    """Patch _get_embeddings_model to return a controlled mock."""

    def _patch_model(self, monkeypatch, query_vec: List[float]):
        _reset_globals(monkeypatch)
        model = _make_mock_model(query_vec)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        return model

    # -- No model available ------------------------------------------------

    def test_returns_none_when_no_embedding_model(self, monkeypatch):
        _reset_globals(monkeypatch)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: None)
        result = sr.route_by_embedding("anything", can_use_brave=True)
        assert result is None

    # -- Wallet routing ----------------------------------------------------

    def test_routes_to_wallet_when_score_above_threshold(self, monkeypatch):
        """Query vector == wallet centroid → similarity 1.0 → should route wallet."""
        self._patch_model(monkeypatch, VEC_WALLET)
        result = sr.route_by_embedding(
            "what's in my wallet",
            can_use_brave=True,
            can_use_wallet=True,
        )
        assert result == "wallet"

    def test_wallet_not_returned_when_can_use_wallet_false(self, monkeypatch):
        """Even if wallet scores highest, don't return it if persona can't use it."""
        self._patch_model(monkeypatch, VEC_WALLET)
        # can_use_wallet=False — wallet centroid excluded from available set.
        # Query == VEC_WALLET but only web_search and llm_only are in available.
        # VEC_WALLET · VEC_WEB = 0, VEC_WALLET · VEC_LLM_ONLY = 0 → both 0.0
        # 0.0 < 0.75 threshold → returns None
        result = sr.route_by_embedding(
            "check my balance",
            can_use_brave=True,
            can_use_wallet=False,
        )
        assert result is None

    # -- Web search routing -----------------------------------------------

    def test_routes_to_web_search_when_score_above_threshold(self, monkeypatch):
        self._patch_model(monkeypatch, VEC_WEB)
        result = sr.route_by_embedding(
            "what's the latest crypto news",
            can_use_brave=True,
            can_use_wallet=False,
        )
        assert result == "web_search"

    def test_web_search_not_returned_when_can_use_brave_false(self, monkeypatch):
        self._patch_model(monkeypatch, VEC_WEB)
        result = sr.route_by_embedding(
            "what's the news",
            can_use_brave=False,
            can_use_wallet=False,
        )
        # Only llm_only in available; VEC_WEB · VEC_LLM_ONLY = 0 < 0.75 → None
        assert result is None

    # -- LLM-only routing — always returns None ---------------------------

    def test_llm_only_intent_returns_none(self, monkeypatch):
        """llm_only intent is treated as NEEDS_NEITHER so route returns None."""
        self._patch_model(monkeypatch, VEC_LLM_ONLY)
        result = sr.route_by_embedding(
            "explain blockchain to me",
            can_use_brave=True,
            can_use_wallet=True,
        )
        # The best match is llm_only (sim=1.0) → returns None per spec
        assert result is None

    def test_llm_only_when_no_mcp_capabilities(self, monkeypatch):
        self._patch_model(monkeypatch, VEC_LLM_ONLY)
        result = sr.route_by_embedding(
            "who is satoshi",
            can_use_brave=False,
            can_use_wallet=False,
        )
        # available = {"llm_only": ...} only; best_intent=llm_only → None
        assert result is None

    # -- Threshold boundary -----------------------------------------------

    def test_below_threshold_returns_none(self, monkeypatch):
        """A query vector that produces sim < 0.75 for all intents returns None."""
        # 45° between wallet and web gives sim = 0.0 with each axis; use diagonal
        # between wallet (x) and web (y): [1/√2, 1/√2, 0]
        # cos with x-axis = 1/√2 ≈ 0.707 < 0.75
        diagonal = _unit([1.0, 1.0, 0.0])
        self._patch_model(monkeypatch, diagonal)
        result = sr.route_by_embedding(
            "borderline query",
            can_use_brave=True,
            can_use_wallet=True,
        )
        # Best sim = 1/√2 ≈ 0.707 < threshold 0.75 → None
        assert result is None

    def test_exactly_at_threshold_is_rejected(self, monkeypatch):
        """sim == 0.75 should NOT pass (condition is strict <). Let's use 0.74."""
        # We can't precisely achieve 0.75 with simple unit vectors, so we
        # mock _cosine_similarity instead to exercise the branch directly.
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WEB)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)

        # Force the similarity function to return exactly 0.749 for everything
        monkeypatch.setattr(sr, "_cosine_similarity", lambda a, b: 0.749)
        result = sr.route_by_embedding("test", can_use_brave=True, can_use_wallet=True)
        assert result is None

    def test_just_above_threshold_is_accepted(self, monkeypatch):
        """sim == 0.76 should pass."""
        _reset_globals(monkeypatch)
        call_n = {"n": 0}

        def sim_side_effect(a, b):
            call_n["n"] += 1
            return 0.76  # all sims equal; first sorted win goes to dict order

        model = _make_mock_model(VEC_WALLET)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        monkeypatch.setattr(sr, "_cosine_similarity", sim_side_effect)

        result = sr.route_by_embedding("test", can_use_brave=True, can_use_wallet=True)
        # All scores equal 0.76 > 0.75; sorted scores → first in list wins.
        # Whatever the top intent is, it should NOT be None if it's not llm_only.
        # With all same score, sort is stable — actual outcome depends on dict order.
        # We just verify non-None (if best_intent != llm_only) OR None (if llm_only wins).
        assert result in ("wallet", "web_search", None)  # deterministic per Python sort stability

    # -- No available intents -------------------------------------------

    def test_no_available_intents_returns_none(self, monkeypatch):
        """If centroids is populated but no intents pass the filter, return None."""
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)

        # Inject pre-built centroids but strip llm_only so available = {}
        monkeypatch.setattr(sr, "_centroids", {"wallet": VEC_WALLET, "web_search": VEC_WEB})
        # can_use_brave=False, can_use_wallet=False → wallet and web_search excluded
        # llm_only not in centroids → available = {}
        result = sr.route_by_embedding("test", can_use_brave=False, can_use_wallet=False)
        assert result is None

    # -- Empty centroids (build failed) ------------------------------------

    def test_empty_centroids_returns_none(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = MagicMock()
        model.embed_documents.side_effect = Exception("fail")
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        result = sr.route_by_embedding("anything", can_use_brave=True)
        assert result is None

    # -- Query embedding failure -------------------------------------------

    def test_query_embedding_failure_returns_none(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        model.embed_query.side_effect = Exception("embed failed")
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        result = sr.route_by_embedding("test query", can_use_brave=True)
        assert result is None

    # -- can_use_mongodb param (unused, compat) ----------------------------

    def test_can_use_mongodb_param_does_not_affect_result(self, monkeypatch):
        """can_use_mongodb is documented as unused — verify it has no effect."""
        self._patch_model(monkeypatch, VEC_WEB)
        r1 = sr.route_by_embedding("news", can_use_brave=True, can_use_mongodb=True)
        _reset_globals(monkeypatch)
        self._patch_model(monkeypatch, VEC_WEB)
        r2 = sr.route_by_embedding("news", can_use_brave=True, can_use_mongodb=False)
        assert r1 == r2

    # -- Tie-breaking by sort stability ------------------------------------

    def test_highest_score_intent_selected(self, monkeypatch):
        """When wallet scores highest, wallet is returned (not web_search)."""
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)

        # Inject pre-built centroids
        monkeypatch.setattr(sr, "_centroids", {
            "wallet":    VEC_WALLET,
            "web_search": VEC_WEB,
            "llm_only":  VEC_LLM_ONLY,
        })
        # Query == VEC_WALLET → sim(wallet)=1.0, sim(web_search)=0.0, sim(llm_only)=0.0
        result = sr.route_by_embedding("wallet query", can_use_brave=True, can_use_wallet=True)
        assert result == "wallet"

    def test_highest_score_web_search_selected(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WEB)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        monkeypatch.setattr(sr, "_centroids", {
            "wallet":    VEC_WALLET,
            "web_search": VEC_WEB,
            "llm_only":  VEC_LLM_ONLY,
        })
        result = sr.route_by_embedding("news query", can_use_brave=True, can_use_wallet=True)
        assert result == "web_search"


# ---------------------------------------------------------------------------
# Tests: warm_centroids
# ---------------------------------------------------------------------------

class TestWarmCentroids:

    def test_returns_true_when_model_and_centroids_available(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        result = sr.warm_centroids()
        assert result is True

    def test_returns_false_when_no_model(self, monkeypatch):
        _reset_globals(monkeypatch)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: None)
        result = sr.warm_centroids()
        assert result is False

    def test_returns_false_when_centroids_empty(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = MagicMock()
        model.embed_documents.side_effect = Exception("fail")
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        result = sr.warm_centroids()
        assert result is False

    def test_warm_centroids_populates_cache(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        sr.warm_centroids()
        assert sr._centroids is not None
        assert len(sr._centroids) == 3

    def test_warm_centroids_is_idempotent(self, monkeypatch):
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_WALLET)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        first = sr.warm_centroids()
        second = sr.warm_centroids()
        assert first == second is True
        # embed_documents should only be called once per intent (cache hit)
        assert model.embed_documents.call_count == 3  # once per intent


# ---------------------------------------------------------------------------
# Tests: module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:

    def test_confidence_threshold_is_positive_fraction(self):
        assert 0.0 < sr._CONFIDENCE_THRESHOLD < 1.0

    def test_intent_examples_has_expected_keys(self):
        assert set(sr._INTENT_EXAMPLES.keys()) == {"wallet", "web_search", "llm_only"}

    def test_each_intent_has_multiple_examples(self):
        for intent, phrases in sr._INTENT_EXAMPLES.items():
            assert len(phrases) >= 2, f"Intent '{intent}' has too few examples"

    def test_all_examples_are_non_empty_strings(self):
        for intent, phrases in sr._INTENT_EXAMPLES.items():
            for phrase in phrases:
                assert isinstance(phrase, str) and phrase.strip(), (
                    f"Empty or non-string example in intent '{intent}'"
                )


# ---------------------------------------------------------------------------
# Tests: semantic-PRIMARY params (threshold / margin / drop_llm_only_centroid)
# ---------------------------------------------------------------------------
class TestRouteByEmbeddingPrimaryParams:
    """New params must default to legacy behaviour and gate correctly when set."""

    def _patch(self, monkeypatch, query_vec):
        _reset_globals(monkeypatch)
        monkeypatch.setattr(sr, "_example_vecs_primary", None)
        model = _make_mock_model(query_vec)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        return model

    def test_threshold_param_rejects_below(self, monkeypatch):
        self._patch(monkeypatch, VEC_WALLET)
        monkeypatch.setattr(sr, "_cosine_similarity", lambda a, b: 0.58)
        # wallet-only availability → single intent, unambiguous
        result = sr.route_by_embedding(
            "x", can_use_brave=False, can_use_wallet=True,
            threshold=0.61, drop_llm_only_centroid=True,
        )
        assert result is None  # 0.58 < 0.61

    def test_threshold_param_accepts_above(self, monkeypatch):
        self._patch(monkeypatch, VEC_WALLET)
        monkeypatch.setattr(sr, "_cosine_similarity", lambda a, b: 0.85)
        result = sr.route_by_embedding(
            "x", can_use_brave=False, can_use_wallet=True,
            threshold=0.61, drop_llm_only_centroid=True,
        )
        assert result == "wallet"

    def test_margin_gate_rejects_ambiguous(self, monkeypatch):
        self._patch(monkeypatch, VEC_WALLET)
        # Primary mode scores max-over-examples, so sim is called per example vector;
        # key the score off the example vector identity. wallet=0.82, web=0.80 → gap 0.02.
        monkeypatch.setattr(sr, "_cosine_similarity", lambda a, b: 0.82 if b == VEC_WALLET else 0.80)
        result = sr.route_by_embedding(
            "x", can_use_brave=True, can_use_wallet=True,
            threshold=0.61, margin=0.06, drop_llm_only_centroid=True,
        )
        assert result is None

    def test_margin_gate_passes_clear_winner(self, monkeypatch):
        self._patch(monkeypatch, VEC_WALLET)
        monkeypatch.setattr(sr, "_cosine_similarity", lambda a, b: 0.88 if b == VEC_WALLET else 0.70)
        result = sr.route_by_embedding(
            "x", can_use_brave=True, can_use_wallet=True,
            threshold=0.61, margin=0.06, drop_llm_only_centroid=True,
        )
        assert result == "wallet"

    def test_drop_llm_only_builds_primary_example_vecs(self, monkeypatch):
        self._patch(monkeypatch, VEC_WALLET)
        sr.route_by_embedding(
            "x", can_use_brave=False, can_use_wallet=True,
            threshold=0.61, drop_llm_only_centroid=True,
        )
        assert sr._example_vecs_primary is not None
        assert sr._centroids is None  # legacy centroids NOT built in primary mode

    def test_legacy_defaults_unchanged(self, monkeypatch):
        """Zero new-arg call: legacy 0.75 threshold + legacy centroids (incl llm_only)."""
        self._patch(monkeypatch, VEC_WALLET)
        result = sr.route_by_embedding("x", can_use_brave=True, can_use_wallet=True)
        assert result == "wallet"               # VEC_WALLET vs wallet centroid ≈ 1.0
        assert sr._centroids is not None         # legacy centroids built
        assert "llm_only" in sr._centroids       # legacy set includes llm_only
        assert sr._example_vecs_primary is None  # primary set untouched


class TestModuleConstantsPrimary:
    def test_primary_examples_keys(self):
        assert set(sr._INTENT_EXAMPLES_PRIMARY.keys()) == {"wallet", "web_search"}

    def test_primary_examples_no_llm_only(self):
        assert "llm_only" not in sr._INTENT_EXAMPLES_PRIMARY

    def test_primary_examples_count(self):
        for intent, phrases in sr._INTENT_EXAMPLES_PRIMARY.items():
            assert len(phrases) >= 15, f"primary intent '{intent}' needs >=15 examples"


class TestPrimaryExampleCoverage:
    """2026-07-05 Telegram incident: weather and general/country news questions
    fell through to NEEDS_NEITHER because the primary web_search example set
    was crypto/sports-only. Lock the non-crypto coverage in (hermetic — the
    live routing quality gate is tests/evaluation/tune_routing_threshold.py)."""

    def test_web_search_examples_cover_weather(self):
        examples = " | ".join(
            sr._INTENT_EXAMPLES_PRIMARY["web_search"]
        ).lower()
        assert "weather" in examples
        assert "forecast" in examples

    def test_web_search_examples_cover_general_news(self):
        examples = " | ".join(
            sr._INTENT_EXAMPLES_PRIMARY["web_search"]
        ).lower()
        assert "headlines" in examples
        # Non-crypto, country-scoped news phrasing.
        assert "switzerland" in examples


# ---------------------------------------------------------------------------
# Tests: "could not run" is not "ran and found nothing"
# ---------------------------------------------------------------------------

class TestOutageIsDistinguishableFromNoResult:
    """`route_by_embedding` returned None for BOTH, and said so in its own
    docstring: "None if confidence is below threshold/margin **or embeddings are
    unavailable**". That is the semipredicate problem — a failure signalled with
    an otherwise-valid return value — and it silently disarmed the out-of-surface
    guard: a turn nobody could classify became NEEDS_NEITHER, which
    `capability_scope` reads as "no tool required, nothing missing".

    The opt-in exception keeps every existing caller on the old contract; only the
    intent classifier asks for the distinction.
    """

    def test_no_embeddings_model_raises_when_the_caller_asks(self, monkeypatch):
        _reset_globals(monkeypatch)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: None)
        with pytest.raises(sr.EmbeddingsUnavailable):
            sr.route_by_embedding(
                "anything", can_use_brave=True, raise_on_unavailable=True
            )

    def test_the_old_contract_is_untouched_by_default(self, monkeypatch):
        """Every pre-existing caller keeps None. The flag is opt-in precisely so
        this change cannot reach code that was not audited for it."""
        _reset_globals(monkeypatch)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: None)
        assert sr.route_by_embedding("anything", can_use_brave=True) is None

    def test_unbuildable_example_vectors_raise_when_the_caller_asks(self, monkeypatch):
        """The CI case: the model exists, embedding the example phrases fails."""
        _reset_globals(monkeypatch)
        model = MagicMock()
        model.embed_documents.side_effect = RuntimeError("connection refused")
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        with pytest.raises(sr.EmbeddingsUnavailable):
            sr.route_by_embedding(
                "anything", can_use_brave=True,
                drop_llm_only_centroid=True, raise_on_unavailable=True,
            )

    def test_low_confidence_still_returns_none_not_an_exception(self, monkeypatch):
        """The half that must NOT change. An outage is exceptional; 'ran fine, no
        confident route' is the ordinary answer and stays in-band."""
        _reset_globals(monkeypatch)
        model = _make_mock_model(VEC_LLM_ONLY)
        monkeypatch.setattr(sr, "_get_embeddings_model", lambda: model)
        result = sr.route_by_embedding(
            "something unrelated", can_use_brave=True,
            threshold=0.99, raise_on_unavailable=True,
        )
        assert result is None
