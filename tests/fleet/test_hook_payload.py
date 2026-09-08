"""Tests for `scripts.fleet.hook_payload` — the generic harness-payload reader.

TDD scaffold only (Phase 2.1). Stubs with `pytest.mark.skip`; the Chunk 1/3
Developer implements each for real, TDD-first, against the golden fixtures
captured by Chunk 1's probe (`tests/fleet/fixtures/payloads/`).

Covers: parsing `session_id`, `cwd`, `agent_type`, `transcript_path`,
`prompt_id` from a file or stdin; never raising on a malformed payload
(FR-18's threat model starts here — a hostile/corrupted stdin payload must
resolve to "no payload", not an exception).
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestHappyPath:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_reads_session_start_payload_from_stdin(self) -> None:
        """A well-formed `SessionStart` JSON payload piped to stdin parses
        into a `HookPayload` with `session_id`, `cwd`, `transcript_path`,
        `permission_mode`, `hook_event_name` populated exactly as given."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_reads_subagent_start_payload_with_agent_type(self) -> None:
        """A `SubagentStart` payload additionally carries `agent_type`."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_reads_payload_from_a_file_path_source(self, tmp_path: Path) -> None:
        """`read_hook_payload(path)` (a file, not `-`/stdin) parses
        identically to the stdin path — used by Chunk 1's probe and by
        tests replaying golden fixtures."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_replays_each_golden_fixture_in_fixtures_payloads(self) -> None:
        """Parametrized over every file under
        `tests/fleet/fixtures/payloads/*.json` (redacted per NFR-8); each
        must parse without error into a `HookPayload`."""


class TestMalformedPayloadNeverRaises:
    """The payload reader is the first line of defense for FR-18: a
    corrupted/hostile payload must degrade to 'no payload', not propagate
    an exception up to the hook wrapper."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_truncated_json_returns_none(self) -> None:
        """A syntactically-broken JSON string (missing closing brace/quote)
        returns `None`, does not raise."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_non_json_stdin_returns_none(self) -> None:
        """Arbitrary non-JSON bytes on stdin return `None`, does not raise."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_empty_stdin_returns_none(self) -> None:
        """Zero bytes on stdin return `None`, does not raise."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_valid_json_missing_all_documented_fields_returns_none_or_empty(
        self,
    ) -> None:
        """A syntactically valid JSON object (e.g. `{}`) that carries none
        of the documented fields is handled gracefully — either `None` or
        a `HookPayload` with all-empty fields, never a `KeyError`."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 1/3")
    def test_missing_file_source_returns_none(self, tmp_path: Path) -> None:
        """`read_hook_payload(nonexistent_path)` returns `None`, does not
        raise `FileNotFoundError`."""
