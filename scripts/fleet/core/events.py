"""Append-only JSONL event log — the manifest's source of truth (NFR-1).

Every write is one self-contained, schema-validated event line, appended
under a short-lived exclusive lockfile (`O_CREAT|O_EXCL` create, then release
by delete). The lock guards only the shared log; per-session fragments are
writer-partitioned and need no lock (see `core/store.py`). A torn final line
(crash mid-append) is skipped on read, never fatal; a stale lock (crashed
holder) is reclaimed by age **and** pid liveness, both required — age alone,
or liveness alone, is never sufficient (ARCHITECTURE "Lockfile protocol").
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import LockTimeoutError, ValidationError
from .schema import Event, validate_event

#: Default bounded-retry contract for lock acquisition.
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_RETRY_INTERVAL = 0.02
_DEFAULT_STALE_AGE = 30.0

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


def _reclaim_if_stale(lock_path: Path, stale_age: float) -> bool:
    """Reclaim `lock_path` if it is both old enough and its holder is dead.

    Both conditions are required (age alone, or liveness alone, never
    triggers reclaim) — see ARCHITECTURE "Lockfile protocol".

    Args:
        lock_path: the lockfile to inspect.
        stale_age: minimum age in seconds before a lock is even considered.

    Returns:
        bool: True if the lock was stale and has been removed (or was
        already gone), meaning the caller should retry acquisition
        immediately; False if the lock is still legitimately held.
    """
    try:
        mtime = lock_path.stat().st_mtime
    except FileNotFoundError:
        return True  # already gone — safe to retry create immediately

    age = time.time() - mtime
    if age < stale_age:
        return False

    try:
        content = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(content.get("pid", -1))
    except (OSError, ValueError, TypeError):
        pid = -1

    if _pid_is_alive(pid):
        return False

    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass
    return True


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
    except FileNotFoundError:
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
    """Append one validated event to the log, deduping on `idempotency_key`.

    The whole read-check-write sequence runs under the exclusive lock, so
    concurrent appenders serialize cleanly (NFR-1). If the log's final byte
    is not a newline (a torn line from a crashed prior append), a newline is
    written first so the new line parses as its own complete record — the
    torn line is left behind as a separate, harmlessly-skippable line rather
    than being glued onto the new one.

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
        ValidationError: if `event` is a dict that fails schema validation.
            Nothing is written in that case.
        LockTimeoutError: if the lock could not be acquired in time.
    """
    ev = event if isinstance(event, Event) else validate_event(event)
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
