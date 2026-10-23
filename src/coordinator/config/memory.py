from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class MemorySettings(BaseSettings):
    """Memory and RAG configuration (Phase 3)."""

    embedding_model: str = Field(
        default="bge-m3:latest",
        description="Ollama embedding model for RAG semantic search "
                    "(bge-m3: 8192-token native context, dense+sparse)",
        alias="MEMORY_EMBEDDING_MODEL"
    )
    embedding_max_tokens: int = Field(
        default=8192,
        ge=256,
        le=40960,
        description="Native input window of the embedding model. Text is "
                    "chunked/truncated below a safety margin of this before "
                    "embedding to avoid Ollama HTTP 500 overflow errors. "
                    "(bge-m3=8192, nomic-embed-text=2048, qwen3-embedding=32768)",
        alias="MEMORY_EMBEDDING_MAX_TOKENS"
    )
    embedding_chunk_overlap_tokens: int = Field(
        default=128,
        ge=0,
        le=1024,
        description="Token overlap between chunks when an oversized message is "
                    "split before embedding (preserves cross-chunk context)",
        alias="MEMORY_EMBEDDING_CHUNK_OVERLAP_TOKENS"
    )
    summarization_interval: int = Field(
        default=30,
        ge=5,
        le=100,
        description="Number of messages before triggering auto-summarization",
        alias="MEMORY_SUMMARIZATION_INTERVAL"
    )
    flush_enabled: bool = Field(
        default=True,
        alias="MEMORY_FLUSH_ENABLED",
        description=(
            "Extract a session's facts when the session ENDS, not only when it "
            "crosses `fact_extraction_interval` user turns. Measured 2026-10-23 "
            "on the live store: 287 of 292 real sessions (98.3%) never reach 10 "
            "user turns, median 1 and mean 2.0, so the interval gate alone can "
            "never fire for almost every conversation that actually happens. A "
            "fresh 3-fact / 3-turn session stored 0 of 3 in SQLite and 0 of 3 "
            "in the graph. Lowering the interval is NOT the fix: "
            "OLLAMA_NUM_PARALLEL=1 serialises extraction against her "
            "generation, so halving it doubles GPU contention with her replies."
        ),
    )
    flush_sweep_seconds: int = Field(
        default=900, ge=0, le=86400,
        alias="MEMORY_FLUSH_SWEEP_SECONDS",
        description=(
            "How often the idle sweeper looks for sessions to flush. 0 disables "
            "the sweeper while KEEPING the /reset and delete triggers. The "
            "sweeper is the load-bearing one: the Telegram gateway has no /new, "
            "no /end and no idle timeout, and at a median of 1 user turn the "
            "dominant way a conversation ends is silence."
        ),
    )
    flush_idle_seconds: int = Field(
        default=1800, ge=60, le=604800,
        alias="MEMORY_FLUSH_IDLE_SECONDS",
        description=(
            "How long a session must be untouched before the sweeper treats it "
            "as over. Keyed on `chat_sessions.updated_at`, which is maintained "
            "on every write by SessionRepository.update_session_timestamp."
        ),
    )
    flush_sweep_max: int = Field(
        default=10, ge=1, le=200,
        alias="MEMORY_FLUSH_SWEEP_MAX",
        description=(
            "Sessions flushed per sweep pass. Bounded because the worker queue "
            "is 64 deep and `enqueue()` returns False when full — an unbounded "
            "pass would silently drop most of its own work, which looks like "
            "coverage and is not."
        ),
    )
    flush_max_user_turns: int = Field(
        default=20, ge=1, le=200,
        alias="MEMORY_FLUSH_MAX_USER_TURNS",
        description=(
            "Cap on the user turns one flush covers. Trimmed from the OLDEST "
            "end, so the cursor advances only over what was actually sent to "
            "the extractor; trimming the newest end would move the cursor past "
            "turns nothing read, which is the one failure the cursor exists to "
            "prevent. The remainder is picked up by the next trigger."
        ),
    )
    fact_extraction_interval: int = Field(
        default=10,
        ge=5,
        le=50,
        description="Number of messages before triggering fact extraction",
        alias="MEMORY_FACT_EXTRACTION_INTERVAL"
    )
    prewarm_sessions: int = Field(
        default=10,
        ge=0,
        le=200,
        description="ADR-006 M1: number of most-recently-updated sessions to "
                    "pre-index into the FAISS store at startup (background daemon "
                    "thread). The index is otherwise rebuilt lazily from SQLite on "
                    "first chat per session — pre-warming only removes that one-time "
                    "cold-start re-index latency after a restart. 0 disables.",
        alias="MEMORY_PREWARM_SESSIONS"
    )
    context_inject_enabled: bool = Field(
        default=False,
        description="ADR-006 M0: pass the session context blocks (cross-session "
                    "user profile, emotional state, unlocked/on-demand lore, seeker "
                    "rank, capability) through to the LLM system prompt. These are "
                    "built by handle_session_chat but were historically DROPPED "
                    "(ChatBody carried no system prompt). "
                    "⚠️ GATE 0 (2026-06-28) FAILED ON VOICE (full six-block inject: "
                    "distinctiveness 0.768→0.643). ⚠️ GATE 0.1 (2026-07-03) ALSO "
                    "FAILED: M0.1 selective injection (user-profile + emotional only, "
                    "the current code path) still homogenizes — 0.768→0.679/0.696 "
                    "across two independent runs, eeva 0.625→0.25/0.375. Identically-"
                    "formatted blocks pull every persona toward the same injected "
                    "text; block CHOICE is not the fix. ADR-006 M1 (2026-07-04) now "
                    "PROVIDES that per-persona framing: prose narrative variants "
                    "wrapped in a non-echoable <remembered> frame (context_framing.py). "
                    "M5 GATE PASSED 2026-07-05: full-7 attribution OFF 0.786 → ON 0.839 "
                    "(+0.054), flatness 0.0 — first injection to match-or-beat (Gate 0 "
                    "−0.125, Gate 0.1 −0.07/−0.09 both FAILed). Eligible to flip; kept "
                    "default OFF for a live soak + instant revert. See ADR-006 Phase 1.",
        alias="MEMORY_CONTEXT_INJECT"
    )
    context_max_tokens: int = Field(
        default=2000,
        ge=0,
        le=12000,
        description="ADR-006 M0: token budget cap for the injected session context "
                    "blocks. Highest-priority blocks (user profile, emotional state) "
                    "are kept first; lower-priority blocks (lore, rank, capability) "
                    "are dropped when the budget is exceeded, protecting the context "
                    "window. 0 = no cap.",
        alias="MEMORY_CONTEXT_MAX_TOKENS"
    )
    facts_enabled: bool = Field(
        default=False,
        description="ADR-006 Phase 1 (M3/M4): enable the ontology-lite fact store — "
                    "async triplet extraction on write (memory_facts) + framed fact "
                    "retrieval on read. Extraction runs fully off the interactive path "
                    "(background worker) at the summarization cadence; retrieval injects "
                    "through the same per-persona <remembered> framing as M1. M5 gate "
                    "PASSED; kept default OFF pending a live soak. Independent of "
                    "MEMORY_CONTEXT_INJECT.",
        alias="MEMORY_FACTS_ENABLED"
    )
    facts_retrieval_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="ADR-006 M4: top-k facts retrieved per turn once the active-fact "
                    "count exceeds facts_inject_all_threshold; below that, inject all.",
        alias="MEMORY_FACTS_RETRIEVAL_K"
    )
    facts_inject_all_threshold: int = Field(
        default=15,
        ge=0,
        le=200,
        description="ADR-006 M4: below this many active facts, inject them all and skip "
                    "vector search (retrieval is wasted complexity at low fact counts); "
                    "at/above it, semantically retrieve top-k.",
        alias="MEMORY_FACTS_INJECT_ALL_THRESHOLD"
    )

    #: The ONE operator this deployment serves. Empty keeps the legacy behaviour.
    #:
    #: 🔴 WHY THIS EXISTS. Identity was recovered by looking up an LLM-EXTRACTED NAME
    #: and minting `user_{uuid4}` when that failed, so every time the roleplay changed
    #: what she called him he became a NEW PERSON with an empty memory. Measured on the
    #: live database 2026-10-18: **397 user profiles, every one with exactly one
    #: session.** 389 of them had no extracted name at all; "Ben" held 475 messages
    #: because he once asked her to use it; his real name held 2 profiles. **130 of the
    #: 397 hold at least one fact** — real accumulated knowledge about one person,
    #: scattered across 130 identities none of which she can reach.
    #:
    #: Memory injection could not have worked while this held, whatever the retrieval
    #: or the rendering did, because there was nothing to retrieve FROM across sessions.
    #:
    #: Default empty so this is opt-in and instantly reversible; a real multi-user
    #: deployment leaves it unset and keeps the name-resolution path.
    single_operator_user_id: str = Field(
        default="",
        alias="SINGLE_OPERATOR_USER_ID",
    )

    #: ADR-022 / the numbered recall frame. Render recalled memory as a NUMBERED
    #: list with a find-it-first instruction instead of the ADR-006 M1 prose
    #: narrative. Measured 2026-10-18 over 160 generations, 16 turns of real
    #: conversation per probe, probe as the unit:
    #:
    #:     arm                   invents   wrongly refuses
    #:     prose block (today)     0.875             0.375
    #:     instruction only        0.000             0.708
    #:     NUMBERED + find-first   0.000             0.208
    #:
    #: Beats the baseline on BOTH axes — total error −78%, 10 probes down and 0
    #: up, p=0.0020 on all three exact tests, zero citation leakage. It converts
    #: a judgement ("do I know this?") into a lookup ("is it in the list?").
    #:
    #: ⚠️ Requires `context_inject_enabled`; it changes the RENDERING of that
    #: injection rather than being a second source. On its own it does nothing,
    #: so the two flags are not independent and the ordering below enforces it.
    #:
    #: ⚠️ It also contradicts ADR-006 Gate 0.1, which tied identically-formatted
    #: injected blocks to a distinctiveness drop 0.768 → 0.643. The ADR-022 run
    #: measured accuracy and NOT voice; replies came back 31% shorter. The
    #: operator accepted that and skipped the distinctiveness run — recorded in
    #: `context_framing.frame_numbered_facts` as a judgement, not a measurement.
    recall_numbered_facts: bool = Field(
        default=False,
        alias="MEMORY_RECALL_NUMBERED",
    )

    #: ADR-022 + the identity graph. Populate the numbered frame's SECOND
    #: section, "Things you have told him about yourself", from the identity
    #: graph. That section has existed in `context_framing.frame_numbered_facts`
    #: since the frame was written and NO call site has ever passed it anything —
    #: verified, one call site, three positional args.
    #:
    #: Measured on the live backend 2026-10-21, 3 reps: self-recall 8/15 with 5
    #: outright REFUSALS, all of them her own hair colour, eye colour and height.
    #: `blue eyes`, `155`, `petite` and `red hair` were ALL already in the
    #: 4377-char prompt, so this is not missing data — it is the same
    #: present-but-unfindable condition the numbered frame fixed for user facts.
    #: One of those 5 refusals came from the GROUNDEDNESS GATE abstaining on a
    #: question about her own height, which is why the gate is given these facts
    #: as grounding too.
    #:
    #: ⚠️ Requires `recall_numbered_facts`, which requires
    #: `context_inject_enabled`. It populates a section of that frame; it is not
    #: an independent injection.
    self_facts_from_graph: bool = Field(
        default=False,
        alias="MEMORY_SELF_FACTS",
    )

    #: Arm C of the self-facts A/B: also list her STRONG expertise, not just
    #: appearance. OFF because appearance is the measured gap and expertise is
    #: not — `idn.cq02` is documented as "the one question the existing
    #: identity() read already fully satisfies", and expertise already renders in
    #: a dedicated prompt block. ⚠️ CAVI (arXiv 2605.09443) finds the larger of
    #: two information sources dominates the smaller, and the other section holds
    #: 32 user facts, so this doubles the self section against a measured-working
    #: one. Flag exists so the A/B can test it rather than assume it.
    self_facts_include_expertise: bool = Field(
        default=False,
        alias="MEMORY_SELF_FACTS_EXPERTISE",
    )

    #: ADR-021: read the operator's facts from the GRAPH rather than from
    #: `user_profiles.profile_data["facts"]`.
    #:
    #: The SQLite store is a JSON blob of BARE STRINGS, so there is nowhere to
    #: record who a fact is ABOUT or who ASSERTED it. Measured 2026-10-21: after
    #: the self-facts section shipped she recited her own appearance and the
    #: extractor filed it under HIM — `Assistant's eyes are a deep ocean blue`
    #: sat in the operator's fact list and would have been read back as "things
    #: you know about him". 38 such rows in ~20 minutes. With no subject field
    #: that contamination is unrepresentable, let alone preventable.
    #:
    #: In the graph each fact carries `origin` and `asserted_by` and points at an
    #: `:Operator` through `ABOUT` — the schema `cmp.cq01`/`cmp.cq02` already
    #: demanded and nothing built. `asserted_by` comes from the SQLite `role`
    #: column and never from a model (ADR-021 Phase 0).
    #:
    #: ⚠️ Falls back to SQLite on ANY failure — flag off, no driver, empty read.
    #: The graph becoming the system of record for facts is a migration, not a
    #: big bang, and a turn must never fail because a read did.
    facts_from_graph: bool = Field(
        default=False,
        alias="MEMORY_FACTS_FROM_GRAPH",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }
