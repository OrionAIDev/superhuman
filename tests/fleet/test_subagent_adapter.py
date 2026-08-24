"""Tests for ``scripts.fleet.adapter.subagent`` — TC-18 (Chunk 5, W-FR-1, Decision C).

Mirrors ``tests/fleet/test_adapter_portable.py``'s structure: the
``SessionAdapter`` ABC contract, then per-method behavior against a real
temp-repo git fixture. The distinguishing assertions for this adapter are
that it reports ``harness="subagent"`` end to end and never attempts to
resolve a native harness session id — per DESIGN's Decision C identity note,
a subagent dispatch is not an addressable session, so there is nothing to
resolve; the dispatch id is supplied directly by the PM that minted it.

The full create -> validate -> query round trip through
``cli.register_session`` (proving `SubagentAdapter` is genuinely registered
into the conformance suite, not just unit-tested in isolation) lives in
``tests/fleet/conformance/test_conformance_suite.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.fleet import observe
from scripts.fleet.adapter.base import GitFacts, SessionAdapter, SessionInfo
from scripts.fleet.adapter.subagent import SubagentAdapter
from scripts.fleet.cli import build_parser


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real temp git repo with one commit on a non-default-named branch.

    Stands in for the *dispatching* PM's workspace — a subagent dispatch has
    no working tree of its own (see `subagent.py`'s module docstring), so
    every fact this adapter reports is derived from this fixture.
    """
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
    def test_subagent_adapter_is_a_session_adapter(self, git_repo: Path) -> None:
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="pm-developer-5-1")
        assert isinstance(adapter, SessionAdapter)

    def test_subagent_adapter_exposes_all_four_methods(self, git_repo: Path) -> None:
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="pm-developer-5-1")
        assert callable(adapter.current_session)
        assert callable(adapter.enumerate_sessions)
        assert callable(adapter.git_facts)
        assert callable(adapter.emit_prompt)


class TestSubagentAdapterCurrentSession:
    def test_current_session_returns_session_info(self, git_repo: Path) -> None:
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="developer-5-1")
        session = adapter.current_session()

        assert isinstance(session, SessionInfo)
        assert session.harness == "subagent"
        assert session.local_id == "developer-5-1"
        assert session.branch == "trunk"
        assert session.node_id.startswith("subagent/")
        assert session.node_id.endswith("/developer-5-1")

    def test_current_session_origination_is_spawned(self, git_repo: Path) -> None:
        """A subagent dispatch is, by construction, always the spawned path (W-FR-1)."""
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="developer-5-1")
        session = adapter.current_session()
        assert session.origination == "spawned"

    def test_local_id_is_required_no_fabricated_fallback(self) -> None:
        """Unlike PortableAdapter's `str(os.getpid())` default, there is no honest
        process-level identity for a dispatch this Python interpreter did not
        itself spawn as an OS process — `local_id` must be supplied.
        """
        with pytest.raises(TypeError):
            SubagentAdapter("workspace", "demo-slug")  # type: ignore[call-arg]

    def test_current_session_never_resolves_a_native_harness_session_id(
        self, git_repo: Path
    ) -> None:
        """Decision C identity note: a dispatch is not an addressable session — this
        adapter never reaches for a `current_session_id`/`sessions` style
        orchestrator-supplied fact the way `ClaudeAdapter` must (there is no
        such parameter on this constructor at all).
        """
        import inspect

        params = inspect.signature(SubagentAdapter.__init__).parameters
        assert "current_session_id" not in params
        assert "sessions" not in params


class TestSubagentAdapterEnumerateSessions:
    def test_enumerate_sessions_returns_only_self(self, git_repo: Path) -> None:
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="only-one")
        sessions = adapter.enumerate_sessions()
        assert [s.local_id for s in sessions] == ["only-one"]

    def test_enumerate_sessions_resolves_via_target_session_id_lookup(
        self, git_repo: Path
    ) -> None:
        """The spawned-path shape `cli._resolve_target_session` relies on: a
        caller supplies the same id as both this adapter's `local_id` and
        `register_session`'s `target_session_id`, and the lookup must find it.
        """
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="developer-5-1")
        matches = [s for s in adapter.enumerate_sessions() if s.local_id == "developer-5-1"]
        assert len(matches) == 1


class TestSubagentAdapterGitFacts:
    """Git facts are derived from the DISPATCHING workspace (module docstring) —
    identical mechanism to `PortableAdapter`/`ClaudeAdapter`, reused verbatim.
    """

    def test_git_facts_reports_real_plumbing_on_clean_repo(self, git_repo: Path) -> None:
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="developer-5-1")
        facts = adapter.git_facts()

        assert isinstance(facts, GitFacts)
        assert facts.is_repo is True
        assert facts.branch == "trunk"
        assert facts.dirty_files == 0
        assert facts.toplevel is not None
        assert Path(facts.toplevel).resolve() == git_repo.resolve()

    def test_git_facts_on_non_repo_directory(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        adapter = SubagentAdapter(not_a_repo, "demo-slug", local_id="developer-5-1")
        facts = adapter.git_facts()
        assert facts.is_repo is False
        assert facts.branch is None


class TestSubagentAdapterEmitPrompt:
    def test_emit_prompt_embeds_literal_handoff_id_line(self, git_repo: Path) -> None:
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="developer-5-1")
        handoff_id = "11111111-2222-3333-4444-555555555555"
        prompt = adapter.emit_prompt("Continue the work.", handoff_id)

        assert "Continue the work." in prompt
        assert f"FLEET-HANDOFF-ID: {handoff_id}" in prompt


# --- TC-19: `observe_dispatch` writes a validated `spawned` entry, driven by
# SubagentAdapter specifically (the generic-adapter version of this test
# lives in `tests/fleet/test_observe.py`, built in Chunk 1) -----------------


@pytest.fixture
def enabled_project(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """A workspace with a resolvable `SUPERHUMAN.md` identity and fleet enabled.

    Mirrors `tests/fleet/test_observe.py`'s fixture of the same name.
    """
    slug = "demo-project"
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "fleet:\n  enabled: true\n  observe_deadline_seconds: 5.0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

    project_dir = git_repo / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(
        f"**Slug:** {slug}\n**Project-id:** fleet-demo123\n", encoding="utf-8"
    )
    return git_repo, slug


class TestObserveDispatchViaSubagentAdapter:
    """TC-19: a role dispatch yields a validated `spawned` entry with `harness=subagent`."""

    def test_observe_dispatch_writes_a_validated_spawned_entry_with_subagent_harness(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = SubagentAdapter(workspace, slug, local_id="developer-5-1")

        result = observe.observe_dispatch(
            adapter,
            workspace=workspace,
            slug=slug,
            dispatch_id="developer-5-1",
            writer_role="pm",
        )

        assert result.ok is True
        assert result.node_id is not None
        assert result.node_id.startswith("subagent/")
        status = observe.observe_status(workspace, slug)
        assert "succeeded" in status

    def test_cli_observe_dispatch_with_harness_subagent(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """The actual `fleet observe dispatch --harness subagent ...` command shape."""
        workspace, slug = enabled_project
        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "dispatch",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--dispatch-id",
                "developer-5-1",
                "--harness",
                "subagent",
                "--local-id",
                "developer-5-1",
                "--writer-role",
                "pm",
            ]
        )

        assert args.func(args) == 0
        status = observe.observe_status(workspace, slug)
        assert "succeeded" in status

    def test_cli_observe_dispatch_with_harness_subagent_missing_local_id_never_raises(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """A malformed `--harness subagent` call (no `--local-id`) still exits 0 (Decision A).

        Phase 3.3 preflight FIX 4: this failure is now routed through
        `observe.py`'s journal (`error_class="adapter_construction_failed"`),
        so it is surfaced by `observe status` rather than being invisible
        beyond a single stderr line — see
        `test_observe.py::TestMalformedHarnessSubagentJournaled`.
        """
        workspace, slug = enabled_project
        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "dispatch",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--dispatch-id",
                "developer-5-1",
                "--harness",
                "subagent",
                "--writer-role",
                "pm",
            ]
        )

        assert args.func(args) == 0
        status = observe.observe_status(workspace, slug)
        assert "last write for this project failed" in status
        assert "adapter_construction_failed" in status
