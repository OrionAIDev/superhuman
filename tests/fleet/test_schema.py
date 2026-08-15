"""Tests for ``scripts.fleet.core.schema`` — TC-1, TC-2, TC-3, TC-4(a).

Covers NFR-7 (malformed writes rejected), FR-5 (five orthogonal status fields,
no collapsing enum), NFR-6 (writer_role is role-only), and the schema-side half
of G3-1 (project_id required on every event).
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.fleet.core.errors import ValidationError
from scripts.fleet.core.events import append, read_all
from scripts.fleet.core.projection import rebuild
from scripts.fleet.core.query import list_sessions
from scripts.fleet.core.schema import (
    REQUIRED_EVENT_FIELDS,
    Event,
    Fragment,
    fold_done_level,
    validate_event,
    validate_fragment,
)


def _valid_event() -> dict:
    return {
        "schema_version": 1,
        "event_id": "11111111-1111-1111-1111-111111111111",
        "idempotency_key": "register:portable/ws/proj/local-1",
        "ts": "2026-08-14T12:00:00Z",
        "type": "session_registered",
        "project_id": "proj-abc123",
        "node_id": "portable/ws/proj/local-1",
        "writer_role": "Developer",
        "payload": {},
    }


def _valid_fragment_kwargs() -> dict:
    return {
        "node_id": "portable/ws/proj/local-1",
        "project_id": "proj-abc123",
        "lifecycle": "active",
        "block_state": "unblocked",
        "review_state": "none",
        "adoption_state": "normal",
        "done_level": "D0-code",
    }


class TestValidateEventAcceptsWellFormed:
    def test_well_formed_event_round_trips(self) -> None:
        data = _valid_event()
        event = validate_event(data)
        assert isinstance(event, Event)
        assert event.schema_version == data["schema_version"]
        assert event.event_id == data["event_id"]
        assert event.idempotency_key == data["idempotency_key"]
        assert event.ts == data["ts"]
        assert event.type == data["type"]
        assert event.project_id == data["project_id"]
        assert event.node_id == data["node_id"]
        assert event.writer_role == data["writer_role"]
        assert event.payload == data["payload"]


class TestValidateEventRejectsMalformed:
    """TC-1: every malformed fixture raises and touches no file."""

    @pytest.mark.parametrize("missing_field", sorted(REQUIRED_EVENT_FIELDS))
    def test_missing_required_field_is_rejected(self, missing_field: str, tmp_path) -> None:
        data = _valid_event()
        del data[missing_field]
        with pytest.raises(ValidationError):
            validate_event(data)
        assert list(tmp_path.iterdir()) == []

    def test_unknown_event_type_is_rejected(self, tmp_path) -> None:
        data = _valid_event()
        data["type"] = "not_a_real_event_type"
        with pytest.raises(ValidationError):
            validate_event(data)
        assert list(tmp_path.iterdir()) == []

    def test_non_iso8601_ts_is_rejected(self, tmp_path) -> None:
        data = _valid_event()
        data["ts"] = "not-a-timestamp"
        with pytest.raises(ValidationError):
            validate_event(data)
        assert list(tmp_path.iterdir()) == []


class TestFiveDecomposedStatusFields:
    """TC-2 (FR-5): the five fields are orthogonal; collapsing them is rejected."""

    def test_active_and_blocked_simultaneously_is_legal(self) -> None:
        kwargs = _valid_fragment_kwargs()
        kwargs["lifecycle"] = "active"
        kwargs["block_state"] = "blocked"
        fragment = validate_fragment(kwargs)
        assert fragment.lifecycle == "active"
        assert fragment.block_state == "blocked"

    def test_collapsing_into_single_status_field_is_rejected(self) -> None:
        kwargs = _valid_fragment_kwargs()
        del kwargs["lifecycle"]
        del kwargs["block_state"]
        kwargs["status"] = "active_blocked"
        with pytest.raises(ValidationError):
            validate_fragment(kwargs)

    def test_dataclass_constructor_itself_rejects_a_status_kwarg(self) -> None:
        # Belt-and-suspenders: even bypassing validate_fragment(), the dataclass
        # has no `status` slot, so a caller cannot smuggle one in directly.
        kwargs = _valid_fragment_kwargs()
        del kwargs["lifecycle"]
        kwargs["status"] = "active"
        with pytest.raises(TypeError):
            Fragment(**kwargs)


class TestWriterRoleIsRoleOnly:
    """TC-3 (NFR-6): writer_role rejects model/vendor strings."""

    @pytest.mark.parametrize(
        "role",
        ["Project Manager", "Developer", "Architect", "QA", "Tester", "CEO"],
    )
    def test_role_names_are_accepted(self, role: str) -> None:
        data = _valid_event()
        data["writer_role"] = role
        event = validate_event(data)
        assert event.writer_role == role

    @pytest.mark.parametrize(
        "role",
        ["Claude", "Claude Sonnet 5", "claude-3", "gpt-4", "anthropic", "opus", "ChatGPT"],
    )
    def test_model_or_vendor_strings_are_rejected(self, role: str) -> None:
        data = _valid_event()
        data["writer_role"] = role
        with pytest.raises(ValidationError):
            validate_event(data)


class TestProjectIdRequired:
    """TC-4(a): project_id is required, not optional (G3-1)."""

    def test_omitted_project_id_is_rejected(self) -> None:
        data = _valid_event()
        del data["project_id"]
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_project_id_is_carried_through_unmodified(self) -> None:
        data = _valid_event()
        data["project_id"] = "proj-xyz789"
        event = validate_event(data)
        assert event.project_id == "proj-xyz789"


class TestValidateEventDoesNotMutateInput:
    def test_input_dict_is_not_mutated(self) -> None:
        data = _valid_event()
        original = copy.deepcopy(data)
        validate_event(data)
        assert data == original


class TestProjectIdGroupingIsByEqualityNotSlugSubstring:
    """TC-4(b): query grouping is project_id equality, never a slug substring.

    Two projects mint distinct project_ids but use slug-adjacent node ids
    (one slug is literally a substring of the other's node id) to prove the
    grouping isn't accidentally done via string containment.
    """

    def test_list_sessions_filters_by_project_id_equality(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"

        def register(node_id: str, project_id: str) -> dict:
            return {
                "schema_version": 1,
                "event_id": f"eid-{node_id}",
                "idempotency_key": f"register:{node_id}",
                "ts": "2026-08-14T12:00:00Z",
                "type": "session_registered",
                "project_id": project_id,
                "node_id": node_id,
                "writer_role": "Developer",
                "payload": {},
            }

        # project "proj" and project "proj-extended" — the second project_id
        # string literally contains the first as a substring, and both use
        # slug-adjacent node ids, to prove grouping never falls back to that.
        append(log_path, register("portable/ws/proj/local-1", "proj"))
        append(log_path, register("portable/ws/proj/local-2", "proj"))
        append(log_path, register("portable/ws/proj-extended/local-1", "proj-extended"))

        rebuild(log_path, sessions_dir)

        proj_sessions = list_sessions(sessions_dir, project_id="proj")
        assert {f.node_id for f in proj_sessions} == {
            "portable/ws/proj/local-1",
            "portable/ws/proj/local-2",
        }

        extended_sessions = list_sessions(sessions_dir, project_id="proj-extended")
        assert {f.node_id for f in extended_sessions} == {"portable/ws/proj-extended/local-1"}

    def test_list_sessions_without_filter_returns_everything(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        for i, project_id in enumerate(["proj-a", "proj-b"]):
            append(
                log_path,
                {
                    "schema_version": 1,
                    "event_id": f"eid-{i}",
                    "idempotency_key": f"register:node-{i}",
                    "ts": "2026-08-14T12:00:00Z",
                    "type": "session_registered",
                    "project_id": project_id,
                    "node_id": f"portable/ws/{project_id}/local-{i}",
                    "writer_role": "Developer",
                    "payload": {},
                },
            )
        rebuild(log_path, sessions_dir)
        assert len(list_sessions(sessions_dir)) == 2


class TestPayloadStatusValuesAreValidatedAtWriteTime:
    """G5 review finding #3: an invalid payload status value (e.g. an empty
    string) used to pass validate_event() untouched, get written to the log,
    then fail validate_fragment() on projection/read and get silently
    skipped by iter_fragments(skip_corrupt=True) — the session would vanish
    from list_sessions() forever (rebuild() just regenerates the same broken
    fragment from the same bad event on every replay). Rejecting at
    validate_event() time means nothing bad is ever persisted in the first
    place (NFR-7's own principle, applied to payload contents too).
    """

    @pytest.mark.parametrize("status_field", ["lifecycle", "block_state", "review_state",
                                               "adoption_state", "done_level"])
    def test_empty_status_value_in_payload_is_rejected(self, status_field: str) -> None:
        data = _valid_event()
        data["payload"] = {status_field: ""}
        with pytest.raises(ValidationError):
            validate_event(data)

    @pytest.mark.parametrize("bad_value", ["", "   ", 123, None, [], {}])
    def test_non_string_or_blank_status_value_is_rejected(self, bad_value: object) -> None:
        data = _valid_event()
        data["payload"] = {"lifecycle": bad_value}
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_valid_status_value_in_payload_is_accepted(self) -> None:
        data = _valid_event()
        data["payload"] = {"lifecycle": "active"}
        event = validate_event(data)
        assert event.payload == {"lifecycle": "active"}

    def test_non_status_payload_keys_are_unrestricted(self) -> None:
        # A payload key that isn't one of the five status fields carries no
        # such requirement — this validation is specifically about the
        # decomposed status vocabulary, not payload contents in general.
        data = _valid_event()
        data["payload"] = {"note": "", "commit_sha": "abc123"}
        event = validate_event(data)
        assert event.payload == {"note": "", "commit_sha": "abc123"}


class TestDoneLevelWriteBoundary:
    """G5 fix #1(a): `done_level` may only be set by a `done_level_advanced`
    event. Without this, a sanctioned event of any other type (e.g.
    `lifecycle_changed`) could carry `done_level` in its payload and, via
    `core.projection`'s generic STATUS_FIELDS fold, set a node's projected
    done_level directly — bypassing every one of `core.done.advance()`'s
    evidence/approver/ceiling/adjacency gates entirely, since `done_level`
    is a "shared" field in `FIELD_OWNERS` (ownership alone does not catch
    this). See `core/projection.py`'s matching fold-exclusion (fix #1(b)).
    """

    def test_done_level_in_a_non_advance_event_payload_is_rejected(self) -> None:
        data = _valid_event()
        data["type"] = "lifecycle_changed"
        data["payload"] = {"lifecycle": "active", "done_level": "D4-prod"}
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_done_level_in_a_non_advance_event_is_rejected_at_append_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        data = _valid_event()
        data["type"] = "lifecycle_changed"
        data["payload"] = {"done_level": "D4-prod"}
        with pytest.raises(ValidationError):
            append(log_path, data)
        assert read_all(log_path) == []

    def test_done_level_advanced_event_may_carry_done_level(self) -> None:
        data = _valid_event()
        data["type"] = "done_level_advanced"
        data["payload"] = {"done_level": "D1-merged", "evidence": {}, "approver": None}
        event = validate_event(data)
        assert event.payload["done_level"] == "D1-merged"

    def test_non_advance_event_without_done_level_in_payload_is_unaffected(self) -> None:
        data = _valid_event()
        data["type"] = "lifecycle_changed"
        data["payload"] = {"lifecycle": "active"}
        event = validate_event(data)
        assert event.payload == {"lifecycle": "active"}


def _done_level_advanced_event(done_level: str) -> Event:
    return Event(
        schema_version=1,
        event_id="eid-fold",
        idempotency_key="done:portable/ws/proj/local-1:" + done_level,
        ts="2026-08-15T00:00:00Z",
        type="done_level_advanced",
        project_id="proj-abc123",
        node_id="portable/ws/proj/local-1",
        writer_role="Developer",
        payload={"done_level": done_level, "evidence": {}, "approver": None},
    )


class TestFoldDoneLevelNeverRaisesOnUnrecognizedCurrent:
    """G5 fix #N2: an unrecognized `current_level` (e.g. a corrupt cached
    fragment's stale `done_level` value) must never raise `KeyError` — it is
    treated as the `D0-code` floor for adjacency purposes, so a subsequent
    legitimate adjacent event still applies from a known base.
    """

    def test_unrecognized_current_with_no_advancing_event_is_unchanged(self) -> None:
        event = Event(
            schema_version=1,
            event_id="eid-other",
            idempotency_key="lifecycle:portable/ws/proj/local-1:active",
            ts="2026-08-15T00:00:00Z",
            type="lifecycle_changed",
            project_id="proj-abc123",
            node_id="portable/ws/proj/local-1",
            writer_role="Developer",
            payload={"lifecycle": "active"},
        )
        # Must not raise KeyError, and a non-advance event leaves the
        # (corrupt) current_level untouched, same as any recognized value.
        assert fold_done_level("D9-bogus", event) == "D9-bogus"

    def test_unrecognized_current_with_an_adjacent_to_floor_event_advances(self) -> None:
        # D1-merged is adjacent to the D0-code FLOOR (index 0 + 1) — an
        # unrecognized current_level is treated as that floor, so this
        # legitimate event still applies from a known base.
        event = _done_level_advanced_event("D1-merged")
        assert fold_done_level("D9-bogus", event) == "D1-merged"

    def test_unrecognized_current_with_a_non_floor_adjacent_event_is_unchanged(self) -> None:
        # D2-test is NOT adjacent to the D0-code floor (it's two rungs up),
        # so this must not raise and must leave current_level untouched —
        # never silently reset to D0-code either.
        event = _done_level_advanced_event("D2-test")
        assert fold_done_level("D9-bogus", event) == "D9-bogus"

    def test_recognized_current_still_folds_normally(self) -> None:
        # No regression: the ordinary, recognized-current path is unaffected.
        event = _done_level_advanced_event("D2-test")
        assert fold_done_level("D1-merged", event) == "D2-test"


class TestInvalidPayloadStatusIsRejectedAtAppendAndNeverStrandsASession:
    """The end-to-end version of the same finding: append() rejects it
    outright (nothing persisted), and a *valid* status written afterward is
    readable via list_sessions() — proving the session never silently
    vanishes.
    """

    def test_append_with_invalid_status_persists_nothing(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        bad = _valid_event()
        bad["payload"] = {"lifecycle": ""}
        with pytest.raises(ValidationError):
            append(log_path, bad)
        assert read_all(log_path) == []

    def test_append_with_valid_status_is_readable_via_list_sessions(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        sessions_dir = tmp_path / "sessions"
        good = _valid_event()
        good["payload"] = {"lifecycle": "active"}
        append(log_path, good)
        rebuild(log_path, sessions_dir)
        sessions = list_sessions(sessions_dir, project_id=good["project_id"])
        assert len(sessions) == 1
        assert sessions[0].lifecycle == "active"
