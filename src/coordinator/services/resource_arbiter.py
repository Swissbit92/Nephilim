"""Who gets the machine.

Named for the machine, not for images: it arbitrates one 48 GB box between the
companion LLM (~17-19 GiB resident, pinned) and an image generation (~20 GB
peak, ~5.5 minutes). Both at once does not fail cleanly — it swap-storms, and
the symptom is a Mac that stops responding for twenty minutes.

Three things this owns:

1. **An exclusive lease.** One holder at a time, `threading.Lock`-backed.
2. **Admission.** While a lease is held, every path that would load the chat
   model must be refused — Ollama cannot be told to refuse, so the refusal has
   to live here.
3. **A free-memory watchdog**, because MLX's own limit does not enforce itself
   (see `scripts/image_gen/mflux_qwen_wrapped.py`).

**`threading.Lock`, not `asyncio.Lock`.** Every chat-touching route in this
repo is a plain `def` — `routes/chat.py` has 0 `async def` out of 14 — so
FastAPI runs them in the anyio threadpool, and the background producers (four
prewarm threads plus the fact-extraction worker) are OS threads too. An
asyncio lock would be invisible to all of them.

**Refuse, never block, on the request path.** A generation runs ~332 s; the
Telegram client gives up at 180 s. Blocking a chat turn on the lease would
hand the user a timeout instead of a reply. `is_exclusive()` lets a route say
something honest and fast instead. Background threads, which nobody is waiting
on, use `wait_for_idle()`.

**Free memory is the only honest instrument**, and that is measured, not
assumed. On this machine:

  - `ps -o rss` reported **0.03 GiB while MLX held 8.00 GiB**, and **8 MiB
    while Ollama held 16.40 GiB**. Metal buffers are not in RSS. A watchdog
    built on RSS or psutil reads "nothing allocated" on a full machine.
  - `kern.memorystatus_vm_pressure_level` stayed at **1 (NORMAL)** from
    13.17 GiB free all the way down to 7.23 GiB. It is a late signal and
    cannot gate anything. (Web research recommended it as the primary signal;
    the measurement says otherwise.)
  - `vm_stat` free+inactive tracked both workloads correctly, in both
    directions.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_PAGE_BYTES = 16384  # Apple Silicon. Read from vm_stat's header when present.


class ResourceBusyError(RuntimeError):
    """The machine is leased to something else."""


@dataclass
class Lease:
    holder: str
    acquired_at: float = field(default_factory=time.monotonic)

    @property
    def held_seconds(self) -> float:
        return time.monotonic() - self.acquired_at


def free_memory_bytes() -> int:
    """Free + inactive pages, as `vm_stat` reports them.

    Inactive counts: those pages are reclaimable without swapping, so excluding
    them would make a healthy machine look starved and the watchdog would kill
    every job.

    Returns -1 when it cannot tell, which every caller must treat as "no
    opinion" rather than "no memory" — a watchdog that reads a parse failure as
    starvation kills jobs whenever the instrument breaks.
    """
    try:
        out = subprocess.run(
            ["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("[Arbiter] vm_stat unavailable: %s", exc)
        return -1
    if out.returncode != 0:
        return -1

    lines = out.stdout.splitlines()
    page = _PAGE_BYTES
    if lines and "page size of" in lines[0]:
        for token in lines[0].replace("(", " ").replace(")", " ").split():
            if token.isdigit():
                page = int(token)
                break

    counts: dict[str, int] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().rstrip(".")
        if value.isdigit():
            counts[key.strip()] = int(value)

    if "Pages free" not in counts:
        return -1
    return (counts.get("Pages free", 0) + counts.get("Pages inactive", 0)) * page


class ResourceArbiter:
    """One machine, one heavy tenant at a time.

    Stateless apart from the lease, so it is safe to share across threads and
    cheap to construct. Lives as a lazy singleton in `di/services.py`, reached
    through `startup.get_resource_arbiter()` like every other service here.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle = threading.Condition(threading.Lock())
        self._lease: Lease | None = None

    # ---------- state ----------

    def is_exclusive(self) -> bool:
        """Is the machine leased? The cheap check for a request path.

        Deliberately takes no lock: a stale answer here is harmless (the lease
        holder is the only writer, and the worst case is one chat turn admitted
        a moment before the window opens or refused a moment after it closes),
        while taking the lock would let a slow holder stall the chat path —
        which is precisely what this class exists to avoid.
        """
        return self._lease is not None

    @property
    def lease(self) -> Lease | None:
        return self._lease

    def describe(self) -> str:
        lease = self._lease
        if lease is None:
            return "idle"
        return f"leased to {lease.holder} for {lease.held_seconds:.0f}s"

    # ---------- admission ----------

    def check_admitted(self, what: str) -> None:
        """Raise if `what` must not run right now.

        Call at the top of anything that would load the CHAT model. Not for
        embeddings: `bge-m3` is 0.63 GiB against the chat model's 16-19 GiB,
        and it sits on the routing hot path, so gating it would cost far more
        than it saves.
        """
        lease = self._lease
        if lease is not None:
            raise ResourceBusyError(
                f"{what} refused: the machine is {self.describe()}. "
                f"The companion model is unloaded for the duration."
            )

    def wait_for_idle(self, timeout: float = 600.0) -> bool:
        """Block until the lease clears. For background threads only.

        The four prewarm threads and the fact-extraction worker call this:
        nobody is waiting on them, so waiting is strictly better than failing.
        A request path must never call this — see the class docstring.
        """
        deadline = time.monotonic() + timeout
        with self._idle:
            while self._lease is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "[Arbiter] wait_for_idle timed out after %.0fs — %s",
                        timeout,
                        self.describe(),
                    )
                    return False
                self._idle.wait(timeout=min(remaining, 1.0))
        return True

    # ---------- the lease ----------

    @contextmanager
    def exclusive(self, holder: str, *, wait_seconds: float = 0.0) -> Iterator[Lease]:
        """Hold the machine. Raises `ResourceBusyError` rather than queueing.

        Refuses by default (`wait_seconds=0`) because two concurrent image jobs
        is never the right answer on one GPU — the second should be told no and
        re-queued by the caller, not silently serialised into a ten-minute wait.
        """
        if wait_seconds > 0:
            got = self._lock.acquire(timeout=wait_seconds)
        else:
            got = self._lock.acquire(blocking=False)
        if not got:
            raise ResourceBusyError(
                f"{holder} cannot take the machine: {self.describe()}"
            )

        lease = Lease(holder=holder)
        self._lease = lease
        logger.info("[Arbiter] %s took the machine", holder)
        try:
            yield lease
        finally:
            self._lease = None
            self._lock.release()
            # Wake the background threads parked in wait_for_idle. Done after
            # clearing the lease, or a woken waiter re-reads it and parks again.
            with self._idle:
                self._idle.notify_all()
            logger.info("[Arbiter] %s released the machine after %.0fs",
                        holder, lease.held_seconds)


def guard_chat_model(what: str) -> None:
    """Refuse `what` if the machine is leased. The chokepoint one-liner.

    Call at the top of anything that can cause the CHAT model to load. There
    are five transports to Ollama in this repo (httpx, langchain-ollama via
    two separate constructions, the `ollama` package, and raw probes), so
    there is no single socket to guard — the guard goes on the shape, not on
    one file.

    NOT for embeddings. `bge-m3` is 0.63 GiB against the chat model's 16-19
    GiB and sits on the routing hot path, so gating it would cost a reload on
    every routing decision to save about 1% of the memory.

    Resolves the arbiter through the `startup` module ATTRIBUTE rather than
    importing the function, because tests patch `src.coordinator.startup.*`
    and a direct import would freeze the unpatched original.

    Inert unless something holds a lease, and only the image worker ever takes
    one, so this is a no-op on a box with IMAGE_GEN_ENABLED off.
    """
    from .. import startup

    getter = getattr(startup, "get_resource_arbiter", None)
    if getter is None:  # pragma: no cover - import-order safety only
        return
    arbiter = getter()
    if arbiter is not None:
        arbiter.check_admitted(what)


@dataclass
class MemoryWatchdog:
    """Is free memory low, and has it STAYED low?

    The enforcement MLX declines to provide. `mx.set_memory_limit()` is
    advisory — measured on mlx 0.32.3: 4 GiB allocated against a 2 GiB limit
    succeeded silently, raising nothing. So the only thing that can stop an
    overrun is something willing to kill the process, which means watching
    from outside it.

    Requires the shortfall to PERSIST for `grace_seconds` before saying kill.
    A single low sample is normal — macOS reclaims lazily, and a 332-second
    generation is far too expensive to abandon on one reading. The watchdog
    forgets the breach as soon as memory recovers, so a dip costs nothing.

    Stateful and not thread-safe: one instance per supervised job, polled by
    that job's supervisor loop.
    """

    min_free_bytes: int
    grace_seconds: float
    _below_since: float | None = None

    def sample(self, free_bytes: int | None = None) -> bool:
        """Record one observation. True means "kill it".

        `free_bytes` is injectable so tests drive this without touching the
        machine's real memory, which is neither controllable nor repeatable.
        """
        free = free_memory_bytes() if free_bytes is None else free_bytes

        if free < 0:
            # The instrument failed. No opinion — never read a broken gauge as
            # an empty tank, or every job dies whenever vm_stat hiccups.
            return False

        if free >= self.min_free_bytes:
            if self._below_since is not None:
                logger.info(
                    "[Watchdog] free memory recovered to %.2f GiB", free / 2**30
                )
            self._below_since = None
            return False

        now = time.monotonic()
        if self._below_since is None:
            self._below_since = now
            logger.warning(
                "[Watchdog] free memory %.2f GiB below floor %.2f GiB — "
                "starting %.0fs grace",
                free / 2**30,
                self.min_free_bytes / 2**30,
                self.grace_seconds,
            )
            return False

        elapsed = now - self._below_since
        if elapsed >= self.grace_seconds:
            logger.error(
                "[Watchdog] free memory %.2f GiB has stayed below %.2f GiB for "
                "%.0fs — killing the job before the machine swaps",
                free / 2**30,
                self.min_free_bytes / 2**30,
                elapsed,
            )
            return True
        return False
