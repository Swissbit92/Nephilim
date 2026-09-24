"""Frozen reference gallery for the persona-eval distinctiveness metric.

Attribution accuracy is a discrimination-against-the-others metric (chance 1/N),
so re-probing only a subset of personas against a *shrunken* label space silently
inflates their scores. The frozen gallery avoids that: it loads the DORMANT
personas' responses from an existing baseline, re-embeds them as fixed reference
prototypes (via ``attribution_accuracy(..., frozen_personas=...)``), and re-probes
only the ACTIVE personas against the full N-centroid field — chance stays 1/N and
the confusable dormant neighbours stay live competitors.

This is closed-set identification against a fixed gallery / a frozen-prototype
NCM classifier. It is valid ONLY while the dormant personas' voices are unchanged
— so every gallery carries a **manifest** (embedding model, companion model,
prompt-builder version, per-persona definition hashes, and the RESOLVED sampler
settings) and a staleness check
refuses on an embedding-model change (frozen vectors would live in a different
space) and warns on dormant-persona / model drift. See the README + the
compare_baselines commensurability guard.

Pure functions are unit-tested headless; ``default_manifest`` (live coordinator
settings) is the only live-wiring bit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_PERSONA_DIR = Path(__file__).resolve().parents[3] / "personas"
_BASELINE_DIR = Path(__file__).parent / "baselines"

# Bump when the prompt builder changes in a way that moves persona voices, so a
# gallery frozen under the old builder is flagged stale.
PROMPT_BUILDER_VERSION = "lean-v1"

# Manifest schema version. Bump when a field is ADDED to the comparability set, so
# every artifact written before the bump is explicitly incomparable rather than
# silently missing a check.
#
# v2 (2026-09-24) adds `sampling`. Measured cause: the 2026-09-22 sampler repair
# changed what actually reached the model (tool-brain prose temperature 0.4 -> the
# card's 0.9, repeat_penalty 1.0 -> 1.05, repeat_last_n 64 -> 384) and NOTHING in
# any artifact recorded it. The v1 manifest cannot express the axis that moved, so
# "absent" here must read as "incomparable", never as "nothing to check" — that
# equivalence is exactly how the blind spot survived.
MANIFEST_SCHEMA_VERSION = 2

# Explicit sentinel for an artifact written before a comparability field existed.
# Deliberately not None and not absent: a reader must be able to tell "recorded and
# equal", "recorded and different", and "never recorded" apart, and only the last
# one may not be treated as agreement.
UNRECORDED = "unrecorded_pre_v2"


# ----- hashing / manifest (pure) -----


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sampling_fingerprint(sampling: Optional[dict]) -> str:
    """Stable hash of a sampling record, or UNRECORDED when there is none.

    Hashed over canonical JSON (sorted keys) rather than the dict's repr, because
    ``get_persona_sampling_overrides`` emits a VARIABLE key set — every key except
    temperature appears only when the persona or a global fallback sets one — so
    dict ordering is not stable across runs and an order-sensitive hash would
    report spurious mismatches.
    """
    if not sampling:
        return UNRECORDED
    return _sha16(json.dumps(sampling, sort_keys=True, separators=(",", ":")))


def persona_def_hash(persona_key: str, persona_dir: Optional[Path] = None) -> Optional[str]:
    """Short content hash of a persona's JSON definition, or None if absent."""
    f = (persona_dir or _PERSONA_DIR) / f"{persona_key}.json"
    if not f.exists():
        return None
    return _sha16(f.read_text(encoding="utf-8"))


def build_manifest(
    personas: List[str],
    *,
    embedding_model: str,
    companion_model: str,
    persona_dir: Optional[Path] = None,
    prompt_builder_version: str = PROMPT_BUILDER_VERSION,
    sampling: Optional[Dict[str, dict]] = None,
    sampling_env: Optional[dict] = None,
) -> dict:
    """Content-addressed record of everything the frozen centroids depend on.

    Checked before a gallery is trusted (see ``check_staleness``): an artifact is
    only comparable to a later run when these inputs still match.

    ``sampling`` is the RESOLVED per-persona override dict, not the card's declared
    ``model_preferences``. The distinction is the point: declared and effective
    differ wherever a global fallback fills in (``PERSONA_TEMPERATURE``,
    ``OLLAMA_MIN_P``) or a value is out of range and dropped rather than clamped.
    ``persona_def_hashes`` already covers the declared half — it hashes the whole
    card file — so recording the declaration again would add nothing, while the
    resolved dict covers the half no file hash can see.

    ``sampling_env`` holds the run-level knobs that are not per-persona at all
    (context window, output cap, completion backend). The completion backend
    matters more than it looks: on the legacy path ``min_p`` never reaches the wire
    unless it is ``http``, so two runs can record an identical ``min_p`` and have
    sent different things.
    """
    ps = sorted(personas)
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "n_personas": len(ps),
        "personas": ps,
        "embedding_model": embedding_model,
        "companion_model": companion_model,
        "prompt_builder_version": prompt_builder_version,
        "persona_def_hashes": {p: persona_def_hash(p, persona_dir) for p in ps},
        "sampling": sampling if sampling else None,
        "sampling_env": sampling_env if sampling_env else None,
        "sampling_fingerprint": sampling_fingerprint(sampling),
    }


def check_staleness(
    current: dict, gallery: Optional[dict], active: Set[str]
) -> Tuple[List[str], List[str]]:
    """Compare a current manifest against a gallery's frozen manifest.

    Returns ``(errors, warnings)``. Errors are hard-stop (the comparison would be
    meaningless); warnings are advisory. Only DORMANT (frozen) personas matter for
    drift — active personas are *expected* to change, that's the point of re-probing
    them. A missing gallery manifest is a warning, not a silent pass.
    """
    if not gallery:
        return [], [
            "gallery has no manifest — cannot verify it is not stale "
            "(frozen before manifests existed?); results may not be comparable"
        ]

    errors: List[str] = []
    warnings: List[str] = []

    # Embedding model defines the vector SPACE. A change makes frozen vectors and
    # freshly-embedded ones incomparable → hard stop.
    if current.get("embedding_model") != gallery.get("embedding_model"):
        errors.append(
            f"embedding model changed {gallery.get('embedding_model')!r} -> "
            f"{current.get('embedding_model')!r}: frozen centroids live in a "
            "different vector space; re-freeze the gallery"
        )

    # Companion model / prompt builder define the VOICE. A change may have drifted
    # the dormant personas away from their frozen centroids → advisory.
    if current.get("companion_model") != gallery.get("companion_model"):
        warnings.append(
            f"companion model changed {gallery.get('companion_model')!r} -> "
            f"{current.get('companion_model')!r}: dormant personas' voices may "
            "have drifted from their frozen centroids"
        )
    if current.get("prompt_builder_version") != gallery.get("prompt_builder_version"):
        warnings.append(
            "prompt builder version changed since the gallery was frozen: dormant "
            "voices may have drifted"
        )

    # Sampler drift. A WARNING here, not an error, and deliberately so: this
    # function guards the frozen GALLERY (are the dormant centroids still valid),
    # and samplers move the VOICE, not the embedding SPACE — the same reasoning that
    # makes companion_model advisory. The hard refusal for comparing two finished
    # baselines across a sampler change belongs in compare_baselines, which is where
    # commensurability is decided and where — until now — the manifest was never
    # read at all.
    cur_fp = current.get("sampling_fingerprint", UNRECORDED)
    gal_fp = gallery.get("sampling_fingerprint", UNRECORDED)
    if gal_fp == UNRECORDED and cur_fp != UNRECORDED:
        warnings.append(
            "gallery predates sampler recording (manifest schema "
            f"v{gallery.get('manifest_schema_version', 1)}): cannot verify the frozen "
            "voices were generated under the current sampler settings"
        )
    elif cur_fp != gal_fp:
        warnings.append(
            f"sampler settings changed since the gallery was frozen ({gal_fp} -> "
            f"{cur_fp}): dormant voices may have drifted"
        )

    # Per-dormant-persona definition drift.
    gh = gallery.get("persona_def_hashes", {})
    ch = current.get("persona_def_hashes", {})
    for p in sorted(set(gh) - active):
        if p in ch and ch[p] != gh[p]:
            warnings.append(
                f"frozen persona '{p}' definition changed since freeze: its centroid may be stale"
            )
    return errors, warnings


# ----- baseline / gallery loading (pure given a path) -----


def resolve_baseline(label_or_path: str) -> Path:
    """Resolve a baseline label ('abliterated') to its newest file, or a path."""
    p = Path(label_or_path)
    if p.exists():
        return p
    matches = sorted(_BASELINE_DIR.glob(f"baseline_{label_or_path}_*.json"))
    if not matches:
        raise FileNotFoundError(
            f"no baseline for label {label_or_path!r} under {_BASELINE_DIR} (and not a path)"
        )
    return matches[-1]


def load_baseline(label_or_path: str) -> dict:
    with open(resolve_baseline(label_or_path), encoding="utf-8") as f:
        return json.load(f)


# Sources that are NOT genuine model voice — a canned/abstention/error string
# (e.g. the ADR-007 groundedness gate returns a fixed "want me to search?" line
# with source "groundedness_abstain"). Freezing one as a reference prototype would
# build the centroid partly from a model-INDEPENDENT constant, making personas look
# artificially identical across models. Excluded from the gallery by default.
# NOTE: "gallery" is deliberately NOT here — a row re-served from an earlier gallery
# is still genuine voice text, so a gallery-produced baseline can be reused as a
# gallery source without its dormant personas silently vanishing.
NON_VOICE_SOURCES = frozenset({"groundedness_abstain", "error"})


def dormant_responses(
    baseline: dict,
    active: Set[str],
    category: str = "distinctiveness",
    exclude_sources: frozenset[str] | set[str] = NON_VOICE_SOURCES,
) -> Dict[str, List[str]]:
    """Extract genuine-voice text for personas NOT in ``active`` — the frozen gallery.

    These become fixed reference prototypes. Only the requested category
    (distinctiveness by default) is used, matching what attribution scores over.
    Rows whose ``source`` is in ``exclude_sources`` are skipped: a canned
    abstention/error string is a model-independent constant and would pollute the
    frozen centroid with non-voice text. Rows with no ``source`` field are kept
    (older baselines predate the field).
    """
    out: Dict[str, List[str]] = {}
    for r in baseline.get("results", []):
        if r.get("category") != category or not r.get("answer"):
            continue
        if r.get("source") in exclude_sources:
            continue
        p = r.get("persona")
        if p and p not in active:
            out.setdefault(p, []).append(r["answer"])
    return out


def dormant_result_rows(
    dormant: Dict[str, List[str]], category: str = "distinctiveness"
) -> List[dict]:
    """Synthesize result rows for gallery-sourced dormant responses so they flow
    through ``compute_report`` alongside freshly-collected active rows. Marked
    ``source: "gallery"`` so a reader can tell them from live generations.
    """
    rows: List[dict] = []
    for persona, texts in dormant.items():
        for i, text in enumerate(texts):
            rows.append(
                {
                    "persona": persona,
                    "category": category,
                    "probe_id": f"gallery-{i}",
                    "prompt": "",
                    "answer": text,
                    "source": "gallery",
                    "elapsed": 0.0,
                }
            )
    return rows


def default_manifest(
    personas: List[str], persona_dir: Optional[Path] = None
) -> dict:  # pragma: no cover - live wiring
    """Build a manifest from live coordinator settings (embedding + companion model)."""
    import sys

    src = Path(__file__).resolve().parents[3] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from coordinator.config import get_settings  # type: ignore

    from coordinator.config import get_persona_sampling_overrides  # type: ignore

    s = get_settings()
    pdir = persona_dir or _PERSONA_DIR

    # Resolve each persona's EFFECTIVE overrides the same way routes/chat.py does.
    # Honest limitation, stated here because it is the failure this whole field
    # exists to prevent: this resolves in the HARNESS process, so it records what
    # this process's settings + cards would produce, not what the live server
    # actually sent. A server on a different `.env` would disagree, and nothing
    # here can see that — which is why transport_preflight.verify_resolved_config
    # exists and should be run alongside.
    sampling: Dict[str, dict] = {}
    for key in sorted(personas):
        f = pdir / f"{key}.json"
        if not f.exists():
            continue
        try:
            card = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a malformed card must not break the manifest
            continue
        sampling[key] = get_persona_sampling_overrides(card)

    return build_manifest(
        personas,
        embedding_model=s.memory.embedding_model,
        companion_model=s.ollama.model,
        persona_dir=persona_dir,
        sampling=sampling or None,
        sampling_env={
            "context_window": s.ollama.context_window,
            "max_output_tokens": s.ollama.max_output_tokens,
            "completion_backend": getattr(s.ollama, "completion_backend", None),
        },
    )
