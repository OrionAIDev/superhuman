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
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# `tests/` (where publication_patterns.py lives) is not itself a package on
# `sys.path` outside pytest's own rootdir-relative import; this mirrors how
# `tests/test_content.py` (a sibling of publication_patterns.py) imports it.
sys.path.insert(0, str(_REPO_ROOT / "tests"))
from publication_patterns import TOKENS_FILE, find_tokens, load_tokens  # noqa: E402

#: The two prose files Chunk 2 is permitted to edit that actually carry the
#: new handoff-emission subsection (`SKILL.md`/`phases/4-acceptance.md` were
#: available but not used for this seam — see PLAN.md Chunk 2 Step 2).
_EDITED_FILES = ("roles/pm.md", "phases/3-implementation.md")

#: The literal command shape the new subsection must name (TC-10, W-NFR-4).
#: Spelled as the module invocation, which is the only form that resolves:
#: superhuman ships no packaging, so no `fleet` executable exists on `PATH`,
#: and `cli.py`'s package-relative imports rule out running it as a script.
#: The `\s*` between tokens tolerates the line wrapping prose requires.
_COMMAND_SHAPE_RE = re.compile(
    r"python -m scripts\.fleet\.cli observe handoff-emit\s+"
    r"--prompt-file \.\.\.\s+--output-file \.\.\."
)

#: Phrases proving the non-blocking / logged-failure-and-proceed language is
#: present in the subsection's own words (additive-edit rule 2).
_NON_BLOCKING_MARKERS = ("never block", "non-gating", "non-blocking")
_LOGGED_AND_PROCEEDS_MARKERS = ("logged", "log")
_PROCEEDS_MARKERS = ("proceed", "continue", "unaffected")

def _operator_tokens() -> list[str]:
    """Read operator tokens the same way `test_content.py`'s canonical
    `test_operator_tokens_are_absent` guard does (W-NFR-5): via
    `publication_patterns.load_tokens` against the gitignored
    `.publication-tokens` file at the repo root, never hardcoded in this
    tracked, shipped test file. Skips cleanly (matching the canonical
    guard's own behavior) when the file is absent — the normal case for
    anyone who is not maintaining a private fork with its own operator
    vocabulary.

    Delegating to `load_tokens` (rather than re-parsing the file here) keeps
    this module's token *loading* on the same single code path as the
    canonical guard — matching is likewise delegated to `find_tokens` at
    each call site below, so this module's operator-token checks and
    `test_operator_tokens_are_absent` share their entire mechanism, not
    just their token source.
    """
    tokens_path = _REPO_ROOT / TOKENS_FILE
    if not tokens_path.is_file():
        pytest.skip(f"no {TOKENS_FILE} — nothing operator-specific to check")
    tokens = load_tokens(tokens_path)
    if not tokens:
        pytest.skip(f"{TOKENS_FILE} exists but lists no tokens")
    return tokens


def _merge_base_with_main() -> str | None:
    """Return the merge-base commit of `HEAD` and `origin/main`, or `None`.

    Symbolic on purpose (per TEST.md's TC-10 guidance) rather than a pinned
    hash: this project has already lived through one history rewrite that
    silently orphaned a pinned SHA. Returns `None` (never raises) if git or
    the ref is unavailable, so callers can skip cleanly instead of failing.

    **Mid-merge correction:** a pre-commit hook runs before the merge commit
    exists, so `HEAD` is still the pre-merge tip and `git merge-base HEAD
    origin/main` resolves to the OLD (pre-merge) merge-base — which then
    picks up `main`'s own independent commits (anything `main` itself
    changed since branching) as if THIS branch had changed them, a false
    positive discovered live merging `origin/main` into `fleet-wiring`. Once
    the merge commit lands, `origin/main` becomes a direct parent and this
    function's normal computation would return `origin/main`'s own tip — so
    while `MERGE_HEAD` exists, this returns it directly rather than the
    stale pre-merge value, matching what the post-commit answer will be.
    """
    merge_head = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "MERGE_HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if merge_head.returncode == 0 and merge_head.stdout.strip():
        return merge_head.stdout.strip()

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
            "'python -m scripts.fleet.cli observe handoff-emit --prompt-file ... "
            "--output-file ...'"
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
        hits = find_tokens(subsection, _operator_tokens())
        assert not hits, f"{relative_path}: operator token(s) found: {hits!r}"


#: W-NFR-4 rule 1 forbids changing a line that already existed in these
#: orchestration files, so that a resumed pre-existing project fires identical
#: gates in identical order. One narrow class of change cannot satisfy that and
#: still be correct: a line naming the fleet CLI as a bare `fleet <verb>`
#: command. No such command has ever been installable — the repo ships no
#: packaging, so nothing puts a `fleet` executable on `PATH`, and
#: `scripts/fleet/cli.py`'s package-relative imports rule out running it as a
#: loose script. Re-spelling those lines to the module form
#: (`python -m scripts.fleet.cli <verb>`) necessarily removes them.
#:
#: The exemption is deliberately as small as the defect: it matches ONLY a
#: line naming a bare-`fleet` subcommand, so any other removal in these files
#: still fails. It also expires on its own — once no line spells a command
#: that way, it matches nothing. The behavioural guarantee it protects is
#: unaffected: these are non-gating observational call-outs, and the
#: gate-sequence check (`TestGateFrontmatterUnchanged`) is independent.
_LEGACY_BARE_FLEET_INVOCATION_RE = re.compile(
    r"`fleet (?:observe|status|handoff|register|query|gen-view|done)\b"
)


def _disallowed_removed_lines(diff_stdout: str) -> list[str]:
    """Return removed diff lines that W-NFR-4 rule 1 does not permit."""
    return [
        line
        for line in diff_stdout.splitlines()
        if line.startswith("-")
        and not line.startswith("---")
        and not _LEGACY_BARE_FLEET_INVOCATION_RE.search(line)
    ]


class TestAdditiveDiffInvariant:
    """TC-10's application of TC-24: the edited files' diffs contain zero removed
    lines, save the narrow bare-`fleet` re-spelling exemption above.
    """

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

        removed_lines = _disallowed_removed_lines(result.stdout)
        assert removed_lines == [], (
            f"{relative_path}: diff against merge-base {merge_base} removed lines:\n"
            + "\n".join(removed_lines)
        )


# --- TC-14: hook templates contain no operator tokens and call the shipped
# entry point (W-NFR-5, Chunk 3) --------------------------------------------

#: The two operator-neutral hook templates Chunk 3 ships. Each must invoke
#: the identical `observe <event>` verb the prose floor uses — never a
#: parallel/divergent invocation (DESIGN's hybrid boundary). Matched on the
#: real module invocation (`python -m scripts.fleet.cli observe <event>`),
#: since no `fleet` executable is ever installed on `PATH`.
_HOOK_TEMPLATES: dict[str, str] = {
    "templates/hooks/SessionStart": "-m scripts.fleet.cli observe launch",
    "templates/hooks/PreToolUse": "-m scripts.fleet.cli observe dispatch",
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
        hits = find_tokens(text, _operator_tokens())
        assert not hits, f"{relative_path}: operator token(s) found: {hits!r}"

    @pytest.mark.parametrize("relative_path", sorted(_HOOK_TEMPLATES))
    def test_template_invokes_the_shipped_entry_point_verbatim(self, relative_path: str) -> None:
        text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        expected_verb = _HOOK_TEMPLATES[relative_path]
        assert expected_verb in text, (
            f"{relative_path}: does not invoke the literal '{expected_verb}' entry point"
        )

    def test_pretooluse_names_harness_subagent(self) -> None:
        """Phase 3.3 preflight FIX 3: PreToolUse must not silently default to
        `--harness portable` — a dispatch unit is represented by
        `--harness subagent`, per DESIGN's Decision C, or every registration
        this template produces is mislabeled.
        """
        text = (_REPO_ROOT / "templates/hooks/PreToolUse").read_text(encoding="utf-8")
        assert "--harness subagent" in text, (
            "templates/hooks/PreToolUse does not name '--harness subagent' — "
            "it will silently default to --harness portable"
        )


# --- TC-20: spawned-dispatch seam content — granularity rule stated once,
# role-prompt predicate checkable (W-FR-1, Q3 / Decision C, Chunk 5) --------

#: The literal command shape the two new subsections must name. `\s*`
#: tolerates the line wrapping prose requires around the longer module form.
_DISPATCH_COMMAND_SHAPE_RE = re.compile(
    r"python -m scripts\.fleet\.cli observe dispatch\s+--harness subagent"
)

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
            "command shape 'python -m scripts.fleet.cli observe dispatch --harness subagent'"
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
        hits = find_tokens(subsection, _operator_tokens())
        assert not hits, f"{relative_path}: operator token(s) found: {hits!r}"

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


# --- TC-15: `SKILL.md`'s launch-flip first-action step names the command,
# and states — in its own words — that it is idempotent and inert-when-off
# (W-FR-4, W-FR-7, Chunk 3/7). The behavioral half (fleet disabled -> zero
# writes; no matching row -> journaled no-op, no exception) is covered in
# `tests/fleet/test_observe.py::TestDisabledWorkspace::
# test_launch_produces_zero_writes_when_disabled` and
# `TestFuzzyLaunchFlip::test_launch_not_found_is_journaled` — this class adds
# the missing content-level half: does SKILL.md's prose actually say so. ----

_SKILL_MD_LAUNCH_COMMAND_RE = re.compile(
    r"python -m scripts\.fleet\.cli observe launch\b"
)
_IDEMPOTENT_MARKERS = ("idempotent",)
_INERT_WHEN_OFF_MARKERS = ("inert",)


def _skill_md_launch_step_text() -> str:
    """Extract SKILL.md's `## Fleet observation — launch flip` subsection body."""
    return _new_subsection_text_by_heading(
        _REPO_ROOT / "SKILL.md", "fleet observation", "launch"
    )


def _new_subsection_text_by_heading(file_path: Path, *substrings: str) -> str:
    """Same extraction shape as `_find_heading_containing`, returned as text."""
    lines, start, end = _find_heading_containing(file_path, *substrings)
    return "\n".join(lines[start:end])


class TestSkillMdLaunchStepSeamContent:
    """TC-15: the additive `SKILL.md` first-action step names the literal
    command, states it is idempotent, states it is inert-when-off, is
    non-gating, and contains no operator token.
    """

    def test_subsection_names_the_literal_command(self) -> None:
        subsection = _skill_md_launch_step_text()
        assert _SKILL_MD_LAUNCH_COMMAND_RE.search(subsection), (
            "SKILL.md launch-flip subsection does not name the literal "
            "'python -m scripts.fleet.cli observe launch' command"
        )

    def test_subsection_states_idempotent(self) -> None:
        subsection = _skill_md_launch_step_text().lower()
        assert any(marker in subsection for marker in _IDEMPOTENT_MARKERS), (
            "SKILL.md launch-flip subsection never states the step is idempotent"
        )

    def test_subsection_states_inert_when_off(self) -> None:
        subsection = _skill_md_launch_step_text().lower()
        assert any(marker in subsection for marker in _INERT_WHEN_OFF_MARKERS), (
            "SKILL.md launch-flip subsection never states the step is inert when off"
        )

    def test_subsection_states_non_blocking_and_failure_logged_and_proceeds(self) -> None:
        subsection = _skill_md_launch_step_text().lower()
        assert any(marker in subsection for marker in _NON_BLOCKING_MARKERS), (
            "SKILL.md launch-flip subsection never states it is non-blocking"
        )
        assert any(marker in subsection for marker in _LOGGED_AND_PROCEEDS_MARKERS), (
            "SKILL.md launch-flip subsection never states a failure is logged"
        )
        assert any(marker in subsection for marker in _PROCEEDS_MARKERS), (
            "SKILL.md launch-flip subsection never states execution proceeds"
        )

    def test_subsection_contains_no_operator_token(self) -> None:
        subsection = _skill_md_launch_step_text()
        hits = find_tokens(subsection, _operator_tokens())
        assert not hits, f"SKILL.md launch-flip subsection: operator token(s) found: {hits!r}"


# --- TC-28: `docs/fleet-observation.md` covers the required topics
# (W-FR-8 doc-level, Decision E mitigation (iv), Chunk 7). ------------------

_FLEET_OBSERVATION_DOC = _REPO_ROOT / "docs" / "fleet-observation.md"

#: Each entry: (topic label, at-least-one-of markers, case-insensitive).
_REQUIRED_DOC_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("enablement", ("enable", "opt-in", "fleet.enabled")),
    ("fail-soft/fail-closed boundary", ("fail-soft", "fail-closed")),
    ("granularity rule", ("granularity rule",)),
    ("hook install", ("install", "session start", "pretooluse")),
    ("observe status", ("observe status", "scripts.fleet.cli observe status")),
    (
        "stale-output-is-candidates-not-verdicts caveat",
        ("candidates to confirm, not a verdict", "candidates to confirm, not as a verdict"),
    ),
)


class TestFleetObservationDocContent:
    """TC-28: the shipped operator doc covers every required topic, and
    carries no operator token (it is subject to the same publication guard
    as every other shipped file).
    """

    def test_doc_exists(self) -> None:
        assert _FLEET_OBSERVATION_DOC.is_file(), "docs/fleet-observation.md is missing"

    @pytest.mark.parametrize("topic, markers", _REQUIRED_DOC_TOPICS, ids=[t for t, _ in _REQUIRED_DOC_TOPICS])
    def test_doc_covers_required_topic(self, topic: str, markers: tuple[str, ...]) -> None:
        # Collapse whitespace (including markdown's natural line-wrapping
        # inside a paragraph) so a marker phrase that happens to wrap across
        # source lines still matches — the guard checks prose content, not
        # line layout.
        raw = _FLEET_OBSERVATION_DOC.read_text(encoding="utf-8").lower()
        text = re.sub(r"\s+", " ", raw)
        assert any(marker in text for marker in markers), (
            f"docs/fleet-observation.md does not appear to cover: {topic}"
        )

    def test_doc_contains_no_operator_token(self) -> None:
        text = _FLEET_OBSERVATION_DOC.read_text(encoding="utf-8")
        hits = find_tokens(text, _operator_tokens())
        assert not hits, f"docs/fleet-observation.md: operator token(s) found: {hits!r}"
