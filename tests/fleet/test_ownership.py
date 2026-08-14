"""Tests for ``scripts.fleet.core.ownership`` — TC-11 (FR-8, safety-critical).

A non-owner write to an owned field is REJECTED (raises), not merely warned.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fleet.core.errors import OwnershipError
from scripts.fleet.core.events import append, read_all
from scripts.fleet.core.ownership import assert_writer_may


class TestSuperhumanOwnedField:
    """lifecycle/block_state/review_state are superhuman-owned."""

    def test_superhuman_role_may_write_lifecycle(self) -> None:
        assert_writer_may("lifecycle", "Project Manager")  # must not raise

    def test_ceo_role_may_not_write_lifecycle(self) -> None:
        with pytest.raises(OwnershipError):
            assert_writer_may("lifecycle", "CEO")

    @pytest.mark.parametrize("field", ["lifecycle", "block_state", "review_state"])
    def test_ceo_role_may_not_write_any_superhuman_owned_field(self, field: str) -> None:
        with pytest.raises(OwnershipError):
            assert_writer_may(field, "CEO")

    @pytest.mark.parametrize("field", ["lifecycle", "block_state", "review_state"])
    def test_superhuman_roles_may_write_every_superhuman_owned_field(self, field: str) -> None:
        for role in ("Project Manager", "Developer", "Architect", "QA", "Tester"):
            assert_writer_may(field, role)  # must not raise


class TestCeoOwnedField:
    """adoption_state (orphan flags) is ceo-owned."""

    def test_ceo_role_may_write_adoption_state(self) -> None:
        assert_writer_may("adoption_state", "CEO")  # must not raise

    def test_superhuman_role_may_not_write_adoption_state(self) -> None:
        with pytest.raises(OwnershipError):
            assert_writer_may("adoption_state", "Project Manager")


class TestSharedField:
    """done_level is shared — either side may write it in Chunk 1.

    (The advancement *rules* — evidence, D-ceiling, approver gate — are
    Chunk 5's `core/done.py`; Chunk 1 only proves the ownership axis itself.)
    """

    def test_shared_field_accepted_from_superhuman_side(self) -> None:
        assert_writer_may("done_level", "Developer")  # must not raise

    def test_shared_field_accepted_from_ceo_side(self) -> None:
        assert_writer_may("done_level", "CEO")  # must not raise


class TestOwnershipErrorIsRaisedNotWarned:
    def test_rejection_is_an_exception_not_a_falsy_return(self) -> None:
        with pytest.raises(OwnershipError) as exc_info:
            assert_writer_may("lifecycle", "CEO")
        assert "lifecycle" in str(exc_info.value)


class TestUnownedFieldIsUnrestricted:
    def test_a_field_with_no_owner_entry_is_not_restricted(self) -> None:
        assert_writer_may("some_free_field_no_one_owns", "CEO")  # must not raise
        assert_writer_may("some_free_field_no_one_owns", "Developer")  # must not raise


def _event(idempotency_key: str, writer_role: str, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "event_id": f"eid-{idempotency_key}",
        "idempotency_key": idempotency_key,
        "ts": "2026-08-14T12:00:00Z",
        "type": "lifecycle_changed",
        "project_id": "proj-abc",
        "node_id": "portable/ws/proj/local-1",
        "writer_role": writer_role,
        "payload": payload,
    }


class TestOwnershipIsEnforcedAtAppendCallSite:
    """G5 review finding #2: assert_writer_may was never wired into the write
    path — nothing called it outside its own tests. `errors.py`'s docstring
    claims OwnershipError is "raised before any append"; this proves it.
    """

    def test_non_owner_write_via_append_is_rejected_and_persists_nothing(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        # "lifecycle" is superhuman-owned; a CEO-role writer must be rejected.
        with pytest.raises(OwnershipError):
            append(log_path, _event("key-1", "CEO", {"lifecycle": "active"}))

        # Nothing persisted — not even the log file, let alone a fragment.
        assert read_all(log_path) == []

    def test_owner_write_via_append_persists_normally(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        result = append(log_path, _event("key-2", "Developer", {"lifecycle": "active"}))
        assert result is not None
        events = read_all(log_path)
        assert len(events) == 1
        assert events[0].payload == {"lifecycle": "active"}

    def test_unowned_payload_key_is_never_restricted(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        # A payload key with no FIELD_OWNERS entry is unrestricted, from any role.
        result = append(log_path, _event("key-3", "CEO", {"note": "not a status field"}))
        assert result is not None
