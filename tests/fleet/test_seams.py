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


# --- TC-20: spawned-dispatch seam content — granularity rule stated once,
# role-prompt predicate checkable (W-FR-1, Q3 / Decision C, Chunk 5) --------

#: The literal command shape the two new subsections must name.
_DISPATCH_COMMAND_SHAPE_RE = re.compile(r"fleet observe dispatch --harness subagent")

#: The canonical granularity-rule statement (Decision C). Matched loosely
#: enough to survive minor rewording, tightly enough that a drifted second
#: copy in the other file would still be caught by the exact-count check
#: below (TC-20's "not duplicated with drifting wording" requirement).
_GRANULARITY_RULE_RE = re.compile(
    r"registers?\s+iff\s+the\s+dispatched\s+prompt\s+leads\s+with\s+a\s+`roles/\*\.md`\s+block",
    re.IGNORECASE,
)


def _find_heading_containing(file_path: Path, *substrings: str) -> tuple[list[str], int, int]:
    """Return `(lines, start, end)` for the first `## ` heading whose text (lowercased)
    contains every one of `substrings`, spanning from the heading line up to
    (not including) the next `## ` heading or end of file.
    """
    lines = file_path.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        lowered = line.lower()
        if line.startswith("## ") and all(s in lowered for s in substrings):
            start = i
            break
    assert start is not None, (
        f"{file_path}: no '## ...' heading found containing all of {substrings!r}"
    )
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return lines, start, end


def _dispatch_subsection_text(file_path: Path) -> str:
    """Extract the new spawned-dispatch-observation subsection's body text."""
    lines, start, end = _find_heading_containing(file_path, "dispatch", "observation")
    return "\n".join(lines[start:end])


class TestSpawnedDispatchSeamContent:
    """TC-20: the granularity rule is stated exactly once, and each new dispatch
    call-out names the literal `fleet observe dispatch --harness subagent` shape.
    """

    @pytest.mark.parametrize("relative_path", _EDITED_FILES)
    def test_subsection_exists_and_names_the_command_shape(self, relative_path: str) -> None:
        subsection = _dispatch_subsection_text(_REPO_ROOT / relative_path)
        assert _DISPATCH_COMMAND_SHAPE_RE.search(subsection), (
            f"{relative_path}: dispatch-observation subsection does not name the literal "
            "command shape 'fleet observe dispatch --harness subagent'"
        )

    @pytest.mark.parametrize("relative_path", _EDITED_FILES)
    def test_subsection_states_non_blocking_and_failure_logged_and_proceeds(
        self, relative_path: str
    ) -> None:
        subsection = _dispatch_subsection_text(_REPO_ROOT / relative_path).lower()
        assert any(marker in subsection for marker in _NON_BLOCKING_MARKERS), (
            f"{relative_path}: dispatch-observation subsection never states it is non-blocking"
        )
        assert any(marker in subsection for marker in _LOGGED_AND_PROCEEDS_MARKERS), (
            f"{relative_path}: dispatch-observation subsection never states a failure is logged"
        )
        assert any(marker in subsection for marker in _PROCEEDS_MARKERS), (
            f"{relative_path}: dispatch-observation subsection never states execution proceeds"
        )

    @pytest.mark.parametrize("relative_path", _EDITED_FILES)
    def test_subsection_contains_no_operator_token(self, relative_path: str) -> None:
        subsection = _dispatch_subsection_text(_REPO_ROOT / relative_path)
        for token in _OPERATOR_TOKENS:
            assert token not in subsection, f"{relative_path}: operator token {token!r} found"

    def test_granularity_rule_stated_exactly_once_across_the_shipped_seams(self) -> None:
        """Q3 / Decision C: a single canonical statement — the dispatch call-out in the
        other file cites it by reference rather than restating it, so a future edit
        cannot update one copy and drift from the other.
        """
        total_matches = 0
        for relative_path in _EDITED_FILES:
            text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
            total_matches += len(_GRANULARITY_RULE_RE.findall(text))
        assert total_matches == 1, (
            "expected the granularity rule ('registers iff the dispatched prompt leads "
            f"with a `roles/*.md` block') to appear exactly once across {_EDITED_FILES}, "
            f"found {total_matches}"
        )

    def test_non_canonical_file_cites_rather_than_restates(self) -> None:
        """`phases/3-implementation.md`'s call-out references `roles/pm.md` by name
        instead of restating the rule (the file that does NOT carry the canonical
        statement, per the exact-count check above).
        """
        subsection = _dispatch_subsection_text(_REPO_ROOT / "phases/3-implementation.md")
        assert not _GRANULARITY_RULE_RE.search(subsection)
        assert "roles/pm.md" in subsection
