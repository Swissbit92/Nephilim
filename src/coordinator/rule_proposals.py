"""Proposed rule changes, and the mechanical gates in front of them (ADR-017).

A preference stated in conversation ("stop bringing up my ex") should be able to
change a SOFT wall. Every stage of that pipeline is model-generated except the last:

    conversation -> extracted preference -> PROPOSAL -> operator approves -> applied

WHY THE OPERATOR'S APPROVAL IS NOT THE SAFETY MARGIN. It is the backstop, not the
control. Measured: 107 sessions of developers (70% with 3+ years, 86% with security
backgrounds) reviewing an agent's work for sabotage — 94% failed to detect it, and 56%
accepted the change even when a monitor explicitly flagged it (arXiv:2606.05647).
Acceptance also drops ~10% for every 5% increase in repetitive alerts (Ancker 2017),
and this deployment's model is abliterated, which is measured to produce malformed
tool output at ~3.5x the aligned rate (arXiv:2605.19722) — so a stream of junk
proposals would erode scrutiny exactly when a bad one needs it.

So the gates below are mechanical and run BEFORE a human sees anything. Each one
exists because of a specific documented failure:

  * VERBATIM QUOTE REQUIRED — the proposal must carry a literal span from a user
    message, not the model's paraphrase of it. Defeats the delayed-invocation attack,
    where poisoned content plants "if the user says X, save this" and the model later
    treats an innocent message as consent it pre-scripted (Rehberger, Gemini memory).
  * CONTENT-HASH-BOUND APPROVAL — approval binds to the exact text, not to an id.
    CVE-2025-54136 ("MCPoison") swapped the content behind an approved name.
  * DETERMINISTIC CONTRADICTION CHECK against the hard walls, in code, never by asking
    the model whether its own proposal is safe. OpenHands' LLMSecurityAnalyzer is the
    named anti-pattern: it trusts model self-assessed risk and auto-executes "LOW".
  * HARD WALLS ARE STRUCTURALLY UNNAMEABLE — there is no `rule_type` parameter here.
    A proposal cannot express "make this a hard wall", so the confused-deputy failure
    cannot occur by construction rather than by rejection-after-the-fact.
  * SEPARATE LABEL — proposals are `:ProposedRule` and deliberately NOT `:Rule`, so
    no prompt-assembly path can read an unapproved proposal even by accident.
  * REJECTIONS ARE KEPT, with a structured reason. A cluster of near-hard-wall
    rejections over time is the earliest signal of the gradual-drift attack, and a
    boolean cannot show that.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Optional

from .graph_driver import read, write
from .graph_ids import new_id, now_iso

logger = logging.getLogger(__name__)

# A proposal nobody answered must not sit there forever looking pending.
PROPOSAL_TTL_SECONDS = 7 * 24 * 3600

# Only these tiers may be proposed. `hard_wall` is absent BY CONSTRUCTION — this is
# the structural-unnameability gate, not a validation list to check against.
PROPOSABLE_TYPES: frozenset[str] = frozenset({"soft_wall", "dial"})

STATUSES: frozenset[str] = frozenset({"pending", "approved", "rejected", "expired"})

REJECTION_REASONS: frozenset[str] = frozenset({
    "no_verbatim_quote",       # the model paraphrased instead of quoting
    "quote_not_in_message",    # the quote does not appear in the user's own words
    "contradicts_hard_wall",   # overlaps a hard wall's protected terms
    "not_durable",             # a mood, not a standing preference
    "target_is_hard_wall",     # tried to aim at an immutable rule
    "text_unchanged",          # a no-op proposal
    "operator_declined",       # the human said no
    "superseded_by_newer",     # a later proposal for the same rule won
})


class ProposalRejected(ValueError):
    """A proposal failed a mechanical gate. Carries a structured reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REJECTION_REASONS:
            raise ValueError(f"unknown rejection reason {reason!r}")
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


def content_hash(rule_id: str, text: str) -> str:
    """Bind an approval to exactly this text for exactly this rule.

    CVE-2025-54136 is the reason this is a hash of the PAIR and not of the text alone:
    approval must not transfer to a different rule, and must not survive a byte
    changing in the text.
    """
    return hashlib.sha256(f"{rule_id}\x00{text}".encode()).hexdigest()


# ── Gate 1: the quote must be the operator's own words ────────────────────────
_WS = re.compile(r"\s+")

# Zero-width space/non-joiner/joiner, the four bidi overrides, and the BOM.
_INVISIBLE = frozenset("​‌‍‪‫‬‭‮﻿")


def _normalise(s: str) -> str:
    """Collapse whitespace and strip zero-width/bidi characters before comparing.

    The Rules File Backdoor hid bidi markers and zero-width joiners inside rule text
    so a human diff review and the stored bytes disagreed. Normalising here means the
    quote check cannot be fooled by invisible characters, and means the text a human
    approves is the text that gets compared.
    """
    # Zero-width and bidi characters become a SPACE, not nothing. A test caught the
    # difference: deleting them turns "stop\u200bbringing" into "stopbringing", while a
    # human reads two words there. Normalising to what the human SEES is the whole
    # point — the Rules File Backdoor works by making the rendered text and the stored
    # bytes disagree, so the comparison has to side with the rendering.
    s = "".join(" " if ch in _INVISIBLE else ch for ch in s)
    return _WS.sub(" ", s).strip().lower()


def check_quote(quote: str, user_message: str, min_words: int = 3) -> None:
    """The proposal must quote the user verbatim. Raises ProposalRejected."""
    if not quote or len(quote.split()) < min_words:
        raise ProposalRejected("no_verbatim_quote",
                               f"quote must be at least {min_words} words, got {quote!r}")
    if _normalise(quote) not in _normalise(user_message):
        raise ProposalRejected("quote_not_in_message",
                               f"{quote!r} does not appear in the user's message")


# ── Gate 2: durability. An open gap in the field, so it is asked, not inferred ──
# No published system measures situational-vs-durable classification, and every one
# surveyed lets the same LLM call that detects contradiction also decide durability,
# with no accuracy figure anywhere. So durability is a REQUIRED, EXPLICIT field and
# only "durable" is accepted. A companion that turns a mood into a permanent rule is
# broken, and this is the cheapest available guard against it.
DURABILITIES: frozenset[str] = frozenset({"durable", "situational"})

_SITUATIONAL_MARKERS = re.compile(
    r"\b(tonight|right now|just now|today|this once|for now|at the moment|"
    r"currently|i'?m tired|not in the mood)\b", re.I)


def check_durability(durability: str, quote: str) -> None:
    if durability not in DURABILITIES:
        raise ProposalRejected("not_durable", f"durability must be one of {sorted(DURABILITIES)}")
    if durability != "durable":
        raise ProposalRejected("not_durable", "only durable preferences become rules")
    # A cheap deterministic second opinion on the model's own label. It cannot catch
    # everything and is not claimed to — it catches the obvious case where the quote
    # itself is time-bounded while the model called it durable.
    if _SITUATIONAL_MARKERS.search(quote):
        raise ProposalRejected(
            "not_durable",
            f"quote contains a time-bounded marker and was labelled durable: {quote!r}")


# ── Gate 3: does the proposal collide with a hard wall? ───────────────────────
_STOP = frozenset("""a an and are as at be but by for from he her him his if in into is it
its me my never no not of on or she that the their them they this to up us was we what when
who will with without you your yours always""".split())


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']{3,}", text.lower()) if w not in _STOP}


def check_no_hard_wall_collision(text: str, hard_wall_texts: list[str],
                                 threshold: float = 0.34) -> None:
    """Refuse a proposal that overlaps a hard wall's distinctive terms.

    THE ATTACK THIS CLOSES. A soft wall engineered to contradict a hard wall gives the
    model a "legitimate" conflict to resolve in its own favour later — it never has to
    write to the database again, it just argues in-context that the newer, narrower
    rule should win. That is rhetoric, not an unauthorised write, so no write-time
    authorisation check can see it. Overlap is the only signal available at write time.

    Deliberately DETERMINISTIC and deliberately blunt. It will produce false positives
    on innocent proposals that share vocabulary with a hard wall; that is the correct
    direction to fail, and the operator can still make such a change by editing the
    card. Asking the model whether its own proposal conflicts is the documented
    anti-pattern and is not an option here.
    """
    prop = _terms(text)
    if not prop:
        return
    for hw in hard_wall_texts:
        hwt = _terms(hw)
        if not hwt:
            continue
        overlap = len(prop & hwt) / len(hwt)
        if overlap >= threshold:
            raise ProposalRejected(
                "contradicts_hard_wall",
                f"shares {overlap:.0%} of the distinctive terms in a hard wall "
                f"({sorted(prop & hwt)}) — change it on the card instead")


# ── The store ─────────────────────────────────────────────────────────────────

class RuleProposalStore:
    """Pending rule changes, as `:ProposedRule` nodes.

    NOT labelled `:Rule`. `standing_rules()` matches `:CurrentRule`, and every other
    read matches `:Rule`, so a proposal is invisible to prompt assembly by
    construction. Getting this wrong would put unapproved, model-authored text into
    her prompt, which is the worst outcome available here.
    """

    def __init__(self, driver: Any, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    # -- propose ---------------------------------------------------------------
    def propose(self, *, persona_id: str, target_rule_id: str, new_text: str,
                quote: str, user_message: str, durability: str,
                target_rule_type: str, target_text: str,
                hard_wall_texts: list[str]) -> dict[str, Any]:
        """Run every mechanical gate, then store a pending proposal.

        There is NO `rule_type` parameter. A proposal cannot express "make this a hard
        wall"; the tier is whatever the target already is, and the target must be
        proposable. That is the structural-unnameability gate.

        A rejection is STORED, not just raised, so the reason is auditable and a
        cluster of near-hard-wall rejections is visible later.
        """
        if target_rule_type == "hard_wall":
            self._record_rejection(persona_id, target_rule_id, new_text,
                                   "target_is_hard_wall")
            raise ProposalRejected("target_is_hard_wall",
                                   f"{target_rule_id} is a hard wall — edit the card")
        if target_rule_type not in PROPOSABLE_TYPES:
            self._record_rejection(persona_id, target_rule_id, new_text,
                                   "target_is_hard_wall")
            raise ProposalRejected("target_is_hard_wall",
                                   f"tier {target_rule_type!r} is not proposable")
        if _normalise(new_text) == _normalise(target_text):
            self._record_rejection(persona_id, target_rule_id, new_text, "text_unchanged")
            raise ProposalRejected("text_unchanged", "the proposal changes nothing")

        for gate, args in ((check_quote, (quote, user_message)),
                           (check_durability, (durability, quote)),
                           (check_no_hard_wall_collision, (new_text, hard_wall_texts))):
            try:
                gate(*args)
            except ProposalRejected as e:
                self._record_rejection(persona_id, target_rule_id, new_text, e.reason)
                raise

        if self._driver is None:
            return {"proposed": False, "reason": "graph unavailable"}
        pid = new_id()
        now = now_iso()
        write(
            self._driver,
            """
            MATCH (p:Persona {persona_id: $persona_id})
            CREATE (x:ProposedRule {
                proposal_id: $pid, persona_id: $persona_id,
                target_rule_id: $target_rule_id, new_text: $new_text,
                quote: $quote, durability: $durability,
                content_hash: $chash, status: 'pending',
                created_at: $now, decided_at: null, reason: null
            })
            CREATE (p)-[:HAS_PROPOSAL]->(x)
            """,
            self._database,
            persona_id=persona_id, pid=pid, target_rule_id=target_rule_id,
            new_text=new_text, quote=quote, durability=durability,
            chash=content_hash(target_rule_id, new_text), now=now,
        )
        logger.info("[Rules] proposal %s pending for %s rule %s", pid, persona_id, target_rule_id)
        return {"proposed": True, "proposal_id": pid,
                "content_hash": content_hash(target_rule_id, new_text)}

    def _record_rejection(self, persona_id: str, target_rule_id: str,
                          new_text: str, reason: str) -> None:
        if self._driver is None:
            return
        write(
            self._driver,
            """
            MERGE (p:Persona {persona_id: $persona_id})
            CREATE (x:ProposedRule {
                proposal_id: $pid, persona_id: $persona_id,
                target_rule_id: $target_rule_id, new_text: $new_text,
                quote: null, durability: null, content_hash: $chash,
                status: 'rejected', created_at: $now, decided_at: $now, reason: $reason
            })
            CREATE (p)-[:HAS_PROPOSAL]->(x)
            """,
            self._database, persona_id=persona_id, pid=new_id(),
            target_rule_id=target_rule_id, new_text=new_text,
            chash=content_hash(target_rule_id, new_text), now=now_iso(), reason=reason,
        )

    # -- read ------------------------------------------------------------------
    def pending(self, persona_id: str) -> list[dict[str, Any]]:
        """Pending proposals, newest first, with expired ones filtered out.

        Expiry is computed at READ time rather than by a sweeper, so a proposal that
        nobody answered cannot keep looking actionable just because no cleanup ran.
        """
        if self._driver is None:
            return []
        rows = read(
            self._driver,
            """
            MATCH (:Persona {persona_id: $persona_id})-[:HAS_PROPOSAL]->(x:ProposedRule)
            WHERE x.status = 'pending'
            RETURN x.proposal_id AS proposal_id, x.target_rule_id AS target_rule_id,
                   x.new_text AS new_text, x.quote AS quote,
                   x.durability AS durability, x.content_hash AS content_hash,
                   x.created_at AS created_at
            ORDER BY x.created_at DESC LIMIT 50
            """,
            self._database, persona_id=persona_id,
        )
        cutoff = _iso_minus(PROPOSAL_TTL_SECONDS)
        return [r for r in rows if (r["created_at"] or "") >= cutoff]

    def pending_for_review(self, persona_id: str, repo: Any) -> list[dict[str, Any]]:
        """Pending proposals with the line she would ACTUALLY READ, not the raw text.

        WHY THIS EXISTS RATHER THAN JUST pending(). A proposal carries no polarity — it
        inherits the target rule's, deliberately, because letting a model choose
        polarity is the inversion class that once flipped four hard walls. But
        inheriting means a reworded rule can read wrong: "Bring up politics only if he
        raises it first" inherits `prohibition` and renders as "Never: Bring up
        politics only if he raises it first", which is not what the operator asked for.

        A human approving raw text would not see that. A human approving the RENDERED
        line does. This is also the countermeasure to approving a name rather than
        content, applied to meaning instead of bytes: show the thing, not a handle to
        the thing.
        """
        from .prompt_builder import build_graph_rules_block  # noqa: PLC0415 - cycle

        out = []
        live = {r["rule_id"]: r for r in repo.standing_rules(persona_id, limit=64)}
        for pr in self.pending(persona_id):
            target = live.get(pr["target_rule_id"], {})
            preview = dict(target, text=pr["new_text"])
            rendered = [
                ln.strip() for ln in
                build_graph_rules_block(persona_id, [preview]).split("\n")
                if ln.strip().startswith("1.")
            ]
            out.append({
                **pr,
                "current_text": target.get("text"),
                "rule_type": target.get("rule_type"),
                "polarity_inherited": target.get("polarity"),
                "renders_as": rendered[0] if rendered else None,
            })
        return out

    def rejections(self, persona_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Rejected proposals with their reasons — the drift signal."""
        if self._driver is None:
            return []
        return read(
            self._driver,
            """
            MATCH (:Persona {persona_id: $persona_id})-[:HAS_PROPOSAL]->(x:ProposedRule)
            WHERE x.status = 'rejected'
            RETURN x.proposal_id AS proposal_id, x.reason AS reason,
                   x.new_text AS new_text, x.created_at AS created_at
            ORDER BY x.created_at DESC LIMIT $limit
            """,
            self._database, persona_id=persona_id, limit=limit,
        )

    # -- decide ----------------------------------------------------------------
    def approve(self, proposal_id: str, expected_content_hash: str,
                repo: Any) -> dict[str, Any]:
        """Apply a pending proposal, binding the approval to its exact content.

        ``expected_content_hash`` is what the operator was SHOWN. If the stored text
        no longer hashes to it, the approval is refused — CVE-2025-54136 swapped the
        content behind an already-approved name, and an id-bound approval cannot see
        that.
        """
        if self._driver is None:
            return {"approved": False, "reason": "graph unavailable"}
        rows = read(
            self._driver,
            """
            MATCH (x:ProposedRule {proposal_id: $pid})
            RETURN x.status AS status, x.target_rule_id AS target_rule_id,
                   x.new_text AS new_text, x.content_hash AS content_hash,
                   x.created_at AS created_at, x.persona_id AS persona_id
            """,
            self._database, pid=proposal_id,
        )
        if not rows:
            return {"approved": False, "reason": "no such proposal"}
        p = rows[0]
        if p["status"] != "pending":
            return {"approved": False, "reason": f"status is {p['status']}, not pending"}
        if (p["created_at"] or "") < _iso_minus(PROPOSAL_TTL_SECONDS):
            self._decide(proposal_id, "expired", "superseded_by_newer")
            return {"approved": False, "reason": "proposal expired"}
        actual = content_hash(p["target_rule_id"], p["new_text"])
        if actual != expected_content_hash or actual != p["content_hash"]:
            return {"approved": False, "reason": "content hash mismatch — text changed "
                                                 "since it was shown; refusing"}
        # supersede_rule refuses a hard wall on its own; this is defence in depth, not
        # the only check.
        result = repo.supersede_rule(p["target_rule_id"], p["new_text"],
                                     origin="conversation")
        if not result.get("superseded", True) and "reason" in result:
            return {"approved": False, "reason": result["reason"]}
        self._decide(proposal_id, "approved", None)
        logger.info("[Rules] proposal %s APPROVED -> rule %s", proposal_id,
                    result.get("new_rule_id"))
        return {"approved": True, **result}

    def reject(self, proposal_id: str, reason: str = "operator_declined") -> dict[str, Any]:
        if reason not in REJECTION_REASONS:
            raise ValueError(f"unknown rejection reason {reason!r}")
        if self._driver is None:
            return {"rejected": False, "reason": "graph unavailable"}
        self._decide(proposal_id, "rejected", reason)
        return {"rejected": True}

    def _decide(self, proposal_id: str, status: str, reason: Optional[str]) -> None:
        write(
            self._driver,
            """
            MATCH (x:ProposedRule {proposal_id: $pid})
            SET x.status = $status, x.decided_at = $now, x.reason = $reason
            """,
            self._database, pid=proposal_id, status=status,
            now=now_iso(), reason=reason,
        )


def _iso_minus(seconds: int) -> str:
    """The fixed-width ISO timestamp `seconds` ago, for string comparison.

    String comparison is valid ONLY because now_iso() is fixed-width and UTC. If that
    ever changes, every comparison here silently becomes lexicographic nonsense.
    """
    import datetime

    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
