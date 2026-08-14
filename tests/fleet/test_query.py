"""Tests for ``scripts.fleet.core.query.stale_handoffs`` — the core half of TC-21.

``handoff.stale_report()`` (tested end-to-end in ``test_handoff.py``) is a
thin profile-resolving wrapper over this function. These tests exercise the
manifest-only computation directly: given an already-resolved ``now`` and
``expiry_seconds``, no adapter or config dependency is involved at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.fleet.core.events import append
from scripts.fleet.core.query import stale_handoffs
from scripts.fleet.core.store import iter_fragments, write_fragment
from scripts.fleet.core.schema import Fragment


def _emitted_event(node_id: str, handoff_id: str, ts: str) -> dict:
    return {
        "schema_version": 1,
        "event_id": f"eid-{handoff_id}",
        "idempotency_key": f"emit:{handoff_id}",
        "ts": ts,
        "type": "handoff_emitted",
        "project_id": "proj-abc",
        "node_id": node_id,
        "writer_role": "Project Manager",
        "payload": {
            "lifecycle": "awaiting-launch",
            "handoff_id": handoff_id,
            "cwd": "/repo/a",
            "branch": "feature/x",
        },
    }


def _fragment(node_id: str, lifecycle: str) -> Fragment:
    return Fragment(
        node_id=node_id,
        project_id="proj-abc",
        lifecycle=lifecycle,
        block_state="unblocked",
        review_state="none",
        adoption_state="normal",
        done_level="D0-code",
    )


class TestStaleHandoffsFromManifestAlone:
    """TC-21(a): stale report computed purely from fragments + log."""

    def test_only_the_past_expiry_row_is_reported_stale(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"

        now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        fresh_ts = (now - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        stale_ts = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        append(log_path, _emitted_event("node-fresh", "hid-fresh", fresh_ts))
        append(log_path, _emitted_event("node-stale", "hid-stale", stale_ts))
        write_fragment(_fragment("node-fresh", "awaiting-launch"), sessions_dir)
        write_fragment(_fragment("node-stale", "awaiting-launch"), sessions_dir)

        rows = stale_handoffs(log_path, sessions_dir, now=now, expiry_seconds=3600)

        assert [r["node_id"] for r in rows] == ["node-stale"]
        assert rows[0]["handoff_id"] == "hid-stale"
        assert rows[0]["age_seconds"] > 3600

    def test_a_row_exactly_at_expiry_is_not_yet_stale(self, tmp_path: Path) -> None:
        # Review FIX #4: documents the `age_seconds > expiry_seconds`
        # boundary is strict-greater-than — a row exactly at the threshold
        # has not yet exceeded it.
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        exact_ts = (now - timedelta(seconds=3600)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        append(log_path, _emitted_event("node-exact", "hid-exact", exact_ts))
        write_fragment(_fragment("node-exact", "awaiting-launch"), sessions_dir)

        rows = stale_handoffs(log_path, sessions_dir, now=now, expiry_seconds=3600)
        assert rows == []

        # One second past the boundary, it IS stale — proving the assertion
        # above isn't vacuously true from a timestamp-parsing bug.
        rows_past = stale_handoffs(log_path, sessions_dir, now=now, expiry_seconds=3599)
        assert [r["node_id"] for r in rows_past] == ["node-exact"]

    def test_no_open_rows_yields_empty_report(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        assert stale_handoffs(
            log_path, sessions_dir, now=datetime.now(timezone.utc), expiry_seconds=3600
        ) == []

    def test_a_non_awaiting_launch_fragment_is_never_reported(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        append(log_path, _emitted_event("node-cancelled", "hid-cancelled", old_ts))
        write_fragment(_fragment("node-cancelled", "cancelled"), sessions_dir)

        rows = stale_handoffs(log_path, sessions_dir, now=now, expiry_seconds=3600)
        assert rows == []

    def test_naive_now_is_treated_as_utc(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        now_aware = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        now_naive = datetime(2026, 8, 14, 12, 0, 0)
        old_ts = (now_aware - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        append(log_path, _emitted_event("node-stale", "hid-stale", old_ts))
        write_fragment(_fragment("node-stale", "awaiting-launch"), sessions_dir)

        rows = stale_handoffs(log_path, sessions_dir, now=now_naive, expiry_seconds=3600)
        assert [r["node_id"] for r in rows] == ["node-stale"]

    def test_read_only_never_writes_the_manifest(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        append(log_path, _emitted_event("node-stale", "hid-stale", old_ts))
        write_fragment(_fragment("node-stale", "awaiting-launch"), sessions_dir)

        log_before = log_path.read_bytes()
        fragments_before = {f.node_id: f for f in iter_fragments(sessions_dir)}

        stale_handoffs(log_path, sessions_dir, now=now, expiry_seconds=3600)

        assert log_path.read_bytes() == log_before
        assert {f.node_id: f for f in iter_fragments(sessions_dir)} == fragments_before
