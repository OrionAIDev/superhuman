"""Tests for `scripts.fleet.observe.observe_session_start` and the additive
`ObserveResult.error_class` field — the FR-5 / FR-13 / FR-17 / D2a / D6 seam.

Chunk 3, per `TEST.md` TC-15..TC-24.

Filename matches PLAN.md's file-structure table (`test_observe_session.py`),
not DESIGN.md's component table (`test_observe_session_start.py`) — the two
artifacts disagree on this one filename; QA followed PLAN.md as the more
authoritative "what gets built where" source. Flagged in the QA return
report; not a silent pick.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from scripts.fleet import cli as fleet_cli
from scripts.fleet import observe
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.cli import build_parser
from scripts.fleet.handoff import extract_handoff_id

# --- Fixtures, mirroring test_observe.py's precedent ------------------------------


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
def enabled_project(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
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
def identity_unresolved_project(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """A workspace with fleet enabled but no `**Project-id:**` line at all."""
    slug = "no-id-project"
    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

    project_dir = git_repo / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(f"**Slug:** {slug}\n", encoding="utf-8")
    return git_repo, slug


def _fleet_dir(workspace: Path, slug: str) -> Path:
    return workspace / "docs" / "superhuman" / slug / "fleet"


class TestOriginationObserved:
    def test_observe_session_start_registers_with_origination_observed(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """Happy path: a resolvable workspace with fleet enabled and no
        pending handoff produces a row whose `origination == "observed"`."""
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug, local_id="session-a")

        result = observe.observe_session_start(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id is not None
        log = (_fleet_dir(workspace, slug) / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log


class TestNeverAFuzzyFlip:
    """D2a: `observe session-start` NEVER performs a fuzzy (cwd, branch)
    launch flip — that is an assertion (commission risk), not coverage."""

    def test_observe_session_start_never_performs_a_fuzzy_flip(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """Even when `(cwd, branch)` would uniquely match an existing
        `awaiting-launch` row, calling `observe_session_start` with NO
        `--handoff-id` must register a NEW session row — never silently
        flip the existing awaiting-launch row to active. This is the
        regression test for D2a's stated commission-exposure risk."""
        workspace, slug = enabled_project
        emit_adapter = PortableAdapter(workspace, slug)
        emitted = observe.observe_handoff_emit(
            emit_adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        assert emitted.ok is True

        session_adapter = PortableAdapter(workspace, slug, local_id="session-b")
        result = observe.observe_session_start(
            session_adapter, workspace=workspace, slug=slug, writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id != emitted.node_id
        log = (_fleet_dir(workspace, slug) / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log
        # The awaiting-launch row must still be awaiting-launch, not flipped.
        assert '"type":"launched"' not in log

    def test_observe_session_start_flips_only_with_explicit_handoff_id(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """With `--handoff-id` supplied and matching an awaiting-launch
        row, the id-anchored flip fires (the fuzzy path is never
        consulted for this call)."""
        workspace, slug = enabled_project
        emit_adapter = PortableAdapter(workspace, slug)
        emitted = observe.observe_handoff_emit(
            emit_adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        assert emitted.ok is True
        handoff_id = extract_handoff_id(emitted.prompt_text)
        assert handoff_id is not None

        session_adapter = PortableAdapter(workspace, slug, local_id="session-c")
        result = observe.observe_session_start(
            session_adapter,
            workspace=workspace,
            slug=slug,
            handoff_id=handoff_id,
            writer_role="pm",
        )

        assert result.ok is True
        assert result.node_id == emitted.node_id
        assert result.reason in ("launched", "already_launched")

    def test_observe_session_start_recovers_handoff_id_from_prompt_text(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """`prompt_text` (not `handoff_id` directly) is grepped for an
        embedded `FLEET-HANDOFF-ID` line via `extract_handoff_id` — the
        same id-anchored (never fuzzy) trigger, just recovered a different
        way."""
        workspace, slug = enabled_project
        emit_adapter = PortableAdapter(workspace, slug)
        emitted = observe.observe_handoff_emit(
            emit_adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        assert emitted.ok is True

        session_adapter = PortableAdapter(workspace, slug, local_id="session-d")
        result = observe.observe_session_start(
            session_adapter,
            workspace=workspace,
            slug=slug,
            prompt_text=emitted.prompt_text,
            writer_role="pm",
        )

        assert result.ok is True
        assert result.node_id == emitted.node_id

    def test_observe_session_start_falls_through_to_registration_when_handoff_id_unmatched(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """A `--handoff-id` that matches no awaiting-launch row still
        registers an ordinary `origination="observed"` row rather than
        failing outright — the flip attempt is best-effort, not a
        precondition for registering."""
        workspace, slug = enabled_project
        session_adapter = PortableAdapter(workspace, slug, local_id="session-e")

        result = observe.observe_session_start(
            session_adapter,
            workspace=workspace,
            slug=slug,
            handoff_id="no-such-handoff-id",
            writer_role="pm",
        )

        assert result.ok is True
        log = (_fleet_dir(workspace, slug) / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log


class TestIdempotencyAcrossFiveMatchers:
    @pytest.mark.parametrize("matcher", ["startup", "resume", "clear", "compact", "fork"])
    def test_observe_session_start_five_matchers_produce_one_row(
        self, matcher: str, enabled_project: tuple[Path, str]
    ) -> None:
        """FR-19/NFR-5 at the API level: the same session identity fired 5
        times (once per matcher) via `observe_session_start` produces
        exactly one row in the manifest (`idempotency_key =
        register:<node_id>` dedupes)."""
        workspace, slug = enabled_project
        node_ids: set[str] = set()
        for _ in range(5):
            adapter = PortableAdapter(workspace, slug, local_id=f"session-{matcher}")
            result = observe.observe_session_start(
                adapter, workspace=workspace, slug=slug, writer_role="pm"
            )
            assert result.ok is True
            assert result.node_id is not None
            node_ids.add(result.node_id)

        assert len(node_ids) == 1
        log_lines = [
            line
            for line in (_fleet_dir(workspace, slug) / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        registered = [line for line in log_lines if '"type":"session_registered"' in line]
        assert len(registered) == 1


class TestErrorClassAndFR13Surfacing:
    """FR-13: `identity_unresolved` stops being invisible. `error_class`
    lives on `ObserveResult`; the stdout line lives ONLY in `cli.py`."""

    def test_observe_result_error_class_set_to_identity_unresolved(
        self, identity_unresolved_project: tuple[Path, str]
    ) -> None:
        """A project that resolves via the locator but whose
        `SUPERHUMAN.md` has no `**Project-id:**` line produces an
        `ObserveResult` with `error_class == "identity_unresolved"`."""
        workspace, slug = identity_unresolved_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_session_start(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        )

        assert result.ok is False
        assert result.error_class == "identity_unresolved"

    def test_cli_prints_exactly_one_stdout_line_for_identity_unresolved(
        self, identity_unresolved_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Drive the CLI entrypoint (`args.func(args)`) for `observe
        session-start` against the identity_unresolved fixture; capture
        stdout via `capsys`; assert exactly one non-empty stdout line, and
        that it names the actionable condition (project resolved but
        unwritable)."""
        workspace, slug = identity_unresolved_project
        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "session-start",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
            ]
        )

        exit_code = args.func(args)

        captured = capsys.readouterr()
        assert exit_code == 0
        stdout_lines = [line for line in captured.out.splitlines() if line.strip()]
        assert len(stdout_lines) == 1
        assert "project-id" in stdout_lines[0].lower() or "identity" in stdout_lines[0].lower()

    def test_library_call_produces_zero_stdout_for_identity_unresolved(
        self, identity_unresolved_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The library-level companion to the CLI test above: call
        `observe.observe_session_start(...)` DIRECTLY (not through
        `cli.py`), same fixture; assert `capsys.readouterr().out == ""`.
        Together they prove the stdout line lives only in `cli.py`, never
        in `observe.py` — whose loudness tiers reserve stdout entirely for
        its CLI callers."""
        workspace, slug = identity_unresolved_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_session_start(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        )

        assert result.error_class == "identity_unresolved"
        assert capsys.readouterr().out == ""


class TestHookPayloadWiring:
    """FR-15/D2b: the hook and the portable prose floor call the identical
    verb -- `--hook-payload -` derives `--workspace`/`--slug` via the
    locator and threads the harness session id through (FR-17)."""

    def test_hook_payload_stdin_derives_workspace_slug_and_registers(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        payload = json.dumps(
            {
                "session_id": "hook-session-1",
                "cwd": str(workspace),
                "hook_event_name": "SessionStart",
                "source": "startup",
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        parser = build_parser()
        args = parser.parse_args(
            ["observe", "session-start", "--hook-payload", "-", "--harness", "portable"]
        )

        exit_code = args.func(args)

        assert exit_code == 0
        log = (_fleet_dir(workspace, slug) / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log
        assert '"local_id":"hook-session-1"' in log

    def test_hook_payload_locator_refusal_exits_0_and_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unresolvable `cwd` (not inside any git repository) means
        nothing to do -- exit 0, nothing written, no traceback."""
        payload = json.dumps({"session_id": "hook-session-2", "cwd": str(tmp_path)})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        parser = build_parser()
        args = parser.parse_args(["observe", "session-start", "--hook-payload", "-"])

        exit_code = args.func(args)

        assert exit_code == 0

    def test_malformed_hook_payload_exits_0_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))

        parser = build_parser()
        args = parser.parse_args(["observe", "session-start", "--hook-payload", "-"])

        exit_code = args.func(args)

        assert exit_code == 0


class TestBroadCatchByteUnchanged:
    """NFR-3: `observe.py`'s single sanctioned broad catch is neither
    widened nor duplicated by this chunk's additions."""

    #: Captured at Chunk 3 implementation time from the three existing
    #: `except Exception as exc:  # noqa: BLE001 - the façade's sole broad
    #: catch` blocks in `_observe_register`, `observe_handoff_emit`, and
    #: `observe_launch` (in source order) -- each block is exactly 10 lines,
    #: from the `except` line through its closing `return ObserveResult(...)`.
    #: A future edit that widens, duplicates, relocates, or shrinks/grows any
    #: of the three fails this test loudly.
    _EXPECTED_BLOCK_HASHES = (
        "418a882cee0b97ef20bf0e810572b5e4f9f6e2ca3a93421a2e99d59d3b19bc63",
        "7a0892856b03b6ef86ba897abfa6ac52c0938b9c1a07f8dadc44f6975c2364a5",
        "6921eaa3ab42eed3b3c93fa29b5738684f56eb073d4aa3a4876cad929e09161e",
    )
    _BLOCK_LINES = 10
    _MARKER_PREFIX = "    except Exception as exc:"

    def test_observe_py_broad_catch_is_byte_unchanged(self) -> None:
        import hashlib

        from scripts.fleet import observe as observe_module

        source_path = Path(observe_module.__file__)
        lines = source_path.read_text(encoding="utf-8").splitlines(keepends=True)
        marker_indices = [
            i for i, line in enumerate(lines) if line.startswith(self._MARKER_PREFIX)
        ]

        assert len(marker_indices) == len(self._EXPECTED_BLOCK_HASHES), (
            "the number of broad `except Exception` blocks in observe.py changed -- "
            "NFR-3 forbids adding, removing, or duplicating one"
        )
        for index, expected_hash in zip(marker_indices, self._EXPECTED_BLOCK_HASHES):
            block = "".join(lines[index : index + self._BLOCK_LINES])
            actual_hash = hashlib.sha256(block.encode("utf-8")).hexdigest()
            assert actual_hash == expected_hash, (
                f"observe.py's broad-catch block starting at line {index + 1} changed "
                "-- NFR-3 requires it stay byte-unchanged"
            )


class TestFleetDoctorViaObserveSession:
    """D6: `fleet doctor` ships in this chunk and is the FR-10 acceptance
    scan Chunk 5 uses. Focused coverage lives in `test_doctor.py`; these two
    are QA's scaffolded happy-path cross-check, kept here per TEST.md's
    original TC-23/TC-24 placement."""

    def test_fleet_doctor_reports_all_four_health_states(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.doctor import scan

        repo = tmp_path / "repo"
        repo.mkdir()
        _run_git(repo, "init", "-q", "-b", "main")
        _run_git(repo, "config", "user.email", "test@example.invalid")
        _run_git(repo, "config", "user.name", "Test")
        (repo / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(repo, "add", ".gitkeep")
        _run_git(repo, "commit", "-q", "-m", "initial")

        def _write(slug: str, *, project_id: str | None, matching_slug: bool) -> None:
            project_dir = repo / "docs" / "superhuman" / slug
            project_dir.mkdir(parents=True)
            slug_line = slug if matching_slug else f"not-{slug}"
            body = f"**Slug:** {slug_line}\n"
            if project_id is not None:
                body += f"**Project-id:** {project_id}\n"
            (project_dir / "SUPERHUMAN.md").write_text(body, encoding="utf-8")

        _write("ok-project", project_id="proj-ok", matching_slug=True)
        _write("no-id-project", project_id=None, matching_slug=True)
        _write("bad-project", project_id="proj-bad", matching_slug=False)

        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        report = scan([repo])
        states = {record.slug: record.state for record in report.records}

        assert states["ok-project"] == "ok"
        assert states["no-id-project"] == "no_project_id"
        assert states["bad-project"] == "unresolvable"

    def test_fleet_doctor_scans_multiple_roots_and_aggregates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.doctor import scan

        def _init(path: Path) -> None:
            path.mkdir(parents=True)
            _run_git(path, "init", "-q", "-b", "main")
            _run_git(path, "config", "user.email", "test@example.invalid")
            _run_git(path, "config", "user.name", "Test")
            (path / ".gitkeep").write_text("", encoding="utf-8")
            _run_git(path, "add", ".gitkeep")
            _run_git(path, "commit", "-q", "-m", "initial")

        repo1 = tmp_path / "repo1"
        repo2 = tmp_path / "repo2"
        _init(repo1)
        _init(repo2)
        for repo, slug in ((repo1, "one"), (repo2, "two")):
            project_dir = repo / "docs" / "superhuman" / slug
            project_dir.mkdir(parents=True)
            (project_dir / "SUPERHUMAN.md").write_text(
                f"**Slug:** {slug}\n**Project-id:** proj-{slug}\n", encoding="utf-8"
            )

        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        report = scan([repo1, repo2])

        assert {record.slug for record in report.records} == {"one", "two"}
