"""Append-only JSONL event log — the manifest's source of truth (NFR-1).

Every write is one self-contained, schema-validated event line, appended
under an OS-native advisory lock: `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on
POSIX, `msvcrt.locking(fd, LK_NBLCK, 1)` on Windows. The lock guards only the
shared log; per-session fragments are writer-partitioned and need no lock
(see `core/store.py`). A torn final line (crash mid-append) is skipped on
read, never fatal.

**Lock protocol (G6 redesign — replaces the former `O_CREAT|O_EXCL` +
manual stale-lock reclaim):**

The `.lock` file beside `events.jsonl` is a **persistent anchor**: it is
opened once (`O_CREAT | O_RDWR`, kept open for the critical section's whole
lifetime) and an OS-level advisory lock is taken on it — never deleted on
release, only unlocked. `acquire_lock` loops a bounded, non-blocking
acquire attempt (`LOCK_NB` / `LK_NBLCK`); a contended attempt raises an
`OSError`-family exception, which is treated as "held, retry" and bounded by
the same `timeout`/`retry_interval` contract as before, raising
`LockTimeoutError` on expiry — the caller must never proceed unlocked.

This is a structural fix, not a tuned one: the **kernel itself releases an
advisory lock the instant its holding process dies** (process exit, crash,
or `SIGKILL` all close every fd the process held, and closing the last fd on
a `flock`'d file — or terminating the process holding a `msvcrt.locking`
byte-range lock — releases the lock automatically). There is therefore no
such thing as a "stale" lock under this design: nothing is ever reclaimed,
because nothing crash-held is ever left locked. The entire former stale-lock
machinery — age/pid-liveness reclaim, a separate `.reclaiming` marker file,
`O_CREAT|O_EXCL` as the mutual-exclusion primitive, `_pid_is_alive` — is
deleted along with it, and with it every TOCTOU/ABA hazard that machinery
was prone to (including the load-dependent double-reclaim race that
motivated this redesign: two racers both winning a reclaim's check→remove
gap on a reused lock path, defeating mutual exclusion).

**Local-filesystem constraint:** OS advisory locks (`flock`/`msvcrt.locking`)
are a promise the *local* filesystem driver enforces between processes on
the *same* machine; they are well known to be unreliable — silently
non-exclusive, or simply unsupported — over some network filesystems (NFS
without `lockd`, older SMB/CIFS mounts, some FUSE backends). The fleet's
`events.jsonl` + `.lock` pair is a local working-copy artifact (never a
shared network path across separate machines/nodes), so this constraint
does not currently bind — documented here so it stays true if the storage
location ever changes.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import LockTimeoutError, PreconditionUnmet, ValidationError
from .ownership import assert_writer_may
from .schema import Event, validate_event

if os.name == "nt":
    import msvcrt
else:
    import fcntl

#: Default bounded-retry contract for lock acquisition.
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_RETRY_INTERVAL = 0.02

#: Errors that mean "the OS lock is currently held by someone else, retry"
#: rather than a real failure.
#:
#: POSIX (`fcntl.flock` with `LOCK_NB`): "If LOCK_NB is used and the lock
#: cannot be acquired, an OSError will be raised and the exception will have
#: an errno attribute set to EACCES or EAGAIN (depending on the operating
#: system; for portability, check for both values)."
#: https://docs.python.org/3.14/library/fcntl.html#fcntl.flock
#:
#: Windows (`msvcrt.locking` with `LK_NBLCK`): "If the file cannot be locked,
#: the exception OSError is raised." No specific errno is documented, so the
#: contended case is any `OSError` from that call.
#: https://docs.python.org/3.14/library/msvcrt.html#msvcrt.locking
#:
#: Both platforms are simply "any OSError from this specific, narrowly
#: scoped lock call" — `BlockingIOError`/`PermissionError` (the concrete
#: subclasses Python raises for EAGAIN/EACCES per PEP 3151) are themselves
#: `OSError` subclasses, so catching `OSError` here already covers them. If
#: some other, genuinely unrelated `OSError` were ever raised by this call,
#: bounded retry+timeout still fails loud (`LockTimeoutError`) rather than
#: proceeding unlocked — the same "worse case is a loud, safe failure, never
#: silent corruption" precedent this module has always used for platform
#: lock-error quirks.
_LOCK_CONTENDED_ERRORS: tuple[type[OSError], ...] = (OSError,)


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


def _lock_fd(fd: int) -> None:
    """Attempt a non-blocking, exclusive OS advisory lock on `fd`.

    POSIX: `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` — "LOCK_EX:
    acquire an exclusive lock. ... LOCK_NB: bitwise OR with any of the other
    three [LOCK_*] to make the request non-blocking."
    https://docs.python.org/3.14/library/fcntl.html#fcntl.flock

    Windows: `msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)` locks a single byte at
    the file's *current* position — "LK_NBLCK: Locks the specified bytes. If
    the bytes cannot be locked, the program immediately raises OSError." The
    locked region is relative to the current file position, so the position
    is reset to the start of the file first.
    https://docs.python.org/3.14/library/msvcrt.html#msvcrt.locking

    Args:
        fd: an open, writable OS file descriptor for the lock anchor file.

    Raises:
        OSError: (see `_LOCK_CONTENDED_ERRORS`) if the lock is currently
            held by another process. Callers must treat this as "held,
            retry" — never as "safe to proceed unlocked."
    """
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: int) -> None:
    """Release the OS advisory lock held on `fd` by a prior `_lock_fd` call.

    POSIX: `fcntl.flock(fd, fcntl.LOCK_UN)` — "LOCK_UN: release an existing
    lock." https://docs.python.org/3.14/library/fcntl.html#fcntl.flock

    Windows: `msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)` unlocks the same
    single byte `_lock_fd` locked, at the same file position.
    https://docs.python.org/3.14/library/msvcrt.html#msvcrt.locking

    Args:
        fd: the file descriptor previously locked by `_lock_fd`.

    Raises:
        OSError: if the underlying unlock call fails. Callers in this
            module never propagate this — closing `fd` immediately after
            releases the OS-level lock regardless (the kernel tears down
            every lock a file descriptor holds when it is closed), so an
            unlock-call failure here is not a correctness problem, only a
            missed optimization of doing it explicitly first.
    """
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class LockHandle:
    """An acquired OS advisory lock, returned by `acquire_lock`.

    Opaque to callers beyond passing it straight to `release_lock` (or
    letting `LockedLog` do that for you) — the fields exist for diagnostics
    and testing, not for callers to act on directly.

    Attributes:
        path: the lock anchor file this handle's lock guards.
        fd: the open OS file descriptor the lock is held on. Must stay open
            for as long as the lock is held; closed by `release_lock`.
    """

    path: Path
    fd: int


def acquire_lock(
    lock_path: Path | str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    retry_interval: float = _DEFAULT_RETRY_INTERVAL,
) -> LockHandle:
    """Acquire the exclusive OS advisory lock anchored at `lock_path`.

    Opens `lock_path` once (creating it if absent) and keeps that file
    descriptor open for the lock's whole lifetime — the anchor file is
    never deleted or recreated by this protocol, only ever locked and
    unlocked, which is what makes the file-path-reuse TOCTOU/ABA class of
    bug (the double-reclaim race this redesign replaces) structurally
    impossible: there is no create/delete cycle on the path to race on.

    Args:
        lock_path: the lock anchor file to acquire (see `lock_path_for`).
        timeout: seconds to keep retrying before giving up.
        retry_interval: seconds to sleep between contended retries.

    Returns:
        LockHandle: pass this to `release_lock` (or use `LockedLog`, which
        does so automatically) once the critical section is done.

    Raises:
        LockTimeoutError: if the lock is not acquired within `timeout`. The
            caller must treat this as "did not acquire" — never proceed as
            if unlocked. The anchor's fd is closed before this is raised, so
            a timed-out attempt leaks nothing.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout

    open_flags = os.O_CREAT | os.O_RDWR
    if os.name == "nt":
        open_flags |= os.O_BINARY
    fd = os.open(str(lock_path), open_flags)

    while True:
        try:
            _lock_fd(fd)
        except _LOCK_CONTENDED_ERRORS:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise LockTimeoutError(f"timed out acquiring lock {lock_path}")
            time.sleep(retry_interval)
            continue

        # Diagnostics only (`{pid, ts}`, same shape the old lock content
        # used) — nothing in this module or its callers reads this back for
        # correctness; a human inspecting a held `.lock` file mid-incident
        # is the only consumer. Written under the lock we just won, so it
        # can never race with another holder's own diagnostic write.
        try:
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"pid": os.getpid(), "ts": _now_iso()}).encode("utf-8"))
        except OSError:
            pass  # purely diagnostic; never fail acquisition over this
        return LockHandle(path=lock_path, fd=fd)


def release_lock(handle: LockHandle) -> None:
    """Release the OS advisory lock held by `handle`.

    Unconditionally closes `handle.fd` — this both releases the OS lock (if
    the explicit unlock below did not already) and frees the descriptor.
    The anchor file at `handle.path` is deliberately left on disk: deleting
    a lock-path anchor is exactly the reuse hazard this redesign removes,
    so leaving it behind (empty or holding the last holder's diagnostic
    `{pid, ts}`) is correct, not a leak.

    Args:
        handle: the handle returned by the matching `acquire_lock` call.
    """
    try:
        _unlock_fd(handle.fd)
    except OSError:
        pass  # closing fd below releases the OS-level lock regardless
    finally:
        os.close(handle.fd)


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
    ) -> None:
        """Initialize a LockedLog for `log_path`.

        Args:
            log_path: the event log this lock guards.
            timeout: seconds to keep retrying acquisition before giving up.
            retry_interval: seconds to sleep between retries.
        """
        self.log_path = Path(log_path)
        self._lock_path = lock_path_for(self.log_path)
        self._timeout = timeout
        self._retry_interval = retry_interval
        self._handle: LockHandle | None = None

    def __enter__(self) -> "LockedLog":
        """Acquire the lock and return this context manager.

        Returns:
            LockedLog: `self`, once the lock is held.

        Raises:
            LockTimeoutError: if the lock could not be acquired in time.
        """
        self._handle = acquire_lock(
            self._lock_path,
            timeout=self._timeout,
            retry_interval=self._retry_interval,
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
        if self._handle is not None:
            release_lock(self._handle)
            self._handle = None
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
    precondition: Callable[[list[Event]], bool] | None = None,
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

    `precondition`, if given, is evaluated **inside the lock**, against the
    exact `existing` event list this call's idempotency check just read —
    the same atomicity the idempotency dedupe itself relies on. This is what
    makes it useful for a caller-side terminal-state check (e.g.
    `handoff.self_register` refusing to flip a handoff that a racing
    `cancel()` already closed): a check performed *before* acquiring the
    lock (reading a fragment, say) can always be stale by the time the lock
    is actually held, because time passes between reading and acquiring —
    `precondition` closes exactly that window by running only once the lock
    (and therefore a guaranteed-fresh view of the log) is held, immediately
    before the write it would gate. It only runs for a genuinely new event —
    if `idempotency_key` already exists, that dedupe is unconditional and
    `precondition` never runs at all (review FIX #2's own scope: "the
    precondition must not break the existing double-launch dedupe").

    Args:
        log_path: path to the event log.
        event: an `Event`, or a raw dict to validate first (NFR-7).
        timeout: seconds to keep retrying lock acquisition.
        retry_interval: seconds to sleep between lock-acquisition retries.
        precondition: optional callable taking the event list already
            persisted in the log (as read fresh, under the lock, for this
            call's own idempotency check) and returning whether the write
            may proceed. `None` (the default) means "no additional
            precondition" — every existing caller is unaffected.

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
        PreconditionUnmet: if `precondition` is given and returns falsy for
            the fresh, under-the-lock event list. Nothing is written. Kept
            distinct from the idempotency dedupe's `None` return — `None`
            means "already recorded, no-op is correct"; this means "must
            not be recorded, something the caller depends on has changed."
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

    with LockedLog(log_path, timeout=timeout, retry_interval=retry_interval):
        existing = read_all(log_path)
        if any(e.idempotency_key == ev.idempotency_key for e in existing):
            return None

        if precondition is not None and not precondition(existing):
            raise PreconditionUnmet(
                f"precondition rejected append of idempotency_key="
                f"{ev.idempotency_key!r} (type={ev.type!r}, node_id={ev.node_id!r}); "
                "nothing was written"
            )

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
