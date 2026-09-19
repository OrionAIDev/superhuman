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
