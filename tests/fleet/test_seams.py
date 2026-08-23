"""TC-10: seam-content assertions for the additive prose seams superhuman's own
process docs carry (W-NFR-4, W-NFR-5).

Chunk 2 adds a new, non-gating subsection to `roles/pm.md` and
`phases/3-implementation.md` describing the manual-handoff-emission step
(`fleet observe handoff-emit`). This module parses each edited file's new
subsection and asserts:

- it exists and names the literal command shape;
- it states, in its own words, that the step never blocks the surrounding
  gate and that a failure is logged while execution proceeds;
- it contains no operator token (a personal name, hostname, or org-internal
  path/token);
- the file's diff against the merge-base with `origin/main` contains zero
  removed lines — the general additive-diff invariant (TC-24), applied here
  specifically to the two files this chunk touches.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The two prose files Chunk 2 is permitted to edit that actually carry the
#: new handoff-emission subsection (`SKILL.md`/`phases/4-acceptance.md` were
#: available but not used for this seam — see PLAN.md Chunk 2 Step 2).
_EDITED_FILES = ("roles/pm.md", "phases/3-implementation.md")

#: The literal command shape the new subsection must name (TC-10, W-NFR-4).
_COMMAND_SHAPE_RE = re.compile(
    r"fleet observe handoff-emit --prompt-file \.\.\. --output-file \.\.\."
)

#: Phrases proving the non-blocking / logged-failure-and-proceed language is
#: present in the subsection's own words (additive-edit rule 2).
_NON_BLOCKING_MARKERS = ("never block", "non-gating", "non-blocking")
_LOGGED_AND_PROCEEDS_MARKERS = ("logged", "log")
_PROCEEDS_MARKERS = ("proceed", "continue", "unaffected")

#: Operator tokens that must never appear in shipped prose (W-NFR-5) — kept
#: intentionally narrow (this is a structural smoke check, not a scanner).
_OPERATOR_TOKENS = ("Chris", "orionlab", "oriontest", "OrionAIDev", "trapezia-llc")


def _merge_base_with_main() -> str | None:
    """Return the merge-base commit of `HEAD` and `origin/main`, or `None`.

    Symbolic on purpose (per TEST.md's TC-10 guidance) rather than a pinned
    hash: this project has already lived through one history rewrite that
    silently orphaned a pinned SHA. Returns `None` (never raises) if git or
    the ref is unavailable, so callers can skip cleanly instead of failing.
    """
    try:
        result = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _new_subsection_text(file_path: Path) -> str:
    """Extract the new `## Handoff prompt emission` / `## Chunk-boundary handoff emission`
    subsection's body text from `file_path` — from its heading line up to (not
    including) the next `## ` heading or end of file.
    """
    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and "handoff" in line.lower() and "emission" in line.lower():
            start = i
            break
    assert start is not None, f"{file_path}: no '## ... handoff ... emission' subsection found"

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


class TestHandoffEmissionSeamContent:
    """TC-10: the new subsection names the command and states non-blocking/logged-and-proceeds."""

    @pytest.mark.parametrize("relative_path", _EDITED_FILES)
    def test_subsection_names_the_literal_command_shape(self, relative_path: str) -> None:
        subsection = _new_subsection_text(_REPO_ROOT / relative_path)
        assert _COMMAND_SHAPE_RE.search(subsection), (
            f"{relative_path}: subsection does not name the literal command shape "
            "'fleet observe handoff-emit --prompt-file ... --output-file ...'"
        )

    @pytest.mark.parametrize("relative_path", _EDITED_FILES)
    def test_subsection_states_non_blocking_and_failure_logged_and_proceeds(
        self, relative_path: str
    ) -> None:
        subsection = _new_subsection_text(_REPO_ROOT / relative_path).lower()
        assert any(marker in subsection for marker in _NON_BLOCKING_MARKERS), (
            f"{relative_path}: subsection never states the step is non-blocking"
        )
        assert any(marker in subsection for marker in _LOGGED_AND_PROCEEDS_MARKERS), (
            f"{relative_path}: subsection never states a failure is logged"
        )
        assert any(marker in subsection for marker in _PROCEEDS_MARKERS), (
            f"{relative_path}: subsection never states execution proceeds after a failure"
        )

    @pytest.mark.parametrize("relative_path", _EDITED_FILES)
    def test_subsection_contains_no_operator_token(self, relative_path: str) -> None:
        subsection = _new_subsection_text(_REPO_ROOT / relative_path)
        for token in _OPERATOR_TOKENS:
            assert token not in subsection, f"{relative_path}: operator token {token!r} found"


class TestAdditiveDiffInvariant:
    """TC-10's application of TC-24: the edited files' diffs contain zero removed lines."""

    @pytest.mark.parametrize("relative_path", _EDITED_FILES)
    def test_diff_against_merge_base_has_zero_removed_lines(self, relative_path: str) -> None:
        merge_base = _merge_base_with_main()
        if merge_base is None:
            pytest.skip("git or origin/main merge-base unavailable in this environment")

        result = subprocess.run(
            ["git", "diff", merge_base, "--", relative_path],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"git diff against {merge_base} failed in this environment")

        removed_lines = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("-") and not line.startswith("---")
        ]
        assert removed_lines == [], (
            f"{relative_path}: diff against merge-base {merge_base} removed lines:\n"
            + "\n".join(removed_lines)
        )


# --- TC-14: hook templates contain no operator tokens and call the shipped
# entry point (W-NFR-5, Chunk 3) --------------------------------------------

#: The two operator-neutral hook templates Chunk 3 ships. Each must invoke
#: the identical `fleet observe <event>` verb the prose floor uses — never a
#: parallel/divergent invocation (DESIGN's hybrid boundary).
_HOOK_TEMPLATES: dict[str, str] = {
    "templates/hooks/SessionStart": "fleet observe launch",
    "templates/hooks/PreToolUse": "fleet observe dispatch",
}


class TestHookTemplateSeamContent:
    """TC-14: templates/hooks/{SessionStart,PreToolUse} exist, are clean, and

    invoke the same CLI verb group the portable prose floor invokes.
    """

    @pytest.mark.parametrize("relative_path", sorted(_HOOK_TEMPLATES))
    def test_template_exists(self, relative_path: str) -> None:
        path = _REPO_ROOT / relative_path
        assert path.is_file(), f"{relative_path}: hook template missing"

    @pytest.mark.parametrize("relative_path", sorted(_HOOK_TEMPLATES))
    def test_template_contains_no_operator_token(self, relative_path: str) -> None:
        text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for token in _OPERATOR_TOKENS:
            assert token not in text, f"{relative_path}: operator token {token!r} found"

    @pytest.mark.parametrize("relative_path", sorted(_HOOK_TEMPLATES))
    def test_template_invokes_the_shipped_entry_point_verbatim(self, relative_path: str) -> None:
        text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        expected_verb = _HOOK_TEMPLATES[relative_path]
        assert expected_verb in text, (
            f"{relative_path}: does not invoke the literal '{expected_verb}' entry point"
        )
