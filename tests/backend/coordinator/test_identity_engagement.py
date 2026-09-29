"""The engagement signal, and the measurement that keeps it out of production.

`idn.cq05` asks "which parts of her have I never actually engaged with?" and declares
`salience`/`reinforced_count`/`last_referenced` as the properties it needs. This module is
the candidate answer. It is NOT wired, for a measured reason, and these tests exist to
stop that reason being forgotten rather than to certify the signal.
"""
from __future__ import annotations

import pytest

from src.coordinator.identity_engagement import (
    MAX_NODES_PER_TURN,
    MIN_INFORMATIVENESS,
    engaged_nodes,
    informativeness,
    terms,
)

#: Padding so the corpus is a realistic size -- the score is normalised by log(N) and a
#: 4-node corpus cannot reach the production threshold however good the match.
FILLER = [f"unrelated filler sentence number {i} about nothing" for i in range(40)]

NODES = [
    {"node_id": "hair", "text": "She has long red hair she keeps in a ponytail"},
    {"node_id": "eyes", "text": "Striking blue eyes that water when she gags"},
    {"node_id": "job", "text": "Gwen is a 21-year-old data analyst by day"},
    {"node_id": "twin", "text": "She has an identical twin sister Debbie"},
]


def test_a_genuine_engagement_is_found():
    """Scored against a REALISTIC corpus size, because the score is normalised by log(N).

    A 4-node corpus cannot reach the production threshold no matter how good the match --
    which was itself the bug that made the first calibration uninterpretable.
    """
    corpus = NODES + [{"node_id": f"f{i}", "text": t} for i, t in enumerate(FILLER)]
    got = engaged_nodes("I let my long red hair out of its ponytail for you", corpus)
    assert [n["node_id"] for n in got] == ["hair"], got


def test_a_single_shared_term_is_never_enough():
    """Measured: single-term matches on 'huge', 'waist', 'dark' and 'were' all cleared a
    summed-IDF threshold, and none is engagement. Two distinct terms is the floor
    regardless of how informative one of them is."""
    assert engaged_nodes("tell me about your twin", NODES) == []


def test_function_words_rare_in_the_card_cannot_carry_a_match():
    """IDF's failure mode on a small corpus: a function word absent from most nodes scores
    HIGH. 'were' scored 4.85 and single-handedly cleared a threshold. Measured false
    positives came via exactly these: ['always','never'], ['completely','used']."""
    for w in ("were", "always", "never", "completely", "around", "still", "made", "take"):
        assert w not in terms(f"we {w} going"), f"{w!r} must not be a scoreable term"


def test_the_score_is_scale_free():
    """Normalised by log(N), so a threshold means the same thing for every persona.

    Unnormalised, a unique term scored 4.85 at N=128 and 1.39 at N=4 -- so one absolute
    threshold silently meant something different per card, and the recorded calibration
    was uninterpretable rather than merely approximate.
    """
    # Letters only: the word regex is [a-z']+, so "unique0" tokenises as "unique".
    tags = ["zebra", "kayak", "quartz", "fjord", "waltz", "sphinx"]
    small = [{"node_id": t, "text": f"{t} padding sentence"} for t in tags]
    large = small + [{"node_id": f"x{i}", "text": f"filler sentence number {i}"}
                     for i in range(60)]
    # A term unique to one node scores exactly 1.0 -- "one maximally-rare term" --
    # at BOTH corpus sizes. That invariance is the whole point of normalising.
    assert informativeness(small)["zebra"] == pytest.approx(1.0)
    assert informativeness(large)["zebra"] == pytest.approx(1.0)


def test_the_number_of_writes_per_turn_is_bounded():
    """A threshold alone marked 7.5 nodes engaged per reply and 29 on one -- a quarter of
    her identity, from one message. Unbounded in reply LENGTH, which is not engagement."""
    long_reply = " ".join(n["text"] for n in NODES) + " ponytail eyes analyst Debbie"
    assert len(engaged_nodes(long_reply, NODES)) <= MAX_NODES_PER_TURN


def test_results_carry_their_own_justification():
    """A signal whose decisions cannot be inspected is the shape that produced two wrong
    results in this repo already."""
    corpus = NODES + [{"node_id": f"f{i}", "text": t} for i, t in enumerate(FILLER)]
    got = engaged_nodes("my long red hair in a ponytail", corpus)
    assert got and got[0]["_shared_terms"] and got[0]["_score"] > 0


def test_it_is_not_reachable_from_any_production_path():
    """THE GUARD THAT MATTERS. Precision@1 measured at ~46% on 34 real replies, so more
    than half of the evidence this would write lands on the wrong node -- and evidence
    pointing at the wrong fact is worse than none, because nothing downstream can tell.

    If this assertion fails, someone wired it. The precondition is hand-labelled
    (reply, node) ground truth at a scale that can FIT a threshold, plus an ablation
    showing the counter buys anything at all -- no published result isolates an access
    counter, and Generative Agents never ablated its own importance weighting.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    offenders = [
        p.relative_to(root) for p in (root / "src").rglob("*.py")
        if p.name != "identity_engagement.py" and "identity_engagement" in p.read_text()
    ]
    assert not offenders, (
        f"identity_engagement is imported by {offenders} — it is measured at ~46% "
        f"precision@1 and must not write to the graph until that is fixed")


def test_the_measured_verdict_is_recorded_in_the_module():
    """The number, in the source, where the next person will read it."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[3] / "src" / "coordinator"
           / "identity_engagement.py").read_text()
    assert "NOT WIRED" in src
    assert "46%" in src, "the measured precision must stay in the docstring"


@pytest.mark.parametrize("thr", [MIN_INFORMATIVENESS])
def test_the_threshold_is_the_one_that_was_measured(thr):
    """Pinned so a later 'improvement' to the constant invalidates the recorded number
    rather than silently inheriting its credibility."""
    assert thr == 2.0
