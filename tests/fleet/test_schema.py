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
    EVENT_TYPES,
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

    def test_schema_version_2_is_rejected(self, tmp_path) -> None:
        """9th-round preflight, BLOCKING, PM-reproduced: an otherwise
        well-formed v1-shaped event whose `schema_version` is `2` must be
        rejected, not accepted and treated as valid v1 manifest truth on
        replay — the field was previously only type-checked (any int
        passed), never pinned to the one version this module actually
        knows how to interpret."""
        data = _valid_event()
        data["schema_version"] = 2
        with pytest.raises(ValidationError):
            validate_event(data)
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("bad_version", ["1", 1.5, None])
    def test_non_int_schema_version_is_still_rejected(self, bad_version, tmp_path) -> None:
        data = _valid_event()
        data["schema_version"] = bad_version
        with pytest.raises(ValidationError):
            validate_event(data)
        assert list(tmp_path.iterdir()) == []

    def test_schema_version_1_is_still_accepted(self) -> None:
        data = _valid_event()
        data["schema_version"] = 1
        event = validate_event(data)
        assert event.schema_version == 1

    @pytest.mark.parametrize("bad_bool", [True, False])
    def test_bool_schema_version_is_rejected(self, bad_bool: bool, tmp_path) -> None:
        """10th-round preflight, BLOCKING, PM-reproduced: `bool` is a
        subclass of `int` in Python (`True == 1`), so a loose
        `isinstance(x, int)` check accepted `schema_version=True` and
        replayed it as valid v1 truth. Both `True` (which equals `1`, the
        real `SCHEMA_VERSION_V1`) and `False` must be rejected outright —
        neither is a legitimate int, regardless of its numeric value."""
        data = _valid_event()
        data["schema_version"] = bad_bool
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


class TestValidateFragmentRejectsUnrecognizedDoneLevel:
    """G5 round-3 fix #R3-1(a): a fragment whose `done_level` is a
    non-empty string but not one of `DONE_LEVELS` (e.g. a stale legacy
    value, or a bogus one from a torn write) must be rejected by
    `validate_fragment`, not merely accepted as "any non-empty string."

    No legitimate fragment can carry such a value — `core.projection` only
    ever writes a `done_level` derived from `fold_done_level`, which itself
    only ever returns a recognized `DONE_LEVELS` member (or, before fix
    #R3-1(b), the unchanged input) — so this closes the gap at the read
    boundary: a cached fragment corrupted to a bogus `done_level` now hits
    `project_event`'s existing "treated as absent" `ValidationError` path
    (the same path a schema-invalid fragment already takes), rather than
    being accepted and the garbage value retained.
    """

    def test_unrecognized_done_level_is_rejected(self) -> None:
        kwargs = _valid_fragment_kwargs()
        kwargs["done_level"] = "D9-bogus"
        with pytest.raises(ValidationError):
            validate_fragment(kwargs)

    def test_every_recognized_done_level_is_still_accepted(self) -> None:
        from scripts.fleet.core.schema import DONE_LEVELS

        for level in DONE_LEVELS:
            kwargs = _valid_fragment_kwargs()
            kwargs["done_level"] = level
            fragment = validate_fragment(kwargs)
            assert fragment.done_level == level


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
    """G5 fix #N2 (round 2) + #R3-1(b) (round 3): an unrecognized
    `current_level` (e.g. a corrupt cached fragment's stale `done_level`
    value) must never raise `KeyError`, AND must never be echoed back
    verbatim as the fold's result either — round 2's fix floored only the
    ladder-position lookup used for the adjacency check, but still returned
    the unchanged garbage `current_level` string when the event was not
    adjacent, so a corrupt cached fragment retained its bogus value
    indefinitely (until a full `rebuild()`). Round 3 floors the RESULT too:
    an unrecognized `current_level` is now treated as the `D0-code` floor
    for the returned value as well as the adjacency check, so this helper
    alone can never emit a value outside `DONE_LEVELS`.
    """

    def test_unrecognized_current_with_no_advancing_event_floors_to_d0(self) -> None:
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
        # Must not raise KeyError, and must not echo back the garbage
        # current_level either (R3-1b) — floors to the D0-code base.
        assert fold_done_level("D9-bogus", event) == "D0-code"

    def test_unrecognized_current_with_an_adjacent_to_floor_event_advances(self) -> None:
        # D1-merged is adjacent to the D0-code FLOOR (index 0 + 1) — an
        # unrecognized current_level is treated as that floor, so this
        # legitimate event still applies from a known base.
        event = _done_level_advanced_event("D1-merged")
        assert fold_done_level("D9-bogus", event) == "D1-merged"

    def test_unrecognized_current_with_a_non_floor_adjacent_event_floors_to_d0(self) -> None:
        # D3-uat is NOT adjacent to the D0-code floor (it's three rungs up),
        # so this must not raise, and (R3-1b) must never return the garbage
        # "D9-bogus" current_level — it floors to the recognized D0-code
        # base instead, never silently advancing past what the event
        # actually justifies.
        event = _done_level_advanced_event("D3-uat")
        assert fold_done_level("D9-bogus", event) == "D0-code"

    def test_recognized_current_still_folds_normally(self) -> None:
        # No regression: the ordinary, recognized-current path is unaffected.
        event = _done_level_advanced_event("D2-test")
        assert fold_done_level("D1-merged", event) == "D2-test"

    def test_recognized_current_non_adjacent_event_still_returns_current_unchanged(
        self,
    ) -> None:
        # No regression: when current_level IS recognized, a non-adjacent
        # event still leaves it unchanged (not floored to D0-code) — the
        # R3-1(b) flooring applies only to an unrecognized current_level.
        event = _done_level_advanced_event("D3-uat")
        assert fold_done_level("D1-merged", event) == "D1-merged"


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


def _ownership_declared_payload() -> dict:
    return {
        "claim_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "anchor": "genesis",
        "prior_owner": None,
        "prior_owner_kind": "none",
        "basis": "unowned",
        "prior_owner_liveness": None,
        "liveness_source": "not-supplied",
        "attestation": None,
    }


def _ownership_declared_event() -> dict:
    data = _valid_event()
    data["type"] = "ownership_declared"
    data["idempotency_key"] = "own:proj-abc123:portable/ws/proj/local-1:after:genesis"
    data["payload"] = _ownership_declared_payload()
    return data


def _ownership_stood_down_payload() -> dict:
    return {
        "stood_down_from": "eid-declared-1",
        "written_by": "self",
        "basis": "self",
        "claimant": None,
        "claim_key": None,
        "reason": None,
    }


def _ownership_stood_down_event() -> dict:
    data = _valid_event()
    data["type"] = "ownership_stood_down"
    data["idempotency_key"] = "standdown:proj-abc123:portable/ws/proj/local-1:from:eid-declared-1:self"
    data["payload"] = _ownership_stood_down_payload()
    return data


class TestOwnershipEventTypesAreRegistered:
    """TC-O1: both new types are recognized in EVENT_TYPES (increment O)."""

    def test_ownership_declared_and_stood_down_are_registered(self) -> None:
        assert "ownership_declared" in EVENT_TYPES
        assert "ownership_stood_down" in EVENT_TYPES


class TestOwnershipDeclaredPayloadValidation:
    """TC-O1: `ownership_declared` payload validation (increment O, O.1)."""

    def test_well_formed_payload_is_accepted(self) -> None:
        event = validate_event(_ownership_declared_event())
        assert event.type == "ownership_declared"
        assert event.payload["basis"] == "unowned"

    def test_well_formed_payload_with_attestation_is_accepted(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        data["payload"]["prior_owner_kind"] = "declared"
        data["payload"]["basis"] = "notified"
        data["payload"]["prior_owner_liveness"] = "active"
        data["payload"]["liveness_source"] = "sessions-json"
        data["payload"]["attestation"] = {
            "notified_owner": "portable/ws/proj/local-2",
            "notified_via": "slack DM",
        }
        event = validate_event(data)
        assert event.payload["attestation"]["notified_via"] == "slack DM"

    def test_missing_claim_id_is_rejected(self) -> None:
        data = _ownership_declared_event()
        del data["payload"]["claim_id"]
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_missing_anchor_is_rejected(self) -> None:
        data = _ownership_declared_event()
        del data["payload"]["anchor"]
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_out_of_enum_basis_is_rejected(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["basis"] = "bogus"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_out_of_enum_prior_owner_kind_is_rejected(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["prior_owner_kind"] = "bogus"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_multiline_notified_via_is_rejected(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        data["payload"]["attestation"] = {
            "notified_owner": "portable/ws/proj/local-2",
            "notified_via": "line one\nline two",
        }
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_notified_via_over_200_chars_is_rejected(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        data["payload"]["attestation"] = {
            "notified_owner": "portable/ws/proj/local-2",
            "notified_via": "x" * 201,
        }
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_attestation_missing_notified_via_is_rejected(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        data["payload"]["attestation"] = {"notified_owner": "portable/ws/proj/local-2"}
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_unrecognized_payload_key_is_rejected(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["extra"] = "nope"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_basis_unowned_with_non_null_prior_owner_is_rejected(self) -> None:
        """M5: `basis="unowned"` (the default fixture) asserts there was no
        prior owner at all — a non-null `prior_owner` alongside it is a
        self-contradictory payload."""
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        # prior_owner_kind/basis left as the fixture's "none"/"unowned".
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_prior_owner_kind_none_with_non_null_prior_owner_is_rejected(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        data["payload"]["basis"] = "notified"
        data["payload"]["prior_owner_kind"] = "none"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_empty_string_notified_via_is_rejected(self) -> None:
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        data["payload"]["attestation"] = {
            "notified_owner": "portable/ws/proj/local-2",
            "notified_via": "   ",
        }
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_line_separator_u2028_in_notified_via_is_rejected(self) -> None:
        """M5: the single-line check must use `len(value.splitlines()) > 1`,
        which (unlike a literal `\\n`/`\\r` search) also catches U+2028 LINE
        SEPARATOR, U+0085 NEL, vertical tab, and form feed."""
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        data["payload"]["attestation"] = {
            "notified_owner": "portable/ws/proj/local-2",
            "notified_via": "line one line two",
        }
        with pytest.raises(ValidationError):
            validate_event(data)

    @pytest.mark.parametrize("terminator", ["\n", "\r\n", "\r", " ", "\x85"])
    def test_trailing_line_break_in_notified_via_is_rejected(self, terminator: str) -> None:
        """Fix round 2: `splitlines()` drops a final terminator, so a
        trailing break must be rejected too, not only an embedded one."""
        data = _ownership_declared_event()
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        data["payload"]["prior_owner_kind"] = "declared"
        data["payload"]["basis"] = "notified"
        data["payload"]["attestation"] = {
            "notified_owner": "portable/ws/proj/local-2",
            "notified_via": "slack" + terminator,
        }
        with pytest.raises(ValidationError):
            validate_event(data)

    @pytest.mark.parametrize("basis", ["notified", "archived", "deleted"])
    def test_takeover_basis_with_null_prior_owner_is_rejected(self, basis: str) -> None:
        """Fix round 2: a takeover basis must name the prior owner it displaced."""
        data = _ownership_declared_event()
        data["payload"]["basis"] = basis
        data["payload"]["prior_owner_kind"] = "declared"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_takeover_basis_with_prior_owner_kind_none_is_rejected(self) -> None:
        """Fix round 2: a takeover basis requires a prior-owner kind other than 'none'."""
        data = _ownership_declared_event()
        data["payload"]["basis"] = "archived"
        data["payload"]["prior_owner"] = "portable/ws/proj/local-2"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_malformed_variant_never_reaches_the_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        data = _ownership_declared_event()
        data["writer_role"] = "pm"
        del data["payload"]["claim_id"]
        with pytest.raises(ValidationError):
            append(log_path, data)
        assert read_all(log_path) == []


class TestOwnershipStoodDownPayloadValidation:
    """TC-O1: `ownership_stood_down` payload validation (increment O, O.1)."""

    def test_well_formed_self_standdown_is_accepted(self) -> None:
        event = validate_event(_ownership_stood_down_event())
        assert event.payload["written_by"] == "self"

    def test_well_formed_on_behalf_standdown_is_accepted(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["written_by"] = "claimant"
        data["payload"]["basis"] = "notified"
        data["payload"]["claimant"] = "portable/ws/proj/local-2"
        data["payload"]["claim_key"] = "own:proj-abc123:portable/ws/proj/local-2:after:eid-declared-1"
        event = validate_event(data)
        assert event.payload["claimant"] == "portable/ws/proj/local-2"

    def test_missing_stood_down_from_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        del data["payload"]["stood_down_from"]
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_out_of_enum_written_by_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["written_by"] = "bogus"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_out_of_enum_basis_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["basis"] = "bogus"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_claimant_written_by_without_claim_key_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["written_by"] = "claimant"
        data["payload"]["basis"] = "notified"
        data["payload"]["claimant"] = "portable/ws/proj/local-2"
        # claim_key left None
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_multiline_reason_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["reason"] = "line one\nline two"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_empty_string_reason_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["reason"] = "   "
        with pytest.raises(ValidationError):
            validate_event(data)

    @pytest.mark.parametrize("terminator", ["\n", "\r\n", "\r", " "])
    def test_trailing_line_break_in_reason_is_rejected(self, terminator: str) -> None:
        """Fix round 2: a trailing line break in `reason` is rejected."""
        data = _ownership_stood_down_event()
        data["payload"]["reason"] = "done" + terminator
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_line_separator_u2028_in_reason_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["reason"] = "line one line two"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_written_by_self_with_non_null_claimant_is_rejected(self) -> None:
        """M5: `written_by="self"` (the default fixture) asserts the OWNER
        stood itself down — a non-null `claimant`/`claim_key` alongside it
        is a self-contradictory payload (that combination belongs to
        `written_by="claimant"`)."""
        data = _ownership_stood_down_event()
        data["payload"]["claimant"] = "portable/ws/proj/local-2"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_written_by_self_with_non_null_claim_key_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["claim_key"] = "own:proj-abc123:portable/ws/proj/local-2:after:genesis"
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_basis_self_with_written_by_claimant_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["written_by"] = "claimant"
        data["payload"]["claimant"] = "portable/ws/proj/local-2"
        data["payload"]["claim_key"] = "own:proj-abc123:portable/ws/proj/local-2:after:genesis"
        # basis left as the fixture's "self" — invalid for written_by="claimant".
        with pytest.raises(ValidationError):
            validate_event(data)

    def test_written_by_claimant_with_basis_notified_and_matching_pair_is_accepted(self) -> None:
        """Sanity check alongside the two rejection tests above: the valid
        combination must still be accepted."""
        data = _ownership_stood_down_event()
        data["payload"]["written_by"] = "claimant"
        data["payload"]["basis"] = "notified"
        data["payload"]["claimant"] = "portable/ws/proj/local-2"
        data["payload"]["claim_key"] = "own:proj-abc123:portable/ws/proj/local-2:after:genesis"
        event = validate_event(data)
        assert event.payload["basis"] == "notified"

    def test_unrecognized_payload_key_is_rejected(self) -> None:
        data = _ownership_stood_down_event()
        data["payload"]["extra"] = "nope"
        with pytest.raises(ValidationError):
            validate_event(data)
