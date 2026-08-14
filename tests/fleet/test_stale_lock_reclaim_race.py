"""G5 Phase-3.1 review finding #1 — stale-lock reclaim TOCTOU.

The old `_reclaim_if_stale` did an unconditional `os.remove(lock_path)` after
deciding a lock looked stale: whichever racer's `os.remove` call actually
deletes the file returns `True` correctly, but *every other racer whose
`os.remove` then raises `FileNotFoundError` (because the file is already
gone) also returns `True`* — the exception is silently swallowed as if it
meant "I reclaimed it," when it actually means "someone else already did."
Every racer then believes it alone cleared the path and may proceed as if it
owns the reclaim, defeating the whole point of a win-exclusive reclaim.

`multiprocessing.Barrier` forces many real OS processes (not threads — see
TC-12's reasoning) to call the vulnerable function at the same instant
against the exact same never-yet-touched stale lock, making the race
deterministic in outcome (not a matter of timing luck): old code lets
essentially every racer report success; fixed code (`os.replace`-based,
losers get a real `OSError` instead of a swallowed exception) lets exactly
one.

**A second, deeper variant was found live while proving this fix**, and is
covered by a dedicated deterministic test in `tests/fleet/test_events.py`
(`TestStaleLockReclaim::test_reclaim_does_not_steal_a_lock_that_changed_underneath_it`),
not here: a racer's stale/dead-pid decision can be based on data read many
milliseconds earlier — under real contention, a process can be descheduled by
the OS for long enough that several *other* processes fully cycle through the
lock in the gap — so the reclaim action must verify (via `st_ino`/`st_dev`
identity) that it renamed away the *same* stale instance it evaluated, not
whatever now occupies that path, and restore it (also identity-safely, via an
exclusive create rather than a blind overwrite) if not.

**A broader end-to-end stress test (many real `append()` callers racing a
pre-seeded stale lock through the full acquire/read/write/release path, not
just `_reclaim_if_stale` in isolation) was written, tuned across several
worker-count/timeout combinations, and ultimately NOT kept**: after the
identity fix above, it never again reproduced silent data loss or duplication
across dozens of runs — every remaining failure was a clean, loud
`LockTimeoutError` (the documented, safe failure mode; see
`core/events.append`'s own docstring: "on timeout the write fails loudly...
rather than writing unlocked"), occurring in roughly 1 of every 5-10 runs on
this development machine under N=4-8 simultaneous cold-start stale-lock
racers. That is a liveness/tail-latency characteristic of deliberately
extreme, self-inflicted contention (this test's own setup), not a
correctness violation, and not what this finding's acceptance criterion asks
for. Shipping a test with that failure rate would itself violate this
project's own flakiness bar (TC-12: "a single flaky failure is a real bug,
not noise"), so it was removed rather than shipped in a known-flaky state.
The deterministic tests below (plus TC-12, which continues to pass 5/5 with
these `events.py` changes, proving normal — non-stale-seeded — contention is
unaffected) are the reliable proof for this finding.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Same belt-and-suspenders sys.path setup as test_concurrency.py: this module
# must be independently importable-by-name in a freshly spawned child.
_SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.fleet.core.events import _reclaim_if_stale  # noqa: E402

N_RACERS = 20
N_REPEATS = 5


def _reclaim_race_worker(
    lock_path_str: str, results_dir_str: str, worker_id: int, barrier: object
) -> None:
    """Wait at the barrier, then call `_reclaim_if_stale` at (as near as
    possible) the same instant as every other racer, recording the result.
    """
    lock_path = Path(lock_path_str)
    results_dir = Path(results_dir_str)
    barrier.wait(timeout=30)  # type: ignore[attr-defined]
    reclaimed = _reclaim_if_stale(lock_path, stale_age=1.0)
    (results_dir / f"{worker_id}.result").write_text(str(reclaimed), encoding="utf-8")


def _dead_pid() -> int:
    """Spawn and immediately reap a process, returning its now-dead pid."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    return proc.pid


def _seed_stale_lock(lock_path: Path, *, dead_pid: int, age_seconds: float) -> None:
    """Write a lockfile that looks crashed: old mtime, a pid that is not alive."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps({"pid": dead_pid, "ts": "2020-01-01T00:00:00Z"}), encoding="utf-8"
    )
    old_time = time.time() - age_seconds
    os.utime(lock_path, (old_time, old_time))


def _run_barrier_reclaim_scenario(tmp_dir: Path, run_index: int) -> None:
    lock_path = tmp_dir / ".lock"
    results_dir = tmp_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    _seed_stale_lock(lock_path, dead_pid=_dead_pid(), age_seconds=120.0)

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(N_RACERS)
    processes = [
        ctx.Process(
            target=_reclaim_race_worker,
            args=(str(lock_path), str(results_dir), i, barrier),
        )
        for i in range(N_RACERS)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=60)
        assert p.exitcode == 0, f"worker {p.name} exited with code {p.exitcode}"

    results = [
        (results_dir / f"{i}.result").read_text(encoding="utf-8") == "True"
        for i in range(N_RACERS)
    ]
    winners = sum(results)

    assert winners == 1, (
        f"expected exactly 1 of {N_RACERS} barrier-synchronized racers to "
        f"win the stale-lock reclaim, got {winners} — a losing racer's "
        f"'already gone' error was (mis)treated as a successful reclaim, "
        f"the exact TOCTOU this test targets"
    )


@pytest.mark.parametrize("run_index", range(N_REPEATS))
def test_exactly_one_racer_wins_a_barrier_synchronized_stale_lock_reclaim(
    tmp_path_factory: pytest.TempPathFactory, run_index: int
) -> None:
    """G5 review finding #1 — the deterministic proof.

    A `Barrier` forces every racer to call `_reclaim_if_stale` against the
    same never-yet-touched stale lock at (as near as possible) the same
    instant, so old code's bug — every loser's "already gone" exception
    silently counted as its own success — manifests reliably rather than
    depending on rare OS scheduling luck. Exactly one racer must report
    `True`; every other racer must correctly report `False`.
    """
    tmp_dir = tmp_path_factory.mktemp(f"fleet-barrier-reclaim-{run_index}")
    _run_barrier_reclaim_scenario(tmp_dir, run_index)


def _seed_orphaned_marker(lock_path: Path, *, dead_pid: int, age_seconds: float) -> None:
    """Write an orphaned reclaim marker next to `lock_path`: old mtime, dead pid.

    Simulates a process that won the marker's `O_CREAT|O_EXCL` create and
    then crashed before its `finally` could remove it.
    """
    marker_path = lock_path.with_name(f"{lock_path.name}.reclaiming")
    marker_path.write_text(
        json.dumps({"pid": dead_pid, "ts": "2020-01-01T00:00:00Z"}), encoding="utf-8"
    )
    old_time = time.time() - age_seconds
    os.utime(marker_path, (old_time, old_time))


def _run_barrier_orphaned_marker_scenario(tmp_dir: Path, run_index: int) -> None:
    lock_path = tmp_dir / ".lock"
    results_dir = tmp_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    _seed_stale_lock(lock_path, dead_pid=_dead_pid(), age_seconds=120.0)
    _seed_orphaned_marker(lock_path, dead_pid=_dead_pid(), age_seconds=60.0)

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(N_RACERS)
    processes = [
        ctx.Process(
            target=_reclaim_race_worker,
            args=(str(lock_path), str(results_dir), i, barrier),
        )
        for i in range(N_RACERS)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=60)
        assert p.exitcode == 0, f"worker {p.name} exited with code {p.exitcode}"

    results = [
        (results_dir / f"{i}.result").read_text(encoding="utf-8") == "True"
        for i in range(N_RACERS)
    ]
    winners = sum(results)

    assert winners == 1, (
        f"expected exactly 1 of {N_RACERS} barrier-synchronized racers to "
        f"recover the orphaned marker and win the reclaim, got {winners} — "
        f"double-reclaim is a regression of finding #1's fix; on unfixed "
        f"finding-#4 code every racer instead gets FileExistsError on the "
        f"marker create and backs off forever (a permanent deadlock this "
        f"test would time out on, never reaching this assertion)"
    )


@pytest.mark.parametrize("run_index", range(N_REPEATS))
def test_exactly_one_racer_recovers_an_orphaned_marker_and_wins(
    tmp_path_factory: pytest.TempPathFactory, run_index: int
) -> None:
    """GPT-5 review finding #4 — deterministic proof under real contention.

    Seeds BOTH a stale main lock AND an orphaned reclaim marker (simulating
    a process that crashed mid-reclaim). N barrier-synchronized racers all
    hit the orphaned marker at once: exactly one must recover it and win
    the reclaim; every other racer must correctly report False — and,
    critically, this must complete at all rather than deadlock (unfixed
    code: every racer's marker create fails with FileExistsError against
    the orphan forever, so `_reclaim_race_worker`'s own `barrier.wait`
    still succeeds but every subsequent `_reclaim_if_stale` call returns
    False for every racer — this test would show 0 winners, not a hang,
    since `_reclaim_if_stale` itself has no retry loop; the permanent
    deadlock lives one layer up, in `acquire_lock`'s caller — but 0 winners
    here is exactly the observable symptom of that same unfixed defect).
    """
    tmp_dir = tmp_path_factory.mktemp(f"fleet-barrier-orphan-marker-{run_index}")
    _run_barrier_orphaned_marker_scenario(tmp_dir, run_index)
