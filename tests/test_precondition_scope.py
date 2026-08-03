"""Regression tests for project-scoping of the autonomous activation gate.

Roadmap #143: `autonomous-precondition.sh` claimed to check four preconditions
and checked one and a half. The rollback-plan check took only a repo root and
``rglob``-ed every ``SUPERHUMAN.md`` beneath it, so in a repo holding concurrent
projects it answered about whichever sibling sorted first. Two failure
directions followed — a false BLOCK on a compliant project because an unrelated
sibling lacked a ``ROLLBACK.md``, and, worse, a vacuous PASS when no sibling
tripped the check at all.

The multi-project fixture is the point of this module: two project directories
in one repo, one compliant and one not, asserting the gate answers about the one
it was *named*. That case had no coverage, which is why the defect shipped.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import superhuman_profile as sp  # noqa: E402

RESOLVER = Path(__file__).resolve().parents[1] / "scripts" / "superhuman_profile.py"

#: A ladder that allows unattended operation everywhere, so that anything this
#: module observes is a project-state verdict rather than a rung verdict.
PERMISSIVE = (
    "version: 1\n"
    "ladder:\n"
    "  - name: anywhere\n"
    "    detect: {default: true}\n"
    "    approvals: {act_unattended: [self], promote_into: none}\n"
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@e",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e",
}


def _run(root: Path, profile: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the resolver CLI against a pinned profile.

    Args:
        root: Project root to check.
        profile: Profile to pin via ``SUPERHUMAN_PROFILE``.
        *args: Extra CLI arguments appended after the level.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    env = dict(os.environ)
    env["SUPERHUMAN_PROFILE"] = str(profile)
    env.pop("SUPERHUMAN_REQUIRE_PROFILE", None)
    return subprocess.run(
        [sys.executable, str(RESOLVER), "check", str(root), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _project(
    root: Path,
    slug: str,
    *,
    modifies: str | None,
    rollback: bool,
    goal: bool = True,
) -> Path:
    """Create one project directory under ``docs/superhuman/``.

    Args:
        root: Repository root.
        slug: Project slug.
        modifies: Value for the ``Modifies-existing-code:`` field, or ``None``
            to omit the field entirely.
        rollback: Whether to write a ``ROLLBACK.md``.
        goal: Whether to write a ``GOAL.md``.

    Returns:
        The project directory.
    """
    directory = root / "docs" / "superhuman" / slug
    directory.mkdir(parents=True, exist_ok=True)
    lines = [f"# Superhuman: {slug}", "", f"**Slug:** {slug}", "**HITL-level:** L"]
    if modifies is not None:
        lines.append(f"**Modifies-existing-code:** {modifies}")
    (directory / "SUPERHUMAN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if rollback:
        (directory / "ROLLBACK.md").write_text("# Rollback\nrevert abc123\n", encoding="utf-8")
    if goal:
        (directory / "GOAL.md").write_text("# Goal\nfitness: pytest\n", encoding="utf-8")
    return directory


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, Path]:
    """Build a git repo with a remote and a pinned permissive profile.

    The remote is a bare repo on disk rather than a URL, so the git+remote
    precondition is satisfied without touching the network.

    Args:
        tmp_path: Test temp directory.

    Returns:
        A ``(root, profile)`` pair.
    """
    root = tmp_path / "repo"
    root.mkdir()
    bare = tmp_path / "origin.git"
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
        ["git", "remote", "add", "origin", str(bare)],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True, env=env)

    profile = tmp_path / "profile.yaml"
    profile.write_text(PERMISSIVE, encoding="utf-8")
    return root, profile


# --------------------------------------------------------------------------- #
# The multi-project fixture — the case that had no coverage
# --------------------------------------------------------------------------- #


def test_compliant_project_passes_beside_a_noncompliant_sibling(
    repo: tuple[Path, Path],
) -> None:
    """A named compliant project must not be judged by its sibling's gaps.

    The false-BLOCK direction of #143: `entry-tiering` lacked a ROLLBACK.md and
    the gate refused `superhuman-init`, which had one.
    """
    root, profile = repo
    _project(root, "compliant", modifies="yes", rollback=True)
    _project(root, "delinquent", modifies="yes", rollback=False)

    proc = _run(root, profile, "--level", "L", "--slug", "compliant")

    assert proc.returncode == sp.EXIT_OK, proc.stderr
    assert "delinquent" not in proc.stdout + proc.stderr


def test_noncompliant_project_is_blocked_beside_a_compliant_sibling(
    repo: tuple[Path, Path],
) -> None:
    """The gate must answer about the project it was named, not a happier one."""
    root, profile = repo
    _project(root, "compliant", modifies="yes", rollback=True)
    _project(root, "delinquent", modifies="yes", rollback=False)

    proc = _run(root, profile, "--level", "L", "--slug", "delinquent")

    assert proc.returncode == sp.EXIT_DENIED
    assert "/delinquent/" in proc.stderr
    # Not a bare "compliant" check: pytest's tmp_path is named after the test,
    # which itself contains the substring.
    assert "/compliant/" not in proc.stderr


def test_sibling_gap_cannot_produce_a_vacuous_pass(repo: tuple[Path, Path]) -> None:
    """The dangerous direction: a project must be inspected, not skipped.

    Pre-fix, a project whose ``SUPERHUMAN.md`` omitted the field was never
    evaluated; if no *other* project tripped the check the gate returned
    ``None`` and authorized a HITL-L run having checked nothing about itself.
    """
    root, profile = repo
    _project(root, "compliant", modifies="no", rollback=False)
    _project(root, "silent", modifies=None, rollback=False)

    proc = _run(root, profile, "--level", "L", "--slug", "silent")

    assert proc.returncode == sp.EXIT_DENIED
    assert "Modifies-existing-code" in proc.stderr


# --------------------------------------------------------------------------- #
# Refusing to guess
# --------------------------------------------------------------------------- #


def test_level_l_without_a_slug_is_unresolved(repo: tuple[Path, Path]) -> None:
    """No slug means no scope; exit 4, never a guess across siblings."""
    root, profile = repo
    _project(root, "compliant", modifies="yes", rollback=True)
    _project(root, "delinquent", modifies="yes", rollback=False)
    (root / "GOAL.md").write_text("# Goal\nfitness: pytest\n", encoding="utf-8")

    proc = _run(root, profile, "--level", "L")

    assert proc.returncode == sp.EXIT_UNRESOLVED
    assert "--slug" in proc.stderr


def test_unknown_slug_is_unresolved_not_allowed(repo: tuple[Path, Path]) -> None:
    """A slug naming no project on disk cannot be evaluated, so it must halt."""
    root, profile = repo
    _project(root, "compliant", modifies="yes", rollback=True)
    (root / "GOAL.md").write_text("# Goal\nfitness: pytest\n", encoding="utf-8")

    proc = _run(root, profile, "--level", "L", "--slug", "no-such-project")

    assert proc.returncode == sp.EXIT_UNRESOLVED


@pytest.mark.parametrize("declared", ["", "maybe", "TBD"])
def test_undeclared_or_unreadable_modifies_field_is_a_gap(
    repo: tuple[Path, Path], declared: str
) -> None:
    """An undeclared field is not a declaration of ``no``."""
    root, profile = repo
    _project(root, "p", modifies=declared or None, rollback=False)

    proc = _run(root, profile, "--level", "L", "--slug", "p")

    assert proc.returncode == sp.EXIT_DENIED


def test_modifies_no_needs_no_rollback_plan(repo: tuple[Path, Path]) -> None:
    """Greenfield projects stay exempt — there is nothing to revert to."""
    root, profile = repo
    _project(root, "greenfield", modifies="no", rollback=False)

    proc = _run(root, profile, "--level", "L", "--slug", "greenfield")

    assert proc.returncode == sp.EXIT_OK, proc.stderr


# --------------------------------------------------------------------------- #
# The two preconditions that regressed at v0.7.0
# --------------------------------------------------------------------------- #


def test_missing_goal_blocks_at_level_m(repo: tuple[Path, Path]) -> None:
    """HITL-M without a fitness function has nothing to measure against."""
    root, profile = repo
    _project(root, "p", modifies="no", rollback=False, goal=False)

    proc = _run(root, profile, "--level", "M", "--slug", "p")

    assert proc.returncode == sp.EXIT_DENIED
    assert "GOAL.md" in proc.stderr


def test_root_level_goal_satisfies_the_precondition(repo: tuple[Path, Path]) -> None:
    """File-first, per phases/0-kickoff.md: a root GOAL.md counts."""
    root, profile = repo
    _project(root, "p", modifies="no", rollback=False, goal=False)
    (root / "GOAL.md").write_text("# Goal\nfitness: pytest\n", encoding="utf-8")

    proc = _run(root, profile, "--level", "M", "--slug", "p")

    assert proc.returncode == sp.EXIT_OK, proc.stderr


def test_repo_without_a_remote_is_blocked(tmp_path: Path) -> None:
    """An unattended loop's rollback story needs a remote that outlives it."""
    root = tmp_path / "local-only"
    root.mkdir()
    env = {**os.environ, **GIT_ENV}
    for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "s"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True, env=env)
    profile = tmp_path / "profile.yaml"
    profile.write_text(PERMISSIVE, encoding="utf-8")
    _project(root, "p", modifies="no", rollback=False)

    proc = _run(root, profile, "--level", "M", "--slug", "p")

    assert proc.returncode == sp.EXIT_DENIED
    assert "origin" in proc.stderr


def test_non_git_directory_is_blocked(tmp_path: Path) -> None:
    """No git at all fails the same precondition, one step earlier."""
    root = tmp_path / "plain"
    root.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(PERMISSIVE, encoding="utf-8")

    proc = _run(root, profile, "--level", "M", "--slug", "p")

    assert proc.returncode == sp.EXIT_DENIED
    assert "git" in proc.stderr


# --------------------------------------------------------------------------- #
# HITL-H and the kickoff escape hatch
# --------------------------------------------------------------------------- #


def test_hitl_h_skips_every_precondition(tmp_path: Path) -> None:
    """HITL-H is always allowed everywhere and never consults the gate."""
    root = tmp_path / "plain"
    root.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(PERMISSIVE, encoding="utf-8")

    assert _run(root, profile, "--level", "H").returncode == sp.EXIT_OK


def test_kickoff_defers_project_state_but_not_the_remote(
    repo: tuple[Path, Path],
) -> None:
    """Step 3 runs before the project's own state exists.

    ``--kickoff`` defers GOAL.md and the rollback plan — which kickoff itself
    goes on to write — while still enforcing git+remote, which is knowable then.
    """
    root, profile = repo

    assert _run(root, profile, "--level", "L", "--kickoff").returncode == sp.EXIT_OK
    # …and the unflagged re-run at the end of kickoff is what actually gates.
    assert _run(root, profile, "--level", "L").returncode == sp.EXIT_UNRESOLVED


def test_kickoff_still_enforces_the_remote(tmp_path: Path) -> None:
    """The escape hatch must not become a way to skip the whole gate."""
    root = tmp_path / "plain"
    root.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(PERMISSIVE, encoding="utf-8")

    proc = _run(root, profile, "--level", "M", "--kickoff")

    assert proc.returncode == sp.EXIT_DENIED


# --------------------------------------------------------------------------- #
# The shim's exit-code contract (SKILL.md HARD-GATE and both phase files
# branch on these numbers)
# --------------------------------------------------------------------------- #


SHIM = Path(__file__).resolve().parents[1] / "scripts" / "autonomous-precondition.sh"


def _usable_bash() -> str | None:
    """Find a bash that can actually see this checkout.

    On Windows the ``bash`` first on PATH is often WSL's, whose filesystem view
    does not include ``C:/…``; it reports "No such file or directory" for a file
    that plainly exists. Probe with ``test -f`` rather than trusting the name.

    Returns:
        An executable that can read the shim, or ``None``.
    """
    candidates = [
        "bash",
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for exe in candidates:
        try:
            proc = subprocess.run(
                [exe, "-c", 'test -f "$1"', "_", SHIM.as_posix()],
                capture_output=True, check=False,
            )
        except OSError:
            continue
        if proc.returncode == 0:
            return exe
    return None


BASH = _usable_bash()


@pytest.mark.skipif(BASH is None, reason="no bash that can see this checkout")
def test_shim_threads_slug_and_preserves_exit_codes(repo: tuple[Path, Path]) -> None:
    """The shim must pass --slug through and keep 0/3/4 meaning what they did."""
    root, profile = repo
    _project(root, "compliant", modifies="yes", rollback=True)
    _project(root, "delinquent", modifies="yes", rollback=False)

    env = dict(os.environ)
    env["SUPERHUMAN_PROFILE"] = str(profile)
    env.pop("SUPERHUMAN_REQUIRE_PROFILE", None)

    def shim_rc(*args: str) -> int:
        assert BASH is not None
        return subprocess.run(
            [BASH, SHIM.as_posix(), root.as_posix(), *args],
            capture_output=True, text=True, env=env, check=False,
        ).returncode

    assert shim_rc("--level", "L", "--slug", "compliant") == sp.EXIT_OK
    assert shim_rc("--level", "L", "--slug", "delinquent") == sp.EXIT_DENIED
    assert shim_rc("--level", "L") == sp.EXIT_UNRESOLVED
    assert shim_rc("--level", "L", "--project", "compliant") == sp.EXIT_OK
    assert shim_rc("--level", "L", "--slug=compliant") == sp.EXIT_OK
