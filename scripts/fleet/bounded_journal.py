"""Shared, lock-protected bounded-tail journal-line writer (Phase 3.3
preflight recommended fix: role-gate and observe-failure logs losing rows
under parallel dispatch).

`observe.py`'s `_write_journal` (`observe-failures.log`) and
`role_block.py`'s `record_role_gate_decision` (`role-gate.jsonl`) each
independently read the whole file, appended one line, trimmed to a
rolling bounded tail, and rewrote the whole file -- with no lock. Under
parallel dispatch (several hook processes appending at once), two
writers' read-modify-write cycles interleave and the second writer's
rewrite silently discards the first writer's already-durable line:
reproduced under load with `N` concurrent writer processes, losing
roughly a quarter of the rows.

This factors the shared primitive out into one place both modules import
-- the same shape B5 used for `path_safety.slug_is_safe` ("one guard,
imported by both, replacing two independently-maintained copies of the
same class of defect") -- and adds the one thing neither had: mutual
exclusion around the read-modify-write.

The lock is `core/events.py`'s own OS-native advisory lock
(`acquire_lock`/`release_lock`), reused rather than reimplemented. That
module's docstring documents the exact cross-platform guarantee relied on
here: `fcntl.flock(fd, LOCK_EX)` on POSIX, `msvcrt.locking(fd, LK_NBLCK,
1)` on Windows, both wrapped in a bounded, non-blocking retry loop, with
the kernel itself releasing the lock if its holder dies (no manual
stale-lock reclaim anywhere -- see that module's own docstring for the
citations and the G6 redesign this replaced). Re-implementing a second,
independently-maintained lock here -- e.g. the `O_CREAT|O_EXCL` +
manual-reclaim pattern `core/events.py` itself moved away from -- was
rejected precisely because that pattern is the one this codebase already
proved defect-prone (the double-reclaim race documented in
`core/events.py`'s module docstring).

Each journal gets its OWN lock file (`<path>.lock`, a sibling of the
journal itself), never `core/events.py`'s shared `<fleet_dir>/.lock`:
`events.jsonl`, `role-gate.jsonl` and `observe-failures.log` all live in
the same `fleet/` directory, and serializing this diagnostic-only traffic
against NFR-1's primary manifest writes would be an unforced slowdown
with no correctness benefit.
"""

from __future__ import annotations

from pathlib import Path

from .core.events import acquire_lock, release_lock

__all__ = ["append_bounded_line"]

#: Matches `core/events.py`'s own default -- see that module's
#: `_DEFAULT_TIMEOUT`. These are best-effort diagnostic writes with a
#: trivial critical section (read a small file, append one line, write it
#: back), so real contention is expected to clear in milliseconds; this
#: bound only guards against a genuinely wedged holder.
_DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0


def append_bounded_line(path: Path, line: str, *, max_lines: int, timeout: float = _DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
    """Atomically append one line to `path`, trimming to the last `max_lines`.

    The read, append, trim, and rewrite all happen while holding the
    exclusive lock anchored at `<path>.lock`, so two callers racing to
    append never interleave their read-modify-write cycles -- the failure
    mode this function exists to close.

    Args:
        path: the journal file to append to. Its parent directory must
            already exist -- callers `mkdir` it before calling this (both
            current callers already do, as part of resolving `fleet_dir`).
        line: one line of text, with no trailing newline.
        max_lines: the rolling bounded tail -- lines beyond this, oldest
            first, are dropped on this write.
        timeout: seconds to wait for the lock before giving up.

    Raises:
        OSError: the read or write of `path` itself failed.
        LockTimeoutError: the lock could not be acquired within `timeout`.
            This is an `OSError` *sibling* from `core.errors`, not an
            `OSError` subclass -- a caller that must degrade silently on
            any failure here (both current callers do) needs to catch
            both explicitly, e.g. `except (OSError, LockTimeoutError):`.
    """
    lock_path = path.with_name(path.name + ".lock")
    handle = acquire_lock(lock_path, timeout=timeout)
    try:
        # Phase 3.3 preflight RE-RUN item D: plain `encoding="utf-8"` (no
        # `errors=`) raises `UnicodeDecodeError` on a single invalid byte --
        # a `ValueError`, NOT an `OSError` -- so one bad byte anywhere in an
        # otherwise-healthy journal killed it permanently: both current
        # callers catch only `(OSError, LockTimeoutError)` (this function's
        # own `Raises:` section above), so the append would raise straight
        # past them, uncaught, forever after. `errors="replace"` substitutes
        # U+FFFD for each invalid byte instead of raising -- this is a
        # best-effort DIAGNOSTIC log, not data this project parses back
        # field-by-field (each row is written, never re-parsed, by
        # `role_block.py`'s bounded tail; `observe.py`'s is read by a human
        # or `fleet doctor`), so a garbled OLD line surviving as
        # replacement-character mush is an acceptable trade against losing
        # the journal outright.
        existing = (
            [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln]
            if path.is_file()
            else []
        )
        existing.append(line)
        if len(existing) > max_lines:
            existing = existing[-max_lines:]
        path.write_text("\n".join(existing) + "\n", encoding="utf-8")
    finally:
        release_lock(handle)
