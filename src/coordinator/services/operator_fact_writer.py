"""Turn a window of conversation into graph facts, with provenance.

The handler the `OperatorFactWorker` runs. Extraction, dedup and the graph write
in one place, off the request thread.

🔴 **`asserted_by` IS "user", AND THE JUSTIFICATION IS WINDOW-LEVEL, NOT
PER-FACT.** `FactExtractor.extract_facts` returns bare strings with no
provenance — no source message id, no role, no quote span — so a given fact
cannot be traced to a given row. What makes `"user"` sound anyway is that
`_format_messages` **physically redacts** every non-user turn before the model
sees it, replacing the content with a marker. So every fact in a window
provably derives from user rows, because nothing else was there to derive from.

That is a weaker claim than per-fact provenance and it is recorded as such. The
stronger version needs the extractor to return a quote span — `TripletExtractor`
already does, and `fact_write_policy.apply_triples` already accepts a
`message_id` that **nothing ever passes** — which is ADR-021's Phase 4 and not
this module's job.

⚠️ `asserted_by` never comes from a model. The provenance research found Mem0 to
be the only production system of six with a speaker field, and its LLM-judged
version failed in exactly our shape — roles stripped upstream of extraction so
everything defaulted to the user (mem0ai/mem0#5642).

🔴 **DEDUP IS REQUIRED AND ITS THRESHOLD IS NOT BORROWED.** `add_fact` has no
dedup, so writing every N turns would duplicate facts forever. The dedup here is
**exact, case-insensitive, whitespace-normalised containment** against what the
graph already holds — deliberately not a similarity threshold. Graphiti's 0.6 /
top-15 constants are confirmed unfitted from their own merge history (PR #929,
#936 carry no empirical rationale), nobody publishes a fitting method for this
problem class, and this repo has measured a borrowed threshold scoring 31% where
a fitted one scored 57%. An exact rule has no free parameter to get wrong; it
under-dedups paraphrases, which is a visible cost rather than a silent one, and
the paraphrase case is ADR-021's embedding+full-text work.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .operator_fact_worker import FactJob

logger = logging.getLogger(__name__)

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Lower, collapse whitespace, strip terminal punctuation.

    ⚠️ The WRITE side of the SQLite store dedups on an exact case-SENSITIVE
    string while the READ side (`get_recall_facts`) dedups case-INSENSITIVELY —
    they disagree, which is one source of the duplicates already in there. This
    normalisation matches the read side, so the graph does not inherit the
    mismatch.
    """
    return _WS.sub(" ", (text or "").strip().lower()).rstrip(".,;!?")


def make_handler(repo_factory, settings_factory, profile_repo_factory=None,
                 episode_repo_factory=None):
    """Build the worker's handler. Factories so nothing is captured at import.

    🔴 ONE EXTRACTION, BOTH WRITES. The first version of this enqueued the worker
    and LEFT the inline extraction in place, so extraction ran TWICE per window:
    the 5.54 s stayed on the request thread AND a second LLM call followed it.
    Measured consequence — the two runs disagree, because they are two
    independent samplings: a "bouldering" fact landed in SQLite and NOT in the
    graph on the same window, which looked like a graph write bug and was not.

    So the worker owns extraction and writes BOTH stores from the one result.
    SQLite is still written deliberately: the graph is becoming the system of
    record for facts but the migration is unfinished, and `_operator_facts`
    falls back to SQLite on any graph failure — dropping the SQLite write would
    make that fallback fall back to nothing.
    """

    def handle(job: FactJob) -> int:
        from ..fact_extractor import FactExtractor
        from ..llm_client import create_llm_client
        from .fact_window import cap_window_from_oldest, window_since_cursor

        mem = settings_factory().memory
        if not job.user_id or not job.messages:
            return 0

        messages = job.messages
        advance_to, advance_id = job.cursor_to, job.cursor_message_id
        episode_repo = episode_repo_factory() if episode_repo_factory else None

        if job.use_cursor:
            # 🔴 THE COMPARE-AND-ADVANCE LIVES HERE, at the single consumer.
            # The worker is one daemon thread, so this is the only point at
            # which two overlapping triggers (a sweep and a /reset, say) are
            # serialised. Doing it at the enqueue site is Mem0 #6515's race.
            if episode_repo is None:
                # Extracting without being able to advance means repeating this
                # window forever, every trigger, at 5.54 s a time. Refuse.
                logger.warning(
                    "[OperatorFactWriter] cursor requested for session %s but no "
                    "episode repository — refusing to extract, because a window "
                    "that cannot be marked done would be re-extracted forever",
                    job.session_id[:8],
                )
                return 0

            stored = episode_repo.fact_cursor(job.user_id, job.session_id) or {}
            stored_at = stored.get("at") or ""

            if stored_at and job.cursor_to and job.cursor_to <= stored_at:
                logger.info(
                    "[OperatorFactWriter] session %s (%s): already covered to %s "
                    "— skipping", job.session_id[:8], job.reason, stored_at,
                )
                return 0

            messages, advance_to, advance_id, has_new = window_since_cursor(
                job.messages, stored_at or None)
            if not has_new:
                # The window is non-empty (the boundary second is re-included by
                # design) but nothing new arrived. Extracting it would burn an
                # LLM call to rediscover stored facts, and under
                # OLLAMA_NUM_PARALLEL=1 that makes her slower to answer.
                logger.info(
                    "[OperatorFactWriter] session %s (%s): nothing new since %s",
                    job.session_id[:8], job.reason, stored_at,
                )
                return 0

            messages, leftover = cap_window_from_oldest(
                messages, mem.flush_max_user_turns)
            if leftover:
                # The cursor must advance only over what was actually sent, so
                # the target is recomputed from the CAPPED window.
                if messages:
                    advance_to = str(messages[-1].get("timestamp") or "")
                    advance_id = str(messages[-1].get("id") or "")
                logger.warning(
                    "[OperatorFactWriter] session %s (%s): capped at %d user "
                    "turn(s), %d left for the next trigger",
                    job.session_id[:8], job.reason, mem.flush_max_user_turns,
                    leftover,
                )

        # An empty card, as the inline path did, so no persona sampling override
        # can leak into the extractor — this repo has a recorded defect where a
        # classifier silently inherited the speaking persona's temperature.
        extractor = FactExtractor(
            create_llm_client({}, temperature=settings_factory().ollama.temp_fact_extraction)
        )
        out = extractor.extract_facts(messages, persona_key=job.persona_key)
        candidates = [
            str(t).strip()
            for t in (list(out.get("facts") or []) + list(out.get("background") or []))
            if str(t).strip()
        ]
        # 🔴 THE SQLITE WRITE IS NOT CONDITIONAL ON THE GRAPH, AND USED TO BE.
        # `if repo is None: return 0` and `if not candidates: return 0` both sat
        # ABOVE the profile update, so a Neo4j outage lost the SQLite write too
        # and `_operator_facts`' SQLite fallback had nothing to fall back to —
        # a graph outage took out BOTH stores. The second early return lost an
        # extracted NAME whenever no facts came with it. Fixed by SHAPE rather
        # than by file: the profile update now runs from one place at the end,
        # on any successful extraction, whatever the graph did.
        repo = repo_factory()
        if repo is None:
            logger.warning(
                "[OperatorFactWriter] no graph repository — %d fact(s) not "
                "written to the graph; the SQLite profile is still updated",
                len(candidates),
            )
            _update_sqlite_profile(job, out)
            return 0

        if not candidates:
            _update_sqlite_profile(job, out)
            _advance(job, episode_repo, advance_to, advance_id)
            return 0

        existing = {_norm(r["text"]) for r in repo.current_facts(job.user_id, limit=1024)}
        written = 0
        for text in candidates:
            if _norm(text) in existing:
                continue
            try:
                if repo.add_fact(
                    job.user_id, text,
                    asserted_by="user",        # window-level claim; see the module note
                    origin="learned",
                    source_session_id=job.session_id,
                ):
                    existing.add(_norm(text))
                    written += 1
            except ValueError as e:
                # A vocabulary refusal is a programming error, not a data error.
                logger.error("[OperatorFactWriter] refused: %s", e)
        if written:
            logger.info(
                "[OperatorFactWriter] %d new fact(s) -> graph for %s "
                "(%d candidate(s), %d already held)",
                written, job.user_id, len(candidates), len(candidates) - written,
            )

        # The SAME extraction result also updates the SQLite profile, so the two
        # stores cannot diverge the way two independent extractions did.
        _update_sqlite_profile(job, out)
        _advance(job, episode_repo, advance_to, advance_id)
        return written

    def _update_sqlite_profile(job: FactJob, out: dict) -> None:
        if profile_repo_factory is None:
            return
        try:
            prepo = profile_repo_factory()
            if prepo is not None:
                profile = prepo.get_or_create_profile(job.user_id)
                profile.update_from_session(out)
                prepo.update_profile(profile)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[OperatorFactWriter] SQLite profile update failed "
                "(non-fatal, the graph write stands): %s", e
            )

    def _advance(job: FactJob, episode_repo, advance_to: str, advance_id: str) -> None:
        """Move the high-water mark, but only on a completed extraction.

        🔴 ADVANCES EVEN WHEN NOTHING WAS WRITTEN. A window containing no facts
        has still been looked at, and refusing to mark it done means every
        quiet session is re-extracted on every trigger, forever, and the
        boundary never moves.

        🔴 AND NEVER ADVANCES WHEN THE WRITE COULD NOT HAPPEN. Reached only
        after the graph repo was available and the extractor returned without
        raising. A full queue, a process death with a job queued, a raising
        extractor or an unavailable repo all leave the cursor where it was, so
        the next trigger re-covers the window. The failure mode is repeated
        work, never a hole — which is the entire reason this is a cursor and
        not a sliding window.
        """
        if not (job.use_cursor and episode_repo is not None and advance_to):
            return
        try:
            episode_repo.advance_fact_cursor(
                job.user_id, job.session_id,
                cursor_at=advance_to, cursor_message_id=advance_id,
                message_count=job.session_message_count,
                persona_key=job.persona_key,
                started_at=job.session_started_at,
            )
        except Exception as e:  # noqa: BLE001
            # A cursor that failed to advance is safe: re-coverage, not loss.
            logger.warning(
                "[OperatorFactWriter] session %s: cursor did not advance "
                "(non-fatal, the window will be re-covered): %s",
                job.session_id[:8], e,
            )

    return handle
