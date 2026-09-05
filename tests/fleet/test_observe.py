"""Tests for `scripts.fleet.observe` — the fail-soft observation façade.

Covers TC-3 (fault injection across the four write verbs), TC-4 (wall-clock
budget), TC-5 (passthrough defaults unchanged), TC-6 (`observe status`
states), and the W-FR-7 disabled-workspace zero-I/O guarantee.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.fleet import cli as fleet_cli
from scripts.fleet import observe
from scripts.fleet.adapter.claude import ClaudeAdapter
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.cli import build_parser
from scripts.fleet.core.errors import LockTimeoutError, OwnershipError, ValidationError
from scripts.fleet.core.nodes import parse_node_id
from scripts.fleet.core.store import read_fragment
from scripts import regen_core_manifest as regen
from scripts.fleet.handoff import extract_handoff_id


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "trunk")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-q", "-m", "initial")
    return repo


@pytest.fixture
def enabled_project(git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A workspace with a resolvable `SUPERHUMAN.md` identity and fleet enabled."""
    slug = "demo-project"
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "fleet:\n"
        "  enabled: true\n"
        "  observe_deadline_seconds: 5.0\n"
        "  lock_timeout_seconds: 0.8\n"
        "  git_timeout_seconds: 0.25\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

    project_dir = git_repo / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(
        f"**Slug:** {slug}\n**Project-id:** fleet-demo123\n", encoding="utf-8"
    )
    return git_repo, slug


@pytest.fixture
def disabled_project(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A workspace with no fleet configuration at all (W-FR-7)."""
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(git_repo / "no-such-profile.yaml"))
    return git_repo, "demo-project"


def _fleet_dir(workspace: Path, slug: str) -> Path:
    return workspace / "docs" / "superhuman" / slug / "fleet"


class TestDisabledWorkspace:
    """W-FR-7: a workspace with no fleet config gets zero writes, zero output."""

    def test_dispatch_produces_zero_writes_when_disabled(
        self, disabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = disabled_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_dispatch(
            adapter, workspace=workspace, slug=slug, dispatch_id="child-1", writer_role="pm"
        )

        assert result.disabled is True
        assert result.ok is False
        assert not _fleet_dir(workspace, slug).exists()

    def test_relay_produces_zero_writes_when_disabled(
        self, disabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = disabled_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_relay(adapter, workspace=workspace, slug=slug, writer_role="pm")

        assert result.disabled is True
        assert not _fleet_dir(workspace, slug).exists()

    def test_launch_produces_zero_writes_when_disabled(
        self, disabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = disabled_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_launch(
            adapter, workspace=workspace, slug=slug, handoff_id="abc", writer_role="pm"
        )

        assert result.disabled is True
        assert not _fleet_dir(workspace, slug).exists()

    def test_handoff_emit_still_delivers_the_draft_prompt_when_disabled(
        self, disabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = disabled_project
        adapter = PortableAdapter(workspace, slug)
        draft = "Please continue the work.\n"

        result = observe.observe_handoff_emit(
            adapter, workspace=workspace, slug=slug, prompt_text=draft, writer_role="pm"
        )

        assert result.disabled is True
        # TC-9(a): the draft is delivered byte-for-byte — this equality is
        # itself the strongest form of the two negatives below (no id line,
        # no self-register instruction), since nothing was appended at all.
        assert result.prompt_text == draft
        assert "FLEET-HANDOFF-ID" not in result.prompt_text
        assert extract_handoff_id(result.prompt_text) is None
        assert "self-register" not in result.prompt_text.lower()
        assert "observe launch" not in result.prompt_text.lower()
        assert not _fleet_dir(workspace, slug).exists()


class TestIdentityUnresolved:
    """W-FR-6: no `SUPERHUMAN.md` identity -> journaled, never an invented id."""

    def test_dispatch_journals_identity_unresolved(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "no-superhuman-md"
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
        adapter = PortableAdapter(git_repo, slug)

        result = observe.observe_dispatch(
            adapter, workspace=git_repo, slug=slug, dispatch_id="child-1", writer_role="pm"
        )

        assert result.ok is False
        assert result.disabled is False
        journal = (_fleet_dir(git_repo, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "identity_unresolved"' in journal


class TestSuccessfulWrites:
    """Each write verb produces a validated entry retrievable via `observe status`."""

    def test_dispatch_writes_a_validated_spawned_entry(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug, local_id="child-session")

        result = observe.observe_dispatch(
            adapter, workspace=workspace, slug=slug, dispatch_id="child-session", writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id is not None
        status = observe.observe_status(workspace, slug)
        assert "succeeded" in status

    def test_relay_writes_a_validated_relayed_entry(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_relay(adapter, workspace=workspace, slug=slug, writer_role="pm")

        assert result.ok is True
        assert result.node_id is not None

    def test_handoff_emit_writes_awaiting_launch_and_embeds_id(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        draft = "Continue the chunk.\n"

        result = observe.observe_handoff_emit(
            adapter, workspace=workspace, slug=slug, prompt_text=draft, writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id is not None
        assert "FLEET-HANDOFF-ID:" in result.prompt_text
        assert draft.strip() in result.prompt_text

    def test_handoff_emit_delivered_id_line_equals_written_row_handoff_id(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """TC-8/W-FR-3: assert the delivered id *equals* the written row's id, not just presence."""
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        draft = "Continue the chunk.\n"

        result = observe.observe_handoff_emit(
            adapter, workspace=workspace, slug=slug, prompt_text=draft, writer_role="pm"
        )

        assert result.ok is True
        delivered_id = extract_handoff_id(result.prompt_text)
        assert delivered_id is not None
        _, _, _, local_id = parse_node_id(result.node_id)
        assert local_id == f"handoff-{delivered_id}"

    def test_handoff_emit_row_identity_matches_superhuman_md_not_passed_slug(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-8/W-FR-6: the row's project_id/slug are carried exactly from `SUPERHUMAN.md`.

        The file's own `**Slug:**` line deliberately differs from the
        `slug` argument used to *locate* the file (a rename in progress),
        and `**Project-id:**` is a value nothing else in this fixture could
        derive (no git remote, no cwd-basename match) — proving the row's
        identity is carried from the file, not re-derived.
        """
        located_slug = "demo-project"
        file_slug = "renamed-slug"
        project_id = "fleet-carried-id-999"
        profile = tmp_path / "profile.yaml"
        profile.write_text(
            "fleet:\n  enabled: true\n  observe_deadline_seconds: 5.0\n", encoding="utf-8"
        )
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
        project_dir = git_repo / "docs" / "superhuman" / located_slug
        project_dir.mkdir(parents=True)
        (project_dir / "SUPERHUMAN.md").write_text(
            f"**Slug:** {file_slug}\n**Project-id:** {project_id}\n", encoding="utf-8"
        )
        adapter = PortableAdapter(git_repo, located_slug)

        result = observe.observe_handoff_emit(
            adapter, workspace=git_repo, slug=located_slug, prompt_text="draft\n", writer_role="pm"
        )

        assert result.ok is True
        harness, _workspace_component, node_slug, _local_id = parse_node_id(result.node_id)
        assert harness == "handoff"
        assert node_slug == file_slug
        assert node_slug != located_slug

        fragment = read_fragment(result.node_id, project_dir / "fleet" / "sessions")
        assert fragment is not None
        assert fragment.project_id == project_id

    def test_launch_flips_row_to_active(self, enabled_project: tuple[Path, str]) -> None:
        """TC-11: id-anchored match, and idempotent on repeat."""
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        emitted = observe.observe_handoff_emit(
            adapter, workspace=workspace, slug=slug, prompt_text="draft\n", writer_role="pm"
        )
        assert emitted.ok is True

        result = observe.observe_launch(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text=emitted.prompt_text,
            writer_role="pm",
        )

        assert result.ok is True
        assert result.node_id == emitted.node_id

        # Idempotent: calling it again with the same prompt does not error
        # and does not double-flip or duplicate the event (TC-11).
        repeat = observe.observe_launch(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text=emitted.prompt_text,
            writer_role="pm",
        )
        assert repeat.ok is True
        assert repeat.node_id == emitted.node_id
        assert repeat.reason == "already_launched"

    def test_launch_fuzzy_match_unambiguous_flips_row_when_id_line_is_corrupted(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """TC-12: the id line is edited away/corrupted, but exactly one

        `awaiting-launch` row uniquely matches by `(cwd, branch)` — the row
        flips to active via the fuzzy path, not the id-anchored one.
        """
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        emitted = observe.observe_handoff_emit(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        assert emitted.ok is True
        assert "FLEET-HANDOFF-ID" in emitted.prompt_text

        # Simulate an operator-edited prompt: the id line is gone, but the
        # rest of the prompt (and thus the (cwd, branch) anchors) survives.
        corrupted_prompt = emitted.prompt_text.split("FLEET-HANDOFF-ID")[0]
        assert extract_handoff_id(corrupted_prompt) is None

        result = observe.observe_launch(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text=corrupted_prompt,
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )

        assert result.ok is True
        assert result.node_id == emitted.node_id
        assert result.reason == "launched"

    def test_launch_ambiguous_fuzzy_match_refuses_and_journals(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        observe.observe_handoff_emit(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft one\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        observe.observe_handoff_emit(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft two\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )

        result = observe.observe_launch(
            adapter, workspace=workspace, slug=slug, cwd=workspace, branch="trunk", writer_role="pm"
        )

        assert result.ok is False
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "ambiguous_fuzzy_match"' in journal


class TestFaultInjectionMatrix:
    """TC-3: each W-NFR-1 failure mode leaves the wrapped operation unchanged and exits soft."""

    def test_dispatch_lock_timeout_is_journaled_and_never_raises(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _raise_lock_timeout(*args: object, **kwargs: object) -> None:
            raise LockTimeoutError("simulated lock contention")

        monkeypatch.setattr("scripts.fleet.cli.register_session", _raise_lock_timeout)

        result = observe.observe_dispatch(
            adapter, workspace=workspace, slug=slug, dispatch_id="child-1", writer_role="pm"
        )

        assert result.ok is False
        assert result.disabled is False
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "lock_timeout"' in journal

    def test_relay_ownership_rejection_is_journaled_as_rejected(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _raise_ownership(*args: object, **kwargs: object) -> None:
            raise OwnershipError("simulated ownership rejection")

        monkeypatch.setattr("scripts.fleet.cli.register_session", _raise_ownership)

        result = observe.observe_relay(adapter, workspace=workspace, slug=slug, writer_role="pm")

        assert result.ok is False
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "rejected"' in journal

    def test_handoff_emit_survives_a_manifest_write_failure_deliverable_intact(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-9: the single most important fail-soft behavior in the design."""
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        draft = "the deliverable prompt\n"

        def _raise_lock_timeout(*args: object, **kwargs: object) -> None:
            raise LockTimeoutError("simulated lock contention")

        monkeypatch.setattr(observe, "handoff_emit_impl", _raise_lock_timeout)

        result = observe.observe_handoff_emit(
            adapter, workspace=workspace, slug=slug, prompt_text=draft, writer_role="pm"
        )

        assert result.ok is False
        assert result.prompt_text == draft
        assert "FLEET-HANDOFF-ID" not in result.prompt_text
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "lock_timeout"' in journal

    def test_launch_validation_rejection_is_journaled(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _raise_validation(*args: object, **kwargs: object) -> None:
            raise ValidationError("simulated validation rejection")

        monkeypatch.setattr(observe, "handoff_self_register_impl", _raise_validation)

        result = observe.observe_launch(
            adapter, workspace=workspace, slug=slug, handoff_id="does-not-matter", writer_role="pm"
        )

        assert result.ok is False
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "rejected"' in journal

    def test_adapter_error_is_backstopped_by_the_facades_own_catch(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mode 5: `collect_git_facts` raising (not just timing out) proves the
        façade's own catch, not an assumption about the adapter's contract."""
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _raise(*args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated adapter crash")

        monkeypatch.setattr(adapter, "git_facts", _raise)

        result = observe.observe_launch(
            adapter, workspace=workspace, slug=slug, cwd=None, branch=None, writer_role="pm"
        )

        assert result.ok is False
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "internal"' in journal

    def test_journal_write_failure_falls_back_to_exactly_one_stderr_line(
        self,
        enabled_project: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Mode 6: manifest write fails AND the journal itself is unwritable."""
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _raise_lock_timeout(*args: object, **kwargs: object) -> None:
            raise LockTimeoutError("simulated lock contention")

        monkeypatch.setattr("scripts.fleet.cli.register_session", _raise_lock_timeout)

        def _raise_mkdir(*args: object, **kwargs: object) -> None:
            raise OSError("simulated unwritable fleet dir")

        monkeypatch.setattr(Path, "mkdir", _raise_mkdir)

        result = observe.observe_dispatch(
            adapter, workspace=workspace, slug=slug, dispatch_id="child-1", writer_role="pm"
        )

        assert result.ok is False
        captured = capsys.readouterr()
        stderr_lines = [ln for ln in captured.err.splitlines() if ln.strip()]
        assert len(stderr_lines) == 1
        assert stderr_lines[0].startswith("fleet observe:")

    def test_deadline_exceeded_mid_call_is_journaled(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mode 9: a slow monkeypatched call exceeding the stage budget."""
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _slow(*args: object, **kwargs: object) -> None:
            time.sleep(6.0)

        monkeypatch.setattr("scripts.fleet.cli.register_session", _slow)

        start = time.monotonic()
        result = observe.observe_dispatch(
            adapter, workspace=workspace, slug=slug, dispatch_id="child-1", writer_role="pm"
        )
        elapsed = time.monotonic() - start

        assert result.ok is False
        assert elapsed < 5.5
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "deadline_exceeded"' in journal

    def test_session_identity_unresolved_from_adapter_is_journaled(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mode 8 (adjacent): the adapter's own identity-unresolved path."""
        from scripts.fleet.core.errors import SessionIdentityUnresolved

        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _raise_identity(*args: object, **kwargs: object) -> None:
            raise SessionIdentityUnresolved("simulated: no session id")

        monkeypatch.setattr("scripts.fleet.cli.register_session", _raise_identity)

        result = observe.observe_relay(adapter, workspace=workspace, slug=slug, writer_role="pm")

        assert result.ok is False
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "identity_unresolved"' in journal


class TestHandoffEmitCli:
    """TC-9 exercised through the CLI path (`--prompt-file` -> `--output-file`).

    The acceptance criterion is about the prompt *actually delivered*, and
    the file write itself lives in `cli._cmd_observe_handoff_emit`, not in
    `observe.observe_handoff_emit` — so the library-level assertions above
    do not, by themselves, cover this path.
    """

    def test_cli_handoff_emit_still_delivers_prompt_when_disabled(
        self, disabled_project: tuple[Path, str], tmp_path: Path
    ) -> None:
        workspace, slug = disabled_project
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("Please continue.\n", encoding="utf-8")
        output_file = tmp_path / "output.md"

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "handoff-emit",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--prompt-file",
                str(prompt_file),
                "--output-file",
                str(output_file),
            ]
        )

        assert args.func(args) == 0

        delivered = output_file.read_text(encoding="utf-8")
        assert delivered == "Please continue.\n"
        assert "FLEET-HANDOFF-ID" not in delivered
        assert not _fleet_dir(workspace, slug).exists()

    def test_cli_handoff_emit_still_delivers_prompt_when_manifest_write_fails(
        self, enabled_project: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("the deliverable prompt\n", encoding="utf-8")
        output_file = tmp_path / "output.md"

        def _raise_lock_timeout(*args: object, **kwargs: object) -> None:
            raise LockTimeoutError("simulated lock contention")

        monkeypatch.setattr(observe, "handoff_emit_impl", _raise_lock_timeout)

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "handoff-emit",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--prompt-file",
                str(prompt_file),
                "--output-file",
                str(output_file),
            ]
        )

        assert args.func(args) == 0

        delivered = output_file.read_text(encoding="utf-8")
        assert delivered == "the deliverable prompt\n"
        assert "FLEET-HANDOFF-ID" not in delivered
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "lock_timeout"' in journal


class TestMalformedHarnessSubagentJournaled:
    """Phase 3.3 preflight FIX 4: a malformed `--harness subagent` call (missing
    `--local-id`) must reach `observe-failures.log`, not just stderr — before
    this fix `cli._safe_build_adapter_for_observe` printed one stderr line and
    returned `None`, invisible to `fleet observe status` (W-FR-8, W-NFR-1).
    """

    def test_dispatch_with_missing_local_id_is_journaled(
        self, enabled_project: tuple[Path, str]
    ) -> None:
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
                "child-1",
                "--harness",
                "subagent",
                # deliberately omitted: --local-id
            ]
        )

        assert args.func(args) == 0

        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "adapter_construction_failed"' in journal
        assert '"event": "dispatch"' in journal

    def test_launch_with_missing_local_id_is_journaled(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "launch",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--handoff-id",
                "abc",
                "--harness",
                "subagent",
                # deliberately omitted: --local-id
            ]
        )

        assert args.func(args) == 0

        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "adapter_construction_failed"' in journal
        assert '"event": "launch"' in journal

    def test_dispatch_with_missing_local_id_writes_nothing_when_disabled(
        self, disabled_project: tuple[Path, str]
    ) -> None:
        """W-FR-7 must still hold at this earlier failure point: disabled
        means zero I/O, even for an otherwise-journal-worthy failure.
        """
        workspace, slug = disabled_project

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
                "child-1",
                "--harness",
                "subagent",
                # deliberately omitted: --local-id
            ]
        )

        assert args.func(args) == 0
        assert not _fleet_dir(workspace, slug).exists()


class TestUnreadablePromptFileNeverCrashes:
    """Phase 3.3 preflight FIX 5: `--prompt-file` reads in
    `_cmd_observe_handoff_emit` / `_cmd_observe_launch` had no exception
    handling — a missing/unreadable file crashed the process with a
    non-zero exit, violating the façade's "always exits 0" contract
    (Decision A / W-NFR-1). Both commands must survive this cleanly.
    """

    def test_handoff_emit_with_missing_prompt_file_exits_0_and_is_journaled(
        self, enabled_project: tuple[Path, str], tmp_path: Path
    ) -> None:
        workspace, slug = enabled_project
        missing_prompt_file = tmp_path / "does-not-exist.md"

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "handoff-emit",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--prompt-file",
                str(missing_prompt_file),
            ]
        )

        # Must not raise — this is the crash Phase 3.3 preflight FIX 5 caught.
        assert args.func(args) == 0

        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "prompt_file_unreadable"' in journal
        assert '"event": "handoff-emit"' in journal

    def test_handoff_emit_with_missing_prompt_file_writes_nothing_when_disabled(
        self, disabled_project: tuple[Path, str], tmp_path: Path
    ) -> None:
        workspace, slug = disabled_project
        missing_prompt_file = tmp_path / "does-not-exist.md"

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "handoff-emit",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--prompt-file",
                str(missing_prompt_file),
            ]
        )

        assert args.func(args) == 0
        assert not _fleet_dir(workspace, slug).exists()

    def test_launch_with_missing_prompt_file_exits_0_and_falls_back_to_fuzzy_match(
        self, enabled_project: tuple[Path, str], tmp_path: Path
    ) -> None:
        """A read failure on `launch`'s optional `--prompt-file` is not
        terminal: it degrades to "no prompt text supplied" and the fuzzy
        (cwd, branch) match still runs, same as omitting the flag entirely.
        """
        workspace, slug = enabled_project
        missing_prompt_file = tmp_path / "does-not-exist.md"

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "launch",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--prompt-file",
                str(missing_prompt_file),
            ]
        )

        # Must not raise, and must still attempt the fuzzy fallback (which,
        # with no matching awaiting-launch row, resolves to a journaled
        # "not_found" — not a crash).
        assert args.func(args) == 0

        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"event": "launch"' in journal


class TestAdditionalBranchCoverage:
    """Fills the remaining branches DESIGN's error-handling table names."""

    def test_pre_stage_deadline_check_fires_before_any_stage_starts(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "tiny-deadline"
        profile = tmp_path / "profile.yaml"
        profile.write_text(
            "fleet:\n  enabled: true\n  observe_deadline_seconds: 0.0000001\n", encoding="utf-8"
        )
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
        project_dir = git_repo / "docs" / "superhuman" / slug
        project_dir.mkdir(parents=True)
        (project_dir / "SUPERHUMAN.md").write_text(
            f"**Slug:** {slug}\n**Project-id:** fleet-tiny\n", encoding="utf-8"
        )
        adapter = PortableAdapter(git_repo, slug)

        result = observe.observe_dispatch(
            adapter, workspace=git_repo, slug=slug, dispatch_id="child-1", writer_role="pm"
        )

        assert result.ok is False
        journal = (_fleet_dir(git_repo, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "deadline_exceeded"' in journal

    def test_journal_appends_to_an_existing_file(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        fleet_dir = _fleet_dir(workspace, slug)
        observe._write_journal(
            fleet_dir, event="dispatch", error_class="lock_timeout", error_text="one",
            elapsed_ms=1.0,
        )
        observe._write_journal(
            fleet_dir, event="dispatch", error_class="lock_timeout", error_text="two",
            elapsed_ms=1.0,
        )

        lines = [
            ln
            for ln in (fleet_dir / "observe-failures.log").read_text(encoding="utf-8").splitlines()
            if ln
        ]
        assert len(lines) == 2

    def test_journal_bounds_its_tail(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        fleet_dir = _fleet_dir(workspace, slug)
        monkeypatch.setattr(observe, "_JOURNAL_MAX_LINES", 2)

        for i in range(4):
            observe._write_journal(
                fleet_dir, event="dispatch", error_class="lock_timeout", error_text=str(i),
                elapsed_ms=1.0,
            )

        lines = [
            ln
            for ln in (fleet_dir / "observe-failures.log").read_text(encoding="utf-8").splitlines()
            if ln
        ]
        assert len(lines) == 2
        assert json.loads(lines[-1])["error_text"] == "3"

    def test_handoff_emit_journals_identity_unresolved(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "no-superhuman-md"
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
        adapter = PortableAdapter(git_repo, slug)

        result = observe.observe_handoff_emit(
            adapter, workspace=git_repo, slug=slug, prompt_text="draft\n", writer_role="pm"
        )

        assert result.ok is False
        assert result.prompt_text == "draft\n"
        journal = (_fleet_dir(git_repo, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "identity_unresolved"' in journal

    def test_launch_derives_cwd_and_branch_from_the_adapter_when_omitted(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        facts = adapter.git_facts()
        # Emit against the SAME source of truth the fuzzy fallback derives
        # from (`adapter.git_facts()`), so the match is deterministic
        # regardless of how the platform spells `workspace` vs. git's own
        # `--show-toplevel` output (case/short-path differences on Windows).
        emitted = observe.observe_handoff_emit(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=facts.toplevel,
            branch=facts.branch,
            writer_role="pm",
        )
        assert emitted.ok is True

        # No handoff_id, prompt_text, cwd, or branch given at all — must fall
        # back to adapter.git_facts() for both fuzzy-match anchors.
        result = observe.observe_launch(adapter, workspace=workspace, slug=slug, writer_role="pm")

        assert result.ok is True
        assert result.node_id == emitted.node_id

    def test_launch_derives_only_the_missing_anchor_when_one_is_given(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        facts = adapter.git_facts()
        emitted = observe.observe_handoff_emit(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=facts.toplevel,
            branch=facts.branch,
            writer_role="pm",
        )
        assert emitted.ok is True

        # cwd given explicitly; branch left to the adapter fallback.
        result = observe.observe_launch(
            adapter, workspace=workspace, slug=slug, cwd=facts.toplevel, writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id == emitted.node_id

    def test_launch_derives_only_cwd_when_branch_is_given(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        facts = adapter.git_facts()
        emitted = observe.observe_handoff_emit(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=facts.toplevel,
            branch=facts.branch,
            writer_role="pm",
        )
        assert emitted.ok is True

        # branch given explicitly; cwd left to the adapter fallback.
        result = observe.observe_launch(
            adapter, workspace=workspace, slug=slug, branch=facts.branch, writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id == emitted.node_id

    def test_launch_not_found_is_journaled(self, enabled_project: tuple[Path, str]) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_launch(
            adapter,
            workspace=workspace,
            slug=slug,
            cwd=workspace,
            branch="no-such-branch-anywhere",
            writer_role="pm",
        )

        assert result.ok is False
        assert result.reason == "not_found"
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "not_found"' in journal

    def test_launch_journals_identity_unresolved(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "no-superhuman-md"
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
        adapter = PortableAdapter(git_repo, slug)

        result = observe.observe_launch(
            adapter, workspace=git_repo, slug=slug, handoff_id="whatever", writer_role="pm"
        )

        assert result.ok is False
        journal = (_fleet_dir(git_repo, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "identity_unresolved"' in journal

    def test_status_swallows_a_journal_read_failure(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        fleet_dir = _fleet_dir(workspace, slug)
        fleet_dir.mkdir(parents=True, exist_ok=True)
        (fleet_dir / "observe-failures.log").write_text('{"a": 1}\n', encoding="utf-8")

        real_read_text = Path.read_text

        def _flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if self.name == "observe-failures.log":
                raise OSError("simulated read failure")
            return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", _flaky_read_text)

        report = observe.observe_status(workspace, slug)

        assert "zero writes recorded" in report

    def test_status_treats_a_blank_journal_file_as_no_failure(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        fleet_dir = _fleet_dir(workspace, slug)
        fleet_dir.mkdir(parents=True, exist_ok=True)
        (fleet_dir / "observe-failures.log").write_text("\n   \n", encoding="utf-8")

        report = observe.observe_status(workspace, slug)

        assert "zero writes recorded" in report

    def test_status_with_no_identity_reports_zero_writes(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "no-superhuman-md"
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        report = observe.observe_status(git_repo, slug)

        assert "zero writes recorded" in report

    def test_status_swallows_a_list_sessions_failure(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project

        def _raise(*args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated corrupt fragment store")

        monkeypatch.setattr("scripts.fleet.core.query.list_sessions", _raise)

        report = observe.observe_status(workspace, slug)

        assert "zero writes recorded" in report


class TestWallClockBudget:
    """TC-4: the wall-clock ceiling holds against a wedged manifest write."""

    def test_dispatch_bounded_against_a_lock_that_blocks_indefinitely(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _wedged(*args: object, **kwargs: object) -> None:
            time.sleep(999)

        monkeypatch.setattr("scripts.fleet.cli.register_session", _wedged)

        start = time.monotonic()
        result = observe.observe_dispatch(
            adapter, workspace=workspace, slug=slug, dispatch_id="child-1", writer_role="pm"
        )
        elapsed = time.monotonic() - start

        assert elapsed < 5.5
        assert result.ok is False

    def test_handoff_emit_bounded_against_a_wedged_write_and_still_delivers(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        draft = "draft text\n"

        def _wedged(*args: object, **kwargs: object) -> None:
            time.sleep(999)

        monkeypatch.setattr(observe, "handoff_emit_impl", _wedged)

        start = time.monotonic()
        result = observe.observe_handoff_emit(
            adapter, workspace=workspace, slug=slug, prompt_text=draft, writer_role="pm"
        )
        elapsed = time.monotonic() - start

        assert elapsed < 5.5
        assert result.prompt_text == draft


class TestPassthroughDefaultsUnchanged:
    """TC-5: existing callers' default timeouts must be byte-identical to pre-wiring."""

    def test_register_session_omits_timeout_kwarg_by_default(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        from scripts.fleet.cli import register_session
        from scripts.fleet.core import events as core_events

        captured: dict[str, object] = {}
        real_append = core_events.append

        def _spy_append(log_path, event, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return real_append(log_path, event, **kwargs)

        monkeypatch.setattr("scripts.fleet.cli.append", _spy_append)

        adapter = PortableAdapter(workspace, slug)
        fleet_dir = _fleet_dir(workspace, slug)
        register_session(
            adapter,
            origination="manual",
            project_id="proj-1",
            writer_role="pm",
            log_path=fleet_dir / "events.jsonl",
            sessions_dir=fleet_dir / "sessions",
        )

        assert "timeout" not in captured

    def test_handoff_emit_omits_timeout_kwarg_by_default(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        from scripts.fleet import handoff as handoff_module
        from scripts.fleet.core import events as core_events

        captured: dict[str, object] = {}
        real_append = core_events.append

        def _spy_append(log_path, event, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return real_append(log_path, event, **kwargs)

        monkeypatch.setattr("scripts.fleet.handoff.append", _spy_append)

        adapter = PortableAdapter(workspace, slug)
        fleet_dir = _fleet_dir(workspace, slug)
        handoff_module.emit(
            adapter,
            slug=slug,
            project_id="proj-1",
            prompt_text="draft\n",
            cwd=workspace,
            writer_role="pm",
            log_path=fleet_dir / "events.jsonl",
            sessions_dir=fleet_dir / "sessions",
        )

        assert "timeout" not in captured

    def test_collect_git_facts_uses_30s_default_when_git_timeout_omitted(self) -> None:
        from scripts.fleet.adapter.portable import _GIT_TIMEOUT_SECONDS

        assert _GIT_TIMEOUT_SECONDS == 30

    def test_portable_adapter_git_facts_omits_override_by_default(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.adapter import portable as portable_module

        captured: dict[str, object] = {}
        real_collect = portable_module.collect_git_facts

        def _spy_collect(cwd, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return real_collect(cwd, **kwargs)

        monkeypatch.setattr("scripts.fleet.adapter.portable.collect_git_facts", _spy_collect)

        adapter = PortableAdapter(git_repo, "demo")
        adapter.git_facts()

        assert "git_timeout" not in captured


class TestRelayedPathSessionRelayPresence:
    """TC-22 (fleet-wiring Chunk 6, W-FR-2/W-NFR-3): `observe_relay` with
    `session-relay` present vs absent, exercised through `ClaudeAdapter` —
    the one adapter that actually knows about `session-relay` (see its
    module docstring). `TestSuccessfulWrites.test_relay_writes_a_validated_
    relayed_entry` already proves the happy path through `PortableAdapter`;
    this class adds the harness where `session-relay` concretely matters.
    """

    def test_relay_happy_path_with_session_relay_present(
        self, enabled_project: tuple[Path, str], tmp_path: Path
    ) -> None:
        """TC-22(a): `session-relay` importable/present -> a validated
        `relayed` entry, driven through the adapter `session-relay` itself
        would construct (a real `current_session_id`, plus a configured
        `session_relay_script` standing in for `session-relay`'s
        `session_scan.py`)."""
        workspace, slug = enabled_project
        script = tmp_path / "fake_session_scan.py"
        script.write_text("import sys\nprint('{\"sessions\": []}')\n", encoding="utf-8")
        adapter = ClaudeAdapter(
            workspace,
            slug,
            current_session_id="relay-session-1",
            session_relay_script=script,
        )

        result = observe.observe_relay(adapter, workspace=workspace, slug=slug, writer_role="pm")

        assert result.ok is True
        assert result.node_id is not None
        fragment = read_fragment(result.node_id, _fleet_dir(workspace, slug) / "sessions")
        assert fragment is not None

    def test_relay_degrades_to_journaled_failure_with_session_relay_absent(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-22(b): `session-relay` absent, simulated two ways at once —
        (1) `sys.modules` blocked for `session_relay`, following the same
        proof-of-independence idiom `test_adapter_portable.py`'s
        `TestPortableAdapterDegradesWithoutSessionRelay` already uses for
        `PortableAdapter`/NFR-3; and (2) the concrete, harness-real analogue
        for the *relayed* path specifically: a `ClaudeAdapter` built exactly
        as it would be if `session-relay` were never installed to supply
        `--session-id` in the first place — no `current_session_id`, no
        `session_relay_script`.

        FLAGGED FOR PM (see Chunk 6 dispatch report, not silently decided
        here): TEST.md's TC-22(b) wording is "nothing raises, nothing is
        written, no error surfaces -- inert, not broken", stronger than
        Decision A's general fail-soft contract (journal-and-proceed).
        `observe_relay` has no branch specific to "session-relay absent" --
        an unresolvable session identity (this scenario) is handled exactly
        like every other façade failure mode, per the pre-existing,
        already-merged `test_session_identity_unresolved_from_adapter_is_
        journaled` above: caught by the broad catch, classified
        `identity_unresolved`, and JOURNALED (Loudness tier 1) -- not fully
        inert. The only truly zero-I/O path in this module today is the
        *disabled*-workspace one (`TestDisabledWorkspace`). This test
        therefore asserts the actual, implemented, already-established
        contract (never raises; no error surfaces to the caller; nothing is
        added to the sessions manifest; the diagnostic journal, which is
        explicitly not the manifest, does get a line) rather than silently
        forcing a behavior change to match the stronger "inert" wording.
        """
        monkeypatch.setitem(sys.modules, "session_relay", None)
        for name in list(sys.modules):
            if "session_relay" in name and name != "session_relay":
                monkeypatch.delitem(sys.modules, name, raising=False)

        workspace, slug = enabled_project
        adapter = ClaudeAdapter(workspace, slug)  # no current_session_id, no session_relay_script

        result = observe.observe_relay(adapter, workspace=workspace, slug=slug, writer_role="pm")

        assert result.ok is False
        assert result.reason is not None  # never raises; the failure is returned, not surfaced
        sessions_dir = _fleet_dir(workspace, slug) / "sessions"
        assert not sessions_dir.exists() or not any(sessions_dir.iterdir())
        journal = (_fleet_dir(workspace, slug) / "observe-failures.log").read_text(encoding="utf-8")
        assert '"error_class": "identity_unresolved"' in journal


class TestObserveStatus:
    """TC-6: `observe status` reports four distinct, documented states."""

    def test_not_configured(self, disabled_project: tuple[Path, str]) -> None:
        workspace, slug = disabled_project

        report = observe.observe_status(workspace, slug)

        assert report.startswith("not configured:")

    def test_configured_and_enabled_zero_writes(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project

        report = observe.observe_status(workspace, slug)

        assert "zero writes recorded" in report

    def test_configured_and_enabled_successful_write(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)
        observe.observe_relay(adapter, workspace=workspace, slug=slug, writer_role="pm")

        report = observe.observe_status(workspace, slug)

        assert "succeeded" in report

    def test_configured_but_last_write_failed(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _raise_lock_timeout(*args: object, **kwargs: object) -> None:
            raise LockTimeoutError("simulated")

        monkeypatch.setattr("scripts.fleet.cli.register_session", _raise_lock_timeout)
        observe.observe_relay(adapter, workspace=workspace, slug=slug, writer_role="pm")

        report = observe.observe_status(workspace, slug)

        assert "failed" in report

    def test_status_reports_success_after_a_later_successful_write(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 3.3 preflight FIX 1 regression.

        `observe_status` previously derived its verdict from "was ANY
        failure ever journaled" with no timestamp comparison, so a single,
        long-recovered failure pinned status to "failed" forever. Journal a
        failure, then perform a successful write, and assert status flips
        back to "succeeded" — the exact scenario the finding describes.
        """
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        def _raise_lock_timeout(*args: object, **kwargs: object) -> None:
            raise LockTimeoutError("simulated")

        real_register_session = fleet_cli.register_session
        monkeypatch.setattr(fleet_cli, "register_session", _raise_lock_timeout)
        failed_result = observe.observe_relay(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        )
        assert failed_result.ok is False
        assert "failed" in observe.observe_status(workspace, slug)

        # Restore the real implementation (not `monkeypatch.undo()`, which
        # would also revert `enabled_project`'s shared SUPERHUMAN_PROFILE
        # env var and re-disable fleet for this workspace).
        monkeypatch.setattr(fleet_cli, "register_session", real_register_session)
        succeeded_result = observe.observe_relay(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        )
        assert succeeded_result.ok is True

        report = observe.observe_status(workspace, slug)

        assert "succeeded" in report
        assert "failed" not in report


class TestObserveStatusSlugValidation:
    """Phase 3.3 preflight FIX 4: a path-traversal-shaped slug is rejected."""

    def test_dispatch_with_traversal_slug_is_disabled_not_a_path_escape(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "../../escape"
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
        adapter = PortableAdapter(git_repo, slug)

        result = observe.observe_dispatch(
            adapter, workspace=git_repo, slug=slug, dispatch_id="child-1", writer_role="pm"
        )

        assert result.disabled is True
        assert result.ok is False
        assert not (git_repo / "escape").exists()
        assert not (git_repo / "docs").exists()

    def test_status_with_traversal_slug_reports_not_configured(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "../../escape"
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        report = observe.observe_status(git_repo, slug)

        assert report.startswith("not configured:")
        assert not (git_repo / "escape").exists()
        assert not (git_repo / "docs").exists()

    def test_journal_early_cli_failure_with_traversal_slug_writes_nothing(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "../../escape"
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        observe.journal_early_cli_failure(
            git_repo,
            slug,
            event="dispatch",
            error_class="adapter_construction_failed",
            error_text="boom",
        )

        assert not (git_repo / "escape").exists()
        assert not (git_repo / "docs").exists()


class TestCoreUntouched:
    """TC-7: `scripts/fleet/core/*` is byte-unchanged and imports no harness module.

    The baseline is a committed CONTENT MANIFEST of per-file SHA-256 digests
    (`scripts/fleet/core-manifest.json`), not a git revision (roadmap #194).
    A revision pin rotted the moment history was rewritten (#184) and needed
    full history in CI; a content manifest is immune to rewrites, rebases,
    squash-merges and repo re-homing, works in a shallow clone, and makes a
    deliberate re-sync a readable diff instead of a one-character SHA bump.

    Regenerate with `python scripts/regen_core_manifest.py`, in the SAME commit
    as the core change it describes.
    """

    def test_core_matches_the_content_manifest(self) -> None:
        core_dir = regen.CORE_DIR
        manifest_path = regen.MANIFEST_PATH

        # Fail CLOSED on an absent or unreadable manifest. A guard whose
        # baseline can go missing and take the check with it is the failure
        # mode this replaces, not an improvement on it.
        assert manifest_path.is_file(), (
            f"vendored-core manifest missing at {manifest_path}. This is the "
            "TC-7 baseline; regenerate it with "
            "`python scripts/regen_core_manifest.py` only when the core is "
            "deliberately re-synced."
        )
        recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(recorded, dict) and recorded, (
            f"vendored-core manifest at {manifest_path} is empty or not a "
            "mapping; it cannot pin anything."
        )

        actual = regen.build_manifest(core_dir)

        # Compared as WHOLE SETS first, so a file added to or deleted from the
        # core is caught. Digest-only comparison over the intersection would
        # let an added file pass unpinned -- the same scoped-predicate defect
        # class Phase 1's preflight kept finding.
        assert set(actual) == set(recorded), (
            "vendored core file set differs from the manifest.\n"
            f"  on disk, not in manifest: {sorted(set(actual) - set(recorded))}\n"
            f"  in manifest, not on disk: {sorted(set(recorded) - set(actual))}"
        )

        changed = sorted(name for name in actual if actual[name] != recorded[name])
        assert not changed, (
            f"scripts/fleet/core/ has been modified against the manifest: {changed}. "
            "If the change is deliberate, regenerate the manifest in the same "
            "commit: `python scripts/regen_core_manifest.py`."
        )

    def test_no_harness_native_import_in_core(self) -> None:
        skill_root = Path(__file__).resolve().parents[2]
        core_dir = skill_root / "scripts" / "fleet" / "core"
        banned = ("import claude", "import anthropic")
        for path in core_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in banned:
                assert token not in text, f"{path} contains {token!r}"
