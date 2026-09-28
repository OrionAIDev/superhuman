"""Every fleet verb resolves its manifest directory the same way.

`fleet owner claim|stand-down|show` (increment O) and the `observe` verbs
honour the profile's `fleet.manifest_dir` override; the older write/read
verbs (`register`, `handoff emit|cancel|stale|self-register`, `done
advance`, `query edges`, `status`, `gen-view`) used to resolve only
`--fleet-dir or <workspace>/docs/superhuman/<slug>/fleet`, ignoring it.
With an override set, a relayed `pm` registered through `fleet register`
landed in a different log from the one `owner claim` reads, so the OQ-1
legacy-owner coordination rule found no prior owner and let a second
session claim the project uncontested.

The order every verb now follows: `--fleet-dir` (where the verb has one),
then `fleet.manifest_dir`, then the slug-validated default. The slug is
validated whenever `--fleet-dir` is not given, override or not (the owner
verbs' M6 rule). The `observe` verbs stay fail-soft (Decision A) on the
same inputs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts.fleet import cli as fleet_cli
from scripts.fleet import observe as fleet_observe
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.core.events import read_all

SLUG = "demo-slug"
PROJECT_ID = "proj-manifest-dir"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A git working tree with a resolvable `SUPERHUMAN.md` identity."""
    repo = tmp_path / "ws"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    project_dir = repo / "docs" / "superhuman" / SLUG
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(
        f"**Slug:** {SLUG}\n**Project-id:** {PROJECT_ID}\n", encoding="utf-8"
    )
    return repo


@pytest.fixture
def override_dir(workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the profile's `fleet.manifest_dir` at `<workspace>/shared-fleet`."""
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "fleet:\n  enabled: true\n  manifest_dir: shared-fleet\n", encoding="utf-8"
    )
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
    return (workspace / "shared-fleet").resolve()


@pytest.fixture
def no_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fleet enabled with no `manifest_dir`, so the default directory applies."""
    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))


def _default_dir(workspace: Path) -> Path:
    return workspace / "docs" / "superhuman" / SLUG / "fleet"


def _events(fleet_dir: Path) -> list[Any]:
    log_path = fleet_dir / "events.jsonl"
    return read_all(log_path) if log_path.exists() else []


def _register_argv(
    workspace: Path, local_id: str, *, slug: str = SLUG, writer_role: str = "developer",
    origination: str = "manual", fleet_dir: Path | None = None,
) -> list[str]:
    argv = [
        "register", "--project-id", PROJECT_ID, "--slug", slug,
        "--workspace", str(workspace), "--harness", "portable", "--local-id", local_id,
        "--origination", origination, "--writer-role", writer_role,
    ]
    if fleet_dir is not None:
        argv += ["--fleet-dir", str(fleet_dir)]
    return argv


def _emit_argv(
    workspace: Path, prompt_file: Path, *, slug: str = SLUG, fleet_dir: Path | None = None
) -> list[str]:
    argv = [
        "handoff", "emit", "--project-id", PROJECT_ID, "--slug", slug,
        "--workspace", str(workspace), "--branch", "feature/x",
        "--prompt-file", str(prompt_file), "--writer-role", "Project Manager",
    ]
    if fleet_dir is not None:
        argv += ["--fleet-dir", str(fleet_dir)]
    return argv


def _emit_into(workspace: Path, fleet_dir: Path) -> tuple[str, str]:
    """Emit a handoff straight into `fleet_dir`; return `(node_id, handoff_id)`."""
    prompt_file = workspace.parent / "prompt.md"
    prompt_file.write_text("Please continue.", encoding="utf-8")
    assert fleet_cli.main(_emit_argv(workspace, prompt_file, fleet_dir=fleet_dir)) == 0
    emitted = _events(fleet_dir)[0]
    return emitted.node_id, emitted.payload["handoff_id"]


class TestLegacyVerbsHonourManifestDirOverride:
    """Each older verb, with no `--fleet-dir`, reads/writes `fleet.manifest_dir`."""

    def test_register_writes_into_the_override(
        self, workspace: Path, override_dir: Path
    ) -> None:
        assert fleet_cli.main(_register_argv(workspace, "sess-a")) == 0

        assert [e.type for e in _events(override_dir)] == ["session_registered"]
        assert not _default_dir(workspace).exists()

    def test_handoff_emit_writes_into_the_override(
        self, workspace: Path, override_dir: Path
    ) -> None:
        prompt_file = workspace.parent / "prompt.md"
        prompt_file.write_text("Please continue.", encoding="utf-8")

        assert fleet_cli.main(_emit_argv(workspace, prompt_file)) == 0

        assert [e.type for e in _events(override_dir)] == ["handoff_emitted"]
        assert not _default_dir(workspace).exists()

    def test_handoff_cancel_finds_a_handoff_emitted_into_the_override(
        self, workspace: Path, override_dir: Path
    ) -> None:
        node_id, _handoff_id = _emit_into(workspace, override_dir)

        code = fleet_cli.main(
            [
                "handoff", "cancel", "--node-id", node_id, "--project-id", PROJECT_ID,
                "--writer-role", "Project Manager", "--workspace", str(workspace),
                "--slug", SLUG,
            ]
        )

        assert code == 0
        assert "handoff_cancelled" in [e.type for e in _events(override_dir)]

    def test_handoff_stale_lists_a_handoff_emitted_into_the_override(
        self, workspace: Path, override_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        node_id, _handoff_id = _emit_into(workspace, override_dir)
        capsys.readouterr()

        code = fleet_cli.main(
            [
                "handoff", "stale", "--workspace", str(workspace), "--slug", SLUG,
                "--expiry-seconds", "0",
            ]
        )

        assert code == 0
        assert node_id in capsys.readouterr().out

    def test_handoff_self_register_finds_a_handoff_emitted_into_the_override(
        self, workspace: Path, override_dir: Path
    ) -> None:
        _node_id, handoff_id = _emit_into(workspace, override_dir)

        code = fleet_cli.main(
            [
                "handoff", "self-register", "--workspace", str(workspace), "--slug", SLUG,
                "--handoff-id", handoff_id, "--writer-role", "session",
            ]
        )

        assert code == 0
        assert "handoff_launched" in [e.type for e in _events(override_dir)]

    def test_done_advance_writes_into_the_override(
        self, workspace: Path, override_dir: Path
    ) -> None:
        evidence = workspace.parent / "evidence.json"
        evidence.write_text(json.dumps({"commit": "abc123"}), encoding="utf-8")

        code = fleet_cli.main(
            [
                "done", "advance", "--node-id", "portable/ws/demo-slug/sess-a",
                "--target-level", "D1-merged", "--project-id", PROJECT_ID,
                "--slug", SLUG, "--workspace", str(workspace),
                "--writer-role", "Project Manager", "--evidence-json", str(evidence),
                "--ceiling", "D1-merged",
            ]
        )

        assert code == 0
        assert [e.type for e in _events(override_dir)] == ["done_level_advanced"]
        assert not _default_dir(workspace).exists()

    def test_query_edges_reads_the_override(
        self, workspace: Path, override_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With no edges in either log the printed output is identical, so
        # observe which log the verb actually opens.
        opened: list[Path] = []

        class _EmptyGraph:
            edges: list[Any] = []

        def _spy(log_path: Path) -> _EmptyGraph:
            opened.append(log_path)
            return _EmptyGraph()

        monkeypatch.setattr(fleet_cli, "resolve_graph", _spy)

        code = fleet_cli.main(["query", "edges", "--workspace", str(workspace), "--slug", SLUG])

        assert code == 0
        assert opened == [override_dir / "events.jsonl"]

    def test_status_reads_the_override(
        self, workspace: Path, override_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert fleet_cli.main(_register_argv(workspace, "sess-a", fleet_dir=override_dir)) == 0
        capsys.readouterr()

        code = fleet_cli.main(
            ["status", "--workspace", str(workspace), "--slug", SLUG, "--project-id", PROJECT_ID]
        )

        assert code == 0
        assert "sess-a" in capsys.readouterr().out

    def test_gen_view_reads_the_override(
        self, workspace: Path, override_dir: Path
    ) -> None:
        assert fleet_cli.main(_register_argv(workspace, "sess-a", fleet_dir=override_dir)) == 0

        code = fleet_cli.main(
            ["gen-view", "--workspace", str(workspace), "--slug", SLUG, "--project-id", PROJECT_ID]
        )

        assert code == 0
        fleet_md = workspace / "docs" / "superhuman" / SLUG / "FLEET.md"
        assert "sess-a" in fleet_md.read_text(encoding="utf-8")


class TestExplicitFleetDirStillWins:
    def test_fleet_dir_flag_beats_the_manifest_dir_override(
        self, workspace: Path, override_dir: Path, tmp_path: Path
    ) -> None:
        explicit = tmp_path / "explicit-fleet"

        assert fleet_cli.main(_register_argv(workspace, "sess-a", fleet_dir=explicit)) == 0

        assert [e.type for e in _events(explicit)] == ["session_registered"]
        assert not override_dir.exists()


def _verb_argvs(workspace: Path, slug: str) -> dict[str, list[str]]:
    """One minimal, parseable invocation of every older verb, without `--fleet-dir`."""
    prompt_file = workspace.parent / "prompt.md"
    prompt_file.write_text("Please continue.", encoding="utf-8")
    common = ["--workspace", str(workspace), "--slug", slug]
    argvs = {
        "register": _register_argv(workspace, "sess-a", slug=slug),
        "handoff emit": _emit_argv(workspace, prompt_file, slug=slug),
        "handoff cancel": [
            "handoff", "cancel", "--node-id", "portable/ws/x/sess-a", "--project-id",
            PROJECT_ID, "--writer-role", "Project Manager", *common,
        ],
        "handoff stale": ["handoff", "stale", *common],
        "handoff self-register": [
            "handoff", "self-register", *common, "--handoff-id", "h-1",
            "--writer-role", "session",
        ],
        "done advance": [
            "done", "advance", "--node-id", "portable/ws/x/sess-a", "--target-level",
            "D1-merged", "--project-id", PROJECT_ID, "--writer-role", "Project Manager",
            "--ceiling", "D1-merged", *common,
        ],
        "query edges": ["query", "edges", *common],
        "status": ["status", *common, "--project-id", PROJECT_ID],
        "gen-view": ["gen-view", *common, "--project-id", PROJECT_ID],
    }
    assert list(argvs) == _VERBS
    return argvs


_VERBS = [
    "register", "handoff emit", "handoff cancel", "handoff stale", "handoff self-register",
    "done advance", "query edges", "status", "gen-view",
]


class TestLegacyVerbsRejectAnUnsafeSlug:
    """Without `--fleet-dir`, an unsafe slug is refused before any path is built."""

    @pytest.mark.usefixtures("no_override")
    @pytest.mark.parametrize("verb", _VERBS)
    def test_unsafe_slug_is_rejected_on_the_default_path(
        self, verb: str, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = _verb_argvs(workspace, "../escape")[verb]

        code = fleet_cli.main(argv)

        err = capsys.readouterr().err
        assert code == 1
        assert f"fleet {verb}: rejected: invalid slug '../escape'" in err
        assert "Traceback" not in err
        assert not (workspace / "docs" / "superhuman" / "escape").exists()
        assert not (workspace / "docs" / "escape").exists()

    @pytest.mark.parametrize("verb", _VERBS)
    def test_unsafe_slug_is_rejected_even_with_a_manifest_dir_override(
        self, verb: str, workspace: Path, override_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        argv = _verb_argvs(workspace, "../escape")[verb]

        code = fleet_cli.main(argv)

        assert code == 1
        assert f"fleet {verb}: rejected: invalid slug '../escape'" in capsys.readouterr().err
        assert _events(override_dir) == []


class TestRegisterThenOwnerClaimShareOneLog:
    """The reported defect end to end: a relayed `pm` registered through
    `fleet register` must be the legacy prior owner `fleet owner claim` sees."""

    def test_claim_requires_coordination_with_a_pm_registered_via_the_cli(
        self, workspace: Path, override_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert fleet_cli.main(
            _register_argv(workspace, "legacy-pm", writer_role="pm", origination="relayed")
        ) == 0
        assert fleet_cli.main(_register_argv(workspace, "claimant-a")) == 0

        code = fleet_cli.main(
            [
                "owner", "claim", "--workspace", str(workspace), "--slug", SLUG,
                "--harness", "portable", "--local-id", "claimant-a",
            ]
        )

        assert code == fleet_cli._OWNER_EXIT_COORDINATION_REQUIRED, capsys.readouterr().err
        assert not any(e.type == "ownership_declared" for e in _events(override_dir))


def _observe_calls(workspace: Path, slug: str) -> dict[str, Callable[[], Any]]:
    adapter = PortableAdapter(workspace, SLUG, local_id="sess-a")
    return {
        "relay": lambda: fleet_observe.observe_relay(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        ),
        "dispatch": lambda: fleet_observe.observe_dispatch(
            adapter, workspace=workspace, slug=slug, dispatch_id="d-1", writer_role="pm"
        ),
        "handoff-emit": lambda: fleet_observe.observe_handoff_emit(
            adapter, workspace=workspace, slug=slug, prompt_text="Please continue.",
            cwd=str(workspace), branch="feature/x", writer_role="pm",
        ),
        "launch": lambda: fleet_observe.observe_launch(
            adapter, workspace=workspace, slug=slug, handoff_id="h-1", writer_role="session"
        ),
        "session-start": lambda: fleet_observe.observe_session_start(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        ),
    }


_OBSERVE_EVENTS = ["relay", "dispatch", "handoff-emit", "launch", "session-start"]


class TestObserveVerbsWithAManifestDirOverride:
    """Decision A: the observe façade never raises, and its failure journal
    lives in the same directory `observe status` reads."""

    @pytest.mark.parametrize("event", _OBSERVE_EVENTS)
    def test_unsafe_slug_with_an_override_is_a_quiet_disabled_result(
        self, event: str, workspace: Path, override_dir: Path
    ) -> None:
        result = _observe_calls(workspace, "../escape")[event]()

        assert result.ok is False
        assert result.disabled is True
        assert "invalid slug" in result.reason
        assert not override_dir.exists()

    @pytest.mark.parametrize("event", _OBSERVE_EVENTS)
    def test_identity_unresolved_is_journaled_into_the_override(
        self, event: str, workspace: Path, override_dir: Path
    ) -> None:
        (workspace / "docs" / "superhuman" / SLUG / "SUPERHUMAN.md").unlink()

        result = _observe_calls(workspace, SLUG)[event]()

        assert result.ok is False
        journal = override_dir / "observe-failures.log"
        assert "identity_unresolved" in journal.read_text(encoding="utf-8")
        assert not (_default_dir(workspace) / "observe-failures.log").exists()
        assert "identity_unresolved" in fleet_observe.observe_status(workspace, SLUG)
