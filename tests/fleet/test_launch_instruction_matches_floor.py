"""Preflight B7 regression: `scripts/fleet/adapter/base.py`'s
`_LAUNCH_INSTRUCTION` (embedded in every adapter's handoff prompt via
`format_launch_instruction()`) must name the SAME `observe <verb>` FR-15
names in `SKILL.md`'s session-start floor step.

**Preflight finding:** PM ruling R7 (chunk 9) migrated SKILL.md's floor
step from `observe launch` to `observe session-start`, but the floor has
TWO carriers — `SKILL.md`'s own prose, and this module's
`_LAUNCH_INSTRUCTION`, which every adapter (`ClaudeAdapter`,
`PortableAdapter`, `SubagentAdapter`) embeds in the handoff prompt it hands
to the NEXT session. R7 named only `SKILL.md`; the instruction text a
launched session actually reads still said `observe launch` until this
chunk. `tests/fleet/test_hook_and_floor_identity.py`'s FR-15 identity test
(B3) also gained a check for this pair once its own regex was fixed to read
the real invocation — this file is the adapter-level regression the brief
called out separately for B7, exercising each adapter's own `emit_prompt`
output rather than the module string directly.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from scripts.fleet.adapter.claude import ClaudeAdapter
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.adapter.subagent import SubagentAdapter

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Same shape as test_hook_and_floor_identity.py's `_VERB_INVOCATION_RE` —
#: captures just the `observe <verb>` group so two occurrences can be
#: compared for equality rather than mere containment.
_VERB_INVOCATION_RE = re.compile(r"python -m scripts\.fleet\.cli (observe [\w-]+)")


def _skill_md_floor_verb() -> str:
    """Return the `observe <verb>` SKILL.md's session-start floor step names."""
    text = (_REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        lowered = line.lower()
        if line.startswith("## ") and "fleet observation" in lowered and "floor" in lowered:
            start = i
            break
    assert start is not None, "SKILL.md: no '## ...fleet observation...floor...' heading found"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    subsection = "\n".join(lines[start:end])
    match = _VERB_INVOCATION_RE.search(subsection)
    assert match, "SKILL.md's session-start floor step names no `observe <verb>` invocation"
    return match.group(1)


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real temp git repo with one commit on a non-default-named branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "trunk")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-q", "-m", "initial")
    return repo


def _verb_in(prompt: str, handoff_id: str) -> str | None:
    """Return the `observe <verb>` invocation named after the handoff-id
    line in `prompt`, or None if there isn't one."""
    id_pos = prompt.index(f"FLEET-HANDOFF-ID: {handoff_id}")
    match = _VERB_INVOCATION_RE.search(prompt[id_pos:])
    return match.group(1) if match else None


class TestAdapterLaunchInstructionNamesTheFloorVerb:
    """Every adapter's `emit_prompt` output must carry the identical verb
    SKILL.md's floor step names — no adapter may point a launched session
    at a superseded invocation."""

    def test_claude_adapter(self, tmp_path: Path) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug")
        handoff_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        prompt = adapter.emit_prompt("Pick up where the last session left off.", handoff_id)
        verb = _verb_in(prompt, handoff_id)
        assert verb == _skill_md_floor_verb(), (
            f"ClaudeAdapter.emit_prompt embeds {verb!r}, but SKILL.md's floor step "
            f"names {_skill_md_floor_verb()!r} -- FR-15 requires the identical verb"
        )

    def test_portable_adapter(self, git_repo: Path) -> None:
        adapter = PortableAdapter(git_repo, "demo-slug")
        handoff_id = "11111111-2222-3333-4444-555555555555"
        prompt = adapter.emit_prompt("Continue the work.", handoff_id)
        verb = _verb_in(prompt, handoff_id)
        assert verb == _skill_md_floor_verb(), (
            f"PortableAdapter.emit_prompt embeds {verb!r}, but SKILL.md's floor step "
            f"names {_skill_md_floor_verb()!r} -- FR-15 requires the identical verb"
        )

    def test_subagent_adapter(self, git_repo: Path) -> None:
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="pm-developer-5-1")
        handoff_id = "22222222-3333-4444-5555-666666666666"
        prompt = adapter.emit_prompt("Continue the work.", handoff_id)
        verb = _verb_in(prompt, handoff_id)
        assert verb == _skill_md_floor_verb(), (
            f"SubagentAdapter.emit_prompt embeds {verb!r}, but SKILL.md's floor step "
            f"names {_skill_md_floor_verb()!r} -- FR-15 requires the identical verb"
        )


# --- TC-O20: owner-claim seam -- present in every adapter's `emit_prompt`,
# ordered after the session-start step (increment O, Chunk O3, O-FR-8) -----
#
# DESIGN O.7 extends `_LAUNCH_INSTRUCTION` (Decision A: one carrier, additive
# literals only) with an `owner claim` line, placed after the session-start
# step because a claim requires prior registration. This class exercises
# each adapter's own `emit_prompt` output, the same way the class above
# does for the session-start verb, plus the instruction text itself (which
# is shared verbatim across all three adapters, so it is only asserted
# once).

_OWNER_CLAIM_COMMAND_RE = re.compile(
    r"python -m scripts\.fleet\.cli owner claim\s+"
    r"--workspace <main checkout root> --slug <[^>]+>"
)
_EXIT_3_MARKERS = ("exit 3",)
_EXIT_5_MARKERS = ("exit 5",)

#: Every `observe <verb>` / `owner claim` invocation in a prompt tail, in
#: the order they appear -- lets ordering be asserted directly rather than
#: just co-presence.
_ANY_FLEET_VERB_RE = re.compile(r"python -m scripts\.fleet\.cli (observe [\w-]+|owner claim)")


def _fleet_verbs_after_handoff_id(prompt: str, handoff_id: str) -> list[str]:
    id_pos = prompt.index(f"FLEET-HANDOFF-ID: {handoff_id}")
    return _ANY_FLEET_VERB_RE.findall(prompt[id_pos:])


class TestOwnerClaimLaunchInstructionSeam:
    """TC-O20: the `owner claim` line reaches every adapter's `emit_prompt`,
    ordered after `observe session-start`, and states the exit-3/exit-5
    handling per DESIGN O.7's specified content.
    """

    def test_claude_adapter_carries_owner_claim_after_session_start(
        self, tmp_path: Path
    ) -> None:
        adapter = ClaudeAdapter(tmp_path, "demo-slug")
        handoff_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        prompt = adapter.emit_prompt("Pick up where the last session left off.", handoff_id)
        verbs = _fleet_verbs_after_handoff_id(prompt, handoff_id)
        assert "owner claim" in verbs, "ClaudeAdapter.emit_prompt carries no `owner claim` line"
        assert verbs.index("observe session-start") < verbs.index("owner claim"), (
            "the `owner claim` line must be ordered after `observe session-start`"
        )

    def test_portable_adapter_carries_owner_claim_after_session_start(
        self, git_repo: Path
    ) -> None:
        adapter = PortableAdapter(git_repo, "demo-slug")
        handoff_id = "11111111-2222-3333-4444-555555555555"
        prompt = adapter.emit_prompt("Continue the work.", handoff_id)
        verbs = _fleet_verbs_after_handoff_id(prompt, handoff_id)
        assert "owner claim" in verbs, "PortableAdapter.emit_prompt carries no `owner claim` line"
        assert verbs.index("observe session-start") < verbs.index("owner claim")

    def test_subagent_adapter_carries_owner_claim_after_session_start(
        self, git_repo: Path
    ) -> None:
        adapter = SubagentAdapter(git_repo, "demo-slug", local_id="pm-developer-5-1")
        handoff_id = "22222222-3333-4444-5555-666666666666"
        prompt = adapter.emit_prompt("Continue the work.", handoff_id)
        verbs = _fleet_verbs_after_handoff_id(prompt, handoff_id)
        assert "owner claim" in verbs, "SubagentAdapter.emit_prompt carries no `owner claim` line"
        assert verbs.index("observe session-start") < verbs.index("owner claim")

    def test_owner_claim_line_names_the_command_shape(self) -> None:
        from scripts.fleet.adapter.base import format_launch_instruction

        text = format_launch_instruction()
        assert _OWNER_CLAIM_COMMAND_RE.search(text), (
            "the launch instruction's owner-claim addition does not name the literal "
            "command shape `owner claim --workspace <main checkout root> --slug <...>`"
        )

    def test_owner_claim_line_states_exit_3_and_exit_5_handling(self) -> None:
        from scripts.fleet.adapter.base import format_launch_instruction

        text = format_launch_instruction().lower()
        assert any(marker in text for marker in _EXIT_3_MARKERS), (
            "the launch instruction's owner-claim addition never states exit-3 handling"
        )
        assert any(marker in text for marker in _EXIT_5_MARKERS), (
            "the launch instruction's owner-claim addition never states exit-5 handling"
        )
        assert "--prior-owner-notified" in text, (
            "the launch instruction's owner-claim addition never names the "
            "--prior-owner-notified re-run flag"
        )
        assert "--notified-via" in text, (
            "the launch instruction's owner-claim addition never names the "
            "--notified-via re-run flag"
        )


# --- TC-O22: increment-scoped zero-deletion check on `roles/pm.md` and the
# `_LAUNCH_INSTRUCTION` block in `adapter/base.py` (OQ-2, replaces the
# retired TC-24; increment O, Chunk O3) -------------------------------------
#
# Reuses TC-25's MERGE_HEAD-aware merge-base logic
# (`tests/fleet/test_resume_regression.py::_merge_base_with_main`) --
# duplicated here rather than imported, since no shared test-utils module
# exists between `tests/fleet/*.py` files and this is a small, self-contained
# subprocess call. See that module's docstring for the mid-merge correction
# rationale; this copy is intentionally kept byte-identical in logic.


def _merge_base_with_main() -> str | None:
    """Return the merge-base commit of `HEAD` and `origin/main`, or `None`.

    See `tests/fleet/test_resume_regression.py::_merge_base_with_main` for
    the full rationale (mid-merge `MERGE_HEAD` correction, symbolic-not-
    pinned lookup). Never raises; returns `None` if git or the ref is
    unavailable, so callers can skip cleanly instead of failing.
    """
    try:
        merge_head = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "MERGE_HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
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


def _blob_at(commit: str, rel_path: str) -> str | None:
    """Return `rel_path`'s full contents at `commit`, or `None` if unreadable."""
    result = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{rel_path}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _removed_lines(base_text: str, head_text: str) -> list[str]:
    """Return every `-` content line a unified diff of `base_text` -> `head_text`
    would show (excluding the `---` file-header line)."""
    import difflib

    base_lines = base_text.splitlines(keepends=True)
    head_lines = head_text.splitlines(keepends=True)
    return [
        line
        for line in difflib.unified_diff(base_lines, head_lines)
        if line.startswith("-") and not line.startswith("---")
    ]


def _launch_instruction_block(source: str) -> str:
    """Extract the `_LAUNCH_INSTRUCTION = ( ... )` block from `adapter/base.py` source.

    Scoped narrowly to this one assignment, per TC-O22's setup ("the
    `_LAUNCH_INSTRUCTION` block ... and nowhere else") -- a whole-file diff
    would also pick up unrelated additive chunks elsewhere in the same file
    (e.g. Chunk O2's `extract_claude_session_local_id`), which are out of
    this check's declared scope.
    """
    lines = source.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("_LAUNCH_INSTRUCTION = ("):
            start = i
            break
    assert start is not None, "adapter/base.py: no `_LAUNCH_INSTRUCTION = (` line found"
    end = None
    for j in range(start + 1, len(lines)):
        if lines[j].rstrip() == ")":
            end = j
            break
    assert end is not None, "adapter/base.py: `_LAUNCH_INSTRUCTION` block has no closing `)`"
    return "\n".join(lines[start : end + 1])


class TestMergeBaseWithMainGuardsBothSubprocessCalls:
    """`_merge_base_with_main`'s FIRST `subprocess.run` (the `MERGE_HEAD`
    rev-parse) must be guarded exactly like the second one -- an environment
    with no `git` on `PATH` (or any other `OSError`/`SubprocessError`) must
    return `None` cleanly, never raise, so every caller's `pytest.skip` path
    is reachable instead of crashing test collection/execution."""

    def test_oserror_on_the_first_subprocess_call_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*args: object, **kwargs: object) -> None:
            raise OSError("git not found (forced for this test)")

        monkeypatch.setattr(subprocess, "run", _raise)

        assert _merge_base_with_main() is None


def _pm_md_ownership_standdown_subsection(source: str) -> str:
    """Extract `roles/pm.md`'s "Ownership stand-down at handoff" subsection.

    Scoped narrowly to this one subsection (TC-O22, OQ-2), the same
    reasoning `_launch_instruction_block` already applies to
    `adapter/base.py`: a whole-FILE zero-deletion check would have to hold
    forever across every future edit ANYWHERE in `roles/pm.md`, not just this
    subsection -- exactly the defect this fix closes ("so later edits
    elsewhere in pm.md don't fail forever").

    Returns "" if the heading is absent (e.g. at a merge-base predating this
    subsection) rather than raising -- the caller then has an empty base to
    diff against, the correct "every line here is new" outcome.
    """
    lines = source.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "## ownership stand-down at handoff":
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ") or lines[j].strip() == "---":
            end = j
            break
    return "\n".join(lines[start:end])


class TestPmMdOwnershipStanddownSubsectionScoping:
    """TC-O22: the pm.md half of the zero-deletion check is scoped to the
    "Ownership stand-down at handoff" subsection only, mirroring how the
    `_LAUNCH_INSTRUCTION` half is already scoped to its own block -- an
    unrelated edit ELSEWHERE in `roles/pm.md` must never make this check
    fail, only a removed line INSIDE the subsection may."""

    _SYNTHETIC_PM_MD = (
        "# roles/pm.md\n"
        "\n"
        "## Some unrelated existing section\n"
        "Existing line one.\n"
        "Existing line two.\n"
        "\n"
        "---\n"
        "\n"
        "## Ownership stand-down at handoff\n"
        "\n"
        "Subsection line one.\n"
        "Subsection line two.\n"
        "\n"
        "---\n"
        "\n"
        "## A later section\n"
        "Trailing line.\n"
    )

    def test_extracts_only_the_named_subsection(self) -> None:
        section = _pm_md_ownership_standdown_subsection(self._SYNTHETIC_PM_MD)
        assert section.startswith("## Ownership stand-down at handoff")
        assert "Subsection line one." in section
        assert "Subsection line two." in section
        assert "Some unrelated existing section" not in section
        assert "A later section" not in section

    def test_returns_empty_string_when_the_heading_is_absent(self) -> None:
        assert _pm_md_ownership_standdown_subsection("# no such heading here\n") == ""

    def test_an_edit_outside_the_subsection_does_not_count_as_a_removed_line(self) -> None:
        base = self._SYNTHETIC_PM_MD
        head = base.replace("Existing line two.\n", "")  # an unrelated line removed
        base_section = _pm_md_ownership_standdown_subsection(base)
        head_section = _pm_md_ownership_standdown_subsection(head)
        assert _removed_lines(base_section, head_section) == [], (
            "an edit OUTSIDE the 'Ownership stand-down at handoff' subsection must "
            "never be reported as a removed line by the scoped check"
        )

    def test_an_edit_inside_the_subsection_is_still_caught(self) -> None:
        base = self._SYNTHETIC_PM_MD
        head = base.replace("Subsection line two.\n", "")  # a subsection line removed
        base_section = _pm_md_ownership_standdown_subsection(base)
        head_section = _pm_md_ownership_standdown_subsection(head)
        removed = _removed_lines(base_section, head_section)
        assert removed, (
            "a removed line INSIDE the 'Ownership stand-down at handoff' subsection "
            "must still be caught by the scoped check"
        )


class TestZeroDeletionCheckIncrementO:
    """TC-O22: `roles/pm.md`'s "Ownership stand-down at handoff" subsection
    and the `_LAUNCH_INSTRUCTION` block in `adapter/base.py` gain zero
    removed lines versus the merge-base with `main` -- no allowance for
    whitespace or blank-line reflow. The pm.md half is scoped to that one
    subsection (OQ-2), not the whole file, so later UNRELATED edits to
    `roles/pm.md` do not fail this check forever.
    """

    def test_roles_pm_md_has_no_removed_lines(self) -> None:
        merge_base = _merge_base_with_main()
        if merge_base is None:
            pytest.skip("git or origin/main merge-base unavailable in this environment")
        base_text = _blob_at(merge_base, "roles/pm.md")
        if base_text is None:
            pytest.skip(f"could not read roles/pm.md at merge-base {merge_base}")
        head_text = (_REPO_ROOT / "roles" / "pm.md").read_text(encoding="utf-8")

        head_subsection = _pm_md_ownership_standdown_subsection(head_text)
        assert head_subsection, (
            "roles/pm.md: 'Ownership stand-down at handoff' subsection is missing"
        )
        base_subsection = _pm_md_ownership_standdown_subsection(base_text)

        removed = _removed_lines(base_subsection, head_subsection)
        assert removed == [], (
            "roles/pm.md 'Ownership stand-down at handoff' subsection: unexpected "
            "removed line(s):\n" + "".join(removed)
        )

    def test_launch_instruction_block_has_no_removed_lines(self) -> None:
        merge_base = _merge_base_with_main()
        if merge_base is None:
            pytest.skip("git or origin/main merge-base unavailable in this environment")
        base_source = _blob_at(merge_base, "scripts/fleet/adapter/base.py")
        if base_source is None:
            pytest.skip(f"could not read scripts/fleet/adapter/base.py at merge-base {merge_base}")
        head_source = (_REPO_ROOT / "scripts" / "fleet" / "adapter" / "base.py").read_text(
            encoding="utf-8"
        )

        base_block = _launch_instruction_block(base_source)
        head_block = _launch_instruction_block(head_source)
        removed = _removed_lines(base_block, head_block)
        assert removed == [], (
            "adapter/base.py: `_LAUNCH_INSTRUCTION` block has unexpected removed line(s):\n"
            + "".join(removed)
        )
