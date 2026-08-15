"""Tests for ``scripts.fleet.core.projection`` — TC-8 plus the incremental path.

Covers "Partial append / crash" error handling: fragments are a rebuildable
cache, never independent truth, so a corrupt fragment is never fatal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fleet.core.done import advance as done_advance
from scripts.fleet.core.done import _current_level as done_current_level
from scripts.fleet.core.errors import FragmentCorrupt, OwnershipError
from scripts.fleet.core.events import append, read_all
from scripts.fleet.core.projection import project_event, rebuild
from scripts.fleet.core.schema import Event, Fragment, validate_event
from scripts.fleet.core.store import fragment_path, read_fragment, write_fragment


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


class TestCorruptCachedFragmentDoneLevelNeverCrashesProjection:
    """G5 fix #N2 (superseded here by round-5, #P4-1/#P4-2 — see below) plus
    G5 round-3 fix #R3-1(a): a cached fragment whose `done_level` is not one
    of `DONE_LEVELS` (e.g. a stale legacy value) is schema-INVALID —
    `validate_fragment` rejects it (`core/schema.py`'s own `done_level not
    in DONE_LEVELS` check) — so reading it back now raises `FragmentCorrupt`
    just like any other existing-but-corrupt fragment. This is exactly the
    same "existing corrupt fragment" case the JSON/UTF-8 corruption tests
    above cover, just reached via a schema violation instead of a decode
    failure; `project_event` no longer distinguishes by *how* a fragment
    ended up corrupt — only "absent" (fold from defaults) vs. "corrupt"
    (raise `FragmentCorrupt`, caller recovers via `rebuild()`).
    """

    def _write_corrupt_fragment(self, node_id: str, sessions_dir: Path) -> None:
        write_fragment(
            Fragment(
                node_id=node_id,
                project_id="proj-abc",
                lifecycle="active",
                block_state="unblocked",
                review_state="none",
                adoption_state="normal",
                done_level="D9-bogus",  # not one of DONE_LEVELS
            ),
            sessions_dir,
        )

    def test_project_event_raises_fragment_corrupt_on_bogus_done_level(
        self, tmp_path: Path
    ) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/corrupt-cached"
        self._write_corrupt_fragment(node_id, sessions_dir)

        event = validate_event(
            {
                "schema_version": 1,
                "event_id": "eid-legit-advance",
                "idempotency_key": f"done:{node_id}:D1-merged",
                "ts": "2026-08-15T00:00:00Z",
                "type": "done_level_advanced",
                "project_id": "proj-abc",
                "node_id": node_id,
                "writer_role": "Developer",
                "payload": {"done_level": "D1-merged", "evidence": {}, "approver": None},
            }
        )

        with pytest.raises(FragmentCorrupt):
            project_event(event, sessions_dir)

    def test_rebuild_still_fully_recovers_regardless_of_the_corrupt_cached_fragment(
        self, tmp_path: Path
    ) -> None:
        """`rebuild()` ignores cached fragments and replays only the log —
        confirms it still recovers correctly even with a corrupt fragment
        sitting on disk (the full-recovery path this fix's narrower
        never-crash guarantee complements, not replaces)."""
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/corrupt-cached-rebuild"

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

        # A corrupt fragment on disk must not affect rebuild()'s outcome.
        self._write_corrupt_fragment(node_id, sessions_dir)

        fragments = rebuild(log_path, sessions_dir, project_id="proj-abc")

        assert fragments[node_id].done_level == "D1-merged"


class TestCorruptCachedFragmentJSONRaisesFragmentCorrupt:
    """G5 round-5 (eliminate-the-class fix, #P4-1/#P4-2) supersedes round-3
    fix #R3-2 here. Round 3 made `project_event` treat a truncated/non-JSON
    cached fragment as "absent" and fold the triggering event alone onto
    `_fresh_fragment(event)` — which quietly RESET every status field the
    event's own payload does not mention (e.g. a `done_level_advanced`
    event, carrying only done/evidence/approver, would reset `block_state`,
    `review_state`, and `adoption_state` back to their registration
    defaults: silent state loss, #P4-2). An EXISTING-but-corrupt fragment is
    not the same thing as a genuinely absent one, and `project_event` no
    longer guesses between them — it raises `FragmentCorrupt` and leaves
    recovery (a full `rebuild()` from the log) to the caller. See
    `TestCliDoneAdvanceRecoversFromCorruptCachedFragment` in
    `tests/fleet/test_done.py` for the caller-side recovery this enables.
    """

    def _write_truncated_json_fragment(self, node_id: str, sessions_dir: Path) -> None:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        path = fragment_path(node_id, sessions_dir)
        # Not valid JSON at all (as opposed to the other test class's
        # schema-valid-but-unrecognized-value scenario) — a torn/truncated
        # write, or plain garbage.
        path.write_text('{"node_id": "portable/ws/proj/x", "lifecy', encoding="utf-8")

    def test_project_event_raises_fragment_corrupt_on_non_json_cached_fragment(
        self, tmp_path: Path
    ) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/truncated-json"
        self._write_truncated_json_fragment(node_id, sessions_dir)

        event = validate_event(_registered_event(node_id))

        with pytest.raises(FragmentCorrupt):
            project_event(event, sessions_dir)

    def test_project_event_raises_fragment_corrupt_for_a_later_event_too(
        self, tmp_path: Path
    ) -> None:
        """Same scenario, but the corrupt cache is hit on a non-first event
        for the node (the more realistic `fleet done advance` shape, where
        the node was already registered and only its cached fragment got
        corrupted later)."""
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/truncated-json-later"
        self._write_truncated_json_fragment(node_id, sessions_dir)

        event = validate_event(_lifecycle_changed_event(node_id, "blocked"))

        with pytest.raises(FragmentCorrupt):
            project_event(event, sessions_dir)

    def test_non_utf8_cached_fragment_also_raises_fragment_corrupt(
        self, tmp_path: Path
    ) -> None:
        """P4-1: undecodable bytes are as much "corrupt" as bad JSON — must
        raise the same typed error, not an uncaught `UnicodeDecodeError`."""
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/badbytes"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        fragment_path(node_id, sessions_dir).write_bytes(b"\xff\xfe garbage not utf-8")

        event = validate_event(_lifecycle_changed_event(node_id, "blocked"))

        with pytest.raises(FragmentCorrupt):
            project_event(event, sessions_dir)
