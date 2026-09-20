"""Tests for `scripts.fleet.doctor` — the read-only estate-health scanner.

Chunk 3, per `TEST.md` TC-23/TC-24. Not named in PLAN.md's file-structure
table (which folds `fleet doctor` into Chunk 3's acceptance criteria
without naming a discrete test file); QA scaffolded it separately because
`doctor.py` is its own new module and `conventions/testing.md` calls for
one test file per source file.

Real git fixtures throughout, matching `test_locate.py`'s precedent —
`doctor.scan()` calls `locate.locate_project` per record, so its own
root-discovery machinery is exercised for real, not mocked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.fleet.cli import build_parser
from scripts.fleet.doctor import ProjectHealth, scan


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path, *, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q", "-b", branch)
    _run(path, "config", "user.email", "test@example.invalid")
    _run(path, "config", "user.name", "Test")
    (path / ".gitkeep").write_text("", encoding="utf-8")
    _run(path, "add", ".gitkeep")
    _run(path, "commit", "-q", "-m", "initial")


def _write_record(
    root: Path, slug: str, *, project_id: str | None = "proj-demo", well_formed: bool = True
) -> Path:
    """Write `<root>/docs/superhuman/<slug>/SUPERHUMAN.md`.

    Args:
        root: the workspace root.
        slug: the project directory/slug.
        project_id: the `**Project-id:**` value, or `None` to omit the line.
        well_formed: if `False`, write a `**Slug:**` line that does NOT
            match `slug` — the locator excludes this from candidacy
            entirely (D1 ruling 2), which is exactly the "unresolvable"
            fixture shape.
    """
    project_dir = root / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    record = project_dir / "SUPERHUMAN.md"
    lines = [f"# Superhuman: {slug}\n\n"]
    written_slug = slug if well_formed else f"not-{slug}"
    lines.append(f"**Slug:** {written_slug}\n")
    if project_id is not None:
        lines.append(f"**Project-id:** {project_id}\n")
    record.write_text("".join(lines), encoding="utf-8")
    return record


def _enable_fleet(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = base / "profile.yaml"
    profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))


def _disable_fleet(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point at a file that does not exist -- `resolve_fleet_config` resolves
    # this to a distinct, human-readable disabled reason, never an
    # exception (matches `test_observe.py`'s `disabled_project` fixture).
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(base / "no-such-profile.yaml"))


class TestHealthStates:
    """D6: `fleet doctor` reports each project record's writability as one
    of four states: ok / no Project-id / fleet disabled / unresolvable."""

    def test_reports_ok_for_a_fully_configured_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A record with a valid `Project-id:`, fleet enabled, resolvable
        via the locator, reports `ok`."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_record(repo, "solo-project", project_id="proj-solo")
        _enable_fleet(tmp_path, monkeypatch)

        report = scan([repo])

        assert len(report.records) == 1
        record = report.records[0]
        assert record.slug == "solo-project"
        assert record.state == "ok"

    def test_reports_no_project_id_when_the_field_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A record that resolves but has no `**Project-id:**` line reports
        that state, matching the FR-10 acceptance scan's own definition of
        'missing the field'."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_record(repo, "solo-project", project_id=None)
        _enable_fleet(tmp_path, monkeypatch)

        report = scan([repo])

        assert len(report.records) == 1
        assert report.records[0].state == "no_project_id"

    def test_reports_fleet_disabled_when_no_profile_enables_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A resolvable record in a workspace with no `fleet.enabled: true`
        profile reports the disabled state, not a false 'unresolvable'."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_record(repo, "solo-project", project_id="proj-solo")
        _disable_fleet(tmp_path, monkeypatch)

        report = scan([repo])

        assert len(report.records) == 1
        assert report.records[0].state == "fleet_disabled"

    def test_reports_unresolvable_for_an_unparseable_or_ambiguous_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A candidate the locator would exclude or refuse on (malformed
        `SUPERHUMAN.md`, or genuine multi-candidate ambiguity) reports
        `unresolvable`, distinct from the other three states."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_record(repo, "solo-project", project_id="proj-solo", well_formed=False)
        _enable_fleet(tmp_path, monkeypatch)

        report = scan([repo])

        assert len(report.records) == 1
        assert report.records[0].state == "unresolvable"


class TestScanRoots:
    def test_scan_ignores_a_plain_file_under_docs_superhuman(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stray non-directory entry under `docs/superhuman/` (e.g. a
        stray `.gitkeep`) is skipped, not treated as a candidate."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "docs" / "superhuman").mkdir(parents=True)
        (repo / "docs" / "superhuman" / "not-a-directory.txt").write_text(
            "stray file\n", encoding="utf-8"
        )
        _write_record(repo, "real-project", project_id="proj-real")
        _enable_fleet(tmp_path, monkeypatch)

        report = scan([repo])

        assert {record.slug for record in report.records} == {"real-project"}

    def test_scan_single_root_enumerates_all_records_under_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`scan([root])` walks down and finds every `SUPERHUMAN.md` under
        `root`, not just the one the cwd happens to be in — `doctor` is a
        DOWN enumeration tool, unlike the locator's UP-then-ladder
        resolution."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_record(repo, "alpha", project_id="proj-alpha")
        _write_record(repo, "beta", project_id="proj-beta")
        _enable_fleet(tmp_path, monkeypatch)

        report = scan([repo])

        slugs = {record.slug for record in report.records}
        assert slugs == {"alpha", "beta"}

    def test_scan_multiple_roots_aggregates_into_one_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`scan([root1, root2])` combines records from both roots into one
        report, each entry stating which root it came from."""
        repo1 = tmp_path / "repo1"
        repo2 = tmp_path / "repo2"
        _init_repo(repo1)
        _init_repo(repo2)
        _write_record(repo1, "one", project_id="proj-one")
        _write_record(repo2, "two", project_id="proj-two")
        _enable_fleet(tmp_path, monkeypatch)

        report = scan([repo1, repo2])

        by_slug = {record.slug: record for record in report.records}
        assert by_slug["one"].root == repo1
        assert by_slug["two"].root == repo2

    def test_scan_states_its_roots_in_the_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The report itself names the roots that were scanned (open issue
        5 in DESIGN.md notes prior scans disagreed on scope because roots
        weren't recorded) — this is the regression test for that. A root
        that yields zero records must still appear."""
        repo1 = tmp_path / "repo1"
        repo2 = tmp_path / "repo2"
        _init_repo(repo1)
        _init_repo(repo2)
        # repo2 has no docs/superhuman at all -- zero records, but the root
        # must still be reported as scanned.
        _write_record(repo1, "one", project_id="proj-one")
        _enable_fleet(tmp_path, monkeypatch)

        report = scan([repo1, repo2])

        assert Path(repo1) in report.roots
        assert Path(repo2) in report.roots

    def test_cli_doctor_scan_prints_a_human_readable_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`fleet doctor --scan <root>...` prints a readable summary line
        per record and exits 0 regardless of findings (read-only, never a
        fail-closed assertion)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_record(repo, "solo-project", project_id=None)
        _enable_fleet(tmp_path, monkeypatch)

        parser = build_parser()
        args = parser.parse_args(["doctor", "--scan", str(repo)])

        exit_code = args.func(args)

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "solo-project" in captured.out
        assert "no_project_id" in captured.out


class TestGitVersionCheck:
    def test_report_carries_a_git_version_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`fleet doctor` measures and reports the operator's git version,
        flagging anything older than the 2.31 `locate.py` depends on
        (`--path-format=absolute`)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _enable_fleet(tmp_path, monkeypatch)

        report = scan([repo])

        assert report.git_version.raw is not None
        assert report.git_version.version is not None
        # This machine's real git is expected to satisfy the floor; the
        # `ok` flag itself is exercised directly against `MIN_GIT_VERSION`
        # in `TestGitVersionParsing` below without needing an old git binary.
        assert report.git_version.ok is True


class TestGitVersionParsing:
    def test_flags_a_git_version_below_2_31(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.fleet import doctor as doctor_module

        def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=(), returncode=0, stdout="git version 2.20.1\n")

        monkeypatch.setattr(doctor_module.subprocess, "run", _fake_run)

        result = doctor_module.check_git_version()

        assert result.version == (2, 20, 1)
        assert result.ok is False

    def test_accepts_a_git_version_at_or_above_2_31(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.fleet import doctor as doctor_module

        def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=(), returncode=0, stdout="git version 2.31.0\n")

        monkeypatch.setattr(doctor_module.subprocess, "run", _fake_run)

        result = doctor_module.check_git_version()

        assert result.version == (2, 31, 0)
        assert result.ok is True

    def test_nonzero_exit_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.fleet import doctor as doctor_module

        def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=(), returncode=1, stdout="")

        monkeypatch.setattr(doctor_module.subprocess, "run", _fake_run)

        result = doctor_module.check_git_version()

        assert result.ok is False
        assert result.raw is None

    def test_unparseable_version_output_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet import doctor as doctor_module

        def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=(), returncode=0, stdout="not a version string\n")

        monkeypatch.setattr(doctor_module.subprocess, "run", _fake_run)

        result = doctor_module.check_git_version()

        assert result.ok is False
        assert result.version is None
        assert result.raw == "not a version string"

    def test_missing_git_executable_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.fleet import doctor as doctor_module

        def _raise(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(doctor_module.subprocess, "run", _raise)

        result = doctor_module.check_git_version()

        assert result.ok is False
        assert result.version is None
