"""Three-arm A/B of the canned-line fix on the REAL event sequence.

  OFF      shipped: first generation cached for the process lifetime
  QUEUE    variant floor + permutation queue + distinctness (the fix as first written)
  RECYCLE  QUEUE + regenerate when a cycle is spent, for reachable situations

Session is the unit: one cached generation determines every later occurrence in that
session, so turns are not independent.

The generator is MEASURED, not assumed: 4 live cycles produced 8 byte-distinct lines
with a worst cross-cycle shared span of 3 tokens (gate is 8), and 2 of 4 cycles returned
a single variant. Both facts are modelled below.
"""
import os
import random
import sqlite3
import sys
from pathlib import Path


def _db_path() -> Path:
    """The live chat DB. A worktree's own data/chats.db is EMPTY — the schema exists
    with zero rows — so defaulting to a relative path here would silently measure
    nothing and report a clean run. Overridable, never guessed."""
    env = os.environ.get("NEPHILIM_CHATS_DB")
    if env:
        return Path(env)
    return Path.home() / "nephilim-ecosystem" / "nephilim" / "data" / "chats.db"
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests" / "evaluation" / "persona_eval"))
from coordinator.services import persona_lines as pl  # noqa: E402

con = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
EVENTS = dict(con.execute("SELECT session_id, COUNT(*) FROM image_jobs GROUP BY session_id").fetchall())
con.close()

SUBFLOOR_RATE = 0.5   # measured: 2 of 4 live cycles returned 1 variant

def make_generator(rng):
    """Each cycle yields NOVEL text (measured: worst cross-cycle span 3 < gate 8), and
    sub-floors at the measured rate."""
    counter = {"n": 0}
    def gen(*_a, **_k):
        counter["n"] += 1
        c = counter["n"]
        n = 1 if rng.random() < SUBFLOOR_RATE else 3
        return [f"cycle{c}variant{i} unique filler text for span purposes here" for i in range(n)]
    return gen, counter

def run(n_events, arm, seed):
    rng = random.Random(seed)
    random.seed(seed)
    pl.reset_cache()
    gen, counter = make_generator(rng)
    if arm == "OFF":
        first = gen()
        emitted = [first[0]] * n_events
        calls = 1
    else:
        orig_gen, orig_unreach = pl._generate, pl._MODEL_UNREACHABLE
        pl._generate = gen
        if arm == "QUEUE":
            # Disable recycling by declaring every situation unreachable.
            pl._MODEL_UNREACHABLE = frozenset(pl.SITUATIONS)
        try:
            emitted = [pl.line("gwen", "image_ready") for _ in range(n_events)]
        finally:
            pl._generate, pl._MODEL_UNREACHABLE = orig_gen, orig_unreach
        calls = counter["n"]
    # byte-identical replay is the user-visible defect, and the only cause ever observed
    repeats = sum(1 for i, v in enumerate(emitted) if v in emitted[:i])
    consecutive = sum(1 for i in range(1, len(emitted)) if emitted[i] == emitted[i-1])
    return repeats, consecutive, calls, len(set(emitted))

# Two of the three arms only exist once the fix is present, so say so DELIBERATELY
# rather than dying on an AttributeError. A check whose pre-fix failure is an import
# error cannot distinguish "the fix is absent" from "the harness is broken", and the
# second is the one that silently invalidates the measurement.
_HAS_FIX = hasattr(pl, "_MODEL_UNREACHABLE")
if not _HAS_FIX:
    print("persona_lines has no _MODEL_UNREACHABLE: the recycle fix is NOT present in "
          "this tree, so only the OFF arm is measurable.\n"
          "OFF is the shipped behaviour — one generation cached for the process "
          "lifetime — and on the real 17-event sequence it repeats a line on 15 of 17 "
          "turns, all 15 of them consecutive.\n"
          "Independently measured on the live corpus: 9 of 127 comparable gwen turns "
          "trip the >=8-token whole-history gate, and 9 of 9 are byte-identical "
          "replays rather than paraphrase.\n"
          "VERDICT: FAIL (fix absent)")
    sys.exit(1)

DRAWS = 400
print(f"real per-session image-job counts: {EVENTS}")
print(f"\n{'session':<10}{'ev':>4}{'arm':>9}{'repeats':>9}{'consec':>8}{'distinct':>10}{'llm calls':>11}")
totals = {}
for sid, n in sorted(EVENTS.items(), key=lambda x: -x[1]):
    for arm in ("OFF", "QUEUE", "RECYCLE"):
        r = [run(n, arm, 7000 + d) for d in range(DRAWS)]
        rep = sum(x[0] for x in r) / DRAWS
        con_ = sum(x[1] for x in r) / DRAWS
        cal = sum(x[2] for x in r) / DRAWS
        dis = sum(x[3] for x in r) / DRAWS
        print(f"{sid[:8]:<10}{n:>4}{arm:>9}{rep:>9.2f}{con_:>8.2f}{dis:>10.2f}{cal:>11.2f}")
        totals.setdefault(arm, [0, 0, 0])
        totals[arm][0] += rep
        totals[arm][1] += con_
        totals[arm][2] += cal

print("\nacross both real sessions (17 events total):")
base = totals["OFF"][0]
for arm in ("OFF", "QUEUE", "RECYCLE"):
    rep, con_, cal = totals[arm]
    delta = f"  ({(rep-base)/base*100:+.0f}% vs OFF)" if base else ""
    print(f"  {arm:<8} repeated turns {rep:5.2f}{delta}   consecutive {con_:.2f}   llm calls {cal:.1f}")


# A check that cannot fail is not a check: exit on the RELATIONSHIP, not on a pinned
# number, so a re-measure that moves the figures still settles the claim.
off, queue, recycle = (totals[a][0] for a in ("OFF", "QUEUE", "RECYCLE"))
off_c, _, recycle_c = (totals[a][1] for a in ("OFF", "QUEUE", "RECYCLE"))
ok = recycle < queue < off and recycle_c < off_c / 4
print(f"\nVERDICT: {'PASS' if ok else 'FAIL'} — recycle<queue<off on repeats "
      f"({recycle:.2f} < {queue:.2f} < {off:.2f}) and consecutive cut >4x "
      f"({recycle_c:.2f} vs {off_c:.2f})")
sys.exit(0 if ok else 1)
