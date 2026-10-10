"""One canned line, replayed, was 69% of a measured repetition defect.

MEASURED on a real 56-reply session: 13 replies failed an 8-word shared-span gate, and
NINE of them were byte-identical repeats of a `persona_lines` situation line — including
the same 17-word sentence five times and a 28-word span. Applying the card n-gram
exemption drops median cross_rep to 0.0000 (her mandated phrasing is compliance, not
degeneration) and leaves those nine untouched, so they are the defect.

Two causes, both here:

  * `_generate` asks for `_VARIANTS` lines and returns `out[:_VARIANTS]` with NO FLOOR.
    Measured live: `image_queued` yields 3, but `image_ready` and `image_not_started`
    yield 1. `random.choice` on a one-element list is a constant.
  * `line()` caches whatever came back **for the process lifetime**. So one degraded
    generation is permanent, and the only signal was an INFO log nobody reads.

No sampler change can reach this: the text is not generated per turn, it is replayed.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.coordinator.services import persona_lines as pl


@pytest.fixture(autouse=True)
def _clean_cache():
    pl.reset_cache()
    yield
    pl.reset_cache()


def test_a_single_variant_is_not_cached_for_the_process_lifetime():
    """A transient bad generation must not become permanent.

    This is the defect's actual mechanism. One sentence came back once, got cached, and
    every subsequent image event in that process replayed it verbatim.
    """
    calls = []

    def flaky(persona_key, situation):
        calls.append(situation)
        return ["only one line"] if len(calls) == 1 else ["one", "two", "three"]

    with patch.object(pl, "_generate", side_effect=flaky):
        first = pl.line("gwen", "image_ready")
        second = pl.line("gwen", "image_ready")

    assert len(calls) == 2, (
        "a one-variant result was cached and never retried — one bad generation is "
        "permanent for the process")
    assert second in {"one", "two", "three"}
    assert first == "only one line"


def test_the_same_line_is_never_returned_twice_in_a_row():
    """Even with several variants, `random.choice` repeats. The observed symptom is
    CONSECUTIVE identical replies, so consecutive identity is what must be excluded."""
    with patch.object(pl, "_generate", return_value=["alpha", "beta"]):
        seen = [pl.line("gwen", "image_ready") for _ in range(8)]
    for a, b in zip(seen, seen[1:], strict=False):
        assert a != b, f"returned {a!r} twice in a row: {seen}"


def test_one_variant_logs_a_WARNING_not_an_info(caplog):
    """The only signal was an INFO line. A situation that can only produce one phrasing
    is a repetition source and has to be visible at WARNING."""
    import logging
    with patch.object(pl, "_generate", return_value=["solo"]):
        with caplog.at_level(logging.WARNING, logger=pl.logger.name):
            pl.line("gwen", "image_ready")
    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "a single-variant situation must warn")


def test_the_cache_still_works_within_a_cycle():
    """The cache must still work — this is called on a hot path.

    The property CHANGED deliberately: it is no longer "generate once, ever". A
    reachable situation regenerates when its permutation cycle is spent, because V
    fixed at 3 made a 12-event session trip the whole-history gate 9 times in BOTH
    arms of the A/B. What must still hold is one call per CYCLE, not per occurrence.
    """
    with patch.object(pl, "_generate", return_value=["a", "b", "c"]) as gen:
        for _ in range(3):
            pl.line("gwen", "image_queued")
    assert gen.call_count == 1, "regenerated inside a cycle — the cache is not working"


def test_a_spent_cycle_regenerates_so_the_phrasing_pool_grows():
    """The point of the whole change: a user in a long session sees more than V lines."""
    sets = [[f"s{g}-{i}" for i in range(3)] for g in range(4)]
    with patch.object(pl, "_generate", side_effect=sets) as gen:
        seen = [pl.line("gwen", "image_ready") for _ in range(9)]
    assert gen.call_count == 3, f"{gen.call_count} generations for 9 events over 3 cycles"
    assert len(set(seen)) == 9, (
        f"only {len(set(seen))} distinct lines in 9 events — recycling is not reaching "
        f"the user: {seen}")


def test_an_unreachable_situation_is_never_recycled():
    """`image_busy` is said while the model is GONE. Recycling it would call a model
    that cannot answer, and the fallback it degrades to is the canned string this
    module exists to avoid."""
    assert "image_busy" in pl._MODEL_UNREACHABLE
    with patch.object(pl, "_generate", return_value=["x", "y", "z"]) as gen:
        for _ in range(9):
            pl.line("gwen", "image_busy")
    assert gen.call_count == 1, "recycled a situation whose model is unreachable"


def test_recycling_backs_off_from_a_failing_generator():
    """The RATE is bounded, not the total. A healthy cached set plus a generator that
    has started failing must not pay an LLM call for every later event."""
    sets = [["a", "b", "c"]] + [[] for _ in range(60)]
    with patch.object(pl, "_generate", side_effect=sets) as gen:
        for _ in range(45):
            pl.line("gwen", "image_ready")
    # 45 events = 15 spent cycles. Backoff 1,2,4,8,16,16... admits ~5 attempts, far
    # below one per cycle. Pinned as a relationship, not a magic number.
    assert gen.call_count < 1 + 15, (
        f"{gen.call_count} generations across 15 spent cycles — backoff is not engaging")


def test_recycling_RECOVERS_once_the_generator_is_healthy_again():
    """QA REJECT #3, reproduced before it was fixed.

    `_recycle_fail` was a hard cap, and a successful recycle was the only thing that
    could clear it — which is circular, because reaching the cap is exactly what blocks
    a recycle. So two transient failures (an Ollama hiccup, GPU contention from an
    overlapping image job — precisely what `_generate` swallows by design) permanently
    reverted a reachable situation to the process-lifetime cache this milestone exists
    to remove, for the rest of the process's uptime, logged only at INFO.

    This is the same shape as the unbounded-retry finding one level up: that one never
    stopped, this one stopped and never restarted. The old test could not see it because
    it never ran the generator healthy again after tripping the cap.
    """
    healthy = ["recovered one", "recovered two", "recovered three"]
    # Exactly the scenario QA reproduced, and the one the old hard cap of 2 tripped on:
    # one healthy cycle, TWO transient failures, then a perfectly healthy generator.
    # Two bad draws is plausible on an always-on service, which is the whole point —
    # this is not a pathological fixture.
    sets = [["a", "b", "c"], [], []] + [list(healthy) for _ in range(20)]
    with patch.object(pl, "_generate", side_effect=sets) as gen:
        seen = [pl.line("gwen", "image_ready") for _ in range(30)]
    assert gen.call_count > 3, (
        f"only {gen.call_count} generations in 30 events — the generator was never "
        f"asked again after the two failures")
    assert any(line in healthy for line in seen), (
        "never recycled again after the failures — the backoff is a one-way latch and "
        "this situation is frozen on its original set for the life of the process")


def test_the_backoff_is_audible_at_WARNING():
    """The old code logged a failed recycle at INFO, with nothing marking the call that
    turned a transient failure into a permanent one. An operator watching at WARNING saw
    a situation silently stop refreshing."""
    sets = [["a", "b", "c"]] + [[] for _ in range(10)]
    with patch.object(pl, "_generate", side_effect=sets):
        with patch.object(pl.logger, "warning") as warn:
            for _ in range(6):
                pl.line("gwen", "image_ready")
    assert warn.called, "a failed recycle is invisible above INFO"
    msg = " ".join(str(c) for c in warn.call_args_list)
    assert "recycle" in msg.lower(), f"the warning does not name what failed: {msg}"


def test_a_failed_recycle_keeps_the_good_lines():
    """Degrade to a reshuffle, never to the hardcoded fallback. A second image job can
    hold the GPU exactly when `image_ready` fires, and three good in-voice lines sitting
    in the cache must beat "Here — I made this for you."."""
    sets = [["alpha one two", "beta three four", "gamma five six"]] + [[] for _ in range(9)]
    with patch.object(pl, "_generate", side_effect=sets):
        seen = [pl.line("gwen", "image_ready") for _ in range(8)]
    assert pl._FALLBACK["image_ready"] not in seen, (
        "served the canned fallback while a good cached set was in hand")
    assert set(seen) <= set(sets[0])


def test_zero_variants_still_falls_back_without_raising():
    with patch.object(pl, "_generate", return_value=[]):
        got = pl.line("gwen", "image_ready")
    assert got == pl._FALLBACK["image_ready"]


def test_every_situation_has_a_fallback():
    """A situation with no fallback returns "" and the user sees an empty message."""
    for situation in pl.SITUATIONS:
        assert pl._FALLBACK.get(situation), f"{situation} has no fallback"


def test_the_variant_floor_is_declared_and_above_one():
    assert getattr(pl, "_MIN_VARIANTS", 1) >= 2, (
        "a floor of 1 is what made random.choice a constant")


class TestTheHolesQACaught:
    """Four defects a QA pass found in the first version of this fix.

    Finding 1 was the serious one: `warm()` is a SECOND writer to the same cache and
    checked truthiness only, so a one-variant generation cached permanently through it
    and defeated the floor for `image_busy` — the situation this module's own docstring
    calls the hardest and most user-visible, because it is said exactly when the
    companion model has been evicted.
    """

    def test_warm_honours_the_floor(self):
        """The hole: `warm()` cached whatever it got, so the floor only applied to
        `line()`. Reproduced by QA before this test existed."""
        with patch.object(pl, "_generate", return_value=["only one"]):
            pl.warm("gwen", ("image_busy",))
        assert pl._cache.get(("gwen", "image_busy")) is None, (
            "warm() cached a sub-floor result — line() will now hit the cache forever "
            "and the floor is defeated for exactly the situation it matters most for")

    def test_warm_still_caches_a_healthy_set(self):
        with patch.object(pl, "_generate", return_value=["a", "b", "c"]):
            pl.warm("gwen", ("image_busy",))
        assert pl._cache.get(("gwen", "image_busy")) == ["a", "b", "c"]

    def test_the_subfloor_retry_is_BOUNDED(self):
        """Finding 2, and the one that would have hurt in production.

        `image_not_started` is called inline in the /persona/chat handler. Refusing to
        cache a sub-floor result means every occurrence pays an LLM call, forever, on a
        synchronous request path — reintroducing the exact cost the cache exists to
        avoid. A persistently one-clause situation is plausible, not a fluke, so the
        retry has to terminate.
        """
        with patch.object(pl, "_generate", return_value=["stubborn"]) as gen:
            for _ in range(10):
                pl.line("gwen", "image_ready")
        assert gen.call_count <= pl._MAX_SUBFLOOR_RETRIES, (
            f"{gen.call_count} LLM calls for 10 occurrences — the retry is unbounded")
        assert pl._cache.get(("gwen", "image_ready")) == ["stubborn"], (
            "after the cap it must cache and stop retrying")

    def test_hitting_the_cap_warns_loudly(self, caplog):
        import logging
        with patch.object(pl, "_generate", return_value=["stubborn"]):
            with caplog.at_level(logging.WARNING, logger=pl.logger.name):
                for _ in range(pl._MAX_SUBFLOOR_RETRIES + 1):
                    pl.line("gwen", "image_ready")
        assert any("CACHING IT ANYWAY" in r.message for r in caplog.records), (
            "accepting a degraded set must be visible, not silent")

    def test_a_recovery_resets_the_counter(self):
        """A transient bad generation must not consume the budget permanently."""
        seq = [["one"], ["one"], ["a", "b", "c"]]
        with patch.object(pl, "_generate", side_effect=seq):
            for _ in range(3):
                pl.line("gwen", "image_ready")
        assert pl._subfloor.get(("gwen", "image_ready")) is None
        assert pl._cache.get(("gwen", "image_ready")) == ["a", "b", "c"]

    def test_every_variant_is_used_before_any_repeats(self):
        """Not a QA finding — my own measurement. Excluding only the immediate
        predecessor still reused at lag 2, which tripped an 8-token span gate 6 times
        over 9 image events. Minimum reuse lag must equal the variant count."""
        with patch.object(pl, "_generate", return_value=["a", "b", "c"]):
            seen = [pl.line("gwen", "image_ready") for _ in range(9)]
        for start in range(0, 9, 3):
            window = seen[start:start + 3]
            assert len(set(window)) == 3, f"a variant repeated inside one cycle: {window}"

    def test_a_reshuffle_cannot_repeat_at_the_seam(self):
        """The case a naive queue gets wrong: the last of one cycle and the first of the
        next are chosen independently."""
        with patch.object(pl, "_generate", return_value=["a", "b", "c"]):
            seen = [pl.line("gwen", "image_ready") for _ in range(60)]
        for a, b in zip(seen, seen[1:], strict=False):
            assert a != b, f"consecutive repeat across a cycle boundary: {seen}"

    def test_pick_decides_under_a_single_lock_acquisition(self):
        """Finding 3. Two acquisitions let two threads for the same key read the same
        predecessor and legally choose the same variant — reproducing the consecutive
        repeat this exists to prevent. Same-key concurrency is real: two images finishing
        together both hit `image_ready`."""
        import inspect
        src = inspect.getsource(pl._pick)
        assert src.count("with _lock") == 1, (
            "read-decide-write must happen under one acquisition")

    def test_concurrent_same_key_calls_never_collide(self):
        import threading
        with patch.object(pl, "_generate", return_value=["a", "b", "c"]):
            pl.line("gwen", "image_ready")
            out: list[str] = []
            lock = threading.Lock()

            def worker():
                v = pl.line("gwen", "image_ready")
                with lock:
                    out.append(v)

            threads = [threading.Thread(target=worker) for _ in range(30)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        counts = {v: out.count(v) for v in set(out)}
        assert max(counts.values()) - min(counts.values()) <= 2, (
            f"distribution skewed under concurrency, suggesting lost updates: {counts}")


def test_the_span_gate_is_arithmetically_unsatisfiable_for_canned_lines():
    """Pinned because I chased this number before working out that it cannot be reached.

    A whole-history shared-span gate flags N - V of N occurrences for V variants, since
    only a variant's first use is novel. Adding variants moves it; nothing reaches zero
    short of generating per occurrence, which is the cost the cache exists to avoid.

    The consequence is a scoping decision, not a tuning one: judge status lines by "never
    twice running", which `_pick` guarantees, and leave the span gate for prose.
    """
    import sys
    from pathlib import Path
    pe = Path(__file__).resolve().parents[3] / "tests" / "evaluation" / "persona_eval"
    sys.path.insert(0, str(pe))
    import repetition_metrics as rm

    # MUTUALLY DISTINCT on purpose. My first fixture used three near-identical strings
    # sharing 10 tokens with each other, which flagged 8 of 9 rather than 6 — so the
    # arithmetic holds only when the variants are actually different from one another,
    # and VARIANT DISTINCTNESS is a second property beyond variant COUNT. `_generate`
    # dedupes exact repeats but not near-duplicates; the live samples were distinct, so
    # this is recorded rather than enforced.
    variants = [
        "the archive index was compiled over several seasons by two assistants",
        "humidity readings are taken twice a week inside the reading room",
        "a later hand added page numbers in pencil occasionally skipping a leaf",
    ]
    with patch.object(pl, "_generate", return_value=variants):
        seq = [pl.line("gwen", "image_ready") for _ in range(9)]
    flagged = sum(1 for i in range(len(seq))
                  if rm.longest_shared_span(seq[i], seq[:i]) >= 8)
    assert flagged == len(seq) - len(variants), (
        f"expected exactly N-V={len(seq)-len(variants)} reuses, got {flagged} — if this "
        f"changed, the arithmetic claim in persona_lines' header note is wrong")
    for a, b in zip(seq, seq[1:], strict=False):
        assert a != b, "the property that IS achievable — no consecutive repeat — broke"


def test_variant_distinctness_matters_not_just_count():
    """Three near-identical variants are barely three variants.

    Measured while fixing the test above: variants sharing 10 tokens with each other
    flagged 8 of 9 against the N-V=6 floor, because a variant's FIRST use already
    matches a different variant. Recorded rather than enforced — `_generate` dedupes
    exact repeats only, and the live samples were distinct — but a situation whose
    phrasings converge gets no benefit from the count.
    """
    import sys
    from pathlib import Path
    pe = Path(__file__).resolve().parents[3] / "tests" / "evaluation" / "persona_eval"
    sys.path.insert(0, str(pe))
    import repetition_metrics as rm

    near = [f"canned phrasing number {i} with enough words to exceed the span gate "
            f"threshold comfortably" for i in range(3)]
    cross = max(rm.longest_shared_span(near[i], [near[j] for j in range(3) if j != i])
                for i in range(3))
    assert cross >= 8, "fixture must actually be near-identical for this to mean anything"
    with patch.object(pl, "_generate", return_value=near):
        seq = [pl.line("gwen", "image_ready") for _ in range(9)]
    flagged = sum(1 for i in range(len(seq))
                  if rm.longest_shared_span(seq[i], seq[:i]) >= 8)
    assert flagged > len(seq) - len(near), (
        "near-identical variants should flag MORE than the N-V floor")
    for a, b in zip(seq, seq[1:], strict=False):
        assert a != b, "no-consecutive-repeat must hold even for near-identical variants"


class TestTheSecondRoundOfQAFindings:
    """Three defects a second QA pass found, all reproduced by it before I fixed them.

    A and B are the same lesson twice: my fix for finding 1 and my fix for finding 2 did
    not COMPOSE. `warm()` honoured the floor but not the retry budget, and the selection
    queue outlived the generation it was built from.
    """

    def test_warm_shares_the_retry_budget_with_line(self):
        """Finding A. `warm()` enforced the floor but never touched `_subfloor`, so a
        persistently sub-floor `image_busy` paid an LLM call on EVERY image job, forever
        — worse than the chat-path case, because `warm()` runs on every job rather than
        on one branch. Both now go through `_accept`."""
        with patch.object(pl, "_generate", return_value=["only one"]) as gen:
            for _ in range(10):
                pl.warm("gwen", ("image_busy",))
        assert gen.call_count <= pl._MAX_SUBFLOOR_RETRIES, (
            f"warm() made {gen.call_count} LLM calls for 10 jobs — unbounded")

    def test_the_floor_decision_lives_in_exactly_one_place(self):
        """Two writers to one cache with two different rules is how A happened."""
        import ast
        import inspect
        import textwrap
        for fn in (pl.line, pl.warm):
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            # Compare on the AST, not the text: a COMMENT naming the constant is fine
            # and `line` has one. What must not exist is a second COMPARISON against it.
            # The SUB-FLOOR RETRY is what must live in one place — that is the state
            # machine finding A was about (`_subfloor`, the retry cap, the "cache it
            # anyway" escape). `line` does read `_MIN_VARIANTS` directly, in the recycle
            # path, and that is deliberate: there the floor is absolute with no escape,
            # because a healthy set is already in hand and a worse replacement is never
            # an improvement. Reusing `_accept` there was the first thing I tried and it
            # replaced good lines with bad ones.
            names = {n.id for n in ast.walk(tree)
                     if isinstance(n, ast.Name)
                     and n.id in {"_subfloor", "_MAX_SUBFLOOR_RETRIES"}}
            assert not names, (
                f"{fn.__name__} re-implements the sub-floor retry instead of calling "
                f"_accept — that is how warm() and line() drifted apart")

    def test_a_rejected_generation_cannot_be_served_later(self):
        """Finding B. `_pick` refills only when its queue is empty, so a list that was
        explicitly NOT cached could still be served on a later call — discarding the
        fresh text that call just paid for. Latent at floor 2 (sub-floor implies length
        1, which drains in one pop) and armed the moment the floor rises, which this
        module's own warning recommends."""
        with patch.object(pl, "_MIN_VARIANTS", 3):
            seq = [["gen1-a", "gen1-b"], ["gen2-a", "gen2-b"], ["gen3-a", "gen3-b"]]
            with patch.object(pl, "_generate", side_effect=seq):
                first = pl.line("gwen", "image_ready")
                second = pl.line("gwen", "image_ready")
        assert first in seq[0]
        assert second in seq[1], (
            f"served {second!r} — a leftover from the rejected first generation, while "
            f"the second generation's text was discarded")

    def test_an_exempted_window_cannot_hide_a_long_verbatim_repeat(self):
        """Finding C, and the one I had flagged as my own biggest uncertainty.

        QA's case: an 11-token byte-identical repeat containing ONE coincidental card
        four-gram scored 4 instead of 11, because a covered window resets the run and
        fragments the block. Status lines and persona cards share warm common phrasing,
        so the collision is ordinary. The exemption now applies only when the block is
        PREDOMINANTLY prescribed.
        """
        import sys
        from pathlib import Path
        pe = Path(__file__).resolve().parents[3] / "tests" / "evaluation" / "persona_eval"
        sys.path.insert(0, str(pe))
        import repetition_metrics as rm

        line = "here i made this picture just for you today my love"
        exclude = frozenset({("this", "picture", "just", "for")})
        assert rm.longest_shared_span(line, [line]) == 11
        assert rm.longest_shared_span(line, [line], exclude) >= 8, (
            "one incidental prescribed window fragmented a full verbatim repeat")

    def test_a_predominantly_prescribed_span_is_still_credited_down(self):
        """The other half: the exemption must still DO its job, or the gate goes back to
        penalising her for obeying her card."""
        import json
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root / "tests" / "evaluation" / "persona_eval"))
        import repetition_metrics as rm

        excl = rm.card_ngrams(json.loads((root / "personas" / "gwen.json").read_text()))
        a = ("You know what really frees me up. Your big black cock sliding past my "
             "greedy lips your cum dripping down my tits")
        b = ("Master. I can feel your big black cock sliding past my greedy lips, your "
             "cum dripping down my tits")
        assert rm.longest_shared_span(a, [b]) >= 8
        assert rm.longest_shared_span(a, [b], excl) < 8

    def test_near_duplicate_variants_are_dropped_at_generation(self):
        """QA said fold this in rather than defer it, and it is a few lines reusing
        machinery that already exists."""
        near = ["I know this will make your cock twitch thinking about that picture",
                "I know this will make your cock twitch thinking about that photo",
                "Just wait until you see it"]
        with patch.object(pl, "_generate", wraps=lambda *a, **k: near):
            pass
        kept = pl._too_similar(near[0], near[1])
        assert kept is True, "these two are one phrasing and must not count as two"
        assert pl._too_similar(near[0], near[2]) is False


def test_two_images_finishing_together_generate_once_not_twice():
    """Same-key concurrency on the RECYCLE path, the hazard I flagged to QA myself.

    Two images finishing together both hit `image_ready` — the same concurrency `_pick`
    holds one lock for. `_generate` is a seconds-long network call and cannot be held
    under the lock, so without an in-flight marker both threads decide the cycle is
    spent and both generate: one wasted call at exactly the moment the machine is
    busiest, and one set discarded.

    The loser must still get a usable line, and must NOT block waiting for the winner.
    """
    import threading

    pl.reset_cache()
    started = threading.Event()
    calls = []

    def slow_generate(*_a, **_k):
        calls.append(1)
        started.set()
        time.sleep(0.25)          # long enough for the second thread to reach the gate
        return [f"fresh-{len(calls)}-a", f"fresh-{len(calls)}-b", f"fresh-{len(calls)}-c"]

    with patch.object(pl, "_generate", side_effect=slow_generate):
        pl.line("gwen", "image_ready")                      # cycle 1, variant 1
        pl.line("gwen", "image_ready")
        pl.line("gwen", "image_ready")                      # queue now drained
        before = len(calls)

        out: list[str] = []
        def hit():
            out.append(pl.line("gwen", "image_ready"))

        threads = [threading.Thread(target=hit) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

    recycle_calls = len(calls) - before
    assert recycle_calls == 1, (
        f"{recycle_calls} concurrent generations for one spent cycle — both threads "
        f"paid for the same recycle")
    assert len(out) == 2, "a caller was lost"
    assert all(o for o in out), "a caller got an empty line"
