"""Structure validation tests for the superhuman skill bundle.

Verifies the directory shape matches DESIGN.md §4 — all expected files
present, no unexpected extras at known-fixed locations.
"""

from __future__ import annotations

from pathlib import Path

import pytest


EXPECTED_ROOT_FILES = {
    "SKILL.md",
    "README.md",
    "NOTICE.md",
    "VERSION",
    "CHANGELOG.md",
    "MIGRATION.md",
    ".gitignore",
}

EXPECTED_TOP_DIRS = {
    "hooks",
    "adaptation",
    "roles",
    "phases",
    "conventions",
    "references",
    "templates",
    "tests",
}

EXPECTED_ROLES = {
    "pm.md",
    "business-expert.md",
    "architect.md",
    "developer.md",
    "qa.md",
    "tester.md",
    "surrogate-user.md",
}

EXPECTED_PHASES = {
    "0-kickoff.md",
    "1-requirements.md",
    "2-design.md",
    "2.1-test-plan.md",
    "3-implementation.md",
    "3-autonomous-loop.md",
    "3.1-test-review.md",
    "3.2-docs-sync.md",
    "3.3-preflight-review.md",
    "4-acceptance.md",
}

EXPECTED_CONVENTIONS = {"python.md", "testing.md", "git.md", "autonomous.md", "source-cited.md"}

EXPECTED_REFERENCES = {
    "brainstorming",
    "deprecating-a-system",
    "dispatching-parallel-agents",
    "doubt-driven-development",
    "executing-plans",
    "finishing-a-development-branch",
    "receiving-code-review",
    "requesting-code-review",
    "roasting-code",
    "roasting-design-specs",
    "roasting-requirements",
    "roasting-shared",
    "subagent-driven-development",
    "systematic-debugging",
    "test-driven-development",
    "using-git-worktrees",
    "using-superpowers",
    "verification-before-completion",
    "writing-plans",
    "writing-skills",
}

# Reference-level markdown files (not skill directories) that must exist under references/.
EXPECTED_REFERENCE_FILES = {
    "definition-of-done.md",
    "orchestration-patterns.md",
}

EXPECTED_TEMPLATES = {
    "gate-headers.md",
    "delta-report.md.tpl",
    "SUPERHUMAN.md.tpl",
    "archive-WHY.md.tpl",
    "archive-RESTORE.md.tpl",
    "autonomous-run-summary.md.tpl",
}

EXPECTED_ARTIFACT_TEMPLATES = {
    "VISION.md.tpl",
    "REQUIREMENTS.md.tpl",
    "DESIGN.md.tpl",
    "ARCHITECTURE.md.tpl",
    "PLAN.md.tpl",
    "TEST.md.tpl",
    "README.md.tpl",
    "USER-GUIDE.md.tpl",
    "API.md.tpl",
    "DATA-MODEL.md.tpl",
    "RUNBOOK.md.tpl",
    "DEPLOYMENT.md.tpl",
    "THREAT-MODEL.md.tpl",
    "DECISIONS.md.tpl",
    "CHANGELOG.md.tpl",
    "DEVELOPING.md.tpl",
    "GOAL.md.tpl",
    "ROLLBACK.md.tpl",
}


def test_expected_root_files_present(skill_root: Path) -> None:
    """All expected root-level files exist."""
    missing = EXPECTED_ROOT_FILES - {p.name for p in skill_root.iterdir() if p.is_file()}
    assert not missing, f"Missing root files: {missing}"


def test_expected_top_dirs_present(skill_root: Path) -> None:
    """All expected top-level directories exist."""
    missing = EXPECTED_TOP_DIRS - {p.name for p in skill_root.iterdir() if p.is_dir()}
    assert not missing, f"Missing top-level dirs: {missing}"


def test_roles_match_spec(skill_root: Path) -> None:
    """All 7 role prompts exist and only those."""
    actual = {p.name for p in (skill_root / "roles").iterdir() if p.is_file()}
    assert actual == EXPECTED_ROLES, (
        f"Roles mismatch.\n  Missing: {EXPECTED_ROLES - actual}\n  Extra: {actual - EXPECTED_ROLES}"
    )


def test_phases_match_spec(skill_root: Path) -> None:
    """All 8 phase recipes exist."""
    actual = {p.name for p in (skill_root / "phases").iterdir() if p.is_file()}
    assert actual == EXPECTED_PHASES, (
        f"Phases mismatch.\n  Missing: {EXPECTED_PHASES - actual}\n  Extra: {actual - EXPECTED_PHASES}"
    )


def test_conventions_match_spec(skill_root: Path) -> None:
    """All 3 convention files exist."""
    actual = {p.name for p in (skill_root / "conventions").iterdir() if p.is_file()}
    assert actual == EXPECTED_CONVENTIONS, f"Conventions mismatch: {actual}"


# Shared resource folders inside references/ that are not skills themselves
# and therefore do not require a SKILL.md.
REFERENCE_SHARED_RESOURCES = {"roasting-shared"}


def test_references_match_spec(skill_root: Path) -> None:
    """All reference bundles exist; skill bundles have a SKILL.md; shared resources do not."""
    refs_root = skill_root / "references"
    actual_dirs = {p.name for p in refs_root.iterdir() if p.is_dir()}
    assert actual_dirs == EXPECTED_REFERENCES, (
        f"References mismatch.\n  Missing: {EXPECTED_REFERENCES - actual_dirs}\n  Extra: {actual_dirs - EXPECTED_REFERENCES}"
    )
    for ref in EXPECTED_REFERENCES:
        if ref in REFERENCE_SHARED_RESOURCES:
            continue  # shared resource folders don't need a SKILL.md
        assert (refs_root / ref / "SKILL.md").is_file(), f"References/{ref}/SKILL.md missing"


def test_reference_files_present(skill_root: Path) -> None:
    """Reference-level markdown files (not skill dirs) exist under references/.

    definition-of-done.md (the standing DoD bar) and orchestration-patterns.md
    (the endorsed/anti-pattern catalog) are harvested reference *files*, not skill
    directories, so they are validated here rather than by test_references_match_spec.
    """
    refs_root = skill_root / "references"
    missing = EXPECTED_REFERENCE_FILES - {p.name for p in refs_root.iterdir() if p.is_file()}
    assert not missing, f"Missing reference files: {missing}"


def test_templates_root_match_spec(skill_root: Path) -> None:
    """Core templates (non-artifacts) exist."""
    actual = {p.name for p in (skill_root / "templates").iterdir() if p.is_file()}
    assert actual == EXPECTED_TEMPLATES, f"Template root mismatch: {actual}"


def test_artifact_templates_match_spec(skill_root: Path) -> None:
    """All 16 artifact templates exist."""
    actual = {p.name for p in (skill_root / "templates" / "artifacts").iterdir() if p.is_file()}
    assert actual == EXPECTED_ARTIFACT_TEMPLATES, (
        f"Artifact templates mismatch.\n  Missing: {EXPECTED_ARTIFACT_TEMPLATES - actual}\n  Extra: {actual - EXPECTED_ARTIFACT_TEMPLATES}"
    )


def test_dispatch_adaptation_exists(skill_root: Path) -> None:
    """The single dispatch adaptation file exists."""
    assert (skill_root / "adaptation" / "dispatch.md").is_file()


def test_session_start_hook_executable(skill_root: Path) -> None:
    """SessionStart hook exists; on POSIX, it's executable."""
    hook = skill_root / "hooks" / "session-start"
    assert hook.is_file(), "hooks/session-start missing"
    import os
    import sys
    if sys.platform != "win32":
        assert os.access(hook, os.X_OK), "hooks/session-start not executable"


# Shell scripts that are executed directly — each carries a `#!/usr/bin/env bash`
# shebang and is documented (MIGRATION.md, README.md, SKILL.md, the phase recipes,
# the smoke checklists) as invoked bare, e.g. `scripts/release.sh`, not `bash
# scripts/release.sh`. git-hooks/pre-commit is additionally installed as
# .git/hooks/pre-commit, which git only runs when it is executable. On POSIX all
# must ship with the exec bit or they fail with "permission denied" at the point
# of use. examples/promote.sh.example is deliberately excluded: it is a template to
# copy and adapt, never run in place.
EXECUTABLE_SHELL_SCRIPTS = [
    "scripts/autonomous-iter.sh",
    "scripts/autonomous-precondition.sh",
    "scripts/autonomous-rollback.sh",
    "scripts/autonomous-summary.sh",
    "scripts/cleanup-project.sh",
    "scripts/git-hooks/pre-commit",
    "scripts/install-hooks.sh",
    "scripts/release.sh",
]


@pytest.mark.parametrize("rel_path", EXECUTABLE_SHELL_SCRIPTS)
def test_shell_script_executable(skill_root: Path, rel_path: str) -> None:
    """Each directly-executed shell script exists; on POSIX, it's executable.

    Mirrors test_session_start_hook_executable. The guard is POSIX-only because git
    does not track the exec bit on Windows and os.access(X_OK) returns True there for
    any file — so the check is meaningful only where the bit exists. On a fresh Linux
    CI checkout the file inherits its git index mode, so a 100644 regression fails here.
    """
    import os
    import sys

    script = skill_root / rel_path
    assert script.is_file(), f"{rel_path} missing"
    if sys.platform != "win32":
        assert os.access(script, os.X_OK), f"{rel_path} not executable"


def test_migration_doc_present_and_substantive(skill_root: Path) -> None:
    """MIGRATION.md exists, is substantive, and references the release scripts."""
    doc = skill_root / "MIGRATION.md"
    assert doc.is_file(), "MIGRATION.md missing at repo root"
    text = doc.read_text(encoding="utf-8")
    assert len(text) > 800, f"MIGRATION.md too short ({len(text)} chars)"
    assert "release.sh" in text, "MIGRATION.md should reference release.sh"
    assert "promote" in text.lower(), "MIGRATION.md should cover promotion/deploy"


def test_smoke_checklists_present(skill_root: Path) -> None:
    """Both platform smoke checklists exist and are non-trivial."""
    cc = skill_root / "tests" / "smoke" / "claude-code" / "SMOKE.md"
    oc = skill_root / "tests" / "smoke" / "openclaw" / "SMOKE.md"
    assert cc.is_file(), "tests/smoke/claude-code/SMOKE.md missing"
    assert oc.is_file(), "tests/smoke/openclaw/SMOKE.md missing"
    assert len(cc.read_text(encoding="utf-8")) > 400, "claude-code SMOKE.md too short"
    assert len(oc.read_text(encoding="utf-8")) > 400, "openclaw SMOKE.md too short"


def test_smoke_fixture_present(skill_root: Path) -> None:
    """The canonical hello-cli smoke fixture exists with its core files."""
    fixture = skill_root / "tests" / "smoke" / "fixtures" / "hello-cli"
    assert fixture.is_dir(), "tests/smoke/fixtures/hello-cli/ missing"
    assert (fixture / "hello_cli.py").is_file(), "hello-cli/hello_cli.py missing"
    assert (fixture / "README.md").is_file(), "hello-cli/README.md missing"


def test_autonomous_smoke_fixture_present(skill_root: Path) -> None:
    """The synthetic-bug autonomous smoke fixture + checklist exist."""
    base = skill_root / "tests" / "smoke" / "autonomous"
    assert (base / "SMOKE.md").is_file()
    proj = base / "synthetic-bug-project"
    assert (proj / "src" / "calculator.py").is_file()
    assert (proj / "GOAL.md").is_file()
    assert (proj / "docs" / "superhuman").is_dir(), "must ship a pre-G0/G1 SUPERHUMAN.md"
    assert (proj / "pristine").is_dir(), "must ship a pristine/ for clean re-runs"


def test_gitignore_includes_worktrees(skill_root: Path) -> None:
    """Per-chunk worktree directories must be gitignored to avoid accidental commits."""
    gitignore = (skill_root / ".gitignore").read_text(encoding="utf-8")
    assert ".worktrees/" in gitignore or ".worktrees" in gitignore, (
        ".gitignore should include .worktrees/ pattern (Phase 3 parallel-dispatch worktrees)"
    )
