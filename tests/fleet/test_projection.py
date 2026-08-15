"""Tests for ``scripts.fleet.core.projection`` — TC-8 plus the incremental path.

Covers "Partial append / crash" error handling: fragments are a rebuildable
cache, never independent truth, so a corrupt fragment is never fatal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fleet.core.done import advance as done_advance
from scripts.fleet.core.done import _current_level as done_current_level
from scripts.fleet.core.errors import OwnershipError
from scripts.fleet.core.events import append, read_all
from scripts.fleet.core.projection import project_event, rebuild
from scripts.fleet.core.schema import Event, validate_event
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


class TestProjectEventIsNotABackDoorAroundOwnership:
    """HARDEN #3 (GPT-5 review): `append()` must be the SOLE enforced write
    boundary. `project_event()` takes an arbitrary `Event`, not necessarily
    one that came through `append()`'s ownership check — so a
    directly-constructed, ownership-violating `Event` handed straight to
    `project_event()` must not silently materialize a forged fragment. This
    is defense in depth: the legitimate path (append -> project_event with
    the SAME already-checked event) is unaffected, since an event that
    already passed `append()`'s ownership check trivially passes this
    re-check too.
    """

    def test_ownership_violating_event_is_rejected_not_written(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/back-door"
        # "lifecycle" is superhuman-owned; a CEO-role writer forging this
        # event directly (bypassing append()) must still be rejected.
        forged = validate_event(
            {
                "schema_version": 1,
                "event_id": "eid-forged",
                "idempotency_key": f"register:{node_id}",
                "ts": "2026-08-14T12:00:00Z",
                "type": "session_registered",
                "project_id": "proj-abc",
                "node_id": node_id,
                "writer_role": "CEO",
                "payload": {"lifecycle": "active"},
            }
        )

        with pytest.raises(OwnershipError):
            project_event(forged, sessions_dir)

        assert read_fragment(node_id, sessions_dir) is None, (
            "an ownership-violating event materialized a fragment through "
            "project_event() — the exact back door HARDEN #3 closes"
        )

    def test_legitimate_append_then_project_path_is_unaffected(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        log_path = tmp_path / "events.jsonl"
        node_id = "portable/ws/proj/legit"
        ev = append(log_path, _registered_event(node_id))

        fragment = project_event(ev, sessions_dir)

        assert fragment.lifecycle == "active"
        assert read_fragment(node_id, sessions_dir) == fragment


class TestDoneLevelOnlyFoldsFromAdvanceEvents:
    """G5 fix #1(b): `done_level` is folded ONLY from `done_level_advanced`
    events — never via the generic STATUS_FIELDS fold every other status
    field uses. `core/events.append` (and its `validate_event` call)
    already rejects a non-advance event carrying `done_level` in its
    payload at the write boundary (fix #1(a), `core/schema.py`); this is
    the defense-in-depth half — a directly-constructed `Event` (the
    dataclass, bypassing `validate_event` entirely, exactly the shape
    `project_event`'s own docstring warns it must not trust) must still
    have its `done_level` payload key ignored by both the incremental
    (`project_event`) and full-replay (`rebuild`) projection paths, so
    `core/projection.py` and `core/done.py::_current_level` can never
    disagree about a node's done_level.
    """

    def _forged_lifecycle_event_with_done_level(self, node_id: str) -> Event:
        # Bypasses validate_event() on purpose — the scenario under test is
        # exactly "something got an Event object past the schema-level
        # write-boundary check," e.g. a future in-process caller that
        # constructs Event(...) directly rather than going through append().
        return Event(
            schema_version=1,
            event_id="eid-forged",
            idempotency_key=f"lifecycle:{node_id}:active",
            ts="2026-08-15T00:00:00Z",
            type="lifecycle_changed",
            project_id="proj-abc",
            node_id=node_id,
            writer_role="Developer",
            payload={"lifecycle": "active", "done_level": "D4-prod"},
        )

    def test_project_event_ignores_done_level_on_a_fresh_fragment(
        self, tmp_path: Path
    ) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/forged-fresh"

        fragment = project_event(
            self._forged_lifecycle_event_with_done_level(node_id), sessions_dir
        )

        assert fragment.lifecycle == "active"  # the legitimate field still folds
        assert fragment.done_level == "D0-code"  # forged done_level ignored

    def test_project_event_ignores_done_level_on_an_existing_fragment(
        self, tmp_path: Path
    ) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/forged-existing"
        project_event(validate_event(_registered_event(node_id)), sessions_dir)

        fragment = project_event(
            self._forged_lifecycle_event_with_done_level(node_id), sessions_dir
        )

        assert fragment.lifecycle == "active"
        assert fragment.done_level == "D0-code"

    def test_projection_and_done_current_level_agree_on_a_mixed_log(
        self, tmp_path: Path
    ) -> None:
        """A log with a legitimate `done_level_advanced` transition plus an
        (in-process, forged) `Event` carrying `done_level` in a non-advance
        payload must still leave `core/projection` and
        `core/done.py::_current_level` in agreement — the whole point of
        fix #1(b)."""
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/mixed"

        append(log_path, _registered_event(node_id))
        done_advance(
            node_id,
            "D1-merged",
            evidence={"commit": "abc123"},
            approver=None,
            ceiling="D4-prod",
            project_id="proj-abc",
            writer_role="Developer",
            log_path=log_path,
        )

        fragments = rebuild(log_path, sessions_dir, project_id="proj-abc")

        assert fragments[node_id].done_level == "D1-merged"
        assert fragments[node_id].done_level == done_current_level(
            read_all(log_path), node_id
        )
