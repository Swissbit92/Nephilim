# tests/evaluation/test_manifest_samplers.py
"""A run's sampler settings are part of its identity, and an unknown is not a match.

Measured cause, 2026-09-24: the persona-eval baseline manifest recorded the
embedding model, the companion model, the prompt-builder version and per-persona
card hashes — and nothing about samplers. The 2026-09-22 repair changed what
actually reached the model (tool-brain prose temperature 0.4 -> the card's 0.9,
repeat_penalty 1.0 -> 1.05, repeat_last_n 64 -> 384), so a run before and a run
after were not comparable.

Two separate defects, and the second is the one that mattered:

1. The manifest could not express the axis that moved.
2. `compare_baselines` never read the manifest AT ALL — zero references. It
   compared only `random_baseline`, so adding a field would have changed nothing.
   The blind spot was not a missing field in a checked record; it was an unchecked
   record.

Why a card hash does not already cover this: `persona_def_hashes` hashes the card
file, so it sees a DECLARED `model_preferences` edit — but only for DORMANT
personas (active ones are exempt by design, since they are expected to change),
and never for the env-level fallbacks (`PERSONA_TEMPERATURE`, `OLLAMA_MIN_P`), the
out-of-range-drop behaviour, or the run-level `num_ctx`/`num_predict`. Declared and
effective differ exactly where it matters.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PE = Path(__file__).parent / "persona_eval"
if str(_PE) not in sys.path:
    sys.path.insert(0, str(_PE))

import compare_baselines as cb  # noqa: E402
import frozen_gallery as fg  # noqa: E402

GWEN_LIKE = {"temperature": 0.9, "min_p": 0.05, "repeat_penalty": 1.05, "repeat_last_n": 384}
PRE_FIX = {"temperature": 0.4, "repeat_last_n": 64}


def _mf(sampling=None, **kw):
    return fg.build_manifest(
        kw.pop("personas", []), embedding_model="bge-m3", companion_model="abl-24b",
        sampling=sampling, **kw,
    )


def _baseline(overall=0.8, n=8, manifest=None):
    """Mirrors test_compare_baselines' fixture shape, plus an optional manifest."""
    b = {
        "report": {
            "distinctiveness": {
                "overall": overall,
                "per_persona": {f"p{i}": overall for i in range(n)},
                "random_baseline": 1.0 / n,
            },
            "flatness_rate_overall": 0.0,
        }
    }
    if manifest is not None:
        b["manifest"] = manifest
    return b


class TestFingerprint:
    def test_no_sampling_record_is_the_sentinel_not_a_hash(self):
        """Absence must be nameable. A None or a bare hash of {} would let
        "never recorded" and "recorded as empty" collapse into one value."""
        assert fg.sampling_fingerprint(None) == fg.UNRECORDED
        assert fg.sampling_fingerprint({}) == fg.UNRECORDED

    def test_same_settings_hash_equal_regardless_of_key_order(self):
        """`get_persona_sampling_overrides` emits a VARIABLE key set — every key
        but temperature appears only when something sets it — so an order-sensitive
        hash would report spurious mismatches between identical runs."""
        a = {"gwen": {"temperature": 0.9, "min_p": 0.05}}
        b = {"gwen": {"min_p": 0.05, "temperature": 0.9}}
        assert fg.sampling_fingerprint(a) == fg.sampling_fingerprint(b)

    def test_the_actual_2026_09_22_change_is_visible(self):
        """The regression this exists for: these two are the before and after of
        the sampler repair and must not hash alike."""
        assert fg.sampling_fingerprint({"gwen": PRE_FIX}) != fg.sampling_fingerprint({"gwen": GWEN_LIKE})


class TestManifest:
    def test_schema_version_is_recorded(self):
        assert _mf()["manifest_schema_version"] == fg.MANIFEST_SCHEMA_VERSION

    def test_sampling_is_recorded_and_fingerprinted(self):
        m = _mf(sampling={"gwen": GWEN_LIKE})
        assert m["sampling"] == {"gwen": GWEN_LIKE}
        assert m["sampling_fingerprint"] == fg.sampling_fingerprint({"gwen": GWEN_LIKE})

    def test_a_manifest_without_samplers_says_so_explicitly(self):
        assert _mf()["sampling_fingerprint"] == fg.UNRECORDED

    def test_adding_the_field_did_not_break_the_existing_fields(self):
        """Additive by construction — the four original fields are what the
        staleness guard already compares."""
        m = _mf(personas=[])
        for k in ("n_personas", "personas", "embedding_model", "companion_model",
                  "prompt_builder_version", "persona_def_hashes"):
            assert k in m


class TestStalenessTreatsSamplersAsVoiceNotSpace:
    """A sampler change moves the VOICE, not the embedding SPACE — so the gallery
    check warns, exactly as it does for companion_model. The hard refusal belongs
    in compare_baselines, where commensurability is actually decided."""

    def test_sampler_change_warns_and_does_not_error(self):
        gal = _mf(sampling={"gwen": PRE_FIX})
        cur = _mf(sampling={"gwen": GWEN_LIKE})
        errors, warnings = fg.check_staleness(cur, gal, active=set())
        assert errors == []
        assert any("sampler settings changed" in w for w in warnings)

    def test_gallery_predating_sampler_recording_is_named_as_such(self):
        """Not "changed" — "cannot verify". The distinction is the whole point."""
        gal = _mf()                                  # v1-shaped: no samplers
        cur = _mf(sampling={"gwen": GWEN_LIKE})
        errors, warnings = fg.check_staleness(cur, gal, active=set())
        assert errors == []
        assert any("predates sampler recording" in w for w in warnings)

    def test_identical_manifests_stay_silent(self):
        """Guards the regression that a new field makes self-comparison noisy."""
        m = _mf(sampling={"gwen": GWEN_LIKE})
        assert fg.check_staleness(m, m, active=set()) == ([], [])

    def test_the_embedding_error_is_still_first(self):
        """`test_staleness_embedding_change_is_hard_error` indexes errors[0], so the
        new block had to be APPENDED. Pinned here so a later insertion cannot
        silently displace it."""
        gal = _mf(sampling={"gwen": PRE_FIX})
        cur = fg.build_manifest([], embedding_model="nomic", companion_model="abl-24b",
                                sampling={"gwen": GWEN_LIKE})
        errors, _ = fg.check_staleness(cur, gal, active=set())
        assert errors and "embedding model" in errors[0]


class TestCompareBaselinesNowReadsTheManifest:
    """The half that was entirely missing."""

    def test_differing_samplers_refuse_a_verdict(self):
        off = _baseline(0.80, manifest=_mf(sampling={"gwen": PRE_FIX}))
        on = _baseline(0.85, manifest=_mf(sampling={"gwen": GWEN_LIKE}))
        res = cb.compare(off, on)
        assert res["commensurable"] is False
        assert res["verdict"] == "INCOMMENSURABLE"
        assert "sampler settings differ" in res["reason"]

    def test_a_better_score_does_not_buy_its_way_past_the_guard(self):
        """The candidate scores HIGHER. A guard that only fired on regressions
        would be useless — the confound flatters as easily as it penalises."""
        off = _baseline(0.50, manifest=_mf(sampling={"gwen": PRE_FIX}))
        on = _baseline(0.99, manifest=_mf(sampling={"gwen": GWEN_LIKE}))
        assert cb.compare(off, on)["verdict"] == "INCOMMENSURABLE"

    def test_matching_samplers_allow_a_verdict(self):
        m = _mf(sampling={"gwen": GWEN_LIKE})
        res = cb.compare(_baseline(0.80, manifest=m), _baseline(0.85, manifest=m))
        assert res["commensurable"] is True
        assert res["verdict"] == "MATCH-OR-BEAT"

    def test_one_side_unrecorded_refuses(self):
        """THE case this was built for: our only 8-persona ruler is v1-shaped, so
        gating a v2 candidate against it must refuse rather than assume agreement.
        An unknown is not a match."""
        off = _baseline(0.80, manifest=_mf())                       # ruler, no samplers
        on = _baseline(0.85, manifest=_mf(sampling={"gwen": GWEN_LIKE}))
        res = cb.compare(off, on)
        assert res["verdict"] == "INCOMMENSURABLE"
        assert "does not record the sampler settings" in res["reason"]
        assert "ruler" in res["reason"]

    def test_neither_side_has_a_manifest_still_gates(self):
        """Both-absent is the pre-manifest era and the synthetic-fixture case.
        Refusing here would break the ten N-guard unit tests and every historical
        artifact, for no information gained — nothing is knowable either way."""
        res = cb.compare(_baseline(0.80), _baseline(0.85))
        assert res["commensurable"] is True

    def test_the_sampler_guard_runs_before_the_N_guard(self):
        """Ordering matters for the message, not the outcome: a run with both a
        different N and different samplers should name the sampler problem, because
        re-cutting under matched samplers is the action either way."""
        off = _baseline(0.80, n=7, manifest=_mf(sampling={"gwen": PRE_FIX}))
        on = _baseline(0.85, n=8, manifest=_mf(sampling={"gwen": GWEN_LIKE}))
        assert "sampler" in cb.compare(off, on)["reason"]


class TestTheGuardCoversFutureFieldsForFree:
    def test_bumping_the_schema_makes_old_artifacts_incomparable_automatically(self):
        """The property worth having: comparability is keyed on a fingerprint, so a
        changed recipe invalidates history without anyone remembering to add a
        check. This is what the previous design lacked — the guard could only see
        fields someone had hand-written a comparison for."""
        assert fg.MANIFEST_SCHEMA_VERSION >= 2
        v1_like = _baseline(0.8, manifest={
            "n_personas": 8, "personas": [], "embedding_model": "bge-m3",
            "companion_model": "abl-24b", "prompt_builder_version": "lean-v1",
            "persona_def_hashes": {},
        })
        v2 = _baseline(0.8, manifest=_mf(sampling={"gwen": GWEN_LIKE}))
        assert cb.compare(v1_like, v2)["verdict"] == "INCOMMENSURABLE"
