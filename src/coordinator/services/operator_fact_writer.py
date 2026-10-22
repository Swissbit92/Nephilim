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


def make_handler(repo_factory, settings_factory):
    """Build the worker's handler. Factories so nothing is captured at import."""

    def handle(job: FactJob) -> int:
        from ..fact_extractor import FactExtractor
        from ..llm_client import create_llm_client

        mem = settings_factory().memory
        if not job.user_id or not job.messages:
            return 0

        # An empty card, as the inline path did, so no persona sampling override
        # can leak into the extractor — this repo has a recorded defect where a
        # classifier silently inherited the speaking persona's temperature.
        extractor = FactExtractor(
            create_llm_client({}, temperature=settings_factory().ollama.temp_fact_extraction)
        )
        out = extractor.extract_facts(job.messages, persona_key=job.persona_key)
        candidates = [
            str(t).strip()
            for t in (list(out.get("facts") or []) + list(out.get("background") or []))
            if str(t).strip()
        ]
        if not candidates:
            return 0

        repo = repo_factory()
        if repo is None:
            logger.warning(
                "[OperatorFactWriter] no graph repository — %d fact(s) not written",
                len(candidates),
            )
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
        return written

    return handle
