"""Tests for ``scripts.fleet.core.events`` — TC-6, TC-9, TC-10.

Covers NFR-1 (locked append-only log, idempotency dedupe) and NFR-7 (torn
final line is skipped, not fatal).

The lock's own crash-release and load-exclusivity properties (the G6
OS-native-advisory-lock redesign's core claims) are covered separately in
``tests/fleet/test_lock_os_native.py`` — they need real, killable OS
processes, not just this file's in-process/thread-level contention checks.
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from pathlib import Path

import pytest

from scripts.fleet.core import events as events_mod
from scripts.fleet.core.errors import LockTimeoutError, PreconditionUnmet, ValidationError
from scripts.fleet.core.events import (
    acquire_lock,
    append,
    lock_path_for,
    read_all,
    release_lock,
)
from scripts.fleet.core.schema import Event


def _event(idempotency_key: str, event_id: str = "11111111-1111-1111-1111-111111111111") -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "idempotency_key": idempotency_key,
        "ts": "2026-08-14T12:00:00Z",
        "type": "session_registered",
        "project_id": "proj-abc",
        "node_id": "portable/ws/proj/local-1",
        "writer_role": "Developer",
        "payload": {},
    }


class TestAppendAndReadAll:
    """TC-6(a)."""

    def test_appended_events_are_read_back_in_order(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        for i in range(3):
            append(log_path, _event(f"key-{i}", event_id=f"eid-{i}"))
        events = read_all(log_path)
        assert [e.idempotency_key for e in events] == ["key-0", "key-1", "key-2"]

    def test_read_all_on_missing_log_returns_empty(self, tmp_path: Path) -> None:
        assert read_all(tmp_path / "no-such-events.jsonl") == []


class TestIdempotencyDedupe:
    """TC-6(b)."""

    def test_duplicate_idempotency_key_is_a_no_op(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        first = append(log_path, _event("dup-key", event_id="eid-first"))
        second = append(log_path, _event("dup-key", event_id="eid-second"))
        assert first is not None
        assert second is None
        events = read_all(log_path)
        assert len(events) == 1
        assert events[0].event_id == "eid-first"


class TestAppendValidatesUnconditionally:
    """GPT-5 review finding #2 (MED): validation must run for EVERY append,
    even when the caller already hands in a constructed `Event` — not just
    for raw dicts.

    `Event` is frozen, but its `payload` dict is mutable, and
    `Event(...)` construction itself does not validate — the schema checks
    only run inside `validate_event`. Before this fix,
    `ev = event if isinstance(event, Event) else validate_event(event)`
    let an already-constructed `Event` skip validation entirely, so
    `append(Event(..., payload={"lifecycle": ""}))` wrote an invalid line
    straight to the log; `read_all` would later silently skip it on replay
    (NFR-7's whole point is that nothing bad is ever persisted in the first
    place, not that it gets quietly dropped on the way back out).
    """

    def _invalid_event(self) -> Event:
        return Event(
            schema_version=1,
            event_id="eid-invalid",
            idempotency_key="key-invalid",
            ts="2026-08-14T12:00:00Z",
            type="session_registered",
            project_id="proj-abc",
            node_id="portable/ws/proj/local-1",
            writer_role="Developer",
            payload={"lifecycle": ""},  # invalid: blank status value
        )

    def test_append_of_a_preconstructed_invalid_event_is_rejected(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        with pytest.raises(ValidationError):
            append(log_path, self._invalid_event())
        assert read_all(log_path) == [], "an invalid Event instance was persisted"

    def test_append_of_a_preconstructed_valid_event_still_works(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        good = Event(
            schema_version=1,
            event_id="eid-valid",
            idempotency_key="key-valid",
            ts="2026-08-14T12:00:00Z",
            type="session_registered",
            project_id="proj-abc",
            node_id="portable/ws/proj/local-1",
            writer_role="Developer",
            payload={"lifecycle": "active"},
        )
        result = append(log_path, good)
        assert result is not None
        events = read_all(log_path)
        assert len(events) == 1
        assert events[0].payload == {"lifecycle": "active"}


class TestLockContention:
    """TC-6(c)."""

    def test_second_acquisition_blocks_then_succeeds_after_release(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        handle = acquire_lock(lock_path, timeout=5.0, retry_interval=0.01)
        result = {}

        def _release_after_delay() -> None:
            time.sleep(0.2)
            release_lock(handle)

        t = threading.Thread(target=_release_after_delay)
        t.start()
        start = time.monotonic()
        second_handle = acquire_lock(lock_path, timeout=5.0, retry_interval=0.01)
        elapsed = time.monotonic() - start
        t.join()
        result["elapsed"] = elapsed
        assert result["elapsed"] >= 0.15  # genuinely waited for the release
        release_lock(second_handle)

    def test_second_acquisition_times_out_rather_than_proceeding_unlocked(
        self, tmp_path: Path
    ) -> None:
        lock_path = tmp_path / ".lock"
        handle = acquire_lock(lock_path, timeout=5.0, retry_interval=0.01)
        try:
            with pytest.raises(LockTimeoutError):
                acquire_lock(lock_path, timeout=0.15, retry_interval=0.01)
        finally:
            release_lock(handle)

    def test_lock_path_for_is_a_sibling_dotfile(self, tmp_path: Path) -> None:
        log_path = tmp_path / "fleet" / "events.jsonl"
        assert lock_path_for(log_path) == tmp_path / "fleet" / ".lock"

    def test_lock_anchor_file_survives_release(self, tmp_path: Path) -> None:
        """The `.lock` anchor is never deleted on release (G6 redesign) —
        deleting-then-recreating a lock path is exactly the reuse hazard the
        OS-native-lock design escapes. A held-then-released lock leaves the
        anchor file on disk, and a fresh acquire on the same path succeeds
        immediately (no contention, since nothing else holds the OS lock).
        """
        lock_path = tmp_path / ".lock"
        handle = acquire_lock(lock_path, timeout=5.0, retry_interval=0.01)
        release_lock(handle)
        assert lock_path.exists(), "the lock anchor file must not be deleted on release"

        # A fresh acquire on the same still-existing anchor must succeed
        # promptly — proving release genuinely cleared the OS-level lock,
        # not just closed a handle while secretly still holding it.
        start = time.monotonic()
        handle2 = acquire_lock(lock_path, timeout=2.0, retry_interval=0.01)
        elapsed = time.monotonic() - start
        release_lock(handle2)
        assert elapsed < 1.0, "re-acquiring the same anchor after release should be immediate"

    def test_double_release_is_a_safe_no_op(self, tmp_path: Path) -> None:
        """`LockHandle` is single-use: a second `release_lock` call on the
        same handle must not `os.close()` an fd number the OS may have
        already reused for something unrelated. The first release flips
        `handle.fd` to `-1`; the second call must see that and do nothing.
        """
        lock_path = tmp_path / ".lock"
        handle = acquire_lock(lock_path, timeout=5.0, retry_interval=0.01)
        release_lock(handle)
        assert handle.fd == -1, "release_lock must mark the handle as released"

        release_lock(handle)  # must not raise (e.g. a double-close OSError)
        assert handle.fd == -1, "a second release must leave the handle exactly as-is"

        # And the anchor must still be genuinely unlocked — a fresh acquire
        # succeeds promptly, proving the double-release didn't somehow
        # re-lock or corrupt anything.
        start = time.monotonic()
        handle2 = acquire_lock(lock_path, timeout=2.0, retry_interval=0.01)
        elapsed = time.monotonic() - start
        release_lock(handle2)
        assert elapsed < 1.0


class TestAcquireLockFdSafety:
    """Cross-model (GPT-5/codex) review Finding 2: `acquire_lock` must not
    leak the anchor fd — nor strand a just-acquired OS lock — if an
    exception lands anywhere between opening the fd and returning the
    `LockHandle` (a non-contention `OSError`, or an async
    `KeyboardInterrupt`/`MemoryError` mid-retry or mid-diagnostic-write).
    The fix wraps the whole acquire path in one `try/finally` that closes
    the fd unless ownership transferred to a returned handle.
    """

    @staticmethod
    def _fd_is_closed(fd: int) -> bool:
        """True iff `fd` is not a currently-open descriptor in this process."""
        try:
            os.fstat(fd)
            return False
        except OSError:
            return True

    def test_error_from_lock_fd_closes_the_fd_on_every_platform(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whatever exit path an `OSError` from `_lock_fd` takes — immediate
        propagation on POSIX, or (since Windows `msvcrt` exposes no errno to
        narrow on) a retry that ends in `LockTimeoutError` — the anchor fd
        must not leak. Both paths run the same `finally`.
        """
        captured: list[int] = []

        def _boom(fd: int) -> None:
            captured.append(fd)
            raise OSError(errno.ENOSPC, "no space left on device")

        monkeypatch.setattr(events_mod, "_lock_fd", _boom)

        # A short timeout keeps the Windows retry-to-timeout branch quick.
        with pytest.raises((OSError, LockTimeoutError)):
            acquire_lock(tmp_path / ".lock", timeout=0.1, retry_interval=0.01)
        assert captured, "_lock_fd should have been called with the opened fd"
        assert self._fd_is_closed(captured[0]), "acquire_lock leaked the anchor fd"

    @pytest.mark.skipif(
        os.name == "nt",
        reason="errno narrowing is POSIX-only; Windows msvcrt exposes no errno to narrow on",
    )
    def test_non_contention_errno_propagates_immediately_on_posix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POSIX Finding-4 behavior: a genuine (non-EACCES/EAGAIN/EWOULDBLOCK)
        errno propagates immediately instead of degrading to a misleading
        `LockTimeoutError` after burning the whole retry budget.
        """
        def _boom(fd: int) -> None:
            raise OSError(errno.ENOSPC, "no space left on device")

        monkeypatch.setattr(events_mod, "_lock_fd", _boom)

        with pytest.raises(OSError) as excinfo:
            acquire_lock(tmp_path / ".lock", timeout=5.0, retry_interval=0.01)
        assert excinfo.value.errno == errno.ENOSPC
        assert not isinstance(excinfo.value, LockTimeoutError)

    def test_async_exception_during_diagnostics_releases_fd_and_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If an async exception (here `KeyboardInterrupt`) lands during the
        post-lock diagnostic write — after `_lock_fd` already acquired the OS
        lock but before the handle is returned — the fd must be closed
        (releasing the OS lock, since no handle exists to release it later),
        proven by both the fd being closed and the anchor being immediately
        re-acquirable.
        """
        lock_path = tmp_path / ".lock"
        captured: list[int] = []

        def _boom(fd: int, length: int) -> None:
            captured.append(fd)
            raise KeyboardInterrupt

        monkeypatch.setattr(os, "ftruncate", _boom)

        with pytest.raises(KeyboardInterrupt):
            acquire_lock(lock_path, timeout=5.0, retry_interval=0.01)
        assert captured, "the diagnostic ftruncate should have run under the held lock"
        assert self._fd_is_closed(captured[0]), "the held-lock fd was leaked on interruption"

        # The OS lock must be gone: a fresh acquire on the same anchor
        # succeeds promptly (the finally released it, not just closed a
        # handle while secretly still holding).
        monkeypatch.undo()  # restore the real os.ftruncate for the real acquire
        start = time.monotonic()
        handle = acquire_lock(lock_path, timeout=2.0, retry_interval=0.01)
        elapsed = time.monotonic() - start
        release_lock(handle)
        assert elapsed < 1.0, "the interrupted acquire stranded the OS lock"


class TestTornFinalLine:
    """TC-9."""

    def test_torn_final_line_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(_event(f"key-{i}", event_id=f"eid-{i}")) for i in range(4)]
        torn = '{"schema_version": 1, "event_id": "eid-torn", "idempoten'
        log_path.write_text("\n".join(lines) + "\n" + torn, encoding="utf-8")

        events = read_all(log_path)
        assert len(events) == 4
        assert [e.idempotency_key for e in events] == ["key-0", "key-1", "key-2", "key-3"]

        # The log stays writable/replayable after a torn tail.
        append(log_path, _event("key-4", event_id="eid-4"))
        events_after = read_all(log_path)
        assert len(events_after) == 5
        assert events_after[-1].idempotency_key == "key-4"


class TestReadAllSkipsBlankAndSchemaInvalidLines:
    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        valid = json.dumps(_event("key-1"))
        log_path.write_text(f"{valid}\n\n\n", encoding="utf-8")
        events = read_all(log_path)
        assert len(events) == 1

    def test_schema_invalid_middle_line_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        good_first = json.dumps(_event("key-1", event_id="eid-1"))
        # Syntactically valid JSON, but missing required fields.
        bad_middle = json.dumps({"schema_version": 1, "type": "session_registered"})
        good_last = json.dumps(_event("key-2", event_id="eid-2"))
        log_path.write_text(f"{good_first}\n{bad_middle}\n{good_last}\n", encoding="utf-8")

        events = read_all(log_path)
        assert [e.idempotency_key for e in events] == ["key-1", "key-2"]


class TestAppendPrecondition:
    """Review FIX #2: an optional `precondition` evaluated ATOMICALLY with
    the write, under the same lock acquisition that reads the fresh event
    list — closing the TOCTOU window an outside-the-lock fragment/state read
    cannot close (e.g. `handoff.self_register`'s cancel-vs-launch race).
    """

    def test_precondition_true_allows_the_write(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        result = append(log_path, _event("key-ok"), precondition=lambda existing: True)
        assert result is not None
        assert len(read_all(log_path)) == 1

    def test_precondition_false_raises_and_writes_nothing(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with pytest.raises(PreconditionUnmet):
            append(log_path, _event("key-blocked"), precondition=lambda existing: False)
        assert read_all(log_path) == []

    def test_precondition_receives_the_fresh_existing_events_under_the_lock(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _event("key-1", event_id="eid-1"))
        append(log_path, _event("key-2", event_id="eid-2"))

        seen: list[list[str]] = []

        def _record(existing: list[Event]) -> bool:
            seen.append([e.idempotency_key for e in existing])
            return True

        append(log_path, _event("key-3", event_id="eid-3"), precondition=_record)
        assert seen == [["key-1", "key-2"]]

    def test_precondition_is_not_evaluated_for_an_idempotent_dedupe(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _event("dup-key", event_id="eid-first"))

        calls = {"n": 0}

        def _never_should_run(existing: list[Event]) -> bool:
            calls["n"] += 1
            return True

        result = append(
            log_path, _event("dup-key", event_id="eid-second"), precondition=_never_should_run
        )
        assert result is None  # existing dedupe behavior, unchanged
        assert calls["n"] == 0
        assert len(read_all(log_path)) == 1

    def test_no_precondition_given_behaves_exactly_as_before(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        result = append(log_path, _event("key-no-precondition"))
        assert result is not None
        assert len(read_all(log_path)) == 1
