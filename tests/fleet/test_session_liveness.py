"""Tests for `scripts.fleet.adapter.session_liveness` -- TC-O16.

`resolve_liveness` is O-FR-5's evidence rule: conservative by construction,
never inferring `"deleted"` from absence, and sharing its id-extraction rule
with `ClaudeAdapter.enumerate_sessions` so the two cannot drift.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.fleet.adapter.claude import ClaudeAdapter
from scripts.fleet.adapter.session_liveness import (
    ACTIVE,
    ARCHIVED,
    UNKNOWN,
    resolve_liveness,
)
from scripts.fleet.core.nodes import make_node_id

_NODE_ID = make_node_id("claude", "some-workspace", "demo-slug", "abc123")


class TestResolveLivenessMatchMatrix:
    def test_exactly_one_archived_match_is_archived(self) -> None:
        sessions = [{"sessionId": "abc123", "isArchived": True}]
        assert resolve_liveness(_NODE_ID, sessions) == ARCHIVED

    def test_exactly_one_active_match_is_active(self) -> None:
        sessions = [{"sessionId": "abc123", "isArchived": False}]
        assert resolve_liveness(_NODE_ID, sessions) == ACTIVE

    def test_zero_matching_records_is_unknown(self) -> None:
        sessions = [{"sessionId": "someone-else", "isArchived": True}]
        assert resolve_liveness(_NODE_ID, sessions) == UNKNOWN

    def test_two_or_more_matching_records_is_unknown(self) -> None:
        sessions = [
            {"sessionId": "abc123", "isArchived": True},
            {"sessionId": "abc123", "isArchived": False},
        ]
        assert resolve_liveness(_NODE_ID, sessions) == UNKNOWN

    @pytest.mark.parametrize("is_archived", ["yes", None, 1, 0, "true"])
    def test_non_boolean_is_archived_is_unknown(self, is_archived: Any) -> None:
        sessions = [{"sessionId": "abc123", "isArchived": is_archived}]
        assert resolve_liveness(_NODE_ID, sessions) == UNKNOWN

    def test_missing_is_archived_field_is_unknown(self) -> None:
        sessions = [{"sessionId": "abc123"}]
        assert resolve_liveness(_NODE_ID, sessions) == UNKNOWN

    def test_non_claude_harness_is_unknown(self) -> None:
        node_id = make_node_id("portable", "some-workspace", "demo-slug", "abc123")
        sessions = [{"sessionId": "abc123", "isArchived": True}]
        assert resolve_liveness(node_id, sessions) == UNKNOWN

    def test_no_records_supplied_is_unknown(self) -> None:
        assert resolve_liveness(_NODE_ID, None) == UNKNOWN
        assert resolve_liveness(_NODE_ID, []) == UNKNOWN

    def test_malformed_node_id_is_unknown(self) -> None:
        assert resolve_liveness("not-a-node-id", [{"sessionId": "abc123", "isArchived": True}]) == UNKNOWN

    def test_never_returns_deleted_from_any_of_the_above(self) -> None:
        """OQ-3: no supplied record format carries a positive 'deleted'
        signal, so this resolver must never manufacture one from absence or
        ambiguity — every uncertain case above must land on `"unknown"`,
        checked once more explicitly here for the emphasis DESIGN gives it."""
        cases: list[list[dict[str, Any]] | None] = [
            None,
            [],
            [{"sessionId": "someone-else", "isArchived": True}],
            [
                {"sessionId": "abc123", "isArchived": True},
                {"sessionId": "abc123", "isArchived": False},
            ],
            [{"sessionId": "abc123", "isArchived": "not-a-bool"}],
        ]
        for sessions in cases:
            assert resolve_liveness(_NODE_ID, sessions) != "deleted"


class TestIdExtractionSharedWithClaudeAdapter:
    """The id-extraction rule must be ONE shared helper, not a re-derivation
    (DESIGN O.4) -- exercised here by confirming `resolve_liveness` and
    `ClaudeAdapter.enumerate_sessions` agree on which record matches which
    node, for both the `sessionId` and legacy `session_id` spellings."""

    def test_is_the_literal_same_function_object_not_merely_agreeing_behavior(self) -> None:
        """I5: the two behavioral agreement tests below could pass even if
        `adapter.claude` re-derived its own copy of the extraction rule with
        matching behavior -- only an identity check catches that class of
        drift directly, rather than waiting for some future edit to one
        copy and not the other."""
        import scripts.fleet.adapter.claude as claude_mod

        from scripts.fleet.adapter.base import (
            extract_claude_session_local_id as base_extract,
        )

        assert claude_mod.extract_claude_session_local_id is base_extract

    def test_agrees_with_enumerate_sessions_on_session_id_spelling(self, tmp_path: Any) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug", sessions=[{"sessionId": "abc123"}])
        enumerated = adapter.enumerate_sessions()
        assert len(enumerated) == 1
        assert enumerated[0].local_id == "abc123"

        sessions = [{"sessionId": "abc123", "isArchived": True}]
        assert resolve_liveness(_NODE_ID, sessions) == ARCHIVED

    def test_agrees_with_enumerate_sessions_on_legacy_session_id_field(self, tmp_path: Any) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug", sessions=[{"session_id": "abc123"}])
        enumerated = adapter.enumerate_sessions()
        assert len(enumerated) == 1
        assert enumerated[0].local_id == "abc123"

        sessions = [{"session_id": "abc123", "isArchived": True}]
        assert resolve_liveness(_NODE_ID, sessions) == ARCHIVED
