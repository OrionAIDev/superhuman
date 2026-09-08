"""Tests for `scripts.fleet.hook_payload` — the generic harness-payload reader.

Chunk 3 (chunk 1 was throwaway/no-production-code per PLAN.md; this module
and its tests are implemented here, against the golden fixtures Chunk 1's
probe captured).

Covers: parsing `session_id`, `cwd`, `agent_type`, `transcript_path`,
`prompt_id` from a file or stdin; never raising on a malformed payload
(FR-18's threat model starts here — a hostile/corrupted stdin payload must
resolve to "no payload", not an exception).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scripts.fleet.hook_payload import HookPayload, read_hook_payload

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "payloads"


class TestHappyPath:
    def test_reads_session_start_payload_from_stdin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A well-formed `SessionStart` JSON payload piped to stdin parses
        into a `HookPayload` with `session_id`, `cwd`, `transcript_path`,
        `hook_event_name` populated exactly as given."""
        raw = json.dumps(
            {
                "session_id": "sess-123",
                "cwd": "/example/workspace",
                "transcript_path": "/example/transcript.jsonl",
                "hook_event_name": "SessionStart",
                "scratchpad_dir": "/example/scratchpad",
                "source": "startup",
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(raw))

        payload = read_hook_payload("-")

        assert payload == HookPayload(
            session_id="sess-123",
            cwd="/example/workspace",
            hook_event_name="SessionStart",
            transcript_path="/example/transcript.jsonl",
            scratchpad_dir="/example/scratchpad",
            source="startup",
            agent_id=None,
            agent_type=None,
            prompt_id=None,
        )

    def test_reads_subagent_start_payload_with_agent_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `SubagentStart` payload additionally carries `agent_type`."""
        raw = json.dumps(
            {
                "session_id": "sess-456",
                "cwd": "/example/workspace",
                "hook_event_name": "SubagentStart",
                "agent_id": "agent-1",
                "agent_type": "developer",
                "prompt_id": "prompt-1",
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(raw))

        payload = read_hook_payload("-")

        assert payload is not None
        assert payload.agent_type == "developer"
        assert payload.agent_id == "agent-1"
        assert payload.prompt_id == "prompt-1"

    def test_reads_payload_from_a_file_path_source(self, tmp_path: Path) -> None:
        """`read_hook_payload(path)` (a file, not `-`/stdin) parses
        identically to the stdin path — used by golden-fixture replay."""
        path = tmp_path / "payload.json"
        path.write_text(
            json.dumps({"session_id": "sess-789", "cwd": "/example/workspace"}),
            encoding="utf-8",
        )

        payload = read_hook_payload(path)

        assert payload is not None
        assert payload.session_id == "sess-789"
        assert payload.cwd == "/example/workspace"

    @pytest.mark.parametrize(
        "fixture_path", sorted(_FIXTURES_DIR.glob("*.json")), ids=lambda p: p.name
    )
    def test_replays_each_golden_fixture_in_fixtures_payloads(
        self, fixture_path: Path
    ) -> None:
        """Every file under `tests/fleet/fixtures/payloads/*.json` (redacted
        per NFR-8) parses without error into a `HookPayload`."""
        payload = read_hook_payload(fixture_path)

        assert payload is not None
        assert payload.session_id
        assert payload.cwd


class TestMalformedPayloadNeverRaises:
    """The payload reader is the first line of defense for FR-18: a
    corrupted/hostile payload must degrade to 'no payload', not propagate
    an exception up to the hook wrapper."""

    def test_truncated_json_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A syntactically-broken JSON string (missing closing brace/quote)
        returns `None`, does not raise."""
        monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "abc", "cwd": "/x'))

        assert read_hook_payload("-") is None

    def test_non_json_stdin_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Arbitrary non-JSON bytes on stdin return `None`, does not raise."""
        monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))

        assert read_hook_payload("-") is None

    def test_empty_stdin_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zero bytes on stdin return `None`, does not raise."""
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        assert read_hook_payload("-") is None

    def test_valid_json_missing_all_documented_fields_returns_none_or_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A syntactically valid JSON object (e.g. `{}`) that carries none
        of the documented fields is handled gracefully — `None`, since
        `session_id`/`cwd` are the two fields every downstream caller
        requires and neither is present."""
        monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

        assert read_hook_payload("-") is None

    def test_missing_file_source_returns_none(self, tmp_path: Path) -> None:
        """`read_hook_payload(nonexistent_path)` returns `None`, does not
        raise `FileNotFoundError`."""
        assert read_hook_payload(tmp_path / "does-not-exist.json") is None

    def test_non_object_top_level_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A well-formed JSON array (not an object) returns `None`."""
        monkeypatch.setattr("sys.stdin", io.StringIO("[1, 2, 3]"))

        assert read_hook_payload("-") is None
