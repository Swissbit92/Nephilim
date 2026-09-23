# src/coordinator/tools/semantic_router.py
"""Semantic embedding fallback router for intent classification.

R4: Hybrid routing — keyword fast path → embedding similarity fallback → LLM slow path.

When keyword matching in intent_classifier.py produces no signal but the persona has
MCP access, this module embeds the user query against intent centroids computed from
canonical example phrases. Uses the configured RAG embedding model (bge-m3)
(same infrastructure as memory_rag.py — zero additional setup required).

Only activated when:
  1. Keyword classifier returns NEEDS_NEITHER
  2. Persona has at least one MCP capability (brave_search or solana_wallet)
  3. The embedding model is available (degrades gracefully if not)

Example queries caught by semantic routing but not keywords:
  - "thinking of moving some funds around" → NEEDS_WALLET
  - "what's the vibe in the market today" → NEEDS_WEB_SEARCH
"""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent centroid definitions — representative example phrases per intent.
# Keep these diverse (varied phrasing) for robust centroid computation.
# ---------------------------------------------------------------------------

_INTENT_EXAMPLES: Dict[str, List[str]] = {
    "wallet": [
        "what's in my wallet",
        "how much sol do I have",
        "thinking of moving some funds around",
        "I want to swap some tokens",
        "check my portfolio",
        "what's my balance looking like",
        "should I rebalance my holdings",
        "thinking about making a trade",
        "I might buy some more",
        "my investments right now",
    ],
    "web_search": [
        "what's the latest news",
        "what's happening with crypto today",
        "recent developments in the market",
        "breaking news on ethereum",
        "what are analysts saying about bitcoin",
        "vibe in the market today",
        "what's the current sentiment",
        "what did the fed say",
        "any updates on the regulation front",
        "what's everyone talking about in crypto",
    ],
    "llm_only": [
        "what is blockchain technology",
        "explain defi to me",
        "how does solana work",
        "tell me about yourself",
        "what do you think about",
        "help me understand",
        "can you explain",
        "who is satoshi nakamoto",
        "what's the difference between proof of work and proof of stake",
        "help me think through this",
    ],
}

# Confidence threshold: below this, fall back to NEEDS_NEITHER rather than guess.
# NOTE: 0.75 was tuned for the legacy nomic embedder. bge-m3's cosine range is
# compressed to ~[0.6, 1.0], so the semantic-PRIMARY path (flag-gated) uses a
# higher, separately-tuned threshold from RoutingSettings — see route_by_embedding.
_CONFIDENCE_THRESHOLD = 0.75

# ---------------------------------------------------------------------------
# Primary-mode intent examples (used only by the semantic-PRIMARY path, when
# route_by_embedding is called with drop_llm_only_centroid=True). Expanded to
# 15-20 diverse utterances per intent across four surface forms
# (command / question / noun-phrase / implied) for robust centroids, and
# deliberately EXCLUDES an "llm_only" centroid — a catch-all centroid smears
# toward the embedding-space center and overlaps the real intents, so llm_only
# is handled as pure fall-through (below threshold/margin → None).
#
# Kept SEPARATE from _INTENT_EXAMPLES so the legacy (flag-off) path is unchanged.
# ---------------------------------------------------------------------------
_INTENT_EXAMPLES_PRIMARY: Dict[str, List[str]] = {
    "wallet": [
        # Commands
        "swap my sol to usdc",
        "sell half my sol",
        "buy more solana",
        "execute the trade",
        "set up a dca strategy",
        "pause my strategy",
        "create a new wallet",
        # Questions
        "what's in my wallet",
        "how much sol do I have",
        "should I rebalance my holdings",
        "what's my balance looking like",
        "how are my trades doing",
        "what's my portfolio worth",
        # Noun-phrase
        "my portfolio status",
        "wallet balance",
        "my current holdings",
        # 2026-07-04 incident (session dcc3693d): "my trade history" shared the
        # bare lexeme "history" with a pure lore/backstory question ("tell me
        # about your history"), and primary mode has no llm_only centroid to
        # compete against (see the design note below) — so a single close
        # nearest-example match was enough to clear threshold with nothing to
        # gate it. Rephrased to preserve the same wallet intent (past trades)
        # without the collision-prone standalone word.
        "show my past trades",
        # Implied
        "thinking of moving some funds around",
        "I might buy some more",
        "maybe it's time to take some profits",
    ],
    "web_search": [
        # Commands
        "search the web for the bitcoin price",
        "look up the latest ethereum news",
        "find recent crypto regulation news",
        # Questions
        "what's the latest crypto news",
        "what's happening in the market today",
        "what did the fed say about rates",
        "what are analysts predicting for bitcoin",
        "how's the market doing today",
        "what's trending in crypto right now",
        "what's the current price of bitcoin",
        # Noun-phrase
        "latest bitcoin news",
        "recent market developments",
        "current crypto sentiment",
        "breaking news on solana",
        # Implied
        "what's the vibe in the market today",
        "what's everyone saying about eth lately",
        "did anything big happen today",
        "how are people feeling about the market",
        # 2026-07-04 incident (session dcc3693d): this example set was 100%
        # crypto/market-phrased, so a sports/current-events temporal follow-up
        # ("what was their last match") scored under threshold and fell
        # through to NEEDS_NEITHER with zero grounding. Non-crypto coverage:
        "what was their last match",
        "who won the game last night",
        "what was the final score",
        "did they make the playoffs",
        "were they eliminated from the tournament",
        # 2026-07-05 incident (Telegram): weather and general/country news
        # questions ("what is the weather tomorrow in Brugg?", "what is the
        # latest news in switzerland today?") scored under threshold against
        # the crypto/sports-only set above and fell through to NEEDS_NEITHER
        # — the model then produced its own trained real-time-data refusal.
        # Weather. NOTE: keep these location-anchored and topical, NOT
        # conversational — short day-rhythm phrasings ("will it rain today",
        # "any big news this morning") sit within 0.66 cosine of greetings/
        # feelings chitchat ("good morning", "i'm feeling a bit down today")
        # and over-route it to search (measured 2026-07-05, bge-m3
        # nearest-example probes).
        "what's the weather tomorrow in Zurich",
        "will it rain in Zurich today",
        "what's the current temperature in Bern",
        "weather forecast for this weekend",
        "how hot will it get in Geneva tomorrow",
        # General / country news & current events
        "what's the latest news in Switzerland today",
        "what are today's top headlines",
        "any major news events this week",
        "what happened in the world today",
        "latest news about the election",
    ],
}

# Module-level caches (computed once per process)
_centroids: Optional[Dict[str, List[float]]] = None  # legacy: {intent: mean-centroid}
# Primary path stores ALL example vectors per intent and scores by MAX similarity
# (nearest-example / kNN), NOT a mean centroid. Averaging diverse phrases smears the
# centroid toward the embedding-space center, so short/benign chitchat ("good morning")
# lands as close to it as a real intent. Max-over-examples keeps a clean separation
# (real intents 0.84-1.0 vs chitchat 0.45-0.65) and restores a ~0.78 threshold.
_example_vecs_primary: Optional[Dict[str, List[List[float]]]] = None
_embeddings_model: Optional[object] = None


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _mean_vector(vectors: List[List[float]]) -> List[float]:
    """Compute element-wise mean of a list of vectors."""
    if not vectors:
        return []
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


def _get_embeddings_model():
    """Lazily initialize the Ollama embeddings model."""
    global _embeddings_model
    if _embeddings_model is not None:
        return _embeddings_model
    try:
        from ..config import get_settings
        cfg = get_settings()
        model = cfg.memory.embedding_model
        base_url = cfg.ollama.base
        # Prefer langchain-ollama (newer, uses /api/embed + forwards num_ctx so
        # bge-m3 gets its full 8192 window); fall back to langchain-community.
        try:
            from langchain_ollama import OllamaEmbeddings
            _embeddings_model = OllamaEmbeddings(
                model=model, base_url=base_url, num_ctx=cfg.memory.embedding_max_tokens
            )
        except ImportError:
            from langchain_community.embeddings import OllamaEmbeddings  # type: ignore[assignment]
            _embeddings_model = OllamaEmbeddings(model=model, base_url=base_url)
        logger.info(f"[SemanticRouter] Embedding model initialized: {model}")
        return _embeddings_model
    except Exception as e:
        logger.debug(f"[SemanticRouter] Embedding model unavailable: {e}")
        return None


def _build_centroids_from(emb_model, examples: Dict[str, List[str]]) -> Dict[str, List[float]]:
    """Build intent centroids from an explicit examples dict by embedding + averaging."""
    centroids: Dict[str, List[float]] = {}
    for intent, phrases in examples.items():
        try:
            vectors = emb_model.embed_documents(phrases)
            centroids[intent] = _mean_vector(vectors)
            logger.debug(f"[SemanticRouter] Centroid computed for intent '{intent}'")
        except Exception as e:
            logger.warning(f"[SemanticRouter] Failed to compute centroid for '{intent}': {e}")
    logger.info(f"[SemanticRouter] Intent centroids built for: {list(centroids.keys())}")
    return centroids


def _build_centroids(emb_model) -> Dict[str, List[float]]:
    """Build legacy intent centroids (incl. llm_only) from _INTENT_EXAMPLES."""
    return _build_centroids_from(emb_model, _INTENT_EXAMPLES)


def _ensure_centroids(emb_model) -> Optional[Dict[str, List[float]]]:
    """Return cached legacy centroids, building them on first call."""
    global _centroids
    if _centroids is not None:
        return _centroids
    try:
        _centroids = _build_centroids(emb_model)
        return _centroids
    except Exception as e:
        logger.warning(f"[SemanticRouter] Centroid build failed: {e}")
        return None


def _ensure_example_vecs_primary(emb_model) -> Optional[Dict[str, List[List[float]]]]:
    """Return cached per-example vectors for the primary intents (wallet/web_search),
    building them on first call. Used for max-over-examples (nearest-example) scoring."""
    global _example_vecs_primary
    if _example_vecs_primary is not None:
        return _example_vecs_primary
    try:
        vecs: Dict[str, List[List[float]]] = {}
        for intent, phrases in _INTENT_EXAMPLES_PRIMARY.items():
            vecs[intent] = emb_model.embed_documents(phrases)
        _example_vecs_primary = vecs
        logger.info(f"[SemanticRouter] Primary example vectors built for: {list(vecs.keys())}")
        return _example_vecs_primary
    except Exception as e:
        logger.warning(f"[SemanticRouter] Primary example-vector build failed: {e}")
        return None


class EmbeddingsUnavailable(RuntimeError):
    """The router could not run at all — as distinct from running and finding no route.

    `route_by_embedding` returned `None` for BOTH cases, and its own docstring said
    so: "None if confidence is below threshold/margin **or embeddings are
    unavailable**". One sentinel, two meanings, and the caller could not tell an
    answer from an outage.

    That conflation silently disarmed the out-of-surface guard. A turn nobody could
    classify became `NEEDS_NEITHER` — "no confident route, answer conversationally"
    — which `capability_scope` maps to "no tool is required, nothing is missing".
    With `TOOL_BRAIN_UNGATED_WEB=true` (production) that turn still reaches the tool
    brain, so gwen was offered her media tools for a weather question: the exact
    defect the guard was built to prevent, reached through the guard's own blind
    spot.

    Same shape as this ecosystem's most-repeated defect — `0.0` meaning both "a
    failed API call" and "genuinely zero" in eeva-exec's funding reader.

    Raised only when a caller passes `raise_on_unavailable=True`, so existing
    callers and their tests keep the old `None` contract unchanged.
    """


def route_by_embedding(
    query: str,
    can_use_brave: bool,
    can_use_mongodb: bool = False,
    can_use_wallet: bool = False,
    threshold: float = _CONFIDENCE_THRESHOLD,
    margin: float = 0.0,
    drop_llm_only_centroid: bool = False,
    raise_on_unavailable: bool = False,
) -> Optional[str]:
    """Attempt to classify query intent via embedding similarity.

    Args:
        query: User query string
        can_use_brave: Whether Brave search is available for this persona
        can_use_mongodb: Unused (MongoDB MCP removed). Kept for call-site compat.
        can_use_wallet: Whether wallet access is available for this persona
        threshold: Cosine confidence floor. Defaults to the legacy
            ``_CONFIDENCE_THRESHOLD`` (0.75) so the existing fallback path is
            unchanged; the semantic-PRIMARY path passes the tuned RoutingSettings
            value.
        margin: Minimum gap between the top and 2nd-best centroid score required
            to accept a route. ``0.0`` (legacy default) disables the gate
            entirely — zero behaviour change for existing callers.
        drop_llm_only_centroid: When True, use the PRIMARY scoring mode —
            max-over-examples (nearest-example) against the wallet/web_search example
            sets, with llm_only as pure fall-through. Defaults to False → legacy mean
            centroids (incl. llm_only). The name is kept for call-site compatibility.

        raise_on_unavailable: When True, raise `EmbeddingsUnavailable` instead of
            returning None in the cases where the router could not RUN (no
            embeddings model, example vectors unbuildable, query embedding
            failed). Low confidence still returns None. Defaults to False so
            every existing caller keeps the old contract.

    Returns:
        Intent label ("wallet", "web_search", "llm_only"), or None when the router
        ran and found no confident route. Whether "could not run" also returns
        None is controlled by `raise_on_unavailable` — see that argument. Do not
        re-merge the two: a caller that cannot tell an outage from an answer is
        how the out-of-surface guard came to be silently disarmed.
    """
    emb_model = _get_embeddings_model()
    if emb_model is None:
        if raise_on_unavailable:
            raise EmbeddingsUnavailable("no embeddings model")
        return None

    # Scoring source depends on mode:
    #   primary (drop_llm_only_centroid): {intent: [example_vec, ...]} → max similarity
    #   legacy:                            {intent: centroid_vec}       → cosine to centroid
    source = (
        _ensure_example_vecs_primary(emb_model)
        if drop_llm_only_centroid
        else _ensure_centroids(emb_model)
    )
    if not source:
        # Example vectors / centroids could not be built — an outage, not a verdict.
        if raise_on_unavailable:
            raise EmbeddingsUnavailable("example vectors unavailable")
        return None

    try:
        # Guard against embedder input overflow (Ollama HTTP 500) for very long
        # first messages. Centroid phrases are short by construction; only the
        # user query needs capping.
        from ..config import get_settings
        from ..memory_text_utils import truncate_for_embedding
        safe_query = truncate_for_embedding(query, get_settings().memory.embedding_max_tokens)
        query_vec = emb_model.embed_query(safe_query)
    except Exception as e:
        logger.debug(f"[SemanticRouter] Query embedding failed: {e}")
        if raise_on_unavailable:
            raise EmbeddingsUnavailable(f"query embedding failed: {e}") from e
        return None

    # Filter to only intents this persona can actually use
    usable: List[str] = []
    if can_use_wallet and "wallet" in source:
        usable.append("wallet")
    if can_use_brave and "web_search" in source:
        usable.append("web_search")
    if "llm_only" in source:  # only present in legacy centroids
        usable.append("llm_only")

    if not usable:
        return None

    # Rank by similarity. Primary mode: max cosine over the intent's example
    # vectors (nearest-example). Legacy mode: cosine to the intent's centroid.
    scores: List[Tuple[float, str]] = []
    for intent in usable:
        if drop_llm_only_centroid:
            sim = max(_cosine_similarity(query_vec, e) for e in source[intent])
        else:
            sim = _cosine_similarity(query_vec, source[intent])
        scores.append((sim, intent))
    scores.sort(reverse=True)

    best_score, best_intent = scores[0]
    logger.debug(
        f"[SemanticRouter] Top matches: {[(f'{s:.3f}', i) for s, i in scores[:3]]}"
    )

    if best_score < threshold:
        logger.debug(
            f"[SemanticRouter] Below confidence threshold ({best_score:.3f} < {threshold}) — falling back to llm"
        )
        return None

    # Margin gate: when two intents score close together the top pick is noise.
    # Disabled when margin == 0.0 (legacy default) — zero behaviour change.
    if margin > 0.0 and len(scores) >= 2:
        gap = scores[0][0] - scores[1][0]
        if gap < margin:
            logger.debug(
                f"[SemanticRouter] Margin gate rejected: gap={gap:.3f} < {margin} "
                f"(top={best_intent}@{scores[0][0]:.3f}, 2nd={scores[1][1]}@{scores[1][0]:.3f})"
            )
            return None

    if best_intent == "llm_only":
        return None  # Treat as NEEDS_NEITHER — no MCP needed

    logger.info(
        f"[SemanticRouter] Semantic route: '{best_intent}' (confidence={best_score:.3f})"
    )
    return best_intent


def warm_centroids(include_primary: bool = False) -> bool:
    """Pre-compute and cache intent centroids at startup.

    Args:
        include_primary: Also warm the primary-mode centroid set (no llm_only).
            Pass True so the first real request does not pay the centroid-build
            cost inline. (The semantic router is always primary.)

    Returns:
        True if the legacy centroids were successfully built, False otherwise.
    """
    emb_model = _get_embeddings_model()
    if emb_model is None:
        return False
    centroids = _ensure_centroids(emb_model)
    if include_primary:
        _ensure_example_vecs_primary(emb_model)
    return bool(centroids)
