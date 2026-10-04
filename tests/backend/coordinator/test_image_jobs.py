# tests/backend/coordinator/test_image_jobs.py
"""The job table, the worker, the reconciler, and the lifespan.

What each load-bearing test would otherwise let through:

- `test_claim_is_atomic_under_concurrency` — two workers running the same
  331-second generation twice on one GPU.
- `test_finish_cannot_resurrect_a_job_the_sweeper_already_failed` — the user
  is told it failed, then it silently succeeds and the message never comes.
- `test_notify_claim_is_exactly_once` — the image delivered twice.
- `test_reconcile_adopts_a_live_job` — the asymmetry that matters: failing a
  LIVE job discards five minutes of work and lies to the user, while leaving a
  dead one merely delays an error. Only positive evidence of death may fail.
- `test_lifespan_sweeps_then_starts_then_stops` — the sweep must adopt a
  live job BEFORE the worker runs, or the worker starts a second generation
  on top of the first.
- `test_bare_testclient_does_not_run_lifespan` — pins the trap that makes
  lifespan tests pass vacuously in this repo.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.coordinator.repositories.image_job_repository import (
    ImageJobRepository,
    JobStatus,
)
from src.coordinator.services.image_gen import reconcile
from src.coordinator.services.image_gen import supervisor as sv


@pytest.fixture
def repo(tmp_path):
    return ImageJobRepository(str(tmp_path / "jobs.db"))


def _make(repo, prompt="a fox"):
    return repo.create(session_id="s1", persona_key="gwen", prompt=prompt)


# ---------- the table ----------


def test_create_and_read_back(repo):
    job = _make(repo)
    assert job.status == JobStatus.QUEUED
    assert len(job.id) == 32 and job.id.islower()
    again = repo.get(job.id)
    assert again is not None and again.prompt == "a fox"


def test_claim_returns_the_oldest_and_marks_it_running(repo):
    first = _make(repo, "first")
    second = _make(repo, "second")
    claimed = repo.claim_next()
    assert claimed is not None
    assert claimed.id == first.id, "claimed out of order"
    assert claimed.status == JobStatus.RUNNING
    assert repo.get(second.id).status == JobStatus.QUEUED


def test_claim_returns_none_on_an_empty_queue(repo):
    assert repo.claim_next() is None


def test_claim_is_atomic_under_concurrency(repo):
    """One job, many threads. Exactly one may win.

    Without the `AND status = 'queued'` predicate two claimers can both see
    the row as queued and both proceed — two 331-second generations on one
    GPU, which is the collision this whole subsystem exists to prevent.
    """
    _make(repo)
    results, lock = [], threading.Lock()
    barrier = threading.Barrier(8)

    def claim():
        barrier.wait()
        got = repo.claim_next()
        if got is not None:
            with lock:
                results.append(got.id)

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 1, f"{len(results)} threads claimed the same job: {results}"


def test_finish_writes_the_terminal_state(repo):
    job = _make(repo)
    repo.claim_next()
    repo.finish(job.id, status=JobStatus.SUCCEEDED, media_path="/x/out.png")
    done = repo.get(job.id)
    assert done.status == JobStatus.SUCCEEDED
    assert done.media_path == "/x/out.png"
    assert done.finished_at and done.is_terminal


def test_finish_refuses_a_non_terminal_status(repo):
    job = _make(repo)
    repo.claim_next()
    with pytest.raises(ValueError, match="not a terminal status"):
        repo.finish(job.id, status=JobStatus.QUEUED)


def test_finish_cannot_resurrect_a_job_the_sweeper_already_failed(repo):
    """The sweeper fails an apparent orphan; the original worker then wakes and
    tries to succeed it. The second write must be dropped — the user has
    already been told it failed."""
    job = _make(repo)
    repo.claim_next()
    repo.finish(job.id, status=JobStatus.FAILED, error="orphaned")
    repo.finish(job.id, status=JobStatus.SUCCEEDED, media_path="/x/out.png")
    settled = repo.get(job.id)
    assert settled.status == JobStatus.FAILED
    assert settled.media_path is None


def test_requeue_returns_a_running_job_to_the_queue(repo):
    job = _make(repo)
    repo.claim_next()
    repo.attach_process(job.id, job_dir="/x", pid=1, pgid=1, proc_start=1.0)
    assert repo.requeue(job.id) is True
    back = repo.get(job.id)
    assert back.status == JobStatus.QUEUED
    assert back.pid is None and back.started_at is None


def test_requeue_does_not_touch_a_finished_job(repo):
    job = _make(repo)
    repo.claim_next()
    repo.finish(job.id, status=JobStatus.SUCCEEDED)
    assert repo.requeue(job.id) is False


def test_attach_process_records_the_identity_needed_after_a_restart(repo):
    job = _make(repo)
    repo.claim_next()
    repo.attach_process(job.id, job_dir="/tmp/j", pid=42, pgid=42, proc_start=1234.5)
    row = repo.get(job.id)
    assert (row.pid, row.pgid, row.proc_start) == (42, 42, 1234.5)
    assert row.job_dir == "/tmp/j", "without job_dir the sweep cannot probe the lock"


# ---------- delivery, exactly once ----------


def test_pending_notification_includes_failures(repo):
    """A generation that died in silence is what the user most needs told."""
    ok, bad = _make(repo, "ok"), _make(repo, "bad")
    repo.claim_next()
    repo.finish(ok.id, status=JobStatus.SUCCEEDED)
    repo.claim_next()
    repo.finish(bad.id, status=JobStatus.FAILED, error="boom")
    ids = {j.id for j in repo.pending_notification()}
    assert ids == {ok.id, bad.id}


def test_pending_notification_excludes_unfinished_and_already_sent(repo):
    queued = _make(repo, "queued")
    done = _make(repo, "done")
    repo.claim_next()  # claims `queued` (oldest) — leave it running
    repo.claim_next()  # claims `done`
    repo.finish(done.id, status=JobStatus.SUCCEEDED)
    assert {j.id for j in repo.pending_notification()} == {done.id}
    repo.claim_for_notify(done.id)
    assert repo.pending_notification() == []
    assert queued.id not in {j.id for j in repo.pending_notification()}


def test_notify_claim_is_exactly_once(repo):
    """Two deliverers race; exactly one may send the image."""
    job = _make(repo)
    repo.claim_next()
    repo.finish(job.id, status=JobStatus.SUCCEEDED)

    wins, lock = [], threading.Lock()
    barrier = threading.Barrier(6)

    def deliver():
        barrier.wait()
        if repo.claim_for_notify(job.id):
            with lock:
                wins.append(1)

    threads = [threading.Thread(target=deliver) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(wins) == 1, f"{sum(wins)} deliverers would each have sent the image"


def test_unclaim_allows_a_retry_after_a_failed_send(repo):
    job = _make(repo)
    repo.claim_next()
    repo.finish(job.id, status=JobStatus.SUCCEEDED)
    assert repo.claim_for_notify(job.id) is True
    repo.unclaim_notify(job.id)
    assert repo.claim_for_notify(job.id) is True


# ---------- reconciliation ----------


def _running_with_dir(repo, job_dir: Path):
    job = _make(repo)
    repo.claim_next()
    repo.attach_process(job.id, job_dir=str(job_dir), pid=1, pgid=1, proc_start=1.0)
    return repo.get(job.id)


def test_reconcile_adopts_a_live_job(repo, tmp_path):
    """THE asymmetry. A job still generating must never be failed: that
    discards five minutes of GPU work AND tells the user it broke while it is
    still running. Only positive evidence of death may fail a job."""
    job_dir = tmp_path / "live"
    job_dir.mkdir()
    spawned = sv.spawn(job_dir, ["/bin/sh", "-c", "sleep 30"])
    try:
        job = _running_with_dir(repo, job_dir)
        assert reconcile.reconcile_job(repo, job) == "adopted"
        assert repo.get(job.id).status == JobStatus.RUNNING
    finally:
        os.killpg(spawned.pgid, 9)
        # Reap it. pytest.ini sets `-W error::ResourceWarning`, so leaving the
        # Popen unwaited turns Popen.__del__'s "still running" warning into a
        # test error — in the FULL suite, not here, which is the sort of
        # cross-file failure that gets blamed on the wrong change.
        spawned.popen.wait(timeout=5)


def test_reconcile_succeeds_a_job_that_finished_while_we_were_down(repo, tmp_path):
    job_dir = tmp_path / "done"
    job_dir.mkdir()
    (job_dir / sv.RC_NAME).write_text("0")
    (job_dir / sv.VERIFIED_NAME).write_text("ok")
    _write_png(job_dir / sv.OUTPUT_NAME)
    job = _running_with_dir(repo, job_dir)
    assert reconcile.reconcile_job(repo, job) == "succeeded"
    assert repo.get(job.id).status == JobStatus.SUCCEEDED


def test_reconcile_fails_an_exit_zero_with_no_image(repo, tmp_path):
    """mflux swallows save errors and exits 0. rc is not evidence."""
    job_dir = tmp_path / "empty"
    job_dir.mkdir()
    (job_dir / sv.RC_NAME).write_text("0")
    job = _running_with_dir(repo, job_dir)
    assert reconcile.reconcile_job(repo, job) == "failed"
    assert "no usable image" in repo.get(job.id).error


def test_reconcile_fails_a_job_that_died_without_an_exit_code(repo, tmp_path):
    job_dir = tmp_path / "dead"
    job_dir.mkdir()  # no lock, no rc -> DIED
    job = _running_with_dir(repo, job_dir)
    assert reconcile.reconcile_job(repo, job) == "failed"
    assert "without recording an exit code" in repo.get(job.id).error


def test_reconcile_requeues_a_job_that_never_started(repo):
    """Crashed between the claim and the spawn: RUNNING, but no process was
    ever recorded. Nothing ran, so requeue — the request is still valid."""
    job = _make(repo)
    repo.claim_next()
    assert reconcile.reconcile_job(repo, repo.get(job.id)) == "requeued"
    assert repo.get(job.id).status == JobStatus.QUEUED


def test_reconcile_leaves_a_queued_job_alone(repo):
    job = _make(repo)
    assert reconcile.reconcile_job(repo, job) == "left"


def test_sweep_counts_outcomes(repo, tmp_path):
    a = _make(repo)
    repo.claim_next()  # claimed but never spawned -> requeued
    sweep_counts = reconcile.sweep(repo)
    assert sweep_counts.get("requeued") == 1
    assert repo.get(a.id).status == JobStatus.QUEUED


# ---------- the worker ----------


def test_worker_requeues_when_the_machine_is_busy(repo):
    """Another tenant holds the lease. The job is still valid — put it back
    rather than failing it."""
    from src.coordinator.services.image_gen.worker import ImageGenWorker
    from src.coordinator.services.resource_arbiter import ResourceArbiter

    arbiter = ResourceArbiter()
    job = _make(repo)
    repo.claim_next()
    worker = ImageGenWorker(repo, arbiter)
    with arbiter.exclusive("someone-else"):
        worker.run_job(repo.get(job.id))
    assert repo.get(job.id).status == JobStatus.QUEUED


def test_worker_refuses_to_generate_if_the_model_will_not_evict(repo):
    """17 GiB resident plus a 20 GB generation on a 48 GiB box is the exact
    collision this subsystem exists to prevent. Fail, never proceed."""
    from src.coordinator.services.image_gen import worker as w
    from src.coordinator.services.image_gen.worker import ImageGenWorker
    from src.coordinator.services.ollama_admin import UnloadResult
    from src.coordinator.services.resource_arbiter import ResourceArbiter

    job = _make(repo)
    repo.claim_next()
    worker = ImageGenWorker(repo, ResourceArbiter())

    stuck = UnloadResult(unloaded=False, seconds=120.0, still_resident_vram=17 * 2**30)
    with patch.object(w, "unload", return_value=stuck), \
            patch.object(w, "repin") as repin, \
            patch.object(w, "create_job_dir") as mk:
        worker.run_job(repo.get(job.id))
        mk.assert_not_called(), "spawned a generation with the model still resident"
    settled = repo.get(job.id)
    assert settled.status == JobStatus.FAILED
    assert "still resident" in settled.error
    repin.assert_not_called()


def test_worker_repins_even_when_the_generation_fails(repo):
    """The user should not pay the 6.4s reload on their next message because
    a generation happened to fail."""
    from src.coordinator.services.image_gen import worker as w
    from src.coordinator.services.image_gen.worker import ImageGenWorker
    from src.coordinator.services.ollama_admin import UnloadResult
    from src.coordinator.services.resource_arbiter import ResourceArbiter

    job = _make(repo)
    repo.claim_next()
    worker = ImageGenWorker(repo, ResourceArbiter())

    with patch.object(w, "unload", return_value=UnloadResult(True, 0.1)), \
            patch.object(w, "repin") as repin, \
            patch.object(w, "create_job_dir", side_effect=RuntimeError("disk full")):
        with pytest.raises(RuntimeError):
            worker.run_job(repo.get(job.id))
    repin.assert_called_once()


def test_build_argv_uses_the_qwen_alias_not_dev():
    """`dev` is a Flux alias; the Qwen CLI exits 1 in two seconds with
    ModelConfigError. Cost one wasted run to learn."""
    from src.coordinator.config.image_gen import ImageGenSettings
    from src.coordinator.services.image_gen.worker import build_argv

    argv = build_argv(ImageGenSettings(), prompt="a fox", output=Path("/tmp/o.png"))
    assert "qwen-image-2.1" in argv
    assert "dev" not in argv
    assert argv[argv.index("--prompt") + 1] == "a fox"
    assert argv[1].endswith("mflux_qwen_wrapped.py")


def test_worker_stop_is_idempotent_and_does_not_hang():
    from src.coordinator.services.image_gen.worker import ImageGenWorker
    from src.coordinator.services.resource_arbiter import ResourceArbiter

    worker = ImageGenWorker(MagicMock(), ResourceArbiter(), poll_seconds=0.05)
    worker.start()
    assert worker.running
    worker.stop(timeout=5)
    assert not worker.running
    worker.stop(timeout=5)  # second call must not raise


# ---------- helpers ----------


def _write_png(path: Path, w: int = 8, h: int = 8) -> None:
    import struct
    import zlib

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# ---------- the lifespan ----------


def test_lifespan_is_a_noop_when_disabled(monkeypatch):
    """Off means off: no worker, and crucially NO SWEEP. Failing rows on a box
    where the feature is switched off would destroy state a re-enable could
    still have used.

    ⚠️ The flag is forced off HERE rather than inherited. This test named a
    precondition it did not establish: with IMAGE_GEN_ENABLED exported true
    it silently exercised the ENABLED path and failed on an uninitialised
    repo. A test whose name states a condition must set that condition.
    """
    import asyncio

    from src.coordinator.config import get_settings
    from src.coordinator.services.image_gen.lifespan import image_gen_lifespan

    monkeypatch.setenv("IMAGE_GEN_ENABLED", "false")
    get_settings.cache_clear()

    async def run():
        with patch("src.coordinator.services.image_gen.reconcile.sweep") as swept:
            async with image_gen_lifespan(None):
                pass
            swept.assert_not_called()

    asyncio.run(run())


def test_lifespan_sweeps_then_starts_then_stops(repo, monkeypatch):
    """The ORDER is the point, not that each step happened.

    A job still generating must be ADOPTED by the sweep before the worker
    runs. If the worker started first it would see a row it could claim and
    launch a SECOND generation on top of the first one — two 20 GB jobs on one
    GPU, which is precisely the collision the whole subsystem exists to stop.
    Asserting the sequence is what catches a future reordering; asserting
    "sweep was called" would not.
    """
    import asyncio

    from src.coordinator import startup
    from src.coordinator.config import get_settings
    from src.coordinator.services.image_gen.lifespan import image_gen_lifespan
    from src.coordinator.services.resource_arbiter import ResourceArbiter

    monkeypatch.setenv("IMAGE_GEN_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(startup, "get_image_job_repo", lambda: repo)
    monkeypatch.setattr(startup, "get_resource_arbiter", ResourceArbiter)

    order: list[str] = []

    class _App:
        class state:  # noqa: N801
            pass

    async def run():
        with patch("src.coordinator.services.image_gen.reconcile.sweep",
                   side_effect=lambda r: (order.append("swept"), {})[1]), \
             patch("src.coordinator.services.image_gen.worker.ImageGenWorker.start",
                   autospec=True, side_effect=lambda self: order.append("started")), \
             patch("src.coordinator.services.image_gen.worker.ImageGenWorker.stop",
                   autospec=True,
                   side_effect=lambda self, timeout=10.0: order.append("stopped")):
            async with image_gen_lifespan(_App):
                order.append("serving")

    try:
        asyncio.run(run())
    finally:
        get_settings.cache_clear()

    assert order == ["swept", "started", "serving", "stopped"], order


def test_bare_testclient_does_not_run_lifespan():
    """Pins the trap, so nobody writes a lifespan test that passes vacuously.

    This repo's testing guide says to use `TestClient(app)` WITHOUT the `with`
    because that skips the lifespan. That convention is right for the other
    ~3000 tests and wrong for any test of a lifespan: without the `with`,
    startup never runs and an assertion about a started worker passes because
    nothing happened at all.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    ran = []

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def ls(app):
        ran.append("startup")
        yield
        ran.append("shutdown")

    app = FastAPI(lifespan=ls)

    TestClient(app)  # no `with` — the house convention
    assert ran == [], "bare TestClient ran the lifespan; the convention changed"

    with TestClient(app):
        pass
    assert ran == ["startup", "shutdown"]


# ---------- the periodic sweeper ----------


def test_the_sweeper_settles_a_job_that_died_while_we_were_up(repo, tmp_path):
    """The worker only claims QUEUED rows, so a job that died while RUNNING is
    invisible to it forever. This is the only thing that will ever look at
    that row again — without it the user waits for an image that never
    arrives and never fails, which is worse than a failure."""
    import time as _time

    from src.coordinator.services.image_gen.lifespan import _PeriodicSweeper

    job_dir = tmp_path / "dead"
    job_dir.mkdir()  # no lock, no rc -> DIED
    job = _running_with_dir(repo, job_dir)
    assert repo.get(job.id).status == JobStatus.RUNNING

    sweeper = _PeriodicSweeper(repo, interval_seconds=0.05)
    sweeper.start()
    try:
        deadline = _time.monotonic() + 5
        while _time.monotonic() < deadline:
            if repo.get(job.id).status != JobStatus.RUNNING:
                break
            _time.sleep(0.05)
    finally:
        sweeper.stop()

    settled = repo.get(job.id)
    assert settled.status == JobStatus.FAILED, "a dead RUNNING row was never settled"
    assert "without recording an exit code" in settled.error


def test_the_sweeper_does_not_sweep_immediately(repo, tmp_path):
    """The startup sweep has just run; sweeping again at once is duplication."""
    from unittest.mock import patch

    from src.coordinator.services.image_gen.lifespan import _PeriodicSweeper

    with patch("src.coordinator.services.image_gen.reconcile.sweep") as swept:
        sweeper = _PeriodicSweeper(repo, interval_seconds=30)
        sweeper.start()
        import time as _time
        _time.sleep(0.2)
        sweeper.stop()
    swept.assert_not_called()


def test_the_sweeper_survives_a_failing_sweep(repo):
    """It is the last line of defence for a stuck row; it must not die."""
    import time as _time
    from unittest.mock import patch

    from src.coordinator.services.image_gen.lifespan import _PeriodicSweeper

    calls = []
    with patch("src.coordinator.services.image_gen.reconcile.sweep",
               side_effect=lambda r: calls.append(1) or (_ for _ in ()).throw(
                   RuntimeError("boom"))):
        sweeper = _PeriodicSweeper(repo, interval_seconds=0.05)
        sweeper.start()
        deadline = _time.monotonic() + 2
        while _time.monotonic() < deadline and len(calls) < 3:
            _time.sleep(0.05)
        sweeper.stop()
    assert len(calls) >= 3, "the sweeper died on the first exception"


# ---------- adoption: the lease does not survive a restart, the job does ----------


def test_adoption_re_takes_the_lease_for_a_surviving_job(repo, tmp_path):
    """THE asymmetry that would have caused a swap storm.

    `start_new_session=True` keeps a generation alive across a coordinator
    restart — that is the whole point. But the arbiter's lease is in-memory
    and dies with the process. So after a restart the subprocess is still
    peaking at ~20 GB while nothing holds the machine, and the next chat turn
    reloads 16-19 GiB of companion model on top of it. On a 48 GB box that is
    the twenty-minute unresponsive Mac this subsystem exists to prevent.

    Adoption must therefore re-take the lease, not merely leave the row alone.
    """
    import time as _time

    from src.coordinator.services.image_gen.worker import ImageGenWorker
    from src.coordinator.services.resource_arbiter import ResourceArbiter

    job_dir = tmp_path / "survivor"
    job_dir.mkdir()
    spawned = sv.spawn(job_dir, ["/bin/sh", "-c", "sleep 20"])
    try:
        job = _running_with_dir(repo, job_dir)
        arbiter = ResourceArbiter()
        assert not arbiter.is_exclusive(), "precondition: nothing holds the machine"

        worker = ImageGenWorker(repo, arbiter)
        with patch("src.coordinator.services.image_gen.worker.repin"):
            worker.adopt(job)
            deadline = _time.monotonic() + 5
            while _time.monotonic() < deadline and not arbiter.is_exclusive():
                _time.sleep(0.05)
            assert arbiter.is_exclusive(), (
                "a surviving generation was adopted WITHOUT the lease — chat "
                "would reload the companion model on top of it"
            )
            assert "adopted" in arbiter.describe()
    finally:
        os.killpg(spawned.pgid, 9)
        spawned.popen.wait(timeout=5)


def test_adoption_never_spawns_a_second_generator(repo, tmp_path):
    """The generator is already running. A second would be the collision
    twice over."""
    from src.coordinator.services.image_gen.worker import ImageGenWorker
    from src.coordinator.services.resource_arbiter import ResourceArbiter

    job_dir = tmp_path / "nospawn"
    job_dir.mkdir()
    (job_dir / sv.RC_NAME).write_text("0")  # already finished
    job = _running_with_dir(repo, job_dir)

    worker = ImageGenWorker(repo, ResourceArbiter())
    with patch("src.coordinator.services.image_gen.worker.sv.spawn") as spawn, \
            patch("src.coordinator.services.image_gen.worker.repin"):
        worker._adopt_blocking(job)
    spawn.assert_not_called()


def test_adoption_finalises_a_job_that_finished_while_we_were_down(repo, tmp_path):
    from src.coordinator.services.image_gen.worker import ImageGenWorker
    from src.coordinator.services.resource_arbiter import ResourceArbiter

    job_dir = tmp_path / "done"
    job_dir.mkdir()
    (job_dir / sv.RC_NAME).write_text("0")
    (job_dir / sv.VERIFIED_NAME).write_text("ok")
    _write_png(job_dir / sv.OUTPUT_NAME)
    job = _running_with_dir(repo, job_dir)

    worker = ImageGenWorker(repo, ResourceArbiter())
    with patch("src.coordinator.services.image_gen.worker.repin"), \
            patch.object(worker, "_store_for_session") as store:
        store.return_value = type("S", (), {"path": job_dir / sv.OUTPUT_NAME})()
        worker._adopt_blocking(job)
    assert repo.get(job.id).status == JobStatus.SUCCEEDED
