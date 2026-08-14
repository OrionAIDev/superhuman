"""Tests for ``scripts.fleet.core.projection`` — TC-8 plus the incremental path.

Covers "Partial append / crash" error handling: fragments are a rebuildable
cache, never independent truth, so a corrupt fragment is never fatal.
"""

from __future__ import annotations

from pathlib import Path

from scripts.fleet.core.events import append
from scripts.fleet.core.projection import project_event, rebuild
from scripts.fleet.core.schema import validate_event
from scripts.fleet.core.store import fragment_path, read_fragment


def _registered_event(node_id: str, project_id: str = "proj-abc") -> dict:
    return {
        "schema_version": 1,
        "event_id": "eid-register",
        "idempotency_key": f"register:{node_id}",
        "ts": "2026-08-14T12:00:00Z",
        "type": "session_registered",
        "project_id": project_id,
        "node_id": node_id,
        "writer_role": "Developer",
        "payload": {"lifecycle": "active"},
    }


def _lifecycle_changed_event(node_id: str, new_lifecycle: str, project_id: str = "proj-abc") -> dict:
    return {
        "schema_version": 1,
        "event_id": f"eid-lifecycle-{new_lifecycle}",
        "idempotency_key": f"lifecycle:{node_id}:{new_lifecycle}",
        "ts": "2026-08-14T12:05:00Z",
        "type": "lifecycle_changed",
        "project_id": project_id,
        "node_id": node_id,
        "writer_role": "Developer",
        "payload": {"lifecycle": new_lifecycle},
    }


class TestRebuildFromCorruptFragment:
    """TC-8."""

    def test_rebuild_overwrites_corrupt_fragment_from_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "fleet" / "events.jsonl"
        sessions_dir = tmp_path / "fleet" / "sessions"
        node_id = "portable/ws/proj/local-1"

        append(log_path, _registered_event(node_id))
        append(log_path, _lifecycle_changed_event(node_id, "done"))

        # Deliberately corrupt the fragment file (truncated JSON).
        sessions_dir.mkdir(parents=True, exist_ok=True)
        corrupt_path = fragment_path(node_id, sessions_dir)
        corrupt_path.write_text('{"node_id": "portable/ws/proj/local-1", "lifecy', encoding="utf-8")

        # rebuild() must not raise despite the corrupt fragment on disk.
        rebuild(log_path, sessions_dir)

        fragment = read_fragment(node_id, sessions_dir)
        assert fragment is not None
        assert fragment.lifecycle == "done"
        assert fragment.project_id == "proj-abc"

    def test_rebuild_replays_events_in_append_order_last_write_wins(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/local-2"

        append(log_path, _registered_event(node_id))
        append(log_path, _lifecycle_changed_event(node_id, "review"))
        append(log_path, _lifecycle_changed_event(node_id, "done"))

        fragments = rebuild(log_path, sessions_dir)
        assert fragments[node_id].lifecycle == "done"

    def test_rebuild_filters_by_project_id(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        append(log_path, _registered_event("portable/ws/proj/a", project_id="proj-1"))
        append(log_path, _registered_event("portable/ws/proj/b", project_id="proj-2"))

        fragments = rebuild(log_path, sessions_dir, project_id="proj-1")
        assert set(fragments) == {"portable/ws/proj/a"}


class TestIncrementalProject:
    def test_project_event_creates_a_fresh_fragment_on_registration(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/local-3"
        event = validate_event(_registered_event(node_id))

        fragment = project_event(event, sessions_dir)

        assert fragment.node_id == node_id
        assert fragment.lifecycle == "active"
        assert read_fragment(node_id, sessions_dir) == fragment

    def test_project_event_updates_an_existing_fragment(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/local-4"
        project_event(validate_event(_registered_event(node_id)), sessions_dir)

        updated = project_event(
            validate_event(_lifecycle_changed_event(node_id, "blocked")), sessions_dir
        )

        assert updated.lifecycle == "blocked"
        assert read_fragment(node_id, sessions_dir).lifecycle == "blocked"
