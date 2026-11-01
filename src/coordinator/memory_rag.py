"""RAG-based episodic memory for conversations.

This module provides semantic search over conversation history using FAISS
vector database and Ollama embeddings for local-first AI memory.
"""

from __future__ import annotations

from typing import List, Optional, Tuple, Dict, Any
import logging

from .schemas import memory_text
from .memory_text_utils import prepare_for_embedding, truncate_for_embedding

try:
    # Prefer langchain-ollama (modern): it calls Ollama's /api/embed endpoint
    # and forwards num_ctx, so we can actually use bge-m3's 8192-token window.
    # The legacy langchain-community class uses /api/embeddings, which ignores
    # num_ctx and returns HTTP 500 on inputs over the default 2048 context.
    try:
        from langchain_ollama import OllamaEmbeddings
        _USING_MODERN_OLLAMA = True
    except ImportError:
        from langchain_community.embeddings import OllamaEmbeddings  # type: ignore[assignment]
        _USING_MODERN_OLLAMA = False
    from langchain_community.vectorstores import FAISS
    import faiss
except ImportError as e:
    raise ImportError(
        "Required packages not installed. Run: pip install faiss-cpu langchain-community langchain-ollama"
    ) from e

logger = logging.getLogger(__name__)


class EpisodicMemoryRAG:
    """Semantic search over conversation history using FAISS vector database.

    This class enables personas to find relevant past messages semantically,
    not just chronologically. Uses local Ollama embeddings for privacy.

    Features:
    - Local-first (no external API calls)
    - Semantic similarity search
    - Per-session vector stores
    - CPU FAISS (faiss-cpu); the CUDA GPU branch is inert on Apple Silicon
    """

    def __init__(self, embedding_model: Optional[str] = None):
        """Initialize the RAG memory system.

        Args:
            embedding_model: Ollama model for embeddings (default: from config)
        """
        from .config import get_settings
        settings = get_settings()
        if embedding_model is None:
            embedding_model = settings.memory.embedding_model
        # Input window of the embedding model. Text is chunked/truncated below a
        # safety margin of this before embedding so we never trip Ollama's
        # HTTP 500 overflow (which silently broke semantic memory every chat).
        self._embed_max_tokens = settings.memory.embedding_max_tokens
        self._embed_overlap = settings.memory.embedding_chunk_overlap_tokens
        # num_ctx tells Ollama to allocate the model's full window (bge-m3=8192)
        # instead of its 2048 default — only the modern langchain-ollama client
        # forwards it to /api/embed.
        embed_kwargs = {"model": embedding_model, "base_url": settings.ollama.base}
        if _USING_MODERN_OLLAMA:
            embed_kwargs["num_ctx"] = self._embed_max_tokens
        self.embeddings = OllamaEmbeddings(**embed_kwargs)
        self.vectorstores: Dict[str, FAISS] = {}  # session_id -> FAISS instance
        # Phase-2: global (non-session) lore corpus index for on-demand retrieval.
        # Built once at startup from the lore wiki; reuses self.embeddings (no new embedder).
        self.lore_store: Optional[FAISS] = None
        # faiss.get_num_gpus() is CUDA-only — always 0 with faiss-cpu. On Apple Silicon
        # GPU inference goes through native Ollama, not FAISS-on-Metal. The GPU transfer
        # branch below is kept for Linux/NVIDIA deployments; inert on Mac.
        self.use_gpu = getattr(faiss, "get_num_gpus", lambda: 0)() > 0

        if self.use_gpu:
            logger.info(f"🚀 FAISS GPU acceleration enabled: {faiss.get_num_gpus()} device(s)")
        else:
            logger.info("⚙️ FAISS running on CPU (GPU not available)")

    def index_session(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Index all messages from a session for semantic search.

        Creates a FAISS vector store from conversation messages, enabling
        fast similarity search for relevant context retrieval.

        Args:
            session_id: Chat session ID
            messages: List of message dicts with keys: id, role, content, timestamp
        """
        if not messages:
            logger.warning(f"[RAG] No messages to index for session {session_id}")
            return

        # Format messages for indexing
        pairs = []
        for i, msg in enumerate(messages):
            # Format: "user: message content" or "assistant: response content"
            text = f"{msg['role']}: {memory_text(msg)}"
            metadata = {
                "session_id": session_id,
                "message_id": msg.get("id"),
                "role": msg["role"],
                "timestamp": msg.get("timestamp"),
                "index": i
            }
            pairs.append((text, metadata))

        # Guard: normalize, drop empties, and chunk oversized messages so no
        # input exceeds the embedder's window (else Ollama returns HTTP 500).
        texts, metadatas = prepare_for_embedding(
            pairs, self._embed_max_tokens, self._embed_overlap
        )
        if not texts:
            logger.warning(f"[RAG] No embeddable text for session {session_id} after prep")
            return

        # Create FAISS vector store
        try:
            vectorstore = FAISS.from_texts(
                texts=texts,
                embedding=self.embeddings,
                metadatas=metadatas
            )

            # Move to GPU if available (10-50x speedup)
            if self.use_gpu:
                try:
                    gpu_resources = faiss.StandardGpuResources()
                    vectorstore.index = faiss.index_cpu_to_gpu(
                        gpu_resources,
                        0,  # GPU device ID
                        vectorstore.index
                    )
                    logger.info(f"✅ Session {session_id} FAISS index running on GPU")
                except Exception as e:
                    logger.warning(f"GPU transfer failed, using CPU: {e}")

            self.vectorstores[session_id] = vectorstore
            logger.info(f"[RAG] Indexed {len(texts)} messages for session {session_id}")

        except Exception as e:
            logger.error(f"[RAG] Failed to index session {session_id}: {e}")
            raise

    def search_memory(
        self,
        session_id: str,
        query: str,
        k: int = 15,  # Optimized via hyperparameter tuning (Jan 2026): k=15 for best recall
        min_relevance: float = 0.5  # Cosine true-negative floor (bge-m3, Jun 2026): recall-leaning
    ) -> List[Tuple[Dict[str, Any], float]]:
        """Search conversation memory semantically.

        Uses vector similarity to find relevant past messages, even if they
        don't share exact keywords with the query.

        Args:
            session_id: Chat session ID
            query: Search query (typically the user's current message)
            k: Number of results to return (default: 15)
               - Corpus-driven, not embedder-driven; k=15 is a robust default
            min_relevance: Minimum cosine similarity (default: 0.5)
               - bge-m3 emits normalized embeddings → score IS cosine in [-1, 1]
               - 0.5 is a true-negative FLOOR (recall-leaning chat memory):
                 surface memories unless even the top hit is clearly unrelated;
                 the LLM discards marginal hits. Missing a real memory is worse
                 than over-retrieving. Formal re-tune is a follow-up (eval harness).

        Returns:
            List of (message_dict, relevance_score) tuples, sorted by relevance
        """
        if session_id not in self.vectorstores:
            logger.warning(f"[RAG] Session {session_id} not indexed yet")
            return []

        vectorstore = self.vectorstores[session_id]

        # Guard: cap the query to one embeddable chunk (a query must map to a
        # single vector). Pathologically long queries would otherwise 500.
        query = truncate_for_embedding(query, self._embed_max_tokens)
        if not query:
            return []

        try:
            # Perform similarity search
            results = vectorstore.similarity_search_with_score(
                query=query,
                k=k
            )

            # FAISS IndexFlatL2 returns SQUARED L2 distance D. bge-m3 emits
            # unit-normalized vectors, for which cosine similarity is exact:
            # D = 2 - 2·cos  =>  cos = 1 - D/2  (verified against dot-product).
            # (langchain's COSINE distance_strategy is unreliable across
            # versions, so we apply the identity directly.) Clamp to [-1, 1] to
            # stay robust if a future embedder is not perfectly normalized.
            filtered_results = []
            for doc, distance in results:
                similarity = max(-1.0, min(1.0, 1.0 - float(distance) / 2.0))

                if similarity >= min_relevance:
                    message_data = {
                        "role": doc.metadata["role"],
                        "content": doc.page_content.split(": ", 1)[1] if ": " in doc.page_content else doc.page_content,
                        "timestamp": doc.metadata.get("timestamp"),
                        "index": doc.metadata["index"],
                        "message_id": doc.metadata.get("message_id")
                    }
                    filtered_results.append((message_data, similarity))

            logger.info(
                f"[RAG] Found {len(filtered_results)}/{len(results)} relevant memories "
                f"for query: '{query[:50]}...'"
            )

            return filtered_results

        except Exception as e:
            logger.error(f"[RAG] Search failed for session {session_id}: {e}")
            return []

    # ------------------------------------------------------------------
    # Phase-2: global lore corpus (on-demand lore retrieval)
    # ------------------------------------------------------------------
    def index_lore_corpus(self, entity_ids: Optional[List[str]] = None) -> None:
        """Build the global lore FAISS store from the wiki, reusing self.embeddings.

        Indexes one vector per entity body, with frontmatter metadata attached.
        Idempotent-ish: rebuilds self.lore_store each call. Safe no-op if empty.

        Args:
            entity_ids: Optional explicit list; defaults to all wiki entity_ids.
        """
        from . import lore_loader

        if entity_ids is None:
            entity_ids = lore_loader.get_all_entity_ids()

        pairs: List[Tuple[str, Dict[str, Any]]] = []
        for eid in entity_ids:
            meta = lore_loader.load_entity_with_metadata(eid)
            if not meta or not meta.get("body"):
                continue
            pairs.append((
                meta["body"],
                {
                    "entity_id": meta["entity_id"],
                    "entity_type": meta["entity_type"],
                    "canon": meta["canon"],
                },
            ))

        texts, metadatas = prepare_for_embedding(
            pairs, self._embed_max_tokens, self._embed_overlap
        )
        if not texts:
            logger.warning("[RAG] Lore corpus empty after prep — lore_store not built")
            return
        try:
            self.lore_store = FAISS.from_texts(
                texts=texts, embedding=self.embeddings, metadatas=metadatas
            )
            logger.info(f"[RAG] Lore corpus indexed: {len(texts)} chunks "
                        f"from {len(entity_ids)} entities")
        except Exception as e:
            logger.error(f"[RAG] Lore corpus indexing failed: {e}")
            self.lore_store = None

    def search_lore(
        self,
        query: str,
        k: int = 5,
        min_relevance: float = 0.5,
        entity_type_filter: Optional[str] = None,
        canon_only: bool = False,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """Semantic search over the global lore corpus.

        Returns ``[(metadata_with_body, cosine_score)]`` sorted by relevance.
        Returns ``[]`` if the lore store is not built. Uses the same exact-cosine
        identity (cos = 1 - D/2) as conversation search.

        Args:
            query: Search query (typically the user's current message).
            k: Max entries to return.
            min_relevance: Cosine floor (bge-m3 normalized → score is cosine).
            entity_type_filter: If set, keep only this entity_type.
            canon_only: If True, drop non-canon entities (avoid surfacing drafts).
        """
        if self.lore_store is None:
            return []
        query = truncate_for_embedding(query, self._embed_max_tokens)
        if not query:
            return []
        try:
            results = self.lore_store.similarity_search_with_score(query=query, k=k)
        except Exception as e:
            logger.error(f"[RAG] Lore search failed: {e}")
            return []

        out: List[Tuple[Dict[str, Any], float]] = []
        for doc, distance in results:
            similarity = max(-1.0, min(1.0, 1.0 - float(distance) / 2.0))
            if similarity < min_relevance:
                continue
            md = doc.metadata
            if entity_type_filter and md.get("entity_type") != entity_type_filter:
                continue
            if canon_only and not md.get("canon", True):
                continue
            out.append((
                {
                    "entity_id": md.get("entity_id"),
                    "entity_type": md.get("entity_type", ""),
                    "canon": md.get("canon", True),
                    "body": doc.page_content,
                },
                similarity,
            ))
        return out

    def get_relevant_context(
        self,
        session_id: str,
        query: str,
        max_messages: int = 15  # Optimized default (was 10)
    ) -> List[Dict[str, Any]]:
        """Get relevant conversation context for a query.

        Retrieves semantically relevant past messages and sorts them
        chronologically for natural context flow.

        Args:
            session_id: Chat session ID
            query: Current user query
            max_messages: Maximum messages to retrieve (default: 15, optimized)

        Returns:
            List of relevant message dicts (sorted chronologically)
        """
        results = self.search_memory(session_id, query, k=max_messages)

        if not results:
            return []

        # Extract messages and sort by conversation order (chronological)
        messages = []
        for message_data, relevance in results:
            message_data["relevance"] = relevance
            messages.append(message_data)

        # Sort by index (chronological order)
        messages.sort(key=lambda x: x["index"])

        logger.debug(
            f"[RAG] Retrieved {len(messages)} relevant messages "
            f"(relevance range: {min(m['relevance'] for m in messages):.2f}-"
            f"{max(m['relevance'] for m in messages):.2f})"
        )

        return messages

    def update_session(
        self,
        session_id: str,
        new_messages: List[Dict[str, Any]],
        full_history: List[Dict[str, Any]]
    ) -> None:
        """Update vector store with new messages using incremental FAISS updates.

        Efficiently updates the index when new messages are added to a session.
        Uses incremental add_texts() for O(k) performance instead of O(n) rebuild.

        Performance:
        - Old: O(n) rebuild entire index on every message
        - New: O(k) add only new messages (10-100x faster for long sessions)

        Args:
            session_id: Chat session ID
            new_messages: Newly added messages (only the new ones)
            full_history: Complete conversation history including new messages
        """
        if not new_messages:
            logger.debug(f"[RAG] No new messages to index for session {session_id}")
            return

        # Check if we need to create index from scratch (first time)
        if session_id not in self.vectorstores:
            logger.info(f"[RAG] Creating initial index for session {session_id}")
            self.index_session(session_id, full_history)
            return

        # Incremental update: add only new messages (O(k) instead of O(n))
        vectorstore = self.vectorstores[session_id]

        # Calculate starting index for new messages (continuation of existing index)
        existing_message_count = len(full_history) - len(new_messages)

        # Format new messages for indexing
        pairs = []
        for i, msg in enumerate(new_messages):
            # Format: "user: message content" or "assistant: response content"
            text = f"{msg['role']}: {memory_text(msg)}"
            metadata = {
                "session_id": session_id,
                "message_id": msg.get("id"),
                "role": msg["role"],
                "timestamp": msg.get("timestamp"),
                "index": existing_message_count + i  # Sequential index
            }
            pairs.append((text, metadata))

        # Guard: same chunk/normalize pass as index_session (avoids HTTP 500).
        texts, metadatas = prepare_for_embedding(
            pairs, self._embed_max_tokens, self._embed_overlap
        )
        if not texts:
            logger.debug(f"[RAG] No embeddable new text for session {session_id} after prep")
            return

        try:
            # Incremental add (fast O(k) operation)
            vectorstore.add_texts(
                texts=texts,
                metadatas=metadatas
            )

            logger.info(
                f"[RAG] ✅ Incremental update: added {len(new_messages)} new messages "
                f"to session {session_id} (total: {len(full_history)} messages)"
            )

        except Exception as e:
            # Fallback: rebuild index if incremental update fails
            logger.warning(
                f"[RAG] Incremental update failed for session {session_id}, "
                f"falling back to full rebuild: {e}"
            )
            self.index_session(session_id, full_history)

    def clear_session(self, session_id: str) -> None:
        """Clear vector store for a session.

        Removes the FAISS index to free memory when a session is deleted.

        Args:
            session_id: Chat session ID to clear
        """
        if session_id in self.vectorstores:
            del self.vectorstores[session_id]
            logger.info(f"[RAG] Cleared vector store for session {session_id}")

    def get_stats(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Get statistics about indexed sessions.

        Args:
            session_id: Specific session (or None for all sessions)

        Returns:
            Dictionary with indexing statistics
        """
        if session_id:
            if session_id not in self.vectorstores:
                return {"error": f"Session {session_id} not indexed"}

            vectorstore = self.vectorstores[session_id]
            return {
                "session_id": session_id,
                "indexed_messages": vectorstore.index.ntotal,
                "using_gpu": self.use_gpu
            }
        else:
            return {
                "total_sessions": len(self.vectorstores),
                "sessions": list(self.vectorstores.keys()),
                "using_gpu": self.use_gpu,
                "total_indexed_messages": sum(
                    vs.index.ntotal for vs in self.vectorstores.values()
                )
            }
