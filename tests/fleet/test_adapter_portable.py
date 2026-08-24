"""Tests for ``scripts.fleet.adapter.base``/``adapter.portable`` — TC-14, TC-16.

TC-14 covers the ``SessionAdapter`` ABC contract (both implementations expose
the four DESIGN-named methods; the bare ABC is not instantiable) and the
``PortableAdapter`` half of it against a real temp-repo git fixture. TC-16
covers NFR-3: with ``session-relay`` unimportable, the Portable path still
registers a valid entry — proven here at the adapter level (the end-to-end
``fleet register`` version lives in ``tests/fleet/test_register.py``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.fleet.adapter.base import GitFacts, SessionAdapter, SessionInfo
from scripts.fleet.adapter.portable import PortableAdapter


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real temp git repo with one commit on a non-default-named branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "trunk")
    _run(repo, "config", "user.email", "test@example.invalid")
    _run(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run(repo, "add", "README.md")
    _run(repo, "commit", "-q", "-m", "initial")
    return repo


class TestSessionAdapterABC:
    def test_bare_abc_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            SessionAdapter()  # type: ignore[abstract]

    def test_portable_adapter_is_a_session_adapter(self, git_repo: Path) -> None:
        adapter = PortableAdapter(git_repo, "demo-slug")
        assert isinstance(adapter, SessionAdapter)

    def test_portable_adapter_exposes_all_four_methods(self, git_repo: Path) -> None:
        adapter = PortableAdapter(git_repo, "demo-slug")
        assert callable(adapter.current_session)
        assert callable(adapter.enumerate_sessions)
        assert callable(adapter.git_facts)
        assert callable(adapter.emit_prompt)


class TestPortableAdapterCurrentSession:
    def test_current_session_returns_session_info(self, git_repo: Path) -> None:
        adapter = PortableAdapter(git_repo, "demo-slug", local_id="fixed-local-id")
        session = adapter.current_session()

        assert isinstance(session, SessionInfo)
        assert session.harness == "portable"
        assert session.local_id == "fixed-local-id"
        assert session.branch == "trunk"
        assert session.node_id.startswith("portable/")
        assert session.node_id.endswith("/fixed-local-id")

    def test_current_session_defaults_local_id_to_process_id(self, git_repo: Path) -> None:
        import os

        adapter = PortableAdapter(git_repo, "demo-slug")
        session = adapter.current_session()
        assert session.local_id == str(os.getpid())


class TestPortableAdapterEnumerateSessions:
    def test_enumerate_sessions_returns_only_self(self, git_repo: Path) -> None:
        adapter = PortableAdapter(git_repo, "demo-slug", local_id="only-one")
        sessions = adapter.enumerate_sessions()
        assert [s.local_id for s in sessions] == ["only-one"]


class TestPortableAdapterGitFacts:
    def test_git_facts_reports_real_plumbing_on_clean_repo(self, git_repo: Path) -> None:
        adapter = PortableAdapter(git_repo, "demo-slug")
        facts = adapter.git_facts()

        assert isinstance(facts, GitFacts)
        assert facts.is_repo is True
        assert facts.branch == "trunk"
        assert facts.dirty_files == 0
        assert facts.toplevel is not None
        assert Path(facts.toplevel).resolve() == git_repo.resolve()

    def test_git_facts_reports_dirty_files(self, git_repo: Path) -> None:
        (git_repo / "untracked.txt").write_text("scratch\n", encoding="utf-8")
        adapter = PortableAdapter(git_repo, "demo-slug")
        facts = adapter.git_facts()
        assert facts.dirty_files == 1

    def test_git_facts_on_non_repo_directory(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        adapter = PortableAdapter(not_a_repo, "demo-slug")
        facts = adapter.git_facts()
        assert facts.is_repo is False
        assert facts.branch is None


class TestPortableAdapterEmitPrompt:
    def test_emit_prompt_embeds_literal_handoff_id_line(self, git_repo: Path) -> None:
        adapter = PortableAdapter(git_repo, "demo-slug")
        handoff_id = "11111111-2222-3333-4444-555555555555"
        prompt = adapter.emit_prompt("Continue the work.", handoff_id)

        assert "Continue the work." in prompt
        assert f"FLEET-HANDOFF-ID: {handoff_id}" in prompt

    def test_emit_prompt_embeds_self_register_instruction_adjacent_to_id_line(
        self, git_repo: Path
    ) -> None:
        """Chunk 3, Decision E: the launched session sees the trigger in its own prompt."""
        adapter = PortableAdapter(git_repo, "demo-slug")
        handoff_id = "11111111-2222-3333-4444-555555555555"
        prompt = adapter.emit_prompt("Continue the work.", handoff_id)

        id_pos = prompt.index(f"FLEET-HANDOFF-ID: {handoff_id}")
        assert "fleet observe launch" in prompt[id_pos:]
        assert "best-effort" in prompt.lower()
        assert "never blocks" in prompt.lower()


class TestPortableAdapterDegradesWithoutSessionRelay:
    """NFR-3 (TC-16, adapter half) — session-relay is never importable here."""

    def test_portable_adapter_never_imports_session_relay(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate session-relay being entirely absent from this interpreter.
        monkeypatch.setitem(sys.modules, "session_relay", None)
        for name in list(sys.modules):
            if "session_relay" in name and name != "session_relay":
                monkeypatch.delitem(sys.modules, name, raising=False)

        adapter = PortableAdapter(git_repo, "demo-slug", local_id="degraded")
        session = adapter.current_session()
        facts = adapter.git_facts()
        enumerated = adapter.enumerate_sessions()

        assert session.node_id
        assert facts.is_repo is True
        assert len(enumerated) == 1
