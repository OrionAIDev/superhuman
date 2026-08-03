"""Tests for scripts/release.sh — laptop release driver guard behavior (F13).

release.sh operates on the CURRENT WORKING DIRECTORY's git repo. To keep the
real skill repo and the network untouched, every test builds a THROWAWAY git
repo in tmp_path and invokes the real script with cwd set to that repo.

The three cases here exercise only side-effect-free / guard paths:
  1. refuses a dirty working tree (before any mutation)
  2. refuses an autonomous/* branch (v0.2.0 safety rail)
  3. --dry-run on a clean trunk prints the plan and creates no tag

Because the guards run before pytest and --dry-run skips pytest, the hook
smoke, tagging, and pushing, NO nested pytest run or network access happens.

Skipped automatically on runners where a suitable bash is not available.
On Windows we require Git Bash (standard install path); WSL bash cannot
resolve Windows absolute paths.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIT_BASH_PATH = r"C:\Program Files\Git\bin\bash.exe"


def _find_bash() -> "str | None":
    """Return a path to a bash executable that can accept native paths.

    On POSIX: use shutil.which("bash").
    On Windows: require Git Bash at the standard install location; reject WSL
    bash because it cannot resolve Windows absolute paths (C-colon backslash).
    """
    if sys.platform == "win32":
        if Path(_GIT_BASH_PATH).is_file():
            return _GIT_BASH_PATH
        return None
    return shutil.which("bash")


_BASH = _find_bash()

_GIT = shutil.which("git")


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command inside ``repo`` and return the completed process."""
    return subprocess.run(
        [_GIT, *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _init_repo(repo: Path, version: str = "0.1.0") -> None:
    """Initialize a throwaway git repo with a VERSION file and one commit."""
    repo.mkdir(parents=True, exist_ok=True)
    assert _run_git(repo, "init").returncode == 0
    # Local identity so commits succeed without global config.
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")
    # Avoid signing surprises on machines that default-sign commits.
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    assert _run_git(repo, "add", "-A").returncode == 0
    assert _run_git(repo, "commit", "-m", "initial").returncode == 0


def _git_tags(repo: Path) -> str:
    """Return the stdout of ``git tag`` (stripped) for the repo."""
    return _run_git(repo, "tag").stdout.strip()


def _run_release(
    repo: Path, *args: str, env: "dict[str, str] | None" = None
) -> subprocess.CompletedProcess:
    """Invoke the real scripts/release.sh with cwd set to the throwaway repo.

    Args:
        repo: throwaway repo to run in (becomes cwd).
        *args: arguments forwarded to release.sh.
        env: extra environment variables layered over the inherited environment.
    """
    script = Path(__file__).resolve().parent.parent / "scripts" / "release.sh"
    assert script.is_file(), f"release.sh not found at {script}"
    return subprocess.run(
        [_BASH, str(script), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={**os.environ, **env} if env else None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
def test_release_refuses_dirty_tree(skill_root: Path, tmp_path: Path) -> None:
    """A dirty working tree is refused before any tag/mutation/test run."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    # Make the tree dirty with an untracked file.
    (repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

    result = _run_release(repo, "--dry-run")

    assert result.returncode != 0, (
        f"Expected non-zero exit for dirty tree.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # No tag should have been created.
    assert _git_tags(repo) == "", (
        f"A tag was created despite the dirty-tree guard: {_git_tags(repo)!r}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
def test_release_refuses_autonomous_branch(skill_root: Path, tmp_path: Path) -> None:
    """An autonomous/* branch is refused (v0.2.0 autonomous-mode safety rail)."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    # Switch to an autonomous/* branch; keep the tree clean.
    assert _run_git(repo, "checkout", "-b", "autonomous/run-x").returncode == 0

    result = _run_release(repo, "--dry-run")

    assert result.returncode != 0, (
        f"Expected non-zero exit on autonomous branch.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "autonomous" in combined, (
        f"Refusal message should mention 'autonomous'.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert _git_tags(repo) == "", (
        f"A tag was created despite the autonomous-branch guard: {_git_tags(repo)!r}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
def test_release_dry_run_on_clean_trunk(skill_root: Path, tmp_path: Path) -> None:
    """--dry-run on a clean trunk prints the planned tag and creates nothing."""
    repo = tmp_path / "repo"
    _init_repo(repo, version="0.1.0")

    result = _run_release(repo, "--dry-run")

    assert result.returncode == 0, (
        f"Expected exit 0 for --dry-run on clean trunk.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # The planned tag must be surfaced.
    assert "v0.1.0" in result.stdout, (
        f"--dry-run output should contain the planned tag v0.1.0.\n"
        f"STDOUT:\n{result.stdout}"
    )
    # No tag was actually created.
    assert _git_tags(repo) == "", (
        f"--dry-run created a tag: {_git_tags(repo)!r}"
    )
    # Working tree is unchanged (VERSION still 0.1.0, no new commits/dirt).
    assert (repo / "VERSION").read_text(encoding="utf-8") == "0.1.0\n", (
        "--dry-run altered VERSION"
    )
    status = _run_git(repo, "status", "--porcelain").stdout.strip()
    assert status == "", f"--dry-run left the working tree dirty: {status!r}"


# ---------------------------------------------------------------------------
# Release URL derivation
#
# The slug was originally a hardcoded constant, so every fork of this skill
# printed the original author's release page — a genericization defect that no
# test covered. These cases pin the derivation, the override, and the absence
# of any hardcoded slug.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
@pytest.mark.parametrize(
    "remote_url",
    [
        "https://github.com/acme/widget.git",
        "https://github.com/acme/widget",
        "git@example.com:acme/widget.git",
        "ssh://git@example.com/acme/widget.git",
        "https://someuser@example.com/acme/widget.git",
    ],
)
def test_release_url_derives_slug_from_origin(
    skill_root: Path, tmp_path: Path, remote_url: str
) -> None:
    """The printed release URL comes from origin, in every remote spelling.

    The ssh/userinfo cases use RFC-2606 `example.com` rather than a real forge
    host: a literal user@host in a shipped file trips this repo's own
    publication guard (`test_no_infrastructure_leaks`). The host is irrelevant
    here anyway — only the trailing owner/repo is derived, and the URL is
    always built against github.com.
    """
    repo = tmp_path / "repo"
    _init_repo(repo, version="0.1.0")
    assert _run_git(repo, "remote", "add", "origin", remote_url).returncode == 0

    result = _run_release(repo, "--dry-run")

    assert result.returncode == 0, (
        f"Expected exit 0.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "https://github.com/acme/widget/releases/tag/v0.1.0" in result.stdout, (
        f"release URL not derived from origin {remote_url!r}.\n"
        f"STDOUT:\n{result.stdout}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
def test_release_url_override_beats_origin(skill_root: Path, tmp_path: Path) -> None:
    """SUPERHUMAN_REPO_SLUG wins when origin is not the publishing remote."""
    repo = tmp_path / "repo"
    _init_repo(repo, version="0.1.0")
    assert _run_git(
        repo, "remote", "add", "origin", "https://github.com/upstream/fork-source.git"
    ).returncode == 0

    result = _run_release(
        repo, "--dry-run", env={"SUPERHUMAN_REPO_SLUG": "acme/widget"}
    )

    assert result.returncode == 0, (
        f"Expected exit 0.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "https://github.com/acme/widget/releases/tag/v0.1.0" in result.stdout
    assert "upstream/fork-source" not in result.stdout, (
        f"override did not take precedence over origin.\nSTDOUT:\n{result.stdout}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
def test_release_url_omitted_without_origin(skill_root: Path, tmp_path: Path) -> None:
    """No origin is not an error — it omits the link rather than guessing one.

    Throwaway repos (including the ones in this file) have no remote, so a
    derivation that hard-failed here would break every guard test.
    """
    repo = tmp_path / "repo"
    _init_repo(repo, version="0.1.0")

    result = _run_release(repo, "--dry-run")

    assert result.returncode == 0, (
        f"Expected exit 0 with no origin.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "https://github.com/" not in result.stdout, (
        f"a release URL was printed with no origin to derive it from.\n"
        f"STDOUT:\n{result.stdout}"
    )
    assert "SUPERHUMAN_REPO_SLUG" in result.stdout, (
        f"the missing-URL line should name the override.\nSTDOUT:\n{result.stdout}"
    )


def test_release_sh_has_no_hardcoded_slug(skill_root: Path) -> None:
    """release.sh must not carry a baked-in "<owner>/<repo>".

    Stated as a property rather than a search for one operator's name, so the
    guard keeps working for whoever forks this next.
    """
    text = (skill_root / "scripts" / "release.sh").read_text(encoding="utf-8")

    literal_assign = re.search(
        r'^\s*REPO_SLUG=(["\']?)[\w.-]+/[\w.-]+\1\s*$', text, re.MULTILINE
    )
    assert literal_assign is None, (
        "REPO_SLUG is assigned a literal <owner>/<repo> — derive it from the "
        f"origin remote instead: {literal_assign.group(0).strip()!r}"  # type: ignore[union-attr]
    )

    hardcoded_url = re.search(r"https://github\.com/[\w.-]+/[\w.-]+/releases", text)
    assert hardcoded_url is None, (
        "a release URL is built from a literal slug rather than $REPO_SLUG: "
        f"{hardcoded_url.group(0)!r}"  # type: ignore[union-attr]
    )
