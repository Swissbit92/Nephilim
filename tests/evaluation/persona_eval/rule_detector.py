# tests/evaluation/persona_eval/rule_detector.py
"""Did the reply break a hard rule from the persona card?

A three-tier cascade. Tier 0 is deterministic and needs no model; tier 1 is an
NLI cross-encoder; tier 2 (a local LLM adjudicating only the uncertain band) is
deliberately NOT implemented here — build it when the calibration set shows the
band is big enough to matter.

**Why not an LLM judge.** On this exact failure class — a model that can recite
the rule and breaks it anyway — a judge was measured at **15% sensitivity**
against human raters (humans found 34 violations, the judge 7) at 97%
specificity. Models restate constraints they are simultaneously violating at
97.3% accuracy, so asking one "did this break the rule?" asks the wrong
question of the wrong witness.

**Why sentence-level.** NLI contradiction is a local relation, and SNLI/MNLI
premises are single sentences. Feeding a 300-word reply as the premise dilutes
one violating clause toward neutral, and at ~1.3 tokens/word a 400-word reply
also silently truncates — dropping the END, which is exactly where a sign-off
or a boundary break lands. Splitting removes both problems.

**Why the label order is asserted.** `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli`
is `{0: entailment, 1: neutral, 2: contradiction}`, while
`cross-encoder/nli-deberta-v3-small` is `{0: contradiction, ...}`. A silent
index swap inverts every number this module produces, and nothing downstream
would look wrong. `_contradiction_index` reads it from the config and raises
rather than guessing.

**Model choice: select on ANLI, not MNLI.** MNLI is saturated (87-92 across
every candidate) and therefore uninformative. On adversarial inference the
SNLI/MNLI-only cross-encoder scores 0.530 on ANLI-R1 where this one's family
scores 0.796 — and persona prose is non-canonical, first-person and fictional,
i.e. closer to the adversarial distribution than to Flickr captions.

Measured on this machine (M4 Pro, 2026-09-22), 184M params, seq len ~64:
CPU unbatched 83.5 ms/pair; CPU batch-32 29.5 ms/pair; **MPS batch-32 1.8
ms/pair (544 pairs/s)**. Use MPS and batch, or scoring a full A/B takes 83
minutes instead of two. Run the NLI pass AFTER generation, never interleaved,
so it does not contend with Ollama for the GPU.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "Rule",
    "SentenceVerdict",
    "RuleVerdict",
    "split_sentences",
    "resolve_first_person",
    "triggered",
    "NLIRuleDetector",
    "DEFAULT_MODEL",
    "TAU_HI",
    "TAU_LO",
]

DEFAULT_MODEL = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"

# Provisional. These are placeholders until the 100-item calibration set sets
# them, and the harness must refuse to gate on an uncalibrated threshold —
# a detector whose own error rate is unmeasured cannot certify anything.
TAU_HI = 0.90
TAU_LO = 0.10

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Rule:
    """One hard rule, expressed as a declarative proposition.

    ``hypothesis`` must be a short general statement in the third person —
    that is the shape SNLI/MNLI hypotheses take. Not "Never flirt with anyone
    else" (an imperative, and negated) but "Gwen is romantically exclusive to
    one person and does not flirt with anyone else."

    ``triggers`` pre-gates the pairs: only sentences containing one of these
    tokens are scored against this rule. It cuts pair count 5-10x with near-zero
    recall loss provided the lexicon is generous, so be generous.

    ``predicate`` is an optional tier-0 check — a pure function of the reply
    text. Where a rule is literally regex-checkable (a required form of address,
    a forbidden abbreviation) this is free, exact, and needs no model at all.
    """

    id: str
    hypothesis: str
    triggers: frozenset[str] = frozenset()
    predicate: Callable[[str], bool] | None = None
    note: str = ""


@dataclass
class SentenceVerdict:
    sentence: str
    p_contradiction: float


@dataclass
class RuleVerdict:
    rule_id: str
    violated: bool | None          # None = uncertain band, needs adjudication
    p_contradiction: float            # max over sentences, never mean
    worst_sentence: str = ""
    tier: str = "nli"                 # "predicate" | "nli" | "skipped"
    sentences_scored: int = 0
    details: list[SentenceVerdict] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    """Cheap sentence split. Good enough because the downstream model only
    needs a local premise, not a perfect parse."""
    parts = [s.strip() for s in _SENT_SPLIT.split(text or "") if s and s.strip()]
    return [p for p in parts if any(c.isalpha() for c in p)]


def resolve_first_person(sentence: str, subject: str) -> str:
    """Rewrite the persona's first person to a named third person.

    Unresolved ``I``/``my`` is a documented grounding failure for NLI on
    dialogue — first-person pronouns have the lowest grounding rates precisely
    because they live in character speech the model cannot bind. A regex
    substitution costs nothing and removes the failure.
    """
    s = sentence
    subs = [
        (r"\bI'm\b", f"{subject} is"), (r"\bI am\b", f"{subject} is"),
        (r"\bI'll\b", f"{subject} will"), (r"\bI'd\b", f"{subject} would"),
        (r"\bI've\b", f"{subject} has"), (r"\bI\b", subject),
        (r"\bmy\b", f"{subject}'s"), (r"\bMy\b", f"{subject}'s"),
        (r"\bmyself\b", "herself"), (r"\bmine\b", f"{subject}'s"),
        (r"\bme\b", "her"),
    ]
    for pat, rep in subs:
        s = re.sub(pat, rep, s)
    return s


def triggered(sentence: str, rule: Rule) -> bool:
    """True when this sentence is worth scoring against this rule."""
    if not rule.triggers:
        return True
    low = sentence.lower()
    return any(t in low for t in rule.triggers)


class NLIRuleDetector:
    """Sentence-level contradiction scoring against persona rules.

    Construct once and reuse — loading the model costs far more than scoring.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str | None = None,
                 batch_size: int = 32):
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as e:  # pragma: no cover - exercised by the skip guard
            raise ImportError(
                "rule_detector needs torch+transformers, which are deliberately NOT "
                "in nephilim's venv (they would be its first ~2GB ML dependency). "
                "Install them in a dedicated eval venv."
            ) from e

        self._torch = torch
        if device is None:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        self.batch_size = batch_size
        self.model_name = model_name
        self._tok = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self._model.eval().to(device)
        self.contradiction_index = self._contradiction_index(self._model.config)

    @staticmethod
    def _contradiction_index(config) -> int:
        """Read the contradiction class from the config. Never hardcode it.

        Label order differs between NLI model families, and guessing wrong
        inverts the metric while everything downstream still looks healthy.
        """
        mapping = getattr(config, "label2id", None) or {}
        for label, idx in mapping.items():
            if str(label).lower().startswith("contradict"):
                return int(idx)
        raise ValueError(
            f"no contradiction label in id2label={getattr(config, 'id2label', None)} — "
            "refusing to guess the index"
        )

    def _score_pairs(self, premises: Sequence[str], hypotheses: Sequence[str]) -> list[float]:
        torch = self._torch
        out: list[float] = []
        for i in range(0, len(premises), self.batch_size):
            p = list(premises[i : i + self.batch_size])
            h = list(hypotheses[i : i + self.batch_size])
            enc = self._tok(p, h, return_tensors="pt", padding=True,
                            truncation=True, max_length=512).to(self.device)
            with torch.no_grad():
                probs = torch.softmax(self._model(**enc).logits, dim=-1)
            out.extend(probs[:, self.contradiction_index].tolist())
        return out

    def check(self, reply: str, rules: Sequence[Rule], subject: str = "She",
              tau_hi: float = TAU_HI, tau_lo: float = TAU_LO) -> list[RuleVerdict]:
        """Score one reply against every rule.

        Aggregates with **max** over sentences, not mean: one violating clause
        in an otherwise compliant reply is a violation, and averaging it against
        nine innocent sentences hides exactly the case this exists to catch.
        """
        sentences = split_sentences(reply)
        verdicts: list[RuleVerdict] = []

        for rule in rules:
            # Tier 0 — deterministic, exact, free.
            if rule.predicate is not None:
                violated = bool(rule.predicate(reply))
                verdicts.append(RuleVerdict(rule.id, violated, 1.0 if violated else 0.0,
                                            tier="predicate"))
                continue

            candidates = [s for s in sentences if triggered(s, rule)]
            if not candidates:
                verdicts.append(RuleVerdict(rule.id, False, 0.0, tier="skipped"))
                continue

            premises = [resolve_first_person(s, subject) for s in candidates]
            scores = self._score_pairs(premises, [rule.hypothesis] * len(premises))
            # strict=: a length mismatch here would mean scores were silently
            # dropped, which would read as "fewer violations" rather than as a bug.
            details = [
                SentenceVerdict(s, p) for s, p in zip(candidates, scores, strict=True)
            ]
            worst = max(details, key=lambda d: d.p_contradiction)

            if worst.p_contradiction >= tau_hi:
                violated: bool | None = True
            elif worst.p_contradiction <= tau_lo:
                violated = False
            else:
                violated = None  # uncertain — escalate, never silently pass

            verdicts.append(RuleVerdict(rule.id, violated, worst.p_contradiction,
                                        worst.sentence, "nli", len(candidates), details))
        return verdicts
