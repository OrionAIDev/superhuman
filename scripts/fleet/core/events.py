"""Append-only JSONL event log — the manifest's source of truth (NFR-1).

Every write is one self-contained, schema-validated event line, appended
under a short-lived exclusive lockfile (`O_CREAT|O_EXCL` create, then release
by delete). The lock guards only the shared log; per-session fragments are
writer-partitioned and need no lock (see `core/store.py`). A torn final line
(crash mid-append) is skipped on read, never fatal; a stale lock (crashed
holder) is reclaimed by age **and** pid liveness, both required — age alone,
or liveness alone, is never sufficient (ARCHITECTURE "Lockfile protocol").

**Known, accepted extreme-timing residuals (GPT-5 review findings #5/#6 —
documented per user decision, not fixed this round; both require pathological
OS scheduling, well beyond anything this module's own test suite has been
able to trigger even under deliberately heavy contention):**

5. A create-then-write gap in `acquire_lock`: the lock file is created via
   `O_CREAT|O_EXCL` (empty) and its `{pid, ts}` content is written a moment
   later. If the writing process is suspended by the OS in exactly that gap
   for longer than `stale_age` (30s default), another process could see an
   empty, apparently-ownerless, old-enough file and reclaim it out from under
   a holder that is not actually dead — merely paused for an extraordinarily
   long time between two adjacent syscalls.
6. Inode reuse (ABA) on the `_reclaim_if_stale` identity re-check: the
   `st_ino`/`st_dev` comparison that protects against a delayed reclaim
   decision acting on a since-replaced lock assumes a reused path won't also
   receive a coincidentally-reused inode number in the same window. Most
   filesystems don't recycle inode numbers quickly, but it's not a
   guarantee.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import LockTimeoutError, ValidationError
from .ownership import assert_writer_may
from .schema import Event, validate_event

#: Default bounded-retry contract for lock acquisition.
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_RETRY_INTERVAL = 0.02
_DEFAULT_STALE_AGE = 30.0

#: How old the reclaim marker (`<lock>.reclaiming`) must be before it is even
#: considered orphaned. Deliberately much shorter than `_DEFAULT_STALE_AGE`:
#: a reclaim attempt is a handful of filesystem calls, not a held write lock,
#: so it should complete in well under a second under any real contention.
_MARKER_STALE_AGE = 5.0

#: Errors that mean "lock is currently held by someone else, retry" rather
#: than a real failure. `O_CREAT|O_EXCL` contention on an existing file is
#: `FileExistsError` on POSIX; on Windows, `CreateFile` under concurrent
#: `O_EXCL` contention can surface as `PermissionError` (ERROR_ACCESS_DENIED)
#: instead of `FileExistsError` (ERROR_FILE_EXISTS) — a documented Windows
#: quirk, not a real permission problem. Both are bounded by the same
#: retry/timeout below, so treating them alike never risks proceeding
#: unlocked; worst case a genuine permission problem surfaces as a
#: `LockTimeoutError` instead of a `PermissionError`, which is still a loud,
#: safe failure.
_LOCK_CONTENDED_ERRORS: tuple[type[OSError], ...] = (
    (FileExistsError, PermissionError) if os.name == "nt" else (FileExistsError,)
)

#: Errors that mean "this file is already gone (or being deleted right now
#: by someone else)" when the intent is `os.remove(path)`-as-cleanup — a
#: no-op, not a failure. `FileNotFoundError` is the POSIX-universal case; on
#: Windows, two processes racing to delete the same file can surface
#: `PermissionError` (ERROR_SHARING_VIOLATION/ERROR_ACCESS_DENIED) for
#: whichever one loses the race, instead of `FileNotFoundError` — the same
#: family of quirk as `_LOCK_CONTENDED_ERRORS` above, verified directly
#: (GPT-5 review finding #4's own test suite reproduced it while proving the
#: marker-recovery fix: multiple racers' `os.remove` on the same orphaned
#: marker occasionally raised `PermissionError`, not `FileNotFoundError`).
_REMOVAL_RACE_ERRORS: tuple[type[OSError], ...] = (
    (FileNotFoundError, PermissionError) if os.name == "nt" else (FileNotFoundError,)
)


def lock_path_for(log_path: Path | str) -> Path:
    """Return the lockfile path guarding appends to `log_path`.

    Args:
        log_path: path to the event log (e.g. `.../fleet/events.jsonl`).

    Returns:
        Path: the sibling `.lock` file in the same directory
        (ARCHITECTURE "System diagram" — `.lock` lives beside `events.jsonl`).
    """
    return Path(log_path).parent / ".lock"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix.

    Returns:
        str: the current time, e.g. `"2026-08-14T12:00:00.000000Z"`.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _pid_is_alive(pid: int) -> bool:
    """Return whether `pid` identifies a currently-running process.

    Args:
        pid: the process id to check.

    Returns:
        bool: True if a process with that pid is alive; False otherwise
        (including for `pid <= 0`, which is never a real process here).
    """
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, but we're not allowed to signal it — still alive.
        return True
    except OSError:
        return False
    return True


def _reclaim_marker_if_orphaned(marker_path: Path, marker_stale_age: float) -> bool:
    """Remove `marker_path` if it is an orphaned reclaim marker.

    The reclaim marker itself needs the same crash-recovery story as the
    main lock, for the same reason (GPT-5 review finding #4): if a process
    crashes after winning the marker's `O_CREAT|O_EXCL` create but before
    its `finally` removes it, the marker orphans, and — without this —
    every future reclaim attempt gets `FileExistsError` on the marker create
    forever, permanently wedging all writers behind one crashed process.

    Orphaned requires **both** conditions, mirroring the main lock's own
    "age and pid liveness, both required" contract: the marker must be older
    than `marker_stale_age` (a reclaim attempt is a handful of filesystem
    calls; a live one should never take seconds) *and* its recorded pid must
    not be alive. A fresh stat is taken immediately before the actual
    removal and compared against the stat this decision was based on, so a
    marker a live reclaimer (re)creates in the gap between this function's
    checks is never removed out from under it.

    Args:
        marker_path: the reclaim marker to inspect.
        marker_stale_age: minimum age in seconds before the marker is even
            considered for orphan recovery.

    Returns:
        bool: True if the marker was orphaned and has been removed (or was
        already gone), meaning the caller may retry the marker's own
        `O_CREAT|O_EXCL` create — that create re-serializes the recovery, so
        multiple racers concurrently reaching this same conclusion is safe,
        only one of them will win the retry. False if the marker is still
        within its fresh window or genuinely live and must not be touched.
    """
    try:
        stat_before = marker_path.stat()
    except FileNotFoundError:
        return True  # already gone — safe to retry the create immediately

    age = time.time() - stat_before.st_mtime
    if age <= marker_stale_age:
        return False

    try:
        content = json.loads(marker_path.read_text(encoding="utf-8"))
        pid = int(content.get("pid", -1))
    except (OSError, ValueError, TypeError):
        pid = -1

    if _pid_is_alive(pid):
        return False

    try:
        stat_now = marker_path.stat()
    except FileNotFoundError:
        return True  # already gone — someone else's cleanup beat us to it

    if stat_now.st_mtime != stat_before.st_mtime:
        # It changed since we started evaluating — do not touch it; treat
        # this exactly like "still fresh" and let the caller back off.
        return False

    try:
        os.remove(marker_path)
    except _REMOVAL_RACE_ERRORS:
        pass
    return True


def _try_create_marker(marker_path: Path) -> bool:
    """Attempt to exclusively create the reclaim marker at `marker_path`.

    Writes `{pid, ts}` — the same shape as the main lock — so an orphaned
    marker can later be identified by `_reclaim_marker_if_orphaned` using
    the same age/pid-liveness logic.

    Args:
        marker_path: the reclaim marker to create.

    Returns:
        bool: True if this process now exclusively holds the marker; False
        if the create was contended (someone else already holds it).
    """
    try:
        marker_fd = os.open(str(marker_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(marker_fd, json.dumps({"pid": os.getpid(), "ts": _now_iso()}).encode("utf-8"))
        finally:
            os.close(marker_fd)
        return True
    except _LOCK_CONTENDED_ERRORS:
        return False


def _reclaim_if_stale(lock_path: Path, stale_age: float) -> bool:
    """Reclaim `lock_path` if it is both old enough and its holder is dead.

    Both conditions are required (age alone, or liveness alone, never
    triggers reclaim) — see ARCHITECTURE "Lockfile protocol".

    Reclaim is gated by a **separate exclusive marker file**
    (`<lock_path>.reclaiming`), created with the same `O_CREAT|O_EXCL`
    primitive `acquire_lock` itself relies on. Only the racer that wins the
    marker's creation may inspect-and-act on `lock_path`; every other racer
    gets a real `OSError` from the marker create and backs off immediately,
    never touching `lock_path` at all.

    This exists because the more obvious design — rename the stale lock
    straight to a unique per-attempt name via `os.replace`, on the theory
    that "two processes can't both succeed renaming the same source" — does
    **not** hold on this platform: empirically, when multiple processes call
    `os.replace(same_source, different_unique_destinations)` at nearly the
    same instant, Windows/NTFS routinely lets *several* of those renames
    report success (verified directly, in isolation from any of this
    module's own logic: 30/30 runs of 20 racers each produced multiple
    "successful" renames of one source file). A first attempt at this fix
    used exactly that rename-based design plus an `st_ino`/`st_dev` identity
    check after the fact; it closed the specific TOCTOU the review finding
    described but still occasionally let two racers both pass the identity
    check, because NTFS's rename race can leave two *different* destination
    paths reporting the *same* inode for the same source. `O_CREAT|O_EXCL`
    create, by contrast, has been exercised at N=8..20 concurrent racers
    (this module's own `acquire_lock`, `tests/fleet/test_concurrency.py`'s
    flagship, and `tests/fleet/test_stale_lock_reclaim_race.py`) without a
    single observed double-success — so the actual mutual-exclusion gate
    here is a create, not a rename, and the rename underneath it only ever
    runs for the one racer holding that gate.

    The marker itself needs the same crash-recovery story as the main lock
    (GPT-5 review finding #4): if this function's own process crashes after
    winning the marker create but before the `finally` below removes it, a
    contended marker create falls through to `_reclaim_marker_if_orphaned`
    rather than unconditionally giving up — otherwise a single crash during
    reclaim would permanently wedge every future writer behind an orphaned
    marker no one can ever clear.

    Args:
        lock_path: the lockfile to inspect.
        stale_age: minimum age in seconds before a lock is even considered.

    Returns:
        bool: True only for the single racer that both won the marker gate
        and confirmed (immediately before acting, while holding that gate)
        that `lock_path` was still the exact stale instance it evaluated —
        meaning the caller should retry acquisition immediately. False for
        every other racer — the caller must not treat a False as "safe to
        proceed."
    """
    try:
        stat_before = lock_path.stat()
    except FileNotFoundError:
        return True  # already gone — safe to retry create immediately

    age = time.time() - stat_before.st_mtime
    if age < stale_age:
        return False

    try:
        content = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(content.get("pid", -1))
    except (OSError, ValueError, TypeError):
        pid = -1

    if _pid_is_alive(pid):
        return False

    marker_path = lock_path.with_name(f"{lock_path.name}.reclaiming")
    if not _try_create_marker(marker_path):
        # Contended — either a live racer holds it, or it is orphaned from a
        # process that crashed between winning the create and its own
        # `finally` cleanup (GPT-5 review finding #4). Attempt orphan
        # recovery and retry the create exactly once — bounded, no nested
        # retry loop; the create itself re-serializes the recovery, so this
        # stays safe even if several racers reach the same conclusion at
        # once.
        if not _reclaim_marker_if_orphaned(marker_path, _MARKER_STALE_AGE):
            return False
        if not _try_create_marker(marker_path):
            return False

    try:
        # Re-verify identity now that this process exclusively holds the
        # marker — no other racer can be mutating lock_path while we hold
        # it, so this check (and the rename below) are genuinely safe. This
        # still matters even under the marker gate: the *decision* above
        # (age/pid) may be based on a snapshot read a while ago, and the
        # lock could have legitimately moved on to a new holder since.
        try:
            stat_now = lock_path.stat()
        except FileNotFoundError:
            return False  # already gone by the time we won the gate

        if stat_now.st_ino != stat_before.st_ino or stat_now.st_dev != stat_before.st_dev:
            return False  # a different (almost certainly live) lock is here now

        claim_path = lock_path.with_name(f"{lock_path.name}.reclaim-{os.getpid()}-{uuid4().hex}")
        os.replace(lock_path, claim_path)
        try:
            os.remove(claim_path)
        except _REMOVAL_RACE_ERRORS:
            pass
        return True
    finally:
        try:
            os.remove(marker_path)
        except _REMOVAL_RACE_ERRORS:
            pass


def acquire_lock(
    lock_path: Path | str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    retry_interval: float = _DEFAULT_RETRY_INTERVAL,
    stale_age: float = _DEFAULT_STALE_AGE,
) -> None:
    """Acquire the exclusive lockfile at `lock_path`.

    Uses `O_CREAT|O_EXCL` so the create itself is the atomic exclusivity
    check (portable across POSIX and Windows). Bounded retry with a stale-lock
    reclaim check on every contended attempt.

    Args:
        lock_path: the lockfile to acquire.
        timeout: seconds to keep retrying before giving up.
        retry_interval: seconds to sleep between retries.
        stale_age: seconds a lock must be untouched before it is even
            considered for reclaim.

    Raises:
        LockTimeoutError: if the lock is not acquired within `timeout`. The
            caller must treat this as "did not acquire" — never proceed as if
            unlocked.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps({"pid": os.getpid(), "ts": _now_iso()}).encode("utf-8"))
            finally:
                os.close(fd)
            return
        except _LOCK_CONTENDED_ERRORS:
            if _reclaim_if_stale(lock_path, stale_age):
                continue
            if time.monotonic() >= deadline:
                raise LockTimeoutError(f"timed out acquiring lock {lock_path}")
            time.sleep(retry_interval)


def release_lock(lock_path: Path | str) -> None:
    """Release the lockfile at `lock_path`.

    Args:
        lock_path: the lockfile to release.
    """
    try:
        os.remove(Path(lock_path))
    except _REMOVAL_RACE_ERRORS:
        pass


class LockedLog:
    """Context manager holding the exclusive append lock for one log file.

    Attributes:
        log_path: the event log this lock guards.
    """

    def __init__(
        self,
        log_path: Path | str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        retry_interval: float = _DEFAULT_RETRY_INTERVAL,
        stale_age: float = _DEFAULT_STALE_AGE,
    ) -> None:
        """Initialize a LockedLog for `log_path`.

        Args:
            log_path: the event log this lock guards.
            timeout: seconds to keep retrying acquisition before giving up.
            retry_interval: seconds to sleep between retries.
            stale_age: seconds before a held lock is even considered stale.
        """
        self.log_path = Path(log_path)
        self._lock_path = lock_path_for(self.log_path)
        self._timeout = timeout
        self._retry_interval = retry_interval
        self._stale_age = stale_age

    def __enter__(self) -> "LockedLog":
        """Acquire the lock and return this context manager.

        Returns:
            LockedLog: `self`, once the lock is held.

        Raises:
            LockTimeoutError: if the lock could not be acquired in time.
        """
        acquire_lock(
            self._lock_path,
            timeout=self._timeout,
            retry_interval=self._retry_interval,
            stale_age=self._stale_age,
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        """Release the lock unconditionally, including on an exception.

        Args:
            exc_type: the exception type, if the `with` block raised; else None.
            exc: the exception instance, if any.
            tb: the traceback, if any.

        Returns:
            bool: always `False` — any exception from the `with` block
            propagates normally; this only releases the lock as a side effect.
        """
        release_lock(self._lock_path)
        return False


def _event_to_json_line(event: Event) -> str:
    """Serialize `event` to one compact JSON line (no embedded newlines).

    Args:
        event: the event to serialize.

    Returns:
        str: a single-line JSON encoding of `event`, ready to append with a
        trailing newline.
    """
    return json.dumps(asdict(event), separators=(",", ":"))


def _ends_with_newline(log_path: Path) -> bool:
    """Return whether `log_path` exists, is non-empty, and ends with `\\n`.

    Args:
        log_path: the log file to check.

    Returns:
        bool: True if `log_path` is missing or empty (nothing to terminate),
        or its final byte is a newline; False if its last line is unterminated
        (a torn write from a crashed prior append).
    """
    if not log_path.exists() or log_path.stat().st_size == 0:
        return True  # nothing to terminate
    with open(log_path, "rb") as f:
        f.seek(-1, os.SEEK_END)
        return f.read(1) == b"\n"


def append(
    log_path: Path | str,
    event: Event | dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    retry_interval: float = _DEFAULT_RETRY_INTERVAL,
    stale_age: float = _DEFAULT_STALE_AGE,
) -> Event | None:
    """Append one validated, ownership-checked event, deduping on `idempotency_key`.

    The whole read-check-write sequence runs under the exclusive lock, so
    concurrent appenders serialize cleanly (NFR-1). If the log's final byte
    is not a newline (a torn line from a crashed prior append), a newline is
    written first so the new line parses as its own complete record — the
    torn line is left behind as a separate, harmlessly-skippable line rather
    than being glued onto the new one.

    Every `payload` key that names an owned field (`core.schema.FIELD_OWNERS`)
    is checked against `event.writer_role` via `core.ownership.assert_writer_may`
    *before* the lock is even acquired — a non-owner write raises and nothing
    is persisted (FR-8). This is the actual write interface DESIGN's data flow
    describes ("validate_event -> ownership.assert_writer_may -> events.append");
    it is enforced here, not left to each caller to remember.

    Args:
        log_path: path to the event log.
        event: an `Event`, or a raw dict to validate first (NFR-7).
        timeout: seconds to keep retrying lock acquisition.
        retry_interval: seconds to sleep between lock-acquisition retries.
        stale_age: seconds before a held lock is even considered stale.

    Returns:
        Event | None: the appended `Event`, or `None` if an event with the
        same `idempotency_key` was already present (a dedupe no-op, not an
        error).

    Raises:
        ValidationError: if `event` (dict or `Event`) fails schema
            validation. Nothing is written in that case.
        OwnershipError: if `event.writer_role` may not write `event.type`
            itself, or one of the owned fields present in `event.payload`
            (FR-8). Nothing is written.
        LockTimeoutError: if the lock could not be acquired in time.
    """
    # Validate unconditionally (GPT-5 review finding #2) — an already
    # constructed `Event` is NOT exempt. `Event` is frozen, but its
    # `payload` dict is mutable and `Event(...)` construction itself never
    # validates; only `validate_event` does. Skipping it for pre-built
    # `Event`s let an invalid one (e.g. `payload={"lifecycle": ""}`) reach
    # the log untouched, defeating NFR-7's "nothing bad is ever persisted"
    # guarantee for that one call path. This is the safety-critical write
    # boundary (DP#5): it validates every time, with no shortcut.
    raw = asdict(event) if isinstance(event, Event) else event
    ev = validate_event(raw)

    # Ownership applies to the event's TYPE as well as its payload fields
    # (GPT-5 review finding #1): FIELD_OWNERS marks `observation` and
    # `recommendation` ceo-owned, and schema.py documents them as
    # "ownership-checked the same way" — but only checking `ev.payload` keys
    # left `ev.type` itself unchecked, so a superhuman-side writer could
    # forge `type="observation"` and it would sail through untouched.
    # assert_writer_may() no-ops for any type with no FIELD_OWNERS entry
    # (ordinary types like session_registered), so this is a pure addition.
    assert_writer_may(ev.type, ev.writer_role)
    for field in ev.payload:
        # assert_writer_may() itself no-ops for a field with no FIELD_OWNERS
        # entry (unowned/free) — calling it for every payload key is simpler
        # and no less correct than pre-filtering to FIELD_OWNERS here.
        assert_writer_may(field, ev.writer_role)

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with LockedLog(log_path, timeout=timeout, retry_interval=retry_interval, stale_age=stale_age):
        existing = read_all(log_path)
        if any(e.idempotency_key == ev.idempotency_key for e in existing):
            return None

        needs_leading_newline = not _ends_with_newline(log_path)
        with open(log_path, "a", encoding="utf-8", newline="\n") as f:
            if needs_leading_newline:
                f.write("\n")
            f.write(_event_to_json_line(ev) + "\n")
            f.flush()
            os.fsync(f.fileno())

    return ev


def read_all(log_path: Path | str) -> list[Event]:
    """Read every valid event from the log, in append order.

    A line that fails to parse as JSON, or fails schema validation, is
    skipped rather than raised — the only expected cause is a torn final
    line from a crash mid-append (NFR-7 / ARCHITECTURE "Failure modes"), and
    a skip keeps the log replayable without a human in the loop.

    Args:
        log_path: path to the event log.

    Returns:
        list[Event]: every valid event, in the order they were appended. An
        empty list if the log does not exist yet.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return []

    events: list[Event] = []
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            events.append(validate_event(data))
        except ValidationError:
            continue
    return events
