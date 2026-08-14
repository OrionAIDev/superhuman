"""Light tests for ``scripts.fleet.adapter.claude`` — TC-14 (Claude half).

Claude's native session tools (``list_sessions``, ``spawn_task``, ...) are
agent/MCP tools, not Python-importable functions (see the module docstring on
``scripts/fleet/adapter/claude.py``). So these tests never touch a live
Claude session — they use fakes/stubs for the orchestrator-supplied facts
(``current_session_id``, ``sessions``) exactly as a real caller would supply
them, and a fake ``session_scan.py``-shaped script for the enrichment path.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts.fleet.adapter.base import GitFacts, SessionAdapter, SessionInfo
from scripts.fleet.adapter.claude import ClaudeAdapter


class TestClaudeAdapterIsASessionAdapter:
    def test_claude_adapter_is_a_session_adapter(self, tmp_path: Path) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug")
        assert isinstance(adapter, SessionAdapter)

    def test_claude_adapter_exposes_all_four_methods(self, tmp_path: Path) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug")
        assert callable(adapter.current_session)
        assert callable(adapter.enumerate_sessions)
        assert callable(adapter.git_facts)
        assert callable(adapter.emit_prompt)


class TestClaudeAdapterCurrentSession:
    def test_current_session_uses_orchestrator_supplied_session_id(
        self, tmp_path: Path
    ) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug", current_session_id="relayed-abc123")
        session = adapter.current_session()

        assert isinstance(session, SessionInfo)
        assert session.harness == "claude"
        assert session.local_id == "relayed-abc123"
        assert session.node_id.endswith("/relayed-abc123")

    def test_current_session_without_orchestrator_supplied_id_is_unknown(
        self, tmp_path: Path
    ) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug")
        session = adapter.current_session()
        assert session.origination == "unknown"
        assert session.local_id  # still non-empty; never a fabricated real id


class TestClaudeAdapterEnumerateSessions:
    def test_enumerate_sessions_with_no_sessions_supplied_degrades_to_empty(
        self, tmp_path: Path
    ) -> None:
        # No `list_sessions` output was supplied — nothing is faked or guessed.
        adapter = ClaudeAdapter(tmp_path, "demo-slug")
        assert adapter.enumerate_sessions() == []

    def test_enumerate_sessions_reflects_orchestrator_supplied_sessions(
        self, tmp_path: Path
    ) -> None:
        raw_sessions = [
            {"sessionId": "child-1", "cwd": str(tmp_path), "branch": "feature/x"},
            {"sessionId": "child-2", "cwd": str(tmp_path), "branch": "feature/y"},
        ]
        adapter = ClaudeAdapter(tmp_path, "demo-slug", sessions=raw_sessions)
        sessions = adapter.enumerate_sessions()

        assert {s.local_id for s in sessions} == {"child-1", "child-2"}
        assert all(s.harness == "claude" for s in sessions)
        assert all(s.origination == "relayed" for s in sessions)

    def test_enumerate_sessions_skips_records_with_no_session_id(
        self, tmp_path: Path
    ) -> None:
        raw_sessions = [{"cwd": str(tmp_path)}]
        adapter = ClaudeAdapter(tmp_path, "demo-slug", sessions=raw_sessions)
        assert adapter.enumerate_sessions() == []


class TestClaudeAdapterSessionScanEnrichment:
    """`session_relay_script` enrichment — a fake standing in for `session_scan.py`.

    A real `session-relay` install is not assumed to exist at any fixed path
    on the machine running this suite (nor should the adapter's production
    wiring assume one — the caller always supplies the path it knows about).
    This fake honors `session_scan.py`'s own documented CLI contract exactly
    (a JSON-array-of-sessions file positional arg, `--json` flag, a
    `{"sessions": [...]}` JSON object on stdout) so the test exercises the
    real subprocess-invocation mechanism, not a mocked-out one.
    """

    @pytest.fixture
    def fake_session_scan_script(self, tmp_path: Path) -> Path:
        script = tmp_path / "fake_session_scan.py"
        script.write_text(
            textwrap.dedent(
                """\
                import json
                import sys

                data = json.loads(open(sys.argv[1], encoding="utf-8").read())
                enriched = [{**s, "branch": "enriched-branch"} for s in data]
                print(json.dumps({"sessions": enriched}))
                """
            ),
            encoding="utf-8",
        )
        return script

    def test_enumerate_sessions_enriches_via_the_configured_script(
        self, tmp_path: Path, fake_session_scan_script: Path
    ) -> None:
        adapter = ClaudeAdapter(
            tmp_path,
            "demo-slug",
            sessions=[{"sessionId": "child-1", "cwd": str(tmp_path)}],
            session_relay_script=fake_session_scan_script,
        )

        sessions = adapter.enumerate_sessions()

        assert len(sessions) == 1
        assert sessions[0].branch == "enriched-branch"

    def test_enumerate_sessions_degrades_when_script_is_missing(self, tmp_path: Path) -> None:
        adapter = ClaudeAdapter(
            tmp_path,
            "demo-slug",
            sessions=[{"sessionId": "child-1", "cwd": str(tmp_path), "branch": "as-supplied"}],
            session_relay_script=tmp_path / "does-not-exist.py",
        )

        sessions = adapter.enumerate_sessions()

        assert len(sessions) == 1
        assert sessions[0].branch == "as-supplied"


class TestClaudeAdapterGitFacts:
    def test_git_facts_on_non_repo_directory(self, tmp_path: Path) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug")
        facts = adapter.git_facts()
        assert isinstance(facts, GitFacts)
        assert facts.is_repo is False


class TestClaudeAdapterEmitPrompt:
    def test_emit_prompt_embeds_literal_handoff_id_line(self, tmp_path: Path) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug")
        handoff_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        prompt = adapter.emit_prompt("Pick up where the last session left off.", handoff_id)

        assert "Pick up where the last session left off." in prompt
        assert f"FLEET-HANDOFF-ID: {handoff_id}" in prompt
