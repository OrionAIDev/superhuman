"""Tests for `templates/hooks/claude-code/{pre-tool-use-role-gate,
pre_tool_use_role_gate.py}` — chunk 7a's `PreToolUse` role gate (DESIGN.md
D7, D7.9's TC-82..TC-91 and TC-93).

The portable predicate itself (`check_role_block`, the four+one verdicts,
the decision log) is unit-tested in `tests/fleet/test_role_block.py`; this
module exercises the HARNESS-SHAPED adapter and the real shell wrapper —
payload parsing, scope, the locator, the deny JSON, and the fault matrix —
mirroring `test_hooks.py`'s `subagent-start` precedent almost exactly.

**NFR-9 is the load-bearing requirement of this whole file.** Every
fault-injection test's PRIMARY assertion is exit code 0 with EMPTY stdout —
this hook is verify-only and must never become the reason a dispatch is
wrongly blocked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_GIT_BASH_PATH = r"C:\Program Files\Git\bin\bash.exe"


def _find_bash() -> "str | None":
    """Return a bash executable that accepts native (drive-letter) paths.

    Mirrors `tests/fleet/test_hooks.py`'s `_find_bash` precedent exactly.
    """
    if sys.platform == "win32":
        return _GIT_BASH_PATH if Path(_GIT_BASH_PATH).is_file() else None
    return shutil.which("bash")


_BASH = _find_bash()

_HOOK_SCRIPT_NAME = "pre-tool-use-role-gate"
_HOOK_CMD_NAME = "pre-tool-use-role-gate.cmd"
_ADAPTER_SCRIPT_NAME = "pre_tool_use_role_gate.py"


def _hook_script(skill_root: Path) -> Path:
    return skill_root / "templates" / "hooks" / "claude-code" / _HOOK_SCRIPT_NAME


def _hook_cmd(skill_root: Path) -> Path:
    return skill_root / "templates" / "hooks" / "claude-code" / _HOOK_CMD_NAME


def _adapter_script(skill_root: Path) -> Path:
    return skill_root / "templates" / "hooks" / "claude-code" / _ADAPTER_SCRIPT_NAME


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


def _write_profile(profile_path: Path) -> None:
    profile_path.write_text(
        "fleet:\n"
        "  enabled: true\n"
        "  observe_deadline_seconds: 5.0\n"
        "  lock_timeout_seconds: 0.8\n"
        "  git_timeout_seconds: 0.25\n",
        encoding="utf-8",
    )


def _write_project(repo: Path, slug: str) -> None:
    project_dir = repo / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(
        f"**Slug:** {slug}\n**Project-id:** fleet-{slug.replace('-', '')[:12]}\n",
        encoding="utf-8",
    )


@pytest.fixture
def enabled_project(git_repo: Path, tmp_path: Path) -> tuple[Path, str, Path]:
    """A resolvable workspace with fleet enabled — `(workspace, slug, profile_path)`."""
    slug = "demo-project"
    profile = tmp_path / "profile.yaml"
    _write_profile(profile)
    _write_project(git_repo, slug)
    return git_repo, slug, profile


def _fleet_dir(workspace: Path, slug: str) -> Path:
    return workspace / "docs" / "superhuman" / slug / "fleet"


def _role_gate_log_text(workspace: Path, slug: str) -> str:
    path = _fleet_dir(workspace, slug) / "role-gate.jsonl"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _pre_tool_use_payload(
    *,
    prompt: str,
    cwd: Path | str,
    subagent_type: str = "developer",
    tool_name: str = "Agent",
    session_id: str = "role-gate-session",
    scratchpad_dir: Path | str | None = None,
    agent_id: str | None = None,
) -> dict:
    payload: dict = {
        "session_id": session_id,
        "cwd": str(cwd),
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_use_id": "toolu_test",
        "tool_input": {"subagent_type": subagent_type, "prompt": prompt},
    }
    if scratchpad_dir is not None:
        payload["scratchpad_dir"] = str(scratchpad_dir)
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def _run_role_gate_hook(
    *,
    skill_root: Path,
    stdin_text: str,
    cwd: Path,
    profile_path: Path | None = None,
    extra_env: dict | None = None,
    unset_claude_project_dir: bool = True,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess:
    """Invoke `templates/hooks/claude-code/pre-tool-use-role-gate` as a real subprocess.

    Mirrors `test_hooks.py`'s `_run_subagent_hook_script` precedent.
    """
    env = os.environ.copy()
    if unset_claude_project_dir:
        env.pop("CLAUDE_PROJECT_DIR", None)
    if profile_path is not None:
        env["SUPERHUMAN_PROFILE"] = str(profile_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(_hook_script(skill_root))],
        input=stdin_text,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _developer_role_content(skill_root: Path) -> str:
    """The REAL `roles/developer.md` content — using the actual file (not a
    synthetic fixture) so these tests exercise the gate's default
    `--roles-dir` faithfully."""
    return (skill_root / "roles" / "developer.md").read_text(encoding="utf-8")


# --- TC-82/TC-83: deny shape and "never allow/ask/defer" -----------------------------


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestRoleGateDenyShape:
    """TC-82: in scope with UNMARKED or MISMATCH, stdout is exactly one
    JSON object with `permissionDecision: "deny"`, naming the literal line
    / role-file path (and, for MISMATCH, the first differing line). Exit 0."""

    def test_unmarked_prose_brief_denies_with_one_json_object(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        payload = _pre_tool_use_payload(
            prompt="You are the Developer for chunk 7a. Implement the role gate...\n",
            cwd=workspace,
        )
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        decision = json.loads(result.stdout)
        hook_output = decision["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "PreToolUse"
        assert hook_output["permissionDecision"] == "deny"
        assert "non-role" in hook_output["permissionDecisionReason"]
        assert "role-gate" not in hook_output["permissionDecisionReason"]  # sanity: no leaked internals
        log = _role_gate_log_text(workspace, slug)
        assert '"verdict": "UNMARKED"' in log

    def test_mismatch_denies_naming_the_first_differing_line(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        role_content = _developer_role_content(skill_root)
        edited = role_content.replace("tier: standard", "tier: cheap", 1)
        assert edited != role_content, "fixture role file must actually contain 'tier: standard'"
        payload = _pre_tool_use_payload(prompt=edited, cwd=workspace)

        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        decision = json.loads(result.stdout)
        reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
        assert "tier: cheap" in reason
        assert "tier: standard" in reason
        assert str(skill_root / "roles" / "developer.md") in reason or "developer.md" in reason
        log = _role_gate_log_text(workspace, slug)
        assert '"verdict": "MISMATCH"' in log
        # B6: the decision log carries the divergence's 1-based line
        # NUMBER, never the line's own text -- the deny reason above (the
        # harness-facing `reason` string) is the only place the actual
        # "tier: cheap"/"tier: standard" text is allowed to appear.
        assert '"mismatch_line_number": 3' in log
        assert "tier: cheap" not in log
        assert "tier: standard" not in log


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestRoleGateNeverAllowAskOrDefer:
    """TC-83: across every verdict, the hook never emits `allow`, `ask` or
    `defer`. A compliant prompt gives empty stdout."""

    def test_role_dispatch_gives_empty_stdout(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        payload = _pre_tool_use_payload(prompt=_developer_role_content(skill_root), cwd=workspace)
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_non_role_dispatch_gives_empty_stdout(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        payload = _pre_tool_use_payload(
            prompt="superhuman-dispatch: non-role\n\nGo research X.\n", cwd=workspace
        )
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        assert result.stdout == ""
        log = _role_gate_log_text(workspace, slug)
        assert '"verdict": "NON_ROLE"' in log

    @pytest.mark.parametrize(
        "prompt_kind",
        ["role", "non_role", "unmarked", "mismatch"],
    )
    def test_no_verdict_ever_emits_allow_ask_or_defer(
        self, prompt_kind: str, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        role_content = _developer_role_content(skill_root)
        prompts = {
            "role": role_content,
            "non_role": "superhuman-dispatch: non-role\n\nResearch.\n",
            "unmarked": "You are the Developer for chunk 7a...\n",
            "mismatch": role_content.replace("tier: standard", "tier: cheap", 1),
        }
        payload = _pre_tool_use_payload(prompt=prompts[prompt_kind], cwd=workspace)
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        assert '"allow"' not in result.stdout
        assert '"ask"' not in result.stdout
        assert '"defer"' not in result.stdout


# --- TC-84: scope ---------------------------------------------------------------------


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestRoleGateScope:
    """TC-84: `agent_id` present, a non-`Agent`/`Task` tool, or an
    unresolvable locator each give empty stdout. A spy asserts the locator
    is NOT called for a ROLE prompt."""

    def test_agent_id_present_gives_empty_stdout(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """A nested dispatch (inside a subagent's own call) is out of scope
        (D7.4 clause 2) even though the prompt itself is a genuine unmarked
        prose brief that WOULD be denied on the main thread."""
        workspace, slug, profile = enabled_project
        payload = _pre_tool_use_payload(
            prompt="You are the Developer for chunk 7a...\n",
            cwd=workspace,
            agent_id="nested-agent-id",
        )
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert _role_gate_log_text(workspace, slug) == ""

    def test_non_agent_task_tool_gives_empty_stdout(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        payload = {
            "session_id": "role-gate-session",
            "cwd": str(workspace),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/x"},
        }
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_task_tool_name_is_in_scope(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """`Task` is the name older transcripts use for the same dispatch
        tool (D7.4 clause 1) — an unmarked prompt via `Task` must still deny."""
        workspace, slug, profile = enabled_project
        payload = _pre_tool_use_payload(
            prompt="You are the Developer for chunk 7a...\n", cwd=workspace, tool_name="Task"
        )
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        decision = json.loads(result.stdout)
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_unresolvable_locator_gives_empty_stdout(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        """A prose brief in a directory that does not resolve to any
        project (not even a git repository) is out of scope entirely — no
        deny, because the gate cannot know whether a superhuman project
        governs this session at all."""
        not_a_project = tmp_path / "not-a-project"
        not_a_project.mkdir()
        payload = _pre_tool_use_payload(
            prompt="You are the Developer for chunk 7a...\n", cwd=not_a_project
        )
        result = _run_role_gate_hook(
            skill_root=skill_root, stdin_text=json.dumps(payload), cwd=not_a_project
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_locator_not_invoked_for_a_role_dispatch(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path], tmp_path: Path
    ) -> None:
        """Spy via the adapter's own locator cache: a ROLE verdict must
        never write a locator-cache entry, because the locator is never
        called for it (D7.9 acceptance; the companion test below proves
        this cache mechanism DOES activate for a verdict that needs scope,
        validating that a missing cache file here is meaningful)."""
        workspace, slug, profile = enabled_project
        scratchpad_dir = tmp_path / "scratchpad"
        scratchpad_dir.mkdir()
        payload = _pre_tool_use_payload(
            prompt=_developer_role_content(skill_root),
            cwd=workspace,
            scratchpad_dir=scratchpad_dir,
            session_id="locator-spy-session",
        )
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        cache_files = list(scratchpad_dir.glob("role-gate-locator-*"))
        assert cache_files == [], (
            f"the locator was invoked (a cache file was written) for a ROLE verdict: {cache_files}"
        )

    def test_locator_is_invoked_for_a_non_role_dispatch(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path], tmp_path: Path
    ) -> None:
        """Companion to the spy above: the SAME cache mechanism DOES
        activate when the verdict needs scope (NON_ROLE here), proving the
        prior test's empty result reflects "not called," not "the cache
        never works."""
        workspace, slug, profile = enabled_project
        scratchpad_dir = tmp_path / "scratchpad"
        scratchpad_dir.mkdir()
        payload = _pre_tool_use_payload(
            prompt="superhuman-dispatch: non-role\n\nResearch.\n",
            cwd=workspace,
            scratchpad_dir=scratchpad_dir,
            session_id="locator-spy-session-2",
        )
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        cache_files = list(scratchpad_dir.glob("role-gate-locator-*"))
        assert len(cache_files) == 1, (
            "expected the locator to be invoked (and cached) for a NON_ROLE verdict"
        )


# --- TC-85: the fault-injection matrix -------------------------------------------------


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestRoleGateFaultInjectionMatrix:
    """TC-85: corrupt/empty/truncated stdin; missing or non-string
    `prompt`; missing interpreter; missing or empty `roles/`; unreadable
    role file; locator raises; deadline exceeded. Each gives EXIT 0 and
    EMPTY STDOUT."""

    def test_exits_0_empty_stdout_on_corrupted_json(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        result = _run_role_gate_hook(
            skill_root=skill_root, stdin_text='{"tool_name": "Agent", "cwd": "/x', cwd=tmp_path
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_exits_0_empty_stdout_on_non_json(self, skill_root: Path, tmp_path: Path) -> None:
        result = _run_role_gate_hook(
            skill_root=skill_root, stdin_text="not json\x00\xff", cwd=tmp_path
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_exits_0_empty_stdout_on_empty_stdin(self, skill_root: Path, tmp_path: Path) -> None:
        result = _run_role_gate_hook(skill_root=skill_root, stdin_text="", cwd=tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_exits_0_empty_stdout_on_missing_prompt(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        payload = {
            "session_id": "s",
            "cwd": str(workspace),
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "developer"},
        }
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_exits_0_empty_stdout_on_non_string_prompt(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        payload = {
            "session_id": "s",
            "cwd": str(workspace),
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "developer", "prompt": {"nested": "object"}},
        }
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_exits_0_empty_stdout_when_interpreter_missing(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        empty_path_dir = tmp_path / "empty-path"
        empty_path_dir.mkdir()
        payload = _pre_tool_use_payload(prompt="anything", cwd=tmp_path)
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=tmp_path,
            extra_env={"PATH": str(empty_path_dir)},
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_stdout_never_carries_a_python_traceback(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        payload = _pre_tool_use_payload(
            prompt=_developer_role_content(skill_root).replace("tier: standard", "tier: cheap", 1),
            cwd=workspace,
        )
        result = _run_role_gate_hook(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0
        assert "Traceback (most recent call last)" not in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-native interpreter invocation")
class TestRoleGateAdapterFaultsDirect:
    """The remaining TC-85 faults — missing/empty `roles/`, an unreadable
    role file, a raising locator — are about the PYTHON adapter's own
    behavior, not the shell wrapper's plumbing (already covered above), so
    these invoke `pre_tool_use_role_gate.py` directly with `sys.executable`,
    the same layer-selection rationale `test_hooks.py`'s TC-49 uses for its
    disk-full fault ("the fault must be injected into THIS process's own
    ..., which a subprocess cannot see" — here, a controlled `--roles-dir`)."""

    def _run_adapter(
        self, *, skill_root: Path, stdin_text: str, roles_dir: Path, cwd: Path
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_adapter_script(skill_root)), "--hook-payload", "-", "--roles-dir", str(roles_dir)],
            input=stdin_text,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )

    def test_exits_0_empty_stdout_on_missing_roles_dir(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        payload = _pre_tool_use_payload(
            prompt="You are the Developer for chunk 7a...\n", cwd=workspace
        )
        result = self._run_adapter(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            roles_dir=workspace / "does-not-exist",
            cwd=workspace,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_exits_0_empty_stdout_on_empty_roles_dir(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path], tmp_path: Path
    ) -> None:
        workspace, slug, profile = enabled_project
        empty_roles = tmp_path / "empty-roles"
        empty_roles.mkdir()
        payload = _pre_tool_use_payload(
            prompt="You are the Developer for chunk 7a...\n", cwd=workspace
        )
        result = self._run_adapter(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            roles_dir=empty_roles,
            cwd=workspace,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    # The remaining TC-85 item -- "unreadable role file" (the ONE role file
    # a prompt's frontmatter names is present but cannot be read, while
    # `roles_dir` itself is otherwise healthy) -- needs a real permission
    # failure or a monkeypatched `Path.read_text`, neither of which a
    # cross-platform subprocess test can inject portably (a directory
    # masquerading as `<name>.md` is filtered out by
    # `dispatch_predicate._existing_role_basenames`'s own `is_file()` guard
    # before the read is ever attempted, so it exercises UNMARKED, not this
    # fault). It is proven precisely at the unit level instead:
    # `tests/fleet/test_role_block.py::TestCheckRoleBlockFaultPosture::
    # test_unreadable_role_file_is_fault_not_mismatch`.


# --- TC-91: cross-chunk interaction with chunk 7's SubagentStart filter ---------------


class TestCrossChunkOneRowOnRetry:
    """TC-91: a transcript with a refused unlabelled call followed by a
    verbatim retry in a later assistant message gives EXACTLY ONE chunk 7
    row. A refused call never starts a subagent, so `SubagentStart` never
    fires for it — the only real hook invocation is for the retry, and
    chunk 7's own "most recent dispatching message" rule (PM ruling 2)
    must ignore the earlier, denied message's candidates entirely."""

    def test_filter_registers_only_the_verbatim_retry(self, skill_root: Path, tmp_path: Path) -> None:
        # `templates/hooks/claude-code/` is not a valid Python package path
        # (the hyphen), so chunk 7's filter module is loaded directly from
        # its file path rather than via a dotted import.
        import importlib.util

        filter_path = (
            skill_root / "templates" / "hooks" / "claude-code" / "subagent_dispatch_filter.py"
        )
        spec = importlib.util.spec_from_file_location("subagent_dispatch_filter", filter_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        decide = module.decide

        role_content = (skill_root / "roles" / "developer.md").read_text(encoding="utf-8")
        prose_brief = "You are the Developer for chunk 7a. Implement the role gate...\n"

        transcript = tmp_path / "transcript.jsonl"
        records = [
            {
                "type": "assistant",
                "message": {
                    "id": "msg_refused",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_refused",
                            "name": "Agent",
                            "input": {"subagent_type": "developer", "prompt": prose_brief},
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "id": "msg_retry",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_retry",
                            "name": "Agent",
                            "input": {"subagent_type": "developer", "prompt": role_content},
                        }
                    ],
                },
            },
        ]
        transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        # The ONE real SubagentStart firing is for the successful retry —
        # a refused dispatch never starts a subagent, so this is the only
        # invocation that could ever happen against this transcript.
        decision = decide(
            transcript_path=str(transcript),
            agent_type="developer",
            roles_dir=skill_root / "roles",
        )
        assert decision == "register"


# --- TC-93: latency --------------------------------------------------------------------


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestRoleGateLatency:
    """TC-93: latency through the real wrapper. ROLE path p95 <= 1.0s;
    locator path measured and reported (D7.9 acceptance).

    Mirrors `test_hooks.py`'s `TestSubagentHookLatency` precedent: the HARD
    ceiling below is what this test asserts (a generous bound proof against
    this suite's own concurrent subprocess load — measured to add ~0.3-0.7s
    of noise per sample when many other subprocess-spawning tests run in
    the same session); the tighter p95 <= 1.0s acceptance figure is
    measured and printed for the chunk's status report rather than gated
    here, exactly as NFR-2's median target is for `session-start`/
    `subagent-start`."""

    def test_role_path_latency_within_budget(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = enabled_project
        role_content = _developer_role_content(skill_root)
        samples = []
        for i in range(15):
            payload = _pre_tool_use_payload(
                prompt=role_content, cwd=workspace, session_id=f"latency-role-{i}"
            )
            start = time.perf_counter()
            result = _run_role_gate_hook(
                skill_root=skill_root,
                stdin_text=json.dumps(payload),
                cwd=workspace,
                profile_path=profile,
            )
            elapsed = time.perf_counter() - start
            assert result.returncode == 0
            assert result.stdout == ""
            samples.append(elapsed)

        samples.sort()
        p95_index = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
        p95 = samples[p95_index]
        maximum = samples[-1]
        assert maximum < 2.0, f"ROLE path exceeded the 2.0s hard ceiling: samples={samples!r}"
        print(f"role gate ROLE-path latency: p95={p95:.3f}s max={maximum:.3f}s samples={samples!r}")

    def test_locator_path_latency_measured_and_reported(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """The locator (NON_ROLE/deny) path is measured and REPORTED, not
        gated to a hard number here — D7.9: "The locator path is measured
        and reported; if it is slow, the locator result is cached...""."""
        workspace, slug, profile = enabled_project
        samples = []
        for i in range(15):
            payload = _pre_tool_use_payload(
                prompt="superhuman-dispatch: non-role\n\nResearch.\n",
                cwd=workspace,
                session_id=f"latency-locator-{i}",
            )
            start = time.perf_counter()
            result = _run_role_gate_hook(
                skill_root=skill_root,
                stdin_text=json.dumps(payload),
                cwd=workspace,
                profile_path=profile,
            )
            elapsed = time.perf_counter() - start
            assert result.returncode == 0
            samples.append(elapsed)

        samples.sort()
        p95_index = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
        p95 = samples[p95_index]
        maximum = samples[-1]
        print(
            f"role gate locator-path (NON_ROLE) latency: p95={p95:.3f}s max={maximum:.3f}s "
            f"samples={samples!r}"
        )


# --- Chunk 7a fix: the gate must decode stdin as UTF-8 --------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-native interpreter invocation")
class TestRoleGateUTF8StdinDecoding:
    """Regression for the defect fixed in this chunk (chunk 7a fix):
    `pre_tool_use_role_gate.py` used to read stdin with `sys.stdin.read()`,
    which decodes using the process's locale-preferred encoding — cp1252 on
    this Windows runner, not UTF-8, even though the harness always writes
    UTF-8 bytes (JSON's own encoding rule, RFC 8259 §8.1). Every shipped
    role file contains an em dash, so as shipped this denied EVERY
    correctly-formed verbatim role dispatch.

    These tests invoke the Python adapter directly (mirrors
    `TestRoleGateAdapterFaultsDirect`'s precedent — a controlled
    `--roles-dir`), feeding RAW UTF-8 BYTES: no `text=True`, no `encoding=`,
    and `PYTHONIOENCODING`/`PYTHONUTF8` stripped from the child
    environment, so the parent process never pre-encodes/re-encodes
    symmetrically the way `_run_role_gate_hook`'s existing `text=True,
    encoding="utf-8"` calls do (which is exactly why they never caught
    this). All role content here is SYNTHETIC — never the captured live
    payload's text (NFR-8/publication guard).
    """

    #: A synthetic role file containing both an em dash (`—`, not
    #: representable in cp1252 as itself — cp1252 maps the single byte
    #: 0x97 to it, so its correct UTF-8 encoding `E2 80 94` mis-decodes as
    #: cp1252 into three wrong characters) and a character with NO cp1252
    #: representation at all (`→`, U+2192), so a cp1252 decode cannot even
    #: round-trip it as a substitute byte.
    _ROLE_CONTENT = (
        "---\n"
        "name: developer\n"
        "tier: standard\n"
        "---\n"
        "\n"
        "# Developer role\n"
        "\n"
        "You are the Developer — you own the chunk end-to-end: code, tests,\n"
        "commit → push. Nothing here is real project content.\n"
    )

    def _roles_dir(self, tmp_path: Path) -> Path:
        directory = tmp_path / "roles"
        directory.mkdir()
        (directory / "developer.md").write_text(self._ROLE_CONTENT, encoding="utf-8")
        return directory

    def _payload_bytes(self, *, prompt: str, cwd: Path, session_id: str) -> bytes:
        payload = {
            "session_id": session_id,
            "cwd": str(cwd),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_use_id": "toolu_utf8_regression",
            "tool_input": {"subagent_type": "developer", "prompt": prompt},
        }
        # `ensure_ascii=False`: a real harness (e.g. Node's `JSON.stringify`)
        # emits non-ASCII characters as literal UTF-8 bytes, never `\uXXXX`
        # escapes. `json.dumps`'s own `ensure_ascii=True` default would
        # sidestep the byte-level defect entirely.
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _run_adapter_raw_bytes(
        self,
        *,
        skill_root: Path,
        payload_bytes: bytes,
        roles_dir: Path,
        cwd: Path,
        profile_path: Path | None = None,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        if profile_path is not None:
            env["SUPERHUMAN_PROFILE"] = str(profile_path)
        return subprocess.run(
            [
                sys.executable,
                str(_adapter_script(skill_root)),
                "--hook-payload",
                "-",
                "--roles-dir",
                str(roles_dir),
            ],
            input=payload_bytes,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            timeout=15,
        )

    def test_verbatim_utf8_role_dispatch_is_allowed(
        self, skill_root: Path, git_repo: Path, tmp_path: Path
    ) -> None:
        """The ROLE case: dispatched verbatim, over raw UTF-8 bytes, must
        give empty stdout (allowed) — NOT a deny. Run against the unfixed
        code, this goes red (a MISMATCH deny), proving the defect; see this
        chunk's Developer report for the captured red output.

        Deliberately IN SCOPE (a real git repo + project, like
        `test_edited_utf8_role_dispatch_is_denied` below) — a MISMATCH
        verdict only denies once scope resolves (D7.4), so an
        out-of-scope `cwd` would give empty stdout regardless of verdict
        and could not distinguish ROLE from a corrupted MISMATCH being
        silently swallowed by scope failure instead of by the fix."""
        roles_dir = self._roles_dir(tmp_path)
        slug = "utf8-role-project"
        profile = tmp_path / "profile.yaml"
        _write_profile(profile)
        _write_project(git_repo, slug)

        payload_bytes = self._payload_bytes(
            prompt=self._ROLE_CONTENT, cwd=git_repo, session_id="utf8-role-session"
        )
        result = self._run_adapter_raw_bytes(
            skill_root=skill_root,
            payload_bytes=payload_bytes,
            roles_dir=roles_dir,
            cwd=git_repo,
            profile_path=profile,
        )
        assert result.returncode == 0, (
            f"adapter exited {result.returncode}\nSTDOUT:\n{result.stdout!r}\nSTDERR:\n{result.stderr!r}"
        )
        assert result.stdout == b"", (
            f"a verbatim role dispatch was denied over raw UTF-8 bytes: {result.stdout!r}"
        )
        # Belt-and-braces: confirm this really was ROLE (never logged),
        # not an accidental out-of-scope short-circuit hiding a MISMATCH.
        assert _role_gate_log_text(git_repo, slug) == ""

    def test_edited_utf8_role_dispatch_is_denied(
        self, skill_root: Path, git_repo: Path, tmp_path: Path
    ) -> None:
        """The MISMATCH case, for contrast: a genuinely edited copy of the
        SAME UTF-8 content must still deny — proving the fix does not make
        the comparison vacuously permissive."""
        roles_dir = self._roles_dir(tmp_path)
        slug = "utf8-mismatch-project"
        profile = tmp_path / "profile.yaml"
        _write_profile(profile)
        _write_project(git_repo, slug)

        edited = self._ROLE_CONTENT.replace("tier: standard", "tier: cheap", 1)
        payload_bytes = self._payload_bytes(
            prompt=edited, cwd=git_repo, session_id="utf8-mismatch-session"
        )
        result = self._run_adapter_raw_bytes(
            skill_root=skill_root,
            payload_bytes=payload_bytes,
            roles_dir=roles_dir,
            cwd=git_repo,
            profile_path=profile,
        )
        assert result.returncode == 0
        decision = json.loads(result.stdout)
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
        log = _role_gate_log_text(git_repo, slug)
        assert '"verdict": "MISMATCH"' in log

    def test_invalid_utf8_bytes_are_a_fault_not_a_deny(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        """Bytes that are not valid UTF-8 at all must degrade to FAULT —
        empty stdout, exit 0 (NFR-9: never deny on this gate's own fault)."""
        roles_dir = self._roles_dir(tmp_path)
        result = self._run_adapter_raw_bytes(
            skill_root=skill_root,
            payload_bytes=b"\xff\xfe{\"session_id\": \"x\"",
            roles_dir=roles_dir,
            cwd=tmp_path,
        )
        assert result.returncode == 0, (
            f"adapter exited {result.returncode}\nSTDOUT:\n{result.stdout!r}\nSTDERR:\n{result.stderr!r}"
        )
        assert result.stdout == b""
