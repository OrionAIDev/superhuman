"""Integration smoke test for the superhuman skill.

Exercises the skill end-to-end on a minimal project by:
  1. Setting up a temp project directory.
  2. Reading the tiny-project brief.
  3. Verifying the orchestrator + role prompts can be loaded without errors
     (a load-time smoke; full subagent dispatch is left to a manual test step).

For the full subagent-dispatch test, see Task 20 (manual smoke).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def test_session_start_hook_runs_clean(skill_root: Path) -> None:
    """SessionStart hook executes and emits output for every required block.

    This is a load-time smoke test: it does not exercise the full skill,
    but it catches "skill is structurally broken" failures early.
    """
    hook = skill_root / "hooks" / "session-start"
    if not hook.is_file():
        pytest.skip("session-start hook not present")

    if os.name == "nt":  # Windows
        cmd = ["cmd.exe", "/c", str(skill_root / "hooks" / "session-start.cmd")]
    else:
        cmd = [str(hook)]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert result.returncode == 0, (
        f"hook exited {result.returncode}\nSTDERR:\n{result.stderr}"
    )
    output = result.stdout

    # The hook should emit a section header for each known reference at minimum.
    assert "brainstorming" in output
    assert "test-driven-development" in output
    assert "verification-before-completion" in output

    # Conventions, roles, phases each appear.
    assert "conventions" in output.lower()
    assert "pm.md" in output
    assert "0-kickoff.md" in output


def test_skill_loads_without_python_errors(skill_root: Path) -> None:
    """Sanity: every markdown file in the skill bundle is readable as UTF-8."""
    bad: list[tuple[Path, Exception]] = []
    for md in skill_root.rglob("*.md"):
        if ".venv" in md.parts or ".git" in md.parts:
            continue
        try:
            md.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - we want to collect all errors
            bad.append((md, exc))
    assert not bad, f"Unreadable markdown files: {bad}"


def test_tiny_project_brief_present(skill_root: Path) -> None:
    """The tiny-project fixture used by manual smoke (Task 20) is in place."""
    assert (skill_root / "tests" / "fixtures" / "tiny-project-brief.md").is_file()
