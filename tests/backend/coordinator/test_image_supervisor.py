# tests/backend/coordinator/test_image_supervisor.py
"""The generation supervisor: liveness, cancellation, and lying successes.

No mflux, no app, no DB. Every generator here is a tiny `python -c`, so the
suite runs in seconds and exercises the failure modes a real generator only
produces by accident.

The load-bearing tests, and what each one would otherwise let through:

- `test_lockf_discriminator` — pins WHY flock and not lockf. A POSIX record
  lock would release when the parent closes its fd, so every live job would
  probe DIED and the sweep would fail jobs that are running fine.
- `test_success_with_no_output_is_a_failure` — mflux exits 0 having written
  nothing when its save throws. Trusting rc alone ships a succeeded row
  pointing at no file.
- `test_torn_png_is_rejected` — mflux rewrites the PNG three times at the same
  path, so a reader can see a perfect header and a missing tail.
- `test_cancel_reaches_a_grandchild` — `Popen.kill()` would signal `/bin/sh`
  and leave the generator running.

⚠️ The two cancellation tests are `darwin_only`. They assert what `killpg` reports
after a kill, and Linux keeps zombies in the process group while Darwin does not, so
on Linux they measure the runner rather than this code. The measurement and the
consequence are in `tests/conftest.py` — read it before assuming a green CI covers these.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

from src.coordinator.services.image_gen import supervisor as sv
from src.coordinator.services.image_gen.verify import walk_png


def _wait_until(predicate, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _png_bytes(width=8, height=8) -> bytes:
    """A real, minimal PNG — built here so the test owns its own fixture."""
    import struct

    def chunk(tag, payload):
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _gen(code: str) -> list[str]:
    return [sys.executable, "-c", code]


# ---------- liveness: the three states ----------


def test_liveness_running_then_finished(tmp_path):
    s = sv.spawn(tmp_path, _gen("import time; time.sleep(2)"))
    assert sv.probe(tmp_path).state is sv.State.RUNNING
    s.popen.wait()
    assert _wait_until(lambda: sv.probe(tmp_path).state is sv.State.FINISHED)
    assert sv.probe(tmp_path).returncode == 0


def test_liveness_reports_a_nonzero_exit(tmp_path):
    s = sv.spawn(tmp_path, _gen("import sys; sys.exit(3)"))
    s.popen.wait()
    assert _wait_until(lambda: sv.probe(tmp_path).returncode == 3)
    assert sv.probe(tmp_path).state is sv.State.FINISHED


def test_liveness_died_when_the_child_is_killed(tmp_path):
    """SIGKILL leaves no rc file. Lock free + no rc must mean DIED, not
    FINISHED(0) — otherwise a killed job reports success."""
    s = sv.spawn(tmp_path, _gen("import time; time.sleep(30)"))
    assert sv.probe(tmp_path).state is sv.State.RUNNING
    os.killpg(s.pgid, signal.SIGKILL)
    assert _wait_until(lambda: sv.probe(tmp_path).state is sv.State.DIED)
    assert not (tmp_path / sv.RC_NAME).exists()


def test_liveness_on_a_directory_that_never_ran(tmp_path):
    assert sv.probe(tmp_path).state is sv.State.DIED


def test_liveness_survives_a_fresh_process(tmp_path):
    """The restart path, without restarting: probe from a SEPARATE interpreter
    that shares no in-process state with the spawner."""
    s = sv.spawn(tmp_path, _gen("import time; time.sleep(3)"))
    repo = Path(__file__).resolve().parents[3]
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'src');"
            "from coordinator.services.image_gen import supervisor as sv;"
            f"print(sv.probe({str(tmp_path)!r}).state.value)",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "running", out.stderr
    os.killpg(s.pgid, signal.SIGKILL)


def test_lockf_discriminator(tmp_path):
    """Pins why the lock is flock and not lockf.

    Same scheme, same live child, one API swapped: a POSIX record lock is
    per-PROCESS, so the parent's os.close() releases it and the job probes as
    free while it is still running. If this ever stops distinguishing them,
    the whole restart-safety design rests on nothing.
    """

    def holds_after_parent_closes(locker) -> bool:
        lock = tmp_path / f"{locker.__name__}.lock"
        fd = os.open(lock, os.O_CREAT | os.O_RDWR)
        locker(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        p = subprocess.Popen(
            _gen("import time; time.sleep(3)"), start_new_session=True, pass_fds=(fd,)
        )
        os.close(fd)
        time.sleep(0.4)
        probe_fd = os.open(lock, os.O_CREAT | os.O_RDWR)
        try:
            locker(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = False
        except OSError:
            held = True
        finally:
            os.close(probe_fd)
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            p.wait()
        return held

    assert holds_after_parent_closes(fcntl.flock) is True
    assert holds_after_parent_closes(fcntl.lockf) is False


# ---------- detachment ----------


def test_child_gets_its_own_process_group(tmp_path):
    """Without this, launchd's process-group sweep kills the generation on
    every coordinator restart — uvicorn's PGID equals its own PID."""
    s = sv.spawn(tmp_path, _gen("import time; time.sleep(2)"))
    assert s.pgid != os.getpgid(os.getpid())
    assert os.getpgid(s.pid) == s.pid
    os.killpg(s.pgid, signal.SIGKILL)


def test_a_second_spawn_in_the_same_dir_is_refused(tmp_path):
    s = sv.spawn(tmp_path, _gen("import time; time.sleep(3)"))
    with pytest.raises(RuntimeError, match="already running"):
        sv.spawn(tmp_path, _gen("pass"))
    os.killpg(s.pgid, signal.SIGKILL)


# ---------- cancellation ----------


@pytest.mark.darwin_only
def test_cancel_reaches_a_grandchild(tmp_path):
    """Popen.kill() signals /bin/sh and leaves the generator alive. killpg is
    the whole job."""
    marker = tmp_path / "grandchild.pid"
    s = sv.spawn(
        tmp_path,
        _gen(
            "import subprocess, sys, pathlib, time;"
            f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
            f"pathlib.Path({str(marker)!r}).write_text(str(p.pid));"
            "time.sleep(60)"
        ),
    )
    assert _wait_until(marker.exists)
    grandchild = int(marker.read_text())

    assert sv.cancel(s.pgid, s.proc_start, grace_seconds=3) is True

    def gone():
        try:
            os.kill(grandchild, 0)
            return False
        except ProcessLookupError:
            return True

    assert _wait_until(gone, timeout=8), "grandchild survived — killpg did not reach it"


@pytest.mark.darwin_only
def test_cancel_escalates_past_a_term_ignorer(tmp_path):
    s = sv.spawn(
        tmp_path,
        _gen("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
             "time.sleep(60)"),
    )
    time.sleep(0.5)
    assert sv.cancel(s.pgid, s.proc_start, grace_seconds=1) is True
    assert _wait_until(lambda: sv.probe(tmp_path).state is not sv.State.RUNNING)


def test_cancel_refuses_a_recycled_pid(tmp_path):
    """A stale pgid whose start time has moved must not be signalled — it is
    now somebody else's process."""
    s = sv.spawn(tmp_path, _gen("import time; time.sleep(5)"))
    assert sv.cancel(s.pgid, recorded_start=s.proc_start - 9999, grace_seconds=1) is False
    assert sv.probe(tmp_path).state is sv.State.RUNNING  # untouched
    os.killpg(s.pgid, signal.SIGKILL)


def test_cancel_on_an_already_dead_group_is_true(tmp_path):
    s = sv.spawn(tmp_path, _gen("pass"))
    s.popen.wait()
    time.sleep(0.3)
    assert sv.cancel(s.pgid, grace_seconds=1) is True


# ---------- "success" that is not ----------


def test_success_with_no_output_is_a_failure(tmp_path):
    """mflux swallows save errors and still exits 0. rc is not evidence."""
    s = sv.spawn(tmp_path, _gen("pass"))
    s.popen.wait()
    assert _wait_until(lambda: sv.probe(tmp_path).returncode == 0)
    assert not (tmp_path / sv.OUTPUT_NAME).exists()


def test_torn_png_is_rejected(tmp_path):
    """mflux writes the PNG three times at the same path. A read between
    writes has a valid header and no IEND."""
    full = _png_bytes()
    torn = tmp_path / "torn.png"
    torn.write_bytes(full[: len(full) // 2])
    result = walk_png(torn)
    assert result.ok is False
    assert "truncated" in result.reason or "IEND" in result.reason


def test_a_real_png_passes(tmp_path):
    good = tmp_path / "good.png"
    good.write_bytes(_png_bytes(16, 16))
    r = walk_png(good)
    assert r.ok and (r.width, r.height) == (16, 16)


def test_corrupt_crc_is_rejected(tmp_path):
    """Bit-rot inside a chunk still has a valid header and an IEND."""
    data = bytearray(_png_bytes())
    data[40] ^= 0xFF
    p = tmp_path / "bitrot.png"
    p.write_bytes(bytes(data))
    assert walk_png(p).ok is False


@pytest.mark.parametrize(
    "payload,why",
    [(b"", "empty"), (b"GIF89a", "magic"), (b"\x89PNG\r\n\x1a\n", "IHDR")],
)
def test_obvious_non_pngs_are_rejected(tmp_path, payload, why):
    p = tmp_path / "bad.png"
    p.write_bytes(payload)
    assert walk_png(p).ok is False


def test_verify_never_raises_on_a_missing_file(tmp_path):
    assert walk_png(tmp_path / "nope.png").ok is False


# ---------- stall signal ----------


def test_progress_size_grows_while_the_job_writes(tmp_path):
    s = sv.spawn(
        tmp_path,
        _gen("import sys, time\n"
             "for _ in range(20):\n"
             "    sys.stderr.write('step\\r'); sys.stderr.flush(); time.sleep(0.1)"),
    )
    assert _wait_until(lambda: sv.progress_size(tmp_path) > 0, timeout=5)
    first = sv.progress_size(tmp_path)
    assert _wait_until(lambda: sv.progress_size(tmp_path) > first, timeout=5)
    os.killpg(s.pgid, signal.SIGKILL)


def test_progress_size_is_flat_for_a_stalled_job(tmp_path):
    """The real signal must not move when the job is wedged."""
    s = sv.spawn(
        tmp_path,
        _gen("import sys, time; sys.stderr.write('x'); sys.stderr.flush();"
             "time.sleep(30)"),
    )
    assert _wait_until(lambda: sv.progress_size(tmp_path) > 0, timeout=5)
    size = sv.progress_size(tmp_path)
    time.sleep(1.0)
    assert sv.progress_size(tmp_path) == size
    os.killpg(s.pgid, signal.SIGKILL)


def test_progress_size_on_a_missing_log_is_zero(tmp_path):
    assert sv.progress_size(tmp_path) == 0
