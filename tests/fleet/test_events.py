"""Tests for ``scripts.fleet.core.events`` — TC-6, TC-9, TC-10.

Covers NFR-1 (locked append-only log, idempotency dedupe), NFR-7 (torn final
line is skipped, not fatal), and the stale-lock reclaim contract (age + pid
liveness, both required).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts.fleet.core.errors import LockTimeoutError, PreconditionUnmet, ValidationError
from scripts.fleet.core.events import (
    _pid_is_alive,
    _reclaim_if_stale,
    _reclaim_marker_if_orphaned,
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
        os.utime(lock_path, (old_time, old_time))

    def _write_marker(self, marker_path: Path, pid: int, age_seconds: float) -> None:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps({"pid": pid, "ts": "2020-01-01T00:00:00Z"}), encoding="utf-8"
        )
        old_time = time.time() - age_seconds
        os.utime(marker_path, (old_time, old_time))

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

        old = time.time() - 10.0
        os.utime(lock_path, (old, old))
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

    def test_reclaim_does_not_steal_a_lock_that_changed_underneath_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """G5 review finding #1, deeper case found while proving the fix live.

        A racer's stale/dead-pid decision (stat + read) can be based on data
        read *milliseconds* earlier — under real contention, a process can be
        descheduled by the OS between two Python bytecode instructions for
        long enough that several *other* processes fully cycle through the
        lock (reclaim -> create -> release -> create again) in the gap. An
        identity-blind reclaim action would then act on whichever *live,
        legitimately held* lock happens to occupy the path at that later
        moment, not the stale instance actually evaluated — silently
        breaking mutual exclusion for whoever currently holds it.

        Proven live: a real multiprocess stress test reproduced exactly this
        (three worker processes with overlapping "I hold the lock" windows,
        one lost event) before this fix. This test forces the same
        interleaving deterministically by injecting the "concurrent cycle"
        the instant this racer wins the exclusive reclaim marker — the exact
        point after which `_reclaim_if_stale` re-verifies identity before
        ever touching `lock_path`.
        """
        lock_path = tmp_path / ".lock"
        dead_pid = self._dead_pid()
        self._write_lock(lock_path, pid=dead_pid, age_seconds=10.0)

        live_pid = 999999999  # a pid our own process did not observe as dead
        live_content = json.dumps({"pid": live_pid, "ts": "2026-08-14T12:00:05Z"})
        state = {"injected": False}
        marker_path = lock_path.with_name(f"{lock_path.name}.reclaiming")

        real_open = __import__("os").open

        def _inject_after_marker_then_open(path: object, flags: int, *a: object, **kw: object):
            fd = real_open(path, flags, *a, **kw)
            if not state["injected"] and str(path) == str(marker_path):
                state["injected"] = True
                # Simulate: between this racer's stale read and the instant
                # it wins the reclaim marker, a DIFFERENT process fully
                # reclaimed the original stale lock and created its own
                # fresh, live one at the same path. Must be a genuinely NEW
                # file (new inode) — rewriting the existing file's content
                # in place would keep the same inode and fail to reproduce
                # the real race, where the other process's own
                # `os.open(O_CREAT|O_EXCL)` creates a brand new filesystem
                # object at this path.
                os.remove(lock_path)
                live_fd = real_open(str(lock_path), os.O_CREAT | os.O_WRONLY)
                try:
                    os.write(live_fd, live_content.encode("utf-8"))
                finally:
                    os.close(live_fd)
            return fd

        monkeypatch.setattr("os.open", _inject_after_marker_then_open)
        monkeypatch.setattr(
            "scripts.fleet.core.events._pid_is_alive",
            lambda pid: pid == live_pid,
        )

        reclaimed = _reclaim_if_stale(lock_path, stale_age=1.0)

        assert state["injected"], "test setup bug: the injection point was never reached"
        assert reclaimed is False, (
            "reclaim reported success after stealing a lock that had "
            "changed underneath it — it must detect the mismatch and back "
            "off, not keep a win it did not actually earn"
        )
        # The live lock must survive untouched — never renamed away at all,
        # since the identity re-check happens before any action on it.
        assert lock_path.is_file(), "the live lock did not survive"
        assert lock_path.read_text(encoding="utf-8") == live_content
        # The exclusive marker must be cleaned up so a later, legitimate
        # reclaim attempt is never permanently blocked by this one.
        assert not marker_path.exists(), "the reclaim marker leaked"

    def test_an_already_held_reclaim_marker_blocks_a_second_racer(self, tmp_path: Path) -> None:
        """The mutual-exclusion gate is the marker's `O_CREAT|O_EXCL` create,
        not the later rename — `os.replace` to distinct destination names is
        NOT reliably exclusive across processes on this platform (verified
        directly, independent of this module's own logic). If another racer
        already holds `<lock>.reclaiming`, this call must back off without
        ever touching `lock_path`.
        """
        lock_path = tmp_path / ".lock"
        dead_pid = self._dead_pid()
        self._write_lock(lock_path, pid=dead_pid, age_seconds=10.0)
        marker_path = lock_path.with_name(f"{lock_path.name}.reclaiming")
        marker_path.write_text("held by another racer", encoding="utf-8")

        reclaimed = _reclaim_if_stale(lock_path, stale_age=1.0)

        assert reclaimed is False
        # lock_path must be completely untouched — still exactly the
        # original stale content, not renamed, not modified.
        assert lock_path.is_file()
        content = json.loads(lock_path.read_text(encoding="utf-8"))
        assert content["pid"] == dead_pid
        # The pre-existing marker (simulating another racer's in-flight
        # reclaim) must not be disturbed by this losing attempt either.
        assert marker_path.read_text(encoding="utf-8") == "held by another racer"

    def test_lock_gone_by_the_time_the_marker_is_won_is_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If `lock_path` vanishes in the gap between the initial staleness
        decision and winning the exclusive marker (e.g. its legitimate
        holder released it right then), the re-check must return `False`
        cleanly — not raise, and not treat "gone" as a win here (unlike the
        very first check at the top of this function, which does treat
        "never existed" as safe-to-retry; this is a *different* branch).
        """
        lock_path = tmp_path / ".lock"
        self._write_lock(lock_path, pid=self._dead_pid(), age_seconds=10.0)
        marker_path = lock_path.with_name(f"{lock_path.name}.reclaiming")

        real_open = __import__("os").open

        def _remove_lock_after_marker_win(path: object, flags: int, *a: object, **kw: object):
            fd = real_open(path, flags, *a, **kw)
            if str(path) == str(marker_path):
                os.remove(lock_path)
            return fd

        monkeypatch.setattr("os.open", _remove_lock_after_marker_win)

        reclaimed = _reclaim_if_stale(lock_path, stale_age=1.0)

        assert reclaimed is False
        assert not marker_path.exists(), "the reclaim marker leaked"

    def test_orphaned_marker_is_recovered_and_acquire_lock_succeeds(
        self, tmp_path: Path
    ) -> None:
        """GPT-5 review finding #4 (HIGH, regression from the finding-#1 fix).

        If a process crashes AFTER creating `<lock>.reclaiming` but BEFORE
        its `finally` removes it, the marker orphans. Without its own
        recovery, every future writer gets `FileExistsError` on the marker
        create, backs off, and every acquisition times out forever — a
        crash-during-reclaim would permanently wedge all writers. Seed
        exactly that: a stale main lock (old mtime, dead pid) AND an
        orphaned marker (old mtime, dead pid) sitting next to it. A fresh
        `acquire_lock` call must recover — well within a short timeout, not
        by luck — never raise `LockTimeoutError`.
        """
        lock_path = tmp_path / ".lock"
        marker_path = lock_path.with_name(f"{lock_path.name}.reclaiming")
        self._write_lock(lock_path, pid=self._dead_pid(), age_seconds=30.0)
        self._write_marker(marker_path, pid=self._dead_pid(), age_seconds=30.0)

        # A short timeout — this must succeed via recovery, not by waiting
        # out a long default timeout.
        acquire_lock(lock_path, timeout=2.0, retry_interval=0.01, stale_age=1.0)
        release_lock(lock_path)
        assert not marker_path.exists(), "the recovered marker was not cleaned up"

    def test_retry_create_losing_after_orphan_recovery_backs_off_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After successfully recovering an orphaned marker, the bounded
        retry create can still lose (a different racer wins the recreated
        marker first) — this must back off cleanly (`False`), not raise or
        loop, matching the "bounded, no nested retry" contract.
        """
        lock_path = tmp_path / ".lock"
        marker_path = lock_path.with_name(f"{lock_path.name}.reclaiming")
        self._write_lock(lock_path, pid=self._dead_pid(), age_seconds=30.0)
        self._write_marker(marker_path, pid=self._dead_pid(), age_seconds=30.0)

        import scripts.fleet.core.events as events_mod

        real_reclaim_marker = events_mod._reclaim_marker_if_orphaned

        def _recover_then_let_someone_else_win(marker_path_arg, marker_stale_age):
            recovered = real_reclaim_marker(marker_path_arg, marker_stale_age)
            if recovered:
                # Simulate a different racer recreating the marker in the
                # instant between our successful orphan-cleanup and our own
                # retry create.
                marker_path_arg.write_text(
                    json.dumps({"pid": os.getpid(), "ts": "2026-08-14T12:00:00Z"}),
                    encoding="utf-8",
                )
            return recovered

        monkeypatch.setattr(
            events_mod, "_reclaim_marker_if_orphaned", _recover_then_let_someone_else_win
        )

        reclaimed = _reclaim_if_stale(lock_path, stale_age=1.0)

        assert reclaimed is False
        # The "someone else's" marker must survive untouched — this racer
        # backed off without disturbing it.
        assert marker_path.is_file()

    def test_fresh_marker_is_never_removed_by_a_racer(self, tmp_path: Path) -> None:
        """A live reclaimer's marker (recent mtime, live pid) must survive a
        contending racer untouched — the racer must back off (and,
        eventually, time out — it must never proceed as if it had reclaimed
        something it did not) rather than steal or clear it.
        """
        lock_path = tmp_path / ".lock"
        marker_path = lock_path.with_name(f"{lock_path.name}.reclaiming")
        self._write_lock(lock_path, pid=self._dead_pid(), age_seconds=30.0)
        self._write_marker(marker_path, pid=os.getpid(), age_seconds=0.0)
        original_marker_bytes = marker_path.read_bytes()

        with pytest.raises(LockTimeoutError):
            acquire_lock(lock_path, timeout=0.3, retry_interval=0.01, stale_age=1.0)

        assert marker_path.is_file(), "a fresh, live marker must never be removed"
        assert marker_path.read_bytes() == original_marker_bytes


class TestReclaimMarkerIfOrphaned:
    """Direct unit coverage of ``_reclaim_marker_if_orphaned`` — the recovery
    helper GPT-5 review finding #4 added. Safety-critical (a bug here either
    permanently wedges every writer, or lets an active reclaimer's marker be
    stolen), so exercised branch-by-branch, not just through the higher-level
    barrier/`acquire_lock` tests above.
    """

    def _write_marker(self, marker_path: Path, pid: int, age_seconds: float) -> None:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps({"pid": pid, "ts": "2020-01-01T00:00:00Z"}), encoding="utf-8"
        )
        old_time = time.time() - age_seconds
        os.utime(marker_path, (old_time, old_time))

    def _dead_pid(self) -> int:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=10)
        return proc.pid

    def test_marker_that_never_existed_is_treated_as_already_gone(self, tmp_path: Path) -> None:
        marker_path = tmp_path / "never-existed" / ".lock.reclaiming"
        assert _reclaim_marker_if_orphaned(marker_path, marker_stale_age=1.0) is True

    def test_old_marker_with_live_pid_is_not_orphaned(self, tmp_path: Path) -> None:
        # Age alone is insufficient — mirrors the main lock's own
        # "age AND pid liveness, both required" contract.
        marker_path = tmp_path / ".lock.reclaiming"
        self._write_marker(marker_path, pid=os.getpid(), age_seconds=30.0)
        assert _reclaim_marker_if_orphaned(marker_path, marker_stale_age=1.0) is False
        assert marker_path.is_file()

    def test_old_marker_with_dead_pid_is_orphaned_and_removed(self, tmp_path: Path) -> None:
        marker_path = tmp_path / ".lock.reclaiming"
        self._write_marker(marker_path, pid=self._dead_pid(), age_seconds=30.0)
        assert _reclaim_marker_if_orphaned(marker_path, marker_stale_age=1.0) is True
        assert not marker_path.exists()

    def test_malformed_marker_content_is_treated_as_dead_pid(self, tmp_path: Path) -> None:
        marker_path = tmp_path / ".lock.reclaiming"
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("not-json-at-all", encoding="utf-8")
        old_time = time.time() - 30.0
        os.utime(marker_path, (old_time, old_time))
        assert _reclaim_marker_if_orphaned(marker_path, marker_stale_age=1.0) is True

    def test_marker_removed_between_initial_and_fresh_stat_is_a_no_op_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker_path = tmp_path / ".lock.reclaiming"
        self._write_marker(marker_path, pid=self._dead_pid(), age_seconds=30.0)

        real_stat = Path.stat
        call_count = {"n": 0}

        def _stat_then_remove_on_second_call(self: Path, *a: object, **kw: object):
            call_count["n"] += 1
            if call_count["n"] == 2 and str(self) == str(marker_path):
                # Simulate another process's cleanup landing exactly between
                # our initial staleness read and our fresh pre-removal stat.
                os.remove(marker_path)
            return real_stat(self, *a, **kw)

        monkeypatch.setattr(Path, "stat", _stat_then_remove_on_second_call)

        assert _reclaim_marker_if_orphaned(marker_path, marker_stale_age=1.0) is True

    def test_marker_touched_between_stats_is_not_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the marker's mtime changed between the two internal stats (a
        live reclaimer (re)touched it), the mismatch must be detected and
        the marker left alone — not removed out from under it.
        """
        marker_path = tmp_path / ".lock.reclaiming"
        self._write_marker(marker_path, pid=self._dead_pid(), age_seconds=30.0)

        real_stat = Path.stat
        call_count = {"n": 0}

        def _touch_before_second_stat(self: Path, *a: object, **kw: object):
            call_count["n"] += 1
            if call_count["n"] == 2 and str(self) == str(marker_path):
                fresh_time = time.time()
                os.utime(marker_path, (fresh_time, fresh_time))
            return real_stat(self, *a, **kw)

        monkeypatch.setattr(Path, "stat", _touch_before_second_stat)

        assert _reclaim_marker_if_orphaned(marker_path, marker_stale_age=1.0) is False
        assert marker_path.is_file(), "a marker touched mid-check must survive"

    def test_remove_race_during_final_removal_is_tolerated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker_path = tmp_path / ".lock.reclaiming"
        self._write_marker(marker_path, pid=self._dead_pid(), age_seconds=30.0)

        real_remove = os.remove

        def _remove_then_pretend_already_gone(path: object) -> None:
            real_remove(path)
            raise FileNotFoundError("simulated race: another process already removed it")

        monkeypatch.setattr("os.remove", _remove_then_pretend_already_gone)

        assert _reclaim_marker_if_orphaned(marker_path, marker_stale_age=1.0) is True


class TestPidIsAlive:
    def test_pid_zero_or_negative_is_never_alive(self) -> None:
        assert _pid_is_alive(0) is False
        assert _pid_is_alive(-1) is False

    def test_own_pid_is_alive(self) -> None:
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
