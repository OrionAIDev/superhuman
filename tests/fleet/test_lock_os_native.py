"""G6 redesign proof: OS-native advisory locking replaces `O_CREAT|O_EXCL` +
manual stale-lock reclaim in `core/events.py`.

Four properties, each a real `multiprocessing.Process` (not a thread —
threads share the GIL and a process table entry, which can mask both
lock-contention bugs and the crash-release property this redesign's
correctness rests on; see `test_concurrency.py`'s own reasoning):

1. **Crash-release** — the core claim motivating the whole redesign. A
   child process acquires the lock and is hard-killed while holding it; a
   second acquirer must obtain the lock within `timeout`, proving the OS
   itself releases the advisory lock when its holder dies, with NO manual
   reclaim logic anywhere in `core/events.py`. This single test subsumes
   everything the deleted `tests/fleet/test_stale_lock_reclaim_race.py` and
   `test_events.py::TestStaleLockReclaim` used to prove about the old
   design — there is no "stale lock" state left to reclaim.

2. **Mutual exclusion under heavy load** — the deterministic,
   load-reproducing proof this redesign must pass: N processes race to
   acquire the same lock and each does an UN-synchronized read-modify-write
   increment of a shared counter file inside the critical section, many
   times over. Any lost update is a mutual-exclusion violation, immediately
   visible as `final_count != total_increments` — no timing-dependent
   guesswork.

3. **Crash-release DURING heavy concurrent load (the combined case)** — the
   historical bug class this whole redesign exists for was always
   crash-plus-concurrency together, never either condition in isolation
   (the double-reclaim race that motivated G6 needed *racers*, not just one
   crashed holder). N "survivor" processes are mid-contention, doing exact
   -count RMW increments, at the exact moment a separate "victim" process —
   holding the lock right then — is hard-killed. Neither property 1 nor
   property 2 alone proves this combination is safe.

4. **Timeout still fails loud** — when the lock is genuinely held by a live
   process, a second acquirer with a short timeout raises `LockTimeoutError`
   rather than ever proceeding unlocked. `test_events.py::TestLockContention`
   already covers this in-process; this is the real-cross-process version.

Worker functions are module-level (not closures) so they are picklable
under `multiprocessing`'s `spawn` start method, which Windows always uses.
"""

from __future__ import annotations

import multiprocessing
import sys
import time
from pathlib import Path

import pytest

# Belt-and-suspenders: tests/fleet/conftest.py already puts the skill root on
# sys.path for the parent process, and multiprocessing's spawn start method
# snapshots the parent's sys.path for the child — but this module must also
# be independently importable-by-name in the freshly spawned child
# interpreter, so we do not rely solely on fixture-time side effects.
_SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.fleet.core.errors import LockTimeoutError  # noqa: E402
from scripts.fleet.core.events import acquire_lock, release_lock  # noqa: E402

N_REPEATS = 5


# ---------------------------------------------------------------------------
# 1. Crash-release
# ---------------------------------------------------------------------------


def _crash_release_child(lock_path_str: str, acquired_event: object) -> None:
    """Acquire the lock, signal acquisition, then block until killed.

    The parent hard-kills this process while it holds the lock — the block
    below is only a safety bound in case the kill somehow fails, so a test
    bug can never hang the suite.
    """
    handle = acquire_lock(lock_path_str, timeout=10.0, retry_interval=0.01)
    acquired_event.set()  # type: ignore[attr-defined]
    time.sleep(60)
    release_lock(handle)  # pragma: no cover — unreachable on the success path


def _run_crash_release_scenario(tmp_dir: Path) -> None:
    lock_path = tmp_dir / ".lock"
    ctx = multiprocessing.get_context("spawn")
    acquired_event = ctx.Event()

    child = ctx.Process(target=_crash_release_child, args=(str(lock_path), acquired_event))
    child.start()
    try:
        assert acquired_event.wait(timeout=10), "child never signaled lock acquisition"

        # Hard-kill while the child is provably still holding the lock.
        # multiprocessing.Process.kill() sends SIGKILL on POSIX and calls
        # TerminateProcess on Windows — both immediately tear down every
        # handle/fd the process held, which is exactly the crash-release
        # property under test.
        child.kill()
        child.join(timeout=10)
        assert child.exitcode is not None, "child did not terminate"
        assert child.exitcode != 0, "child exited cleanly instead of being killed"

        # The whole point: a second acquirer must succeed within a bounded
        # timeout, with no manual reclaim step anywhere in acquire_lock.
        start = time.monotonic()
        handle = acquire_lock(lock_path, timeout=5.0, retry_interval=0.01)
        elapsed = time.monotonic() - start
        release_lock(handle)
        assert elapsed < 5.0, (
            f"second acquirer took {elapsed:.2f}s after the holder was killed — "
            "the OS should release the advisory lock immediately on process death"
        )
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=10)


@pytest.mark.parametrize("run_index", range(N_REPEATS))
def test_lock_is_released_when_holder_is_hard_killed(
    tmp_path_factory: pytest.TempPathFactory, run_index: int
) -> None:
    """THE core property: no manual reclaim, no deadlock, no stale state.

    Proves the kernel itself releases the advisory lock when its holding
    process dies — the structural reason the entire former stale-lock /
    reclaim-marker / pid-liveness machinery in `core/events.py` was deleted
    rather than merely patched again.
    """
    tmp_dir = tmp_path_factory.mktemp(f"fleet-crash-release-{run_index}")
    _run_crash_release_scenario(tmp_dir)


# ---------------------------------------------------------------------------
# 2. Mutual exclusion under heavy load (the load-reproducing stress test)
# ---------------------------------------------------------------------------

N_WORKERS = 8
ITERATIONS_PER_WORKER = 50
TOTAL_INCREMENTS = N_WORKERS * ITERATIONS_PER_WORKER


def _increment_worker(
    lock_path_str: str, counter_path_str: str, iterations: int, barrier: object
) -> None:
    """Repeatedly acquire the lock and do an UN-synchronized RMW increment.

    The read-modify-write of the counter file is deliberately naive (plain
    `open`/read/write, no atomic primitive of its own) — its only protection
    against lost updates is the OS advisory lock. Any race in the lock
    implementation shows up directly as a lost increment.
    """
    barrier.wait(timeout=60)  # type: ignore[attr-defined]
    for _ in range(iterations):
        handle = acquire_lock(lock_path_str, timeout=30.0, retry_interval=0.005)
        try:
            with open(counter_path_str, "r+", encoding="utf-8") as f:
                current = int(f.read().strip() or "0")
                current += 1
                f.seek(0)
                f.truncate()
                f.write(str(current))
        finally:
            release_lock(handle)


def _run_mutual_exclusion_scenario(tmp_dir: Path) -> None:
    lock_path = tmp_dir / ".lock"
    counter_path = tmp_dir / "counter.txt"
    counter_path.write_text("0", encoding="utf-8")

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(N_WORKERS)
    processes = [
        ctx.Process(
            target=_increment_worker,
            args=(str(lock_path), str(counter_path), ITERATIONS_PER_WORKER, barrier),
        )
        for _ in range(N_WORKERS)
    ]

    start = time.monotonic()
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=120)
        assert p.exitcode == 0, f"worker {p.name} exited with code {p.exitcode}"
    elapsed = time.monotonic() - start

    final_count = int(counter_path.read_text(encoding="utf-8").strip())
    assert final_count == TOTAL_INCREMENTS, (
        f"expected exactly {TOTAL_INCREMENTS} increments ({N_WORKERS} workers x "
        f"{ITERATIONS_PER_WORKER} each), got {final_count} — "
        f"{TOTAL_INCREMENTS - final_count} lost update(s), a mutual-exclusion "
        "violation in the OS-native lock"
    )
    return elapsed  # noqa: RET504 — surfaced for the caller's own sanity check


@pytest.mark.parametrize("run_index", range(N_REPEATS))
def test_concurrent_lock_holders_never_lose_an_increment_under_load(
    tmp_path_factory: pytest.TempPathFactory, run_index: int
) -> None:
    """The deterministic, load-reproducing exclusivity proof (repeated for
    flake detection, matching `test_concurrency.py`'s own precedent): N
    processes x many iterations each of acquire-lock -> unsynchronized
    read-modify-write -> release-lock. The final counter must equal the
    exact total number of increments attempted — any deviation is a lost
    update, proving the lock let two holders into the critical section at
    once.
    """
    tmp_dir = tmp_path_factory.mktemp(f"fleet-mutex-load-{run_index}")
    _run_mutual_exclusion_scenario(tmp_dir)


# ---------------------------------------------------------------------------
# 3. Crash-release DURING heavy concurrent load (the combined case)
# ---------------------------------------------------------------------------

N_CRASH_LOAD_SURVIVORS = 6
CRASH_LOAD_ITERATIONS_PER_SURVIVOR = 40
CRASH_LOAD_TOTAL_INCREMENTS = N_CRASH_LOAD_SURVIVORS * CRASH_LOAD_ITERATIONS_PER_SURVIVOR
_VICTIM_HOLD_SECONDS = 0.5


def _crash_load_victim(lock_path_str: str, start_barrier: object, acquired_event: object) -> None:
    """Contend for the lock alongside the survivors, win it, signal, hold
    briefly, then get hard-killed by the parent.

    Deliberately never touches the counter file — only the survivors do —
    so the survivors' final total stays exactly predictable regardless of
    when in its own hold this process gets killed.
    """
    start_barrier.wait(timeout=60)  # type: ignore[attr-defined]
    handle = acquire_lock(lock_path_str, timeout=30.0, retry_interval=0.005)
    acquired_event.set()  # type: ignore[attr-defined]
    time.sleep(_VICTIM_HOLD_SECONDS)
    release_lock(handle)  # pragma: no cover — unreachable; the parent kills us first


def _run_crash_under_load_scenario(tmp_dir: Path) -> None:
    """N survivors do exact-count RMW increments while a separate victim
    process — holding the same lock right then — is hard-killed mid-hold,
    in the middle of the same contention.
    """
    lock_path = tmp_dir / ".lock"
    counter_path = tmp_dir / "counter.txt"
    counter_path.write_text("0", encoding="utf-8")

    ctx = multiprocessing.get_context("spawn")
    # +1 so the victim crosses the same start line as the survivors — it
    # must be contending for the SAME lock at the SAME moment they are, not
    # started early/late where this would just be an ordinary crash-release
    # test with incidental bystanders.
    start_barrier = ctx.Barrier(N_CRASH_LOAD_SURVIVORS + 1)
    acquired_event = ctx.Event()

    survivors = [
        ctx.Process(
            target=_increment_worker,
            args=(
                str(lock_path),
                str(counter_path),
                CRASH_LOAD_ITERATIONS_PER_SURVIVOR,
                start_barrier,
            ),
        )
        for _ in range(N_CRASH_LOAD_SURVIVORS)
    ]
    victim = ctx.Process(
        target=_crash_load_victim, args=(str(lock_path), start_barrier, acquired_event)
    )

    for p in [*survivors, victim]:
        p.start()
    try:
        assert acquired_event.wait(timeout=15), "victim never signaled lock acquisition"

        # Hard-kill while the victim is provably holding the lock AND while
        # survivors are actively contending for it — they already crossed
        # the same start barrier, so several are necessarily mid-retry
        # right now, since the victim is the one holding the lock.
        victim.kill()
        victim.join(timeout=10)
        assert victim.exitcode is not None, "victim did not terminate"
        assert victim.exitcode != 0, "victim exited cleanly instead of being killed"

        for p in survivors:
            p.join(timeout=60)
            assert p.exitcode == 0, (
                f"survivor {p.name} exited with code {p.exitcode} — likely an "
                "unhandled LockTimeoutError, meaning the victim's death did "
                "not release the lock (a deadlock) or something else failed"
            )
    finally:
        for p in [*survivors, victim]:
            if p.is_alive():
                p.kill()
                p.join(timeout=10)

    final_count = int(counter_path.read_text(encoding="utf-8").strip())
    assert final_count == CRASH_LOAD_TOTAL_INCREMENTS, (
        f"expected exactly {CRASH_LOAD_TOTAL_INCREMENTS} increments "
        f"({N_CRASH_LOAD_SURVIVORS} survivors x {CRASH_LOAD_ITERATIONS_PER_SURVIVOR} "
        "each; the victim never touches the counter), got "
        f"{final_count} — {CRASH_LOAD_TOTAL_INCREMENTS - final_count} lost "
        "update(s) among the survivors despite a holder dying mid-contention"
    )


@pytest.mark.parametrize("run_index", range(N_REPEATS))
def test_survivors_lose_nothing_when_a_holder_is_killed_mid_contention(
    tmp_path_factory: pytest.TempPathFactory, run_index: int
) -> None:
    """The combined proof neither component test above covers alone: a lock
    holder dying is not just survivable in isolation (crash-release, #1
    above) and concurrency is not just safe in isolation (mutual exclusion
    under load, #2 above) — the two together, at the same instant, must
    neither deadlock the survivors NOR lose any of their updates. This is
    exactly the historical bug class (crash + concurrency together) that
    motivated the G6 redesign; no other test in this suite combines both
    conditions in one scenario.

    Two assertions carry the whole proof: every survivor must exit 0 (no
    `LockTimeoutError` — the victim's death genuinely released the lock, no
    deadlock), and the final counter must equal the exact expected total
    (no lost update among survivors despite a holder dying mid-contention).
    """
    tmp_dir = tmp_path_factory.mktemp(f"fleet-crash-under-load-{run_index}")
    _run_crash_under_load_scenario(tmp_dir)


# ---------------------------------------------------------------------------
# 4. Timeout still fails loud against a genuinely live, cross-process holder
# ---------------------------------------------------------------------------


def _hold_lock_child(lock_path_str: str, hold_seconds: float, acquired_event: object) -> None:
    """Acquire the lock, signal acquisition, hold it for `hold_seconds`, release."""
    handle = acquire_lock(lock_path_str, timeout=10.0, retry_interval=0.01)
    acquired_event.set()  # type: ignore[attr-defined]
    time.sleep(hold_seconds)
    release_lock(handle)


def test_second_acquirer_times_out_against_a_live_cross_process_holder(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A genuinely live holder (a different, still-running OS process) means
    a short-timeout acquirer must raise `LockTimeoutError` — never proceed
    as if unlocked. `test_events.py::TestLockContention` covers this
    in-process (two fds/threads in the same interpreter); this is the same
    property proven across a real process boundary.
    """
    tmp_dir = tmp_path_factory.mktemp("fleet-timeout-live-holder")
    lock_path = tmp_dir / ".lock"

    ctx = multiprocessing.get_context("spawn")
    acquired_event = ctx.Event()
    child = ctx.Process(
        target=_hold_lock_child, args=(str(lock_path), 3.0, acquired_event)
    )
    child.start()
    try:
        assert acquired_event.wait(timeout=10), "child never signaled lock acquisition"
        with pytest.raises(LockTimeoutError):
            acquire_lock(lock_path, timeout=0.3, retry_interval=0.01)
    finally:
        child.join(timeout=10)
        assert child.exitcode == 0, f"holder child exited with code {child.exitcode}"
