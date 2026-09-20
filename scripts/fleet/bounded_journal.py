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

import os
import tempfile
from pathlib import Path

from .core.events import acquire_lock, release_lock

__all__ = ["append_bounded_line"]

#: Preflight item 5 (Minor, correctness lens): this USED to match
#: `core/events.py`'s own `_DEFAULT_TIMEOUT` (10.0s) -- but that value is
#: also the INSTALLED HOOK's entire `"timeout": 10` budget
#: (`hooks_install.py::_TIMEOUT_SECONDS`), so contention on this
#: best-effort, diagnostic-only write alone could consume the hook's
#: WHOLE budget, leaving nothing for its actual primary work. These are a
#: trivial critical section (read a small file, append one line, write it
#: back), so real contention is expected to clear in milliseconds; a
#: genuinely wedged holder should fail this fast, not linger for the
#: hook's entire allotment. Lowered to 1.0s -- a full second is still
#: generous against a millisecond-scale critical section, while bounding
#: the worst case to a small fraction of the 10s hook budget rather than
#: all of it.
#:
#: Both current callers already make their behaviour on a timeout
#: explicit: `observe.journal_early_cli_failure` catches
#: `(OSError, ValueError, LockTimeoutError)` and degrades to one
#: `stderr`-printed line (Loudness tier 2); `role_block.record_role_gate_decision`
#: catches the identical triple and degrades to a silent no-op (there is
#: no primary write for it to protect). Lowering this default only makes
#: that already-documented degrade path trigger sooner under genuine
#: contention -- it does not change whether either caller catches it.
_DEFAULT_LOCK_TIMEOUT_SECONDS = 1.0


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: never truncate-in-place.

    Preflight item 3 (Minor, correctness lens): this module's own
    docstring says "Atomically append", but the original implementation
    called `path.write_text(...)` directly -- which truncates the file
    the instant it opens in write mode, before any new content lands. A
    kill between that truncation and the rewrite completing (the hook's
    own 10s timeout can do this) loses the ENTIRE bounded tail, not just
    the newest line -- exactly the D7.7 decision log this primitive backs.

    Mirrors `hooks_install.py::_atomic_write`'s pattern -- temp file in
    the SAME directory, then `os.replace()`, so a crash between the two
    leaves `path` exactly as it was, never observed half-written or
    truncated. Reimplemented locally rather than imported: `hooks_install`
    is this codebase's one deliberate harness-specific module (D4), and
    this primitive is shared by two harness-agnostic callers
    (`observe.py`, `role_block.py`), so it must not gain a dependency on
    the harness-specific module.

    Args:
        path: the real destination path (the journal file).
        text: the full new file content.

    Raises:
        OSError: the write or replace failed; `path` itself is unmodified
            either way -- `os.replace` is atomic on both POSIX and Windows
            for a same-volume rename.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        replaced = True
    finally:
        if not replaced:
            tmp_path.unlink(missing_ok=True)


def append_bounded_line(path: Path, line: str, *, max_lines: int, timeout: float = _DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
    """Atomically append one line to `path`, trimming to the last `max_lines`.

    The read, append, trim, and rewrite all happen while holding the
    exclusive lock anchored at `<path>.lock`, so two callers racing to
    append never interleave their read-modify-write cycles -- the failure
    mode this function exists to close. The rewrite itself also never
    truncates `path` in place (preflight item 3): it goes through
    `_atomic_write_text`'s temp-file-then-`os.replace()` pattern, so a
    kill mid-write leaves the file exactly as it was before this call,
    never observed half-written or empty.

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
        _atomic_write_text(path, "\n".join(existing) + "\n")
    finally:
        release_lock(handle)
