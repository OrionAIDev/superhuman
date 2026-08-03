"""Tests for the pre-commit hook + installer (F15).

The pre-commit hook (scripts/git-hooks/pre-commit) runs the fast test suite in
the repo it's committing and REFUSES the commit (exit 1) if those tests fail.
install-hooks.sh wires that hook into a repo's .git/hooks/pre-commit.

To keep the real skill repo and the network untouched, every test builds a
THROWAWAY git repo in tmp_path and invokes the real scripts with cwd set to
that repo. The throwaway repos contain a single tiny test file so the nested
pytest run the hook performs stays trivial and fast.

Skipped automatically on runners where a suitable bash is not available.
On Windows we require Git Bash (standard install path); WSL bash cannot
resolve Windows absolute paths.
"""

from __future__ import annotations

import os
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


def _init_repo(repo: Path) -> None:
    """Initialize a throwaway git repo with a local identity."""
    repo.mkdir(parents=True, exist_ok=True)
    assert _run_git(repo, "init").returncode == 0
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "commit.gpgsign", "false")


def _scripts_dir() -> Path:
    """Return the real scripts/ dir of the skill repo under test."""
    return Path(__file__).resolve().parent.parent / "scripts"


def _hook_path() -> Path:
    """Path to the real pre-commit hook script."""
    return _scripts_dir() / "git-hooks" / "pre-commit"


def _installer_path() -> Path:
    """Path to the real install-hooks.sh script."""
    return _scripts_dir() / "install-hooks.sh"


def _run_hook(repo: Path) -> subprocess.CompletedProcess:
    """Invoke the real pre-commit hook with cwd set to the throwaway repo."""
    script = _hook_path()
    assert script.is_file(), f"pre-commit hook not found at {script}"
    return subprocess.run(
        [_BASH, str(script)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _seed_test_file(repo: Path, name: str, body: str) -> None:
    """Write a tiny pytest file under the repo's tests/ directory."""
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / name).write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
def test_pre_commit_refuses_on_failing_tests(skill_root: Path, tmp_path: Path) -> None:
    """The hook exits non-zero when the repo's fast test suite fails."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _seed_test_file(repo, "test_fail.py", "def test_fail():\n    assert False\n")

    result = _run_hook(repo)

    assert result.returncode != 0, (
        f"Expected non-zero exit when tests fail (commit refused).\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
def test_pre_commit_passes_on_green_tests(skill_root: Path, tmp_path: Path) -> None:
    """The hook exits 0 when the repo's fast test suite passes."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _seed_test_file(repo, "test_ok.py", "def test_ok():\n    assert True\n")

    result = _run_hook(repo)

    assert result.returncode == 0, (
        f"Expected exit 0 when tests pass.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
@pytest.mark.skipif(_GIT is None, reason="git not available on this runner")
def test_install_hooks_installs_executable_pre_commit(skill_root: Path, tmp_path: Path) -> None:
    """install-hooks.sh installs an executable .git/hooks/pre-commit, idempotently."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    # Stage the real scripts inside the throwaway repo so the installer resolves
    # them relative to its own location (and uses git rev-parse for the toplevel).
    repo_scripts = repo / "scripts"
    repo_hooks = repo_scripts / "git-hooks"
    repo_hooks.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_hook_path(), repo_hooks / "pre-commit")
    shutil.copy2(_installer_path(), repo_scripts / "install-hooks.sh")

    installer = repo_scripts / "install-hooks.sh"

    result = subprocess.run(
        [_BASH, str(installer)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, (
        f"install-hooks.sh failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    installed = repo / ".git" / "hooks" / "pre-commit"
    assert installed.exists() or installed.is_symlink(), (
        f"pre-commit hook was not installed at {installed}"
    )
    # Executable, or a symlink (symlink target carries the exec bit).
    is_exec = os.access(str(installed), os.X_OK)
    assert is_exec or installed.is_symlink(), (
        f"Installed pre-commit hook is neither executable nor a symlink: {installed}"
    )

    # Idempotent: a second run must also succeed.
    result2 = subprocess.run(
        [_BASH, str(installer)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result2.returncode == 0, (
        f"Second install-hooks.sh run (idempotency) failed.\n"
        f"STDOUT:\n{result2.stdout}\nSTDERR:\n{result2.stderr}"
    )
    assert installed.exists() or installed.is_symlink(), (
        "pre-commit hook missing after idempotent re-run"
    )
