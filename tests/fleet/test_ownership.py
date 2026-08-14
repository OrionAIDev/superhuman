"""Tests for ``scripts.fleet.core.ownership`` — TC-11 (FR-8, safety-critical).

A non-owner write to an owned field is REJECTED (raises), not merely warned.
"""

from __future__ import annotations

import pytest

from scripts.fleet.core.errors import OwnershipError
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
