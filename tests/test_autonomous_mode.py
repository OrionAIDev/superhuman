"""Tests for superhuman v0.2.0 autonomous mode.

Covers the deterministic UAT/Prod precondition guard (the single safety-critical
gate, per skill-design Rule 5) plus content/regression checks for the surrogate
role, loop wording, and the operator scripts. Bash tests are skipped where a
suitable bash is unavailable (Windows requires Git Bash; WSL bash cannot resolve
Windows absolute paths).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_GIT_BASH_PATH = r"C:\Program Files\Git\bin\bash.exe"


def _find_bash() -> "str | None":
    if sys.platform == "win32":
        return _GIT_BASH_PATH if Path(_GIT_BASH_PATH).is_file() else None
    return shutil.which("bash")


_BASH = _find_bash()
SKILL_ROOT = Path(__file__).resolve().parent.parent
PRECOND = SKILL_ROOT / "scripts" / "autonomous-precondition.sh"

#: From v0.7.0 the gate is policy-free: the ladder lives in a profile. These
#: tests pin SUPERHUMAN_PROFILE to a GENERIC fixture so they exercise the
#: *mechanism* only — independent of any organisation's environment names, and
#: of whether the developer running them has ~/.superhuman/profile.yaml at all.
#: Installation-specific policy is pinned separately by tests/fixtures/golden/.
GOLDEN_LADDER = SKILL_ROOT / "tests" / "fixtures" / "ladder-generic.yaml"

requires_bash = pytest.mark.skipif(_BASH is None, reason="no suitable bash available")


def _pinned_env() -> dict:
    """Build an environment pinned to the golden ladder fixture.

    Returns:
        A copy of ``os.environ`` with the profile pinned and any
        require-profile override cleared.
    """
    env = dict(os.environ, SUPERHUMAN_PROFILE=str(GOLDEN_LADDER))
    env.pop("SUPERHUMAN_REQUIRE_PROFILE", None)
    return env


def _run_precond(project_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_BASH, str(PRECOND), str(project_root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        env=_pinned_env(),
    )


@requires_bash
def test_precondition_allows_plain_lab_path(tmp_path: Path) -> None:
    """A neutral project dir with no UAT/Prod markers is allowed (exit 0)."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    cp = _run_precond(proj)
    assert cp.returncode == 0, f"expected allow, got rc={cp.returncode}: {cp.stderr}"


@requires_bash
@pytest.mark.parametrize("seg", ["staging", "uat", "production"])
def test_precondition_blocks_uat_path_globs(tmp_path: Path, seg: str) -> None:
    """Forbidden /opt/<uat-or-prod>/... path segments are hard-blocked."""
    proj = tmp_path / "opt" / seg / "workspace" / "myproj"
    proj.mkdir(parents=True)
    cp = _run_precond(proj)
    assert cp.returncode != 0, "expected refusal for UAT path"
    assert "Release policy" in (cp.stdout + cp.stderr), "refusal must cite the profile's policy"


@requires_bash
def test_precondition_blocks_prod_substring(tmp_path: Path) -> None:
    """Any path containing a prod segment is blocked."""
    proj = tmp_path / "opt" / "acme-prod" / "app"
    proj.mkdir(parents=True)
    assert _run_precond(proj).returncode != 0


@requires_bash
@pytest.mark.parametrize("env", ["uat", "prod", "UAT", "Prod"])
def test_precondition_blocks_environment_marker(tmp_path: Path, env: str) -> None:
    """A SUPERHUMAN.md '## Environment:' of uat/prod blocks even on a neutral path."""
    proj = tmp_path / "neutral"
    sh = proj / "docs" / "superhuman" / "x"
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        f"# Superhuman: x\n\n## Environment: {env}\n", encoding="utf-8"
    )
    cp = _run_precond(proj)
    assert cp.returncode != 0, f"expected refusal for environment={env}"
    assert "Release policy" in (cp.stdout + cp.stderr)


@requires_bash
@pytest.mark.parametrize("env", ["lab", "test"])
def test_precondition_allows_lab_test_marker(tmp_path: Path, env: str) -> None:
    """An explicit lab/test environment marker is allowed."""
    proj = tmp_path / "neutral"
    sh = proj / "docs" / "superhuman" / "x"
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        f"# Superhuman: x\n\n## Environment: {env}\n", encoding="utf-8"
    )
    assert _run_precond(proj).returncode == 0


@requires_bash
def test_precondition_allows_laptop_prelab_checkout(tmp_path: Path) -> None:
    """v0.6.0: a laptop/pre-lab checkout (skill repo path, no env marker) is allowed.

    Laptop/pre-lab is the primary authoring surface, upstream of Lab, so
    the autonomous loop is a first-class capability there — it must PASS (exit 0),
    not be treated as forbidden. Mirrors ~/.claude/skills/<name>/ with no
    ``## Environment:`` marker.
    """
    proj = tmp_path / "Users" / "Chris" / ".claude" / "skills" / "my-project"
    sh = proj / "docs" / "superhuman" / "collector"
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        "# Superhuman: collector\n\n(no Environment marker — laptop authoring)\n",
        encoding="utf-8",
    )
    cp = _run_precond(proj)
    assert cp.returncode == 0, f"laptop/pre-lab must pass, got rc={cp.returncode}: {cp.stderr}"


@requires_bash
def test_precondition_still_blocks_uat_beside_laptop_allow(tmp_path: Path) -> None:
    """Companion to the laptop-allow case: a protected path still hard-aborts."""
    proj = tmp_path / "opt" / "production" / "workspace" / "skills" / "my-project"
    proj.mkdir(parents=True)
    cp = _run_precond(proj)
    assert cp.returncode != 0, "UAT path must still be blocked even after the laptop allowance"
    assert "Release policy" in (cp.stdout + cp.stderr), "refusal must cite the profile's policy"


# ---------------------------------------------------------------------------
# HITL-level 2 (Low) — rollback-plan guard (v0.5.0)
# ---------------------------------------------------------------------------


def _run_precond_level(project_root: Path, level: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_BASH, str(PRECOND), str(project_root), "--level", level],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        env=_pinned_env(),
    )


@requires_bash
def test_level1_ignores_modifies_existing_code(tmp_path: Path) -> None:
    """Level 1 never requires a ROLLBACK.md, even when Modifies-existing-code: yes."""
    proj = tmp_path / "neutral"
    sh = proj / "docs" / "superhuman" / "x"
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        "# Superhuman: x\n\n**Modifies-existing-code:** yes\n\n## Environment: lab\n",
        encoding="utf-8",
    )
    assert _run_precond_level(proj, "1").returncode == 0


@requires_bash
def test_level2_blocks_without_rollback_plan(tmp_path: Path) -> None:
    """Level 2 refuses to run when Modifies-existing-code: yes and no ROLLBACK.md exists."""
    proj = tmp_path / "neutral"
    sh = proj / "docs" / "superhuman" / "x"
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        "# Superhuman: x\n\n**Modifies-existing-code:** yes\n\n## Environment: lab\n",
        encoding="utf-8",
    )
    cp = _run_precond_level(proj, "2")
    assert cp.returncode != 0
    assert "ROLLBACK.md" in (cp.stdout + cp.stderr)


@requires_bash
def test_level2_allows_with_rollback_plan_present(tmp_path: Path) -> None:
    """Level 2 allows once ROLLBACK.md exists alongside SUPERHUMAN.md."""
    proj = tmp_path / "neutral"
    sh = proj / "docs" / "superhuman" / "x"
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        "# Superhuman: x\n\n**Modifies-existing-code:** yes\n\n## Environment: lab\n",
        encoding="utf-8",
    )
    (sh / "ROLLBACK.md").write_text("# Rollback plan\n\nrevert to <sha>\n", encoding="utf-8")
    assert _run_precond_level(proj, "2").returncode == 0


@requires_bash
def test_level2_exempts_greenfield_projects(tmp_path: Path) -> None:
    """Level 2 does not require ROLLBACK.md when Modifies-existing-code: no."""
    proj = tmp_path / "neutral"
    sh = proj / "docs" / "superhuman" / "x"
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        "# Superhuman: x\n\n**Modifies-existing-code:** no\n\n## Environment: lab\n",
        encoding="utf-8",
    )
    assert _run_precond_level(proj, "2").returncode == 0


@requires_bash
def test_level_flag_rejects_invalid_value(tmp_path: Path) -> None:
    """--level only accepts 1 or 2 (usage error, exit 2)."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    assert _run_precond_level(proj, "3").returncode == 2


# ---------------------------------------------------------------------------
# autonomous-rollback.sh tests
# ---------------------------------------------------------------------------


def _git(repo, *args):
    return subprocess.run([shutil.which("git"), *args], cwd=str(repo),
                          capture_output=True, text=True, timeout=30)


ROLLBACK = SKILL_ROOT / "scripts" / "autonomous-rollback.sh"


@requires_bash
def test_rollback_requires_slug_arg(tmp_path: Path) -> None:
    """Rollback refuses to run without an explicit <slug> (concurrent-run safety, Q3)."""
    cp = subprocess.run([_BASH, str(ROLLBACK)], cwd=str(tmp_path),
                        capture_output=True, text=True, timeout=30)
    assert cp.returncode != 0
    assert "slug" in (cp.stdout + cp.stderr).lower()


@requires_bash
def test_rollback_dry_run_reports_plan(tmp_path: Path) -> None:
    """--dry-run on a repo with an autonomous branch prints the plan, mutates nothing."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "VERSION").write_text("0.2.0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "autonomous/demo/20260625T0-aaaa")
    cp = subprocess.run([_BASH, str(ROLLBACK), "demo", "--dry-run"], cwd=str(repo),
                        capture_output=True, text=True, timeout=30)
    assert cp.returncode == 0, cp.stderr
    branches = _git(repo, "branch", "-a").stdout
    assert "archive/" not in branches  # dry-run creates nothing


# ---------------------------------------------------------------------------
# autonomous-summary.sh tests
# ---------------------------------------------------------------------------

SUMMARY = SKILL_ROOT / "scripts" / "autonomous-summary.sh"


def test_summary_template_has_required_sections() -> None:
    text = (SKILL_ROOT / "templates" / "autonomous-run-summary.md.tpl").read_text(encoding="utf-8")
    for needle in ("Run metadata", "Goal", "Per-iteration", "Final state",
                   "Declared artifacts", "Rollback command", "G8"):
        assert needle in text, f"summary template missing '{needle}'"


@requires_bash
def test_summary_runs_against_a_superhuman_md(tmp_path: Path) -> None:
    """summary.sh reads an iterations log and emits a report referencing the slug."""
    sh = tmp_path / "docs" / "superhuman" / "demo"
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        "# Superhuman: demo\n\n## Environment: lab\n\n## Autonomous iterations log\n"
        "| iter | fitness before | fitness after | delta | KEEP/ROLLBACK | tag | archive ref |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | 0.50 | 1.00 | +0.50 | KEEP | v0.2.0-alpha-x.iter-1 | - |\n",
        encoding="utf-8")
    cp = subprocess.run([_BASH, str(SUMMARY), "demo"], cwd=str(tmp_path),
                        capture_output=True, text=True, timeout=30)
    assert cp.returncode == 0, cp.stderr
    assert "demo" in cp.stdout
    assert "iter-1" in cp.stdout or "iter | " in cp.stdout


# --- autonomous-iter.sh: deterministic per-iteration audit-trail driver -------

ITER = SKILL_ROOT / "scripts" / "autonomous-iter.sh"


def _init_iter_project(repo: Path, slug: str = "demo") -> None:
    """A throwaway git project with a SUPERHUMAN.md iterations-log table."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "work.txt").write_text("seed\n", encoding="utf-8")
    sh = repo / "docs" / "superhuman" / slug
    sh.mkdir(parents=True)
    (sh / "SUPERHUMAN.md").write_text(
        "# Superhuman: " + slug + "\n\n## Environment: lab\n\n"
        "## Autonomous iterations log\n"
        "| iter | fitness before | fitness after | delta | KEEP/ROLLBACK | tag | archive ref |\n"
        "|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")


def _run_iter(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([_BASH, str(ITER), *args], cwd=str(repo),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


@requires_bash
def test_iter_pre_tags_snapshot_and_measures(tmp_path: Path) -> None:
    """`pre` creates the -pre snapshot tag and prints fitness_before."""
    repo = tmp_path / "p"
    _init_iter_project(repo)
    cp = _run_iter(repo, "pre", "--project-root", str(repo), "--version", "0.2.2",
                   "--run-id", "R", "--iter", "1", "--measure", "echo 0.5")
    assert cp.returncode == 0, cp.stderr
    assert "fitness_before=0.5" in cp.stdout
    assert "v0.2.2-alpha-R.iter-1-pre" in _git(repo, "tag").stdout


@requires_bash
def test_iter_decide_keep_commits_and_tags(tmp_path: Path) -> None:
    """`decide` KEEPs a strict improvement: commit + iter tag + KEEP row."""
    repo = tmp_path / "p"
    _init_iter_project(repo)
    _run_iter(repo, "pre", "--project-root", str(repo), "--version", "0.2.2",
              "--run-id", "R", "--iter", "1", "--measure", "echo 0.5")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "work.txt").write_text("improved\n", encoding="utf-8")
    cp = _run_iter(repo, "decide", "--project-root", str(repo), "--slug", "demo",
                   "--version", "0.2.2", "--run-id", "R", "--iter", "1",
                   "--measure", "echo 1.0", "--fitness-before", "0.5")
    assert cp.returncode == 0, cp.stderr
    assert "decision=KEEP" in cp.stdout
    assert "v0.2.2-alpha-R.iter-1" in _git(repo, "tag").stdout
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != head_before, "should have committed"
    log = (repo / "docs" / "superhuman" / "demo" / "SUPERHUMAN.md").read_text(encoding="utf-8")
    assert "| 1 |" in log and "KEEP" in log


@requires_bash
def test_iter_decide_rollback_on_tie(tmp_path: Path) -> None:
    """A tie (not strictly improving) rolls back: reset, archive, no keep tag."""
    repo = tmp_path / "p"
    _init_iter_project(repo)
    _run_iter(repo, "pre", "--project-root", str(repo), "--version", "0.2.2",
              "--run-id", "R", "--iter", "2", "--measure", "echo 0.5")
    (repo / "work.txt").write_text("churn-that-did-not-help\n", encoding="utf-8")
    cp = _run_iter(repo, "decide", "--project-root", str(repo), "--slug", "demo",
                   "--version", "0.2.2", "--run-id", "R", "--iter", "2",
                   "--measure", "echo 0.5", "--fitness-before", "0.5")
    assert cp.returncode == 0, cp.stderr
    assert "decision=ROLLBACK" in cp.stdout
    # working tree restored to the snapshot
    assert (repo / "work.txt").read_text(encoding="utf-8") == "seed\n"
    # the -pre snapshot exists; no bare KEEP tag for this iteration
    tags = set(_git(repo, "tag").stdout.split())
    assert "v0.2.2-alpha-R.iter-2-pre" in tags
    assert "v0.2.2-alpha-R.iter-2" not in tags
    # archive written
    arch = repo / "docs" / "superhuman" / "demo" / "archive"
    why = list(arch.glob("*-iter-2-rolled-back/WHY.md"))
    assert why, "rollback must archive a WHY.md"
    assert "ROLLBACK" in (repo / "docs" / "superhuman" / "demo" / "SUPERHUMAN.md").read_text(encoding="utf-8")


@requires_bash
def test_iter_pytest_mode_measures_pass_rate(tmp_path: Path) -> None:
    """--measure-pytest yields pass-rate; a fix moves 0.5 -> 1.0 and KEEPs."""
    repo = tmp_path / "p"
    _init_iter_project(repo)
    tdir = repo / "tests"
    tdir.mkdir()
    (tdir / "test_x.py").write_text(
        "def test_a():\n    assert 1 == 1\n\ndef test_b():\n    assert 1 == 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add tests (1 fail)")
    cp_pre = _run_iter(repo, "pre", "--project-root", str(repo), "--version", "0.2.2",
                       "--run-id", "R", "--iter", "1", "--measure-pytest", "tests")
    assert cp_pre.returncode == 0, cp_pre.stderr
    assert "fitness_before=0.5" in cp_pre.stdout, cp_pre.stdout
    # fix the failing test
    (tdir / "test_x.py").write_text(
        "def test_a():\n    assert 1 == 1\n\ndef test_b():\n    assert 1 == 1\n", encoding="utf-8")
    cp = _run_iter(repo, "decide", "--project-root", str(repo), "--slug", "demo",
                   "--version", "0.2.2", "--run-id", "R", "--iter", "1",
                   "--measure-pytest", "tests", "--fitness-before", "0.5")
    assert cp.returncode == 0, cp.stderr
    assert "decision=KEEP" in cp.stdout
    assert "fitness_after=1.0" in cp.stdout


@requires_bash
def test_iter_final_tags_beta(tmp_path: Path) -> None:
    """`final` tags the run result v<V>-beta-<run-id>."""
    repo = tmp_path / "p"
    _init_iter_project(repo)
    cp = _run_iter(repo, "final", "--project-root", str(repo), "--version", "0.2.2", "--run-id", "R")
    assert cp.returncode == 0, cp.stderr
    assert "v0.2.2-beta-R" in _git(repo, "tag").stdout
