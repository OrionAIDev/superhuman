"""Tests for scripts/cleanup-project.sh — archive-never-delete guarantee.

The cleanup script MUST move project state to an archive location and MUST NOT
delete any content. This test exercises that core safety guarantee.

Skipped automatically on runners where a suitable bash is not available.

On Windows we require Git Bash (at the standard install path) because WSL bash
cannot resolve Windows absolute paths. On POSIX any bash will do.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
def test_cleanup_archives_not_deletes(skill_root: Path, tmp_path: Path) -> None:
    """cleanup-project.sh moves slug contents to archive dir; nothing is deleted.

    Setup:
        <tmp_project>/
            docs/superhuman/
                my-slug/
                    SUPERHUMAN.md   (sentinel file with known content)

    After running the script the sentinel must be present inside
    docs/superhuman/archive/my-slug-pre-cleanup-<timestamp>/ and MUST NOT
    remain at docs/superhuman/my-slug/ (i.e. it was moved, not copied or left).
    The archive directory itself must exist and contain the sentinel content.
    No data is lost.
    """
    slug = "my-slug"
    slug_dir = tmp_path / "docs" / "superhuman" / slug
    slug_dir.mkdir(parents=True)

    sentinel_content = "sentinel: archive-never-delete test\n"
    sentinel = slug_dir / "SUPERHUMAN.md"
    sentinel.write_text(sentinel_content, encoding="utf-8")

    script = skill_root / "scripts" / "cleanup-project.sh"
    assert script.is_file(), f"cleanup script not found at {script}"

    result = subprocess.run(
        [_BASH, str(script), str(tmp_path), "--slug", slug],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, (
        f"cleanup-project.sh exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )

    # Archive directory must exist
    archive_root = tmp_path / "docs" / "superhuman" / "archive"
    assert archive_root.is_dir(), "archive/ directory was not created"

    archive_subdirs = list(archive_root.iterdir())
    assert len(archive_subdirs) == 1, (
        f"Expected exactly one archive subdirectory, got: {[d.name for d in archive_subdirs]}"
    )
    archive_dir = archive_subdirs[0]
    assert archive_dir.name.startswith(f"{slug}-pre-cleanup-"), (
        f"Archive dir name does not match expected pattern: {archive_dir.name}"
    )

    # Sentinel must be inside the archive (content preserved)
    archived_sentinel = archive_dir / "SUPERHUMAN.md"
    assert archived_sentinel.is_file(), (
        f"Sentinel file not found in archive at {archived_sentinel}"
    )
    assert archived_sentinel.read_text(encoding="utf-8") == sentinel_content, (
        "Archived SUPERHUMAN.md content differs from original — data was corrupted"
    )

    # Slug directory must no longer exist (it was moved, not copied)
    assert not slug_dir.is_dir(), (
        f"Original slug dir {slug_dir} still exists after cleanup — content was not moved"
    )

    # WHY.md and RESTORE.md must be present per archive-never-delete principle
    assert (archive_dir / "WHY.md").is_file(), "WHY.md missing from archive"
    assert (archive_dir / "RESTORE.md").is_file(), "RESTORE.md missing from archive"


@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
def test_cleanup_auto_detects_slug(skill_root: Path, tmp_path: Path) -> None:
    """cleanup-project.sh auto-detects the slug when --slug is omitted."""
    slug = "auto-detected-slug"
    slug_dir = tmp_path / "docs" / "superhuman" / slug
    slug_dir.mkdir(parents=True)
    (slug_dir / "VISION.md").write_text("# Vision\nautomated test\n", encoding="utf-8")

    script = skill_root / "scripts" / "cleanup-project.sh"
    result = subprocess.run(
        [_BASH, str(script), str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, (
        f"cleanup-project.sh exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    archive_root = tmp_path / "docs" / "superhuman" / "archive"
    assert archive_root.is_dir(), "archive/ directory was not created on auto-detect path"
    archive_subdirs = list(archive_root.iterdir())
    assert len(archive_subdirs) == 1
    assert archive_subdirs[0].name.startswith(f"{slug}-pre-cleanup-")
    assert not slug_dir.is_dir(), "slug dir still exists after auto-detected cleanup"


# ---------------------------------------------------------------------------
# E12 — root-doc archiving with --include-code
# ---------------------------------------------------------------------------

_ROOT_DOCS = {
    "README.md": "# Readme\nproject readme\n",
    "CHANGELOG.md": "# Changelog\n- v0\n",
    "LICENSE": "MIT License — sentinel\n",
    ".gitignore": "__pycache__/\n*.pyc\n",
    ".env.example": "API_KEY=replace-me\n",
}

_ENV_SECRET = "API_KEY=super-secret-do-not-archive\n"


def _make_root_doc_project(root: Path, slug: str, *, with_env: bool) -> None:
    """Create a tmp project with a slug dir, the five root docs, and optionally .env.

    cleanup-project.sh exits non-zero unless docs/superhuman/<slug>/ exists, so a
    sentinel slug dir is always created.
    """
    slug_dir = root / "docs" / "superhuman" / slug
    slug_dir.mkdir(parents=True)
    (slug_dir / "SUPERHUMAN.md").write_text(
        "sentinel: root-doc archiving test\n", encoding="utf-8"
    )
    for name, content in _ROOT_DOCS.items():
        (root / name).write_text(content, encoding="utf-8")
    if with_env:
        (root / ".env").write_text(_ENV_SECRET, encoding="utf-8")


@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
def test_cleanup_includes_root_docs_with_include_code(skill_root: Path, tmp_path: Path) -> None:
    """A4 — --include-code archives the five root docs but NEVER archives .env.

    The archive dir must contain README.md, CHANGELOG.md, LICENSE, .gitignore,
    and .env.example with content preserved. A real .env (secrets) must stay at
    the project root, untouched.
    """
    slug = "my-slug"
    _make_root_doc_project(tmp_path, slug, with_env=True)

    script = skill_root / "scripts" / "cleanup-project.sh"
    assert script.is_file(), f"cleanup script not found at {script}"

    result = subprocess.run(
        [_BASH, str(script), str(tmp_path), "--slug", slug, "--include-code"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, (
        f"cleanup-project.sh exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    archive_root = tmp_path / "docs" / "superhuman" / "archive"
    assert archive_root.is_dir(), "archive/ directory was not created"
    archive_subdirs = list(archive_root.iterdir())
    assert len(archive_subdirs) == 1, (
        f"Expected exactly one archive subdirectory, got: {[d.name for d in archive_subdirs]}"
    )
    archive_dir = archive_subdirs[0]

    # All five root docs must be archived with content preserved.
    for name, content in _ROOT_DOCS.items():
        archived = archive_dir / name
        assert archived.is_file(), (
            f"root doc {name} not archived (expected at {archived})\n"
            f"STDOUT:\n{result.stdout}"
        )
        assert archived.read_text(encoding="utf-8") == content, (
            f"archived {name} content differs from original"
        )
        # And it should no longer be at the project root (moved, not copied).
        assert not (tmp_path / name).exists(), (
            f"root doc {name} still at project root after archiving"
        )

    # .env must NEVER be archived and must remain at the project root.
    assert not (archive_dir / ".env").exists(), (
        ".env was archived — secrets must never be moved"
    )
    env_at_root = tmp_path / ".env"
    assert env_at_root.is_file(), ".env was removed from project root — must stay put"
    assert env_at_root.read_text(encoding="utf-8") == _ENV_SECRET, (
        ".env content was altered"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-native .cmd shim test")
@pytest.mark.skipif(_BASH is None, reason="bash not available on this runner (Windows: Git Bash not found at standard path)")
def test_cleanup_handles_windows_native_paths(skill_root: Path, tmp_path: Path) -> None:
    """A5 — the .cmd shim forwards a native backslash path + --include-code.

    Drives scripts/cleanup-project.cmd directly (shell=False) with a Windows
    absolute path. The archive must contain the slug's SUPERHUMAN content and
    the five root docs.
    """
    slug = "my-slug"
    _make_root_doc_project(tmp_path, slug, with_env=False)

    cmd_path = skill_root / "scripts" / "cleanup-project.cmd"
    assert cmd_path.is_file(), f"cleanup shim not found at {cmd_path}"

    result = subprocess.run(
        [str(cmd_path), str(tmp_path), "--slug", slug, "--include-code"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, (
        f"cleanup-project.cmd exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    archive_root = tmp_path / "docs" / "superhuman" / "archive"
    assert archive_root.is_dir(), "archive/ directory was not created via .cmd shim"
    archive_subdirs = list(archive_root.iterdir())
    assert len(archive_subdirs) == 1, (
        f"Expected exactly one archive subdirectory, got: {[d.name for d in archive_subdirs]}"
    )
    archive_dir = archive_subdirs[0]
    assert archive_dir.name.startswith(f"{slug}-pre-cleanup-")

    # Slug's SUPERHUMAN.md must be archived.
    archived_sentinel = archive_dir / "SUPERHUMAN.md"
    assert archived_sentinel.is_file(), "SUPERHUMAN.md not archived via .cmd path"
    assert archived_sentinel.read_text(encoding="utf-8") == (
        "sentinel: root-doc archiving test\n"
    )

    # The five root docs must be archived with content preserved.
    for name, content in _ROOT_DOCS.items():
        archived = archive_dir / name
        assert archived.is_file(), f"root doc {name} not archived via .cmd path"
        assert archived.read_text(encoding="utf-8") == content, (
            f"archived {name} content differs from original (.cmd path)"
        )
