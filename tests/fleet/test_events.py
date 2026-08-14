"""Tests for ``scripts.fleet.core.events`` — TC-6, TC-9, TC-10.

Covers NFR-1 (locked append-only log, idempotency dedupe), NFR-7 (torn final
line is skipped, not fatal), and the stale-lock reclaim contract (age + pid
liveness, both required).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts.fleet.core.errors import LockTimeoutError
from scripts.fleet.core.events import (
    _pid_is_alive,
    _reclaim_if_stale,
    acquire_lock,
    append,
    lock_path_for,
    read_all,
    release_lock,
)


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


class TestLockContention:
    """TC-6(c)."""

    def test_second_acquisition_blocks_then_succeeds_after_release(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        acquire_lock(lock_path, timeout=5.0, retry_interval=0.01, stale_age=999.0)
        result = {}

        def _release_after_delay() -> None:
            time.sleep(0.2)
            release_lock(lock_path)

        t = threading.Thread(target=_release_after_delay)
        t.start()
        start = time.monotonic()
        acquire_lock(lock_path, timeout=5.0, retry_interval=0.01, stale_age=999.0)
        elapsed = time.monotonic() - start
        t.join()
        result["elapsed"] = elapsed
        assert result["elapsed"] >= 0.15  # genuinely waited for the release
        release_lock(lock_path)

    def test_second_acquisition_times_out_rather_than_proceeding_unlocked(
        self, tmp_path: Path
    ) -> None:
        lock_path = tmp_path / ".lock"
        acquire_lock(lock_path, timeout=5.0, retry_interval=0.01, stale_age=999.0)
        try:
            with pytest.raises(LockTimeoutError):
                acquire_lock(lock_path, timeout=0.15, retry_interval=0.01, stale_age=999.0)
        finally:
            release_lock(lock_path)

    def test_lock_path_for_is_a_sibling_dotfile(self, tmp_path: Path) -> None:
        log_path = tmp_path / "fleet" / "events.jsonl"
        assert lock_path_for(log_path) == tmp_path / "fleet" / ".lock"


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


class TestStaleLockReclaim:
    """TC-10."""

    def _write_lock(self, lock_path: Path, pid: int, age_seconds: float) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({"pid": pid, "ts": "2020-01-01T00:00:00Z"}), encoding="utf-8")
        old_time = time.time() - age_seconds
        import os

        os.utime(lock_path, (old_time, old_time))

    def _dead_pid(self) -> int:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=10)
        return proc.pid

    def test_dead_pid_and_old_mtime_is_reclaimed(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        self._write_lock(lock_path, pid=self._dead_pid(), age_seconds=10.0)
        # Should succeed promptly — the stale lock is reclaimed, not waited out.
        acquire_lock(lock_path, timeout=2.0, retry_interval=0.01, stale_age=1.0)
        release_lock(lock_path)

    def test_live_pid_and_old_mtime_is_not_reclaimed(self, tmp_path: Path) -> None:
        import os

        lock_path = tmp_path / ".lock"
        self._write_lock(lock_path, pid=os.getpid(), age_seconds=10.0)
        with pytest.raises(LockTimeoutError):
            acquire_lock(lock_path, timeout=0.15, retry_interval=0.01, stale_age=1.0)

    def test_fresh_mtime_and_dead_pid_is_not_reclaimed(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        self._write_lock(lock_path, pid=self._dead_pid(), age_seconds=0.0)
        with pytest.raises(LockTimeoutError):
            acquire_lock(lock_path, timeout=0.15, retry_interval=0.01, stale_age=999.0)

    def test_reclaim_check_on_an_already_gone_lock_is_a_no_op_retry(self, tmp_path: Path) -> None:
        # A race where the lock disappeared between the FileExistsError and
        # the staleness check — treated as "safe to retry immediately".
        lock_path = tmp_path / "never-existed" / ".lock"
        assert _reclaim_if_stale(lock_path, stale_age=1.0) is True

    def test_reclaim_tolerates_malformed_lock_content(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("not-json-at-all", encoding="utf-8")
        import os as _os

        old = time.time() - 10.0
        _os.utime(lock_path, (old, old))
        # Unparseable content -> treated as pid=-1 -> not alive -> reclaimed.
        assert _reclaim_if_stale(lock_path, stale_age=1.0) is True

    def test_reclaim_tolerates_lock_removed_by_a_racing_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lock_path = tmp_path / ".lock"
        self._write_lock(lock_path, pid=self._dead_pid(), age_seconds=10.0)

        real_remove = __import__("os").remove

        def _remove_then_pretend_already_gone(path: object) -> None:
            real_remove(path)
            raise FileNotFoundError("simulated race: another process already removed it")

        monkeypatch.setattr("os.remove", _remove_then_pretend_already_gone)
        assert _reclaim_if_stale(lock_path, stale_age=1.0) is True


class TestPidIsAlive:
    def test_pid_zero_or_negative_is_never_alive(self) -> None:
        assert _pid_is_alive(0) is False
        assert _pid_is_alive(-1) is False

    def test_own_pid_is_alive(self) -> None:
        import os

        assert _pid_is_alive(os.getpid()) is True


class TestReleaseLockIsIdempotent:
    def test_release_on_a_missing_lock_is_a_no_op(self, tmp_path: Path) -> None:
        release_lock(tmp_path / "never-acquired.lock")  # must not raise


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
