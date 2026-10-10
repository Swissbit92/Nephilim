# tests/backend/coordinator/test_resource_arbiter.py
"""The machine arbiter, the Ollama eviction, and the memory watchdog.

The load-bearing tests, and what each would otherwise let through:

- `test_unload_does_not_trust_the_response` — the measured bug. Ollama answers
  `{"done_reason": "unload"}` in 2 ms while the model is still resident, so a
  caller that trusted the 200 would launch a 20 GB generation on top of 17 GiB.
- `test_unload_reports_failure_when_it_never_leaves` — the alternative to
  reporting a success that did not happen.
- `test_watchdog_ignores_a_dip` / `test_watchdog_never_kills_on_a_broken_gauge`
  — a 332-second job must not die on one sample, nor on vm_stat failing.
- `test_wait_for_idle_wakes_on_release` — if the notify is missed, five
  background threads park until their timeout on every generation.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from src.coordinator.services import ollama_admin as oa
from src.coordinator.services.resource_arbiter import (
    MemoryWatchdog,
    ResourceArbiter,
    ResourceBusyError,
    free_memory_bytes,
)

GIB = 2**30


# ---------- the lease ----------


def test_lease_is_exclusive():
    arb = ResourceArbiter()
    with arb.exclusive("image-gen"):
        assert arb.is_exclusive()
        with pytest.raises(ResourceBusyError, match="cannot take the machine"):
            with arb.exclusive("another-job"):
                pass
    assert not arb.is_exclusive()


def test_lease_is_released_when_the_body_raises():
    """A generation that crashes must not strand the machine as leased."""
    arb = ResourceArbiter()
    with pytest.raises(ValueError):
        with arb.exclusive("image-gen"):
            raise ValueError("boom")
    assert not arb.is_exclusive()
    with arb.exclusive("next-job"):  # must be takeable
        pass


def test_check_admitted_refuses_only_while_leased():
    arb = ResourceArbiter()
    arb.check_admitted("chat turn")  # idle: no raise
    with arb.exclusive("image-gen"):
        with pytest.raises(ResourceBusyError, match="chat turn refused"):
            arb.check_admitted("chat turn")
    arb.check_admitted("chat turn")


def test_refusal_names_the_holder_and_says_the_model_is_unloaded():
    """The message is read by a user via the persona — it has to explain."""
    arb = ResourceArbiter()
    with arb.exclusive("image-gen"):
        with pytest.raises(ResourceBusyError) as exc:
            arb.check_admitted("chat turn")
    text = str(exc.value)
    assert "image-gen" in text
    assert "unloaded" in text


def test_describe_reports_idle_and_held():
    arb = ResourceArbiter()
    assert arb.describe() == "idle"
    with arb.exclusive("image-gen"):
        assert "leased to image-gen" in arb.describe()


# ---------- wait_for_idle (the background-thread path) ----------


def test_wait_for_idle_returns_immediately_when_idle():
    arb = ResourceArbiter()
    t = time.monotonic()
    assert arb.wait_for_idle(timeout=5) is True
    assert time.monotonic() - t < 0.5


def test_wait_for_idle_wakes_on_release():
    """Without the notify, five background threads park until timeout on
    every single generation."""
    arb = ResourceArbiter()
    woke = threading.Event()
    result = {}

    def waiter():
        result["ok"] = arb.wait_for_idle(timeout=10)
        woke.set()

    cm = arb.exclusive("image-gen")
    cm.__enter__()
    th = threading.Thread(target=waiter, daemon=True)
    th.start()
    time.sleep(0.3)
    assert not woke.is_set(), "waiter did not park while the lease was held"

    t = time.monotonic()
    cm.__exit__(None, None, None)
    assert woke.wait(timeout=5), "waiter was never woken"
    assert result["ok"] is True
    assert time.monotonic() - t < 2.0, "woke only on the poll, not the notify"


def test_wait_for_idle_times_out_rather_than_hanging():
    arb = ResourceArbiter()
    with arb.exclusive("image-gen"):
        assert arb.wait_for_idle(timeout=0.5) is False


# ---------- the memory watchdog ----------


def test_watchdog_kills_only_after_the_grace_period():
    wd = MemoryWatchdog(min_free_bytes=6 * GIB, grace_seconds=0.3)
    assert wd.sample(free_bytes=2 * GIB) is False  # first breach starts grace
    assert wd.sample(free_bytes=2 * GIB) is False
    time.sleep(0.35)
    assert wd.sample(free_bytes=2 * GIB) is True


def test_watchdog_ignores_a_dip():
    """macOS reclaims lazily. A 332s generation must survive one low sample."""
    wd = MemoryWatchdog(min_free_bytes=6 * GIB, grace_seconds=0.3)
    assert wd.sample(free_bytes=2 * GIB) is False
    assert wd.sample(free_bytes=20 * GIB) is False  # recovered
    time.sleep(0.35)
    assert wd.sample(free_bytes=2 * GIB) is False, "grace did not reset on recovery"


def test_watchdog_never_kills_on_a_broken_gauge():
    """free_memory_bytes returns -1 when it cannot tell. Reading that as
    'no memory' would kill every job whenever vm_stat hiccups."""
    wd = MemoryWatchdog(min_free_bytes=6 * GIB, grace_seconds=0.0)
    for _ in range(5):
        assert wd.sample(free_bytes=-1) is False


def test_watchdog_is_quiet_above_the_floor():
    wd = MemoryWatchdog(min_free_bytes=6 * GIB, grace_seconds=0.0)
    assert wd.sample(free_bytes=6 * GIB) is False  # exactly at the floor
    assert wd.sample(free_bytes=40 * GIB) is False


@pytest.mark.darwin_only
def test_free_memory_reads_the_real_machine():
    """Not a mock: the instrument has to work here, since the whole design
    rests on it being the one thing that sees both workloads."""
    free = free_memory_bytes()
    assert free > 0, "vm_stat parse failed on this machine"
    assert free < 64 * GIB, f"implausible: {free / GIB:.1f} GiB"


def test_free_memory_returns_minus_one_when_vm_stat_fails():
    with patch("src.coordinator.services.resource_arbiter.subprocess.run",
               side_effect=OSError("no such binary")):
        assert free_memory_bytes() == -1


# ---------- Ollama eviction ----------


class _FakePS:
    """A fake /api/ps whose answer can change between polls."""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def __call__(self, base_url, path):
        if path == "/api/ps":
            self.calls += 1
            idx = min(self.calls - 1, len(self.sequence) - 1)
            models = self.sequence[idx]
            return {"models": [
                {"model": m, "size_vram": 17 * GIB, "expires_at": "2319-01-11T23:13:54"}
                for m in models
            ]}
        raise AssertionError(path)


def test_unload_does_not_trust_the_response():
    """THE measured bug, pinned.

    Ollama returns {"done": true, "done_reason": "unload"} in ~2 ms even while
    a generation is in flight and the model is fully resident — verified live
    on 0.34.2. The model only left 0.1 s after that in-flight request finished.
    A caller that believed the body would start a 20 GB job on top of 17 GiB.

    Here /api/ps reports the model present for three polls, then gone. unload()
    must keep polling, and the elapsed time must show it waited.
    """
    ps = _FakePS([["chat-model"], ["chat-model"], ["chat-model"], []])
    lying_response = {"done": True, "done_reason": "unload"}

    with patch.object(oa, "_post", return_value=lying_response), \
            patch.object(oa, "_get", side_effect=ps), \
            patch.object(oa, "_POLL_SECONDS", 0.01):
        result = oa.unload("http://x", "chat-model", timeout_seconds=5)

    assert result.unloaded is True
    assert ps.calls >= 4, "stopped polling as soon as the response said unload"


def test_unload_reports_failure_when_it_never_leaves():
    ps = _FakePS([["chat-model"]])
    with patch.object(oa, "_post", return_value={"done_reason": "unload"}), \
            patch.object(oa, "_get", side_effect=ps), \
            patch.object(oa, "_POLL_SECONDS", 0.01):
        result = oa.unload("http://x", "chat-model", timeout_seconds=0.2)

    assert result.unloaded is False
    assert result.still_resident_vram == 17 * GIB


def test_unload_succeeds_when_the_request_itself_fails_but_it_is_gone():
    """An already-evicted model makes the POST fail. That is not an error —
    the postcondition holds. Raising would turn 'nothing to do' into a failed
    generation."""
    ps = _FakePS([[]])
    with patch.object(oa, "_post", side_effect=RuntimeError("connection refused")), \
            patch.object(oa, "_get", side_effect=ps):
        result = oa.unload("http://x", "chat-model", timeout_seconds=1)
    assert result.unloaded is True


def test_resident_models_is_empty_when_ollama_is_unreachable():
    """A guard that raises on an unreachable server converts a harmless
    condition into a failed generation. An unreachable Ollama holds nothing."""
    with patch.object(oa, "_get", side_effect=RuntimeError("no route")):
        assert oa.resident_models("http://x") == []
        assert oa.resident_vram_bytes("http://x") == 0
        assert oa.is_resident("http://x", "anything") is False


def test_unload_sends_integer_zero_not_the_string():
    """Ollama parses keep_alive as a Go duration: a bare numeric STRING is a
    400 ('time: missing unit in duration'). The same trap wire_keep_alive
    documents for '-1'."""
    captured = {}

    def fake_post(base_url, path, payload):
        captured.update(payload)
        return {"done_reason": "unload"}

    with patch.object(oa, "_post", side_effect=fake_post), \
            patch.object(oa, "_get", side_effect=_FakePS([[]])):
        oa.unload("http://x", "chat-model", timeout_seconds=1)

    assert captured["keep_alive"] == 0
    assert not isinstance(captured["keep_alive"], str)


def test_resident_reports_pinned_from_the_expiry_year():
    r = oa.Resident(model="m", size_vram=1, expires_at="2319-01-11T23:13:54")
    assert r.pinned is True
    r = oa.Resident(model="m", size_vram=1, expires_at="2026-10-02T10:01:19")
    assert r.pinned is False
    r = oa.Resident(model="m", size_vram=1, expires_at="")
    assert r.pinned is False


def test_repin_reports_false_when_the_model_is_not_resident_after():
    """A 200 from the load request is not evidence either."""
    with patch.object(oa, "_post", return_value={}), \
            patch.object(oa, "_get", side_effect=_FakePS([[]])):
        assert oa.repin("http://x", "chat-model") is False


def test_repin_reports_true_when_it_is_back():
    with patch.object(oa, "_post", return_value={}), \
            patch.object(oa, "_get", side_effect=_FakePS([["chat-model"]])):
        assert oa.repin("http://x", "chat-model") is True


# ---------- the floor is calibrated, not argued ----------


def test_the_memory_floor_clears_a_real_generation():
    """Regression-pin for a constant that was measured WRONG.

    `min_free_gb` was 6.0, chosen by reasoning. The first real end-to-end
    generation on this machine (331 s, rc 0, valid 1024x1024 PNG) bottomed at
    5.28 GiB free during the VAE decode — the actual peak, not the denoise
    loop, which sat at 19.2 GiB. The watchdog entered its kill grace at
    t≈310 s and the job completed at 331 s: it survived by about a second, and
    a marginally slower run would have been killed while succeeding.

    So the floor must stay clear of the measured minimum. If someone raises it
    back above ~5 GiB, this fails and says why.
    """
    from src.coordinator.config.image_gen import ImageGenSettings

    s = ImageGenSettings()
    measured_floor_gib = 5.28
    assert s.min_free_gb < measured_floor_gib, (
        f"min_free_gb={s.min_free_gb} would kill a HEALTHY generation: a real "
        f"run bottomed at {measured_floor_gib} GiB free and still succeeded."
    )
    # ...and must stay high enough to catch the case it exists for: a
    # generation running alongside the 16-19 GiB chat model.
    assert s.min_free_gb >= 1.0


def test_a_healthy_generations_dip_never_breaches_the_floor():
    """Replays the real run's free-memory trace against the configured floor.

    Asserts the floor is never BREACHED, not merely that no kill fired. The
    first version of this test ran the trace through the watchdog and checked
    the return value — which could not fail for the reason it claimed, because
    synthetic samples take no wall-clock time, so the grace period never
    elapses and `sample()` returns False no matter how low the floor is. It
    passed at the old, wrong 6.0 setting. Breach is the honest property: once
    the floor is breached the kill is only a matter of the job lasting longer
    than the grace.
    """
    from src.coordinator.config.image_gen import ImageGenSettings

    floor = ImageGenSettings().min_free_gb
    # Sampled from the real run: steady denoise, the decode dip, recovery.
    trace = [19.19, 19.14, 19.21, 19.11, 19.17, 19.16, 19.13, 19.24,
             19.33, 19.21, 19.25, 5.54, 5.28, 5.40, 20.97]
    breaches = [g for g in trace if g < floor]
    assert not breaches, (
        f"a SUCCESSFUL generation dipped to {breaches} GiB, below the "
        f"{floor} GiB floor — the watchdog would kill it once the dip "
        f"outlasted the grace period"
    )
