"""Tests for `templates/hooks/claude-code/{session-start,subagent-start}` —
the deterministic hook wrappers themselves (Chunks 6 and 7).

Chunk 6 (`SessionStart`, TC-35..TC-41 below, plus two `--anchor` regression
tests required by the chunk-6 PM ruling on ARCHITECTURE.md Addendum §A) and
Chunk 7 (`SubagentStart`, TC-42..TC-53) are both implemented.

**FR-18 is the load-bearing requirement of this whole file for
`subagent-start`: exit code 2 on `SubagentStart` BLOCKS THE SUBAGENT.**
"Exits 0 under every injected fault" is therefore a correctness
requirement, not a quality goal — every fault-injection test below asserts
exit 0 as its primary assertion, not as an afterthought.

**Honesty note (PM instruction, chunk 6):** of the five `SessionStart`
matchers exercised below (`startup`, `resume`, `clear`, `compact`, `fork`),
only `startup` was ever observed against a live harness (Chunk 1's probe,
CHUNK-1-FINDINGS.md "Still open"). The payloads for `resume`, `clear`,
`compact` and `fork` are SYNTHESISED here — a `source` field value plugged
into the same measured 6-field shape — not harness-observed. This is
coverage of the documented/measured `source` values, not proof the harness
actually fires each of those four matchers the way the probe fired
`startup`.
"""

from __future__ import annotations

import errno
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.fleet.cli import build_parser


def _fake_stdin(text: str) -> io.StringIO:
    """A monkeypatch-ready fake `sys.stdin` carrying `text` on both its
    text (`.read()`) and buffer (`.buffer.read()`) surfaces.

    `read_hook_payload`'s `"-"` branch reads `sys.stdin.buffer` (raw
    bytes, then decodes as UTF-8 itself — chunk 7a fix); a plain
    `io.StringIO` has no `.buffer`, so every fake stdin in this module
    needs one.
    """
    fake = io.StringIO(text)
    fake.buffer = io.BytesIO(text.encode("utf-8"))  # type: ignore[attr-defined]
    return fake


# ---------------------------------------------------------------------------
# Shared fixtures / helpers (Chunk 6) — mirrors test_observe_session.py's
# `git_repo`/`enabled_project` precedent so a hook-script subprocess test and
# a library-level test build an identical resolvable workspace.
# ---------------------------------------------------------------------------

_GIT_BASH_PATH = r"C:\Program Files\Git\bin\bash.exe"


def _find_bash() -> "str | None":
    """Return a bash executable that accepts native (drive-letter) paths.

    Mirrors `tests/test_git_hooks.py`'s `_find_bash` precedent exactly: on
    Windows, WSL bash cannot resolve `C:\\...` paths, so only Git Bash at
    the standard install location counts.
    """
    if sys.platform == "win32":
        return _GIT_BASH_PATH if Path(_GIT_BASH_PATH).is_file() else None
    return shutil.which("bash")


_BASH = _find_bash()

_HOOK_SCRIPT_NAME = "session-start"
_HOOK_CMD_NAME = "session-start.cmd"


def _hook_script(skill_root: Path) -> Path:
    return skill_root / "templates" / "hooks" / "claude-code" / _HOOK_SCRIPT_NAME


def _hook_cmd(skill_root: Path) -> Path:
    return skill_root / "templates" / "hooks" / "claude-code" / _HOOK_CMD_NAME


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


def _init_second_repo(repo: Path) -> None:
    """Initialize a SEPARATE throwaway git repo (mirrors `git_repo`), for
    tests that need two independently-resolvable workspaces."""
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "trunk")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-q", "-m", "initial")


def _fleet_dir(workspace: Path, slug: str) -> Path:
    return workspace / "docs" / "superhuman" / slug / "fleet"


def _events_log(workspace: Path, slug: str) -> str:
    log_path = _fleet_dir(workspace, slug) / "events.jsonl"
    if not log_path.is_file():
        return ""
    return log_path.read_text(encoding="utf-8")


def _run_hook_script(
    *,
    skill_root: Path,
    payload: dict,
    cwd: Path,
    profile_path: Path,
    extra_env: dict | None = None,
    unset_claude_project_dir: bool = True,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess:
    """Invoke `templates/hooks/claude-code/session-start` as a real subprocess.

    Args:
        skill_root: the superhuman skill checkout (from the `skill_root` fixture).
        payload: the synthetic harness stdin JSON payload.
        cwd: the subprocess's OWN OS working directory — deliberately
            independent of `payload["cwd"]` so TC-37 can set them to
            different workspaces.
        profile_path: `SUPERHUMAN_PROFILE` override so fleet is enabled.
        extra_env: additional/overriding environment variables (e.g.
            `CLAUDE_PROJECT_DIR` for the anchor tests).
        unset_claude_project_dir: pop `CLAUDE_PROJECT_DIR` from the
            subprocess environment first — the ambient environment this
            suite runs in may itself carry a real one, and most of these
            tests must not accidentally exercise the anchor path.
        timeout: bounded wait (never rely on an unbounded subprocess call).

    Returns:
        subprocess.CompletedProcess: with `text=True` stdout/stderr.
    """
    env = os.environ.copy()
    if unset_claude_project_dir:
        env.pop("CLAUDE_PROJECT_DIR", None)
    env["SUPERHUMAN_PROFILE"] = str(profile_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(_hook_script(skill_root))],
        input=json.dumps(payload),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


# --- Chunk 6: SessionStart hook -----------------------------------------------------


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestSessionStartHookValueDefinition:
    """TC-35: the chunk's acceptance demonstration — a row lands with no
    prose instruction ever having run."""

    def test_session_start_hook_produces_a_row_with_no_prose_instruction(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """End-to-end: build a resolvable workspace, invoke
        `templates/hooks/claude-code/session-start` directly (not through
        `cli.py`) with a synthetic stdin payload, and assert a manifest row
        exists afterward with NOTHING beyond the hook invocation having
        run. This is the literal acceptance demonstration named in
        PLAN.md's Chunk 6 — the value chunk."""
        workspace, slug, profile = enabled_project
        payload = {
            "session_id": "value-definition-session",
            "cwd": str(workspace),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }

        result = _run_hook_script(
            skill_root=skill_root, payload=payload, cwd=workspace, profile_path=profile
        )

        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        log = _events_log(workspace, slug)
        assert '"type":"session_registered"' in log
        assert '"origination":"observed"' in log


class TestSessionStartHookContent:
    """TC-36: FR-8/FR-16 — the shipped hook templates carry no leftover
    placeholder and no `$(pwd)` call, across the WHOLE `templates/hooks/**`
    tree."""

    def test_no_replace_with_or_pwd_in_hook_templates(self, skill_root: Path) -> None:
        """Content test over the WHOLE `templates/hooks/**` tree — named
        literally by TEST.md's TC-36/PLAN.md's FR-8 acceptance criterion —
        for zero occurrences of `REPLACE_WITH_` (FR-8) and zero occurrences
        of the literal string `$(pwd)` (FR-16, the Phase 1.1 defect this
        project fixes).

        Chunk 6 scoped this scan to `templates/hooks/claude-code/**` only,
        because `templates/hooks/PreToolUse` still carried both tokens and
        was explicitly Chunk 7's file to delete (PLAN.md's chunk-7 PM
        ruling on TC-36's narrowing). Chunk 7 deletes that file, so the
        full-tree assertion is widened here to what TEST.md always named,
        and the chunk-6 narrowing note is removed rather than carried
        forward as a stale caveat."""
        hooks_dir = skill_root / "templates" / "hooks"
        checked = 0
        for path in hooks_dir.rglob("*"):
            if not path.is_file():
                continue
            checked += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "REPLACE_WITH_" not in text, f"{path} still carries a REPLACE_WITH_ placeholder"
            assert "$(pwd)" not in text, f"{path} still calls $(pwd)"
        assert checked > 0, "no files found under templates/hooks/ to scan"
        # templates/hooks/SessionStart and templates/hooks/PreToolUse (the
        # files chunks 6 and 7 supersede) must be gone entirely, not merely
        # cleaned up in place.
        assert not (skill_root / "templates" / "hooks" / "SessionStart").exists()
        assert not (skill_root / "templates" / "hooks" / "PreToolUse").exists()


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestSessionStartHookUsesStdinCwd:
    """TC-37: FR-16's direct regression test — the hook must resolve the
    workspace from the payload's `cwd`, never from its own OS cwd."""

    def test_hook_resolves_workspace_from_stdin_cwd_not_process_cwd(
        self, skill_root: Path, git_repo: Path, tmp_path: Path
    ) -> None:
        """Differential test: invoke the hook subprocess with its OWN OS
        working directory set to workspace A, but the stdin JSON payload's
        `cwd` field naming workspace B. Assert the resulting row lands
        under workspace B. This is the direct regression test for FR-16 —
        the subprocess's own working directory is undocumented and must
        never be trusted."""
        # Workspace A: a resolvable project the process's own OS cwd is set to.
        workspace_a = git_repo
        slug_a = "workspace-a-project"
        _write_project(workspace_a, slug_a)

        # Workspace B: a SEPARATE resolvable project named by the payload's cwd.
        workspace_b = tmp_path / "repo-b"
        workspace_b.mkdir()
        _run_git(workspace_b, "init", "-q", "-b", "trunk")
        _run_git(workspace_b, "config", "user.email", "test@example.invalid")
        _run_git(workspace_b, "config", "user.name", "Test")
        (workspace_b / "README.md").write_text("hello\n", encoding="utf-8")
        _run_git(workspace_b, "add", "README.md")
        _run_git(workspace_b, "commit", "-q", "-m", "initial")
        slug_b = "workspace-b-project"
        _write_project(workspace_b, slug_b)

        profile = tmp_path / "profile.yaml"
        _write_profile(profile)

        payload = {
            "session_id": "fr16-regression-session",
            "cwd": str(workspace_b),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }

        result = _run_hook_script(
            skill_root=skill_root,
            payload=payload,
            cwd=workspace_a,  # the subprocess's OWN OS cwd — deliberately A, not B
            profile_path=profile,
        )

        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert '"type":"session_registered"' in _events_log(workspace_b, slug_b), (
            "row did not land under workspace B (the payload's cwd)"
        )
        assert _events_log(workspace_a, slug_a) == "", (
            "row leaked into workspace A (the subprocess's own OS cwd) — "
            "the hook used $(pwd) or an equivalent instead of the payload's cwd"
        )


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestSessionStartHookForwardsClaudeProjectDirAsAnchor:
    """ARCHITECTURE.md Addendum §A, end to end through the real script (not
    just `cli.py`'s `--anchor` flag directly): the hook script itself must
    read `$CLAUDE_PROJECT_DIR` from its own environment and forward it as
    `--anchor`. The CLI-level anchor tests below prove `cli.py` prefers a
    given anchor; this test proves the SHELL SCRIPT actually wires
    `$CLAUDE_PROJECT_DIR` into that flag rather than only documenting the
    intent in a comment."""

    def test_hook_uses_claude_project_dir_env_var_over_payload_cwd(
        self, skill_root: Path, git_repo: Path, tmp_path: Path
    ) -> None:
        """Set `$CLAUDE_PROJECT_DIR` in the hook subprocess's own
        environment to workspace A, and the payload's `cwd` to a
        DIFFERENT resolvable workspace B; assert the row lands under A."""
        # Workspace A: named by $CLAUDE_PROJECT_DIR — must win.
        workspace_a = git_repo
        slug_a = "anchor-env-project"
        _write_project(workspace_a, slug_a)

        # Workspace B: a SEPARATE resolvable project named by the payload's
        # cwd — stands in for a drifted cwd; must NOT be used.
        workspace_b = tmp_path / "repo-b"
        _init_second_repo(workspace_b)
        slug_b = "anchor-env-drifted-project"
        _write_project(workspace_b, slug_b)

        profile = tmp_path / "profile.yaml"
        _write_profile(profile)

        payload = {
            "session_id": "claude-project-dir-session",
            "cwd": str(workspace_b),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }

        result = _run_hook_script(
            skill_root=skill_root,
            payload=payload,
            cwd=workspace_b,
            profile_path=profile,
            extra_env={"CLAUDE_PROJECT_DIR": str(workspace_a)},
            unset_claude_project_dir=False,
        )

        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert '"type":"session_registered"' in _events_log(workspace_a, slug_a), (
            "row did not land under $CLAUDE_PROJECT_DIR's project"
        )
        assert _events_log(workspace_b, slug_b) == "", (
            "row leaked into the payload cwd's project instead of $CLAUDE_PROJECT_DIR's"
        )


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestSessionStartHookSessionIdPassthrough:
    """TC-38: FR-17 — the payload's `session_id` reaches the registered
    fragment verbatim."""

    def test_hook_passes_session_id_through_to_the_cli(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """FR-17: the payload's `session_id` reaches the registered
        fragment exactly, via `--session-id`, rather than being re-derived
        via the fuzzy `(cwd, branch)` anchors `observe launch` falls back
        to when no explicit id is available."""
        workspace, slug, profile = enabled_project
        payload = {
            "session_id": "fr17-passthrough-session",
            "cwd": str(workspace),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }

        result = _run_hook_script(
            skill_root=skill_root, payload=payload, cwd=workspace, profile_path=profile
        )

        assert result.returncode == 0
        log = _events_log(workspace, slug)
        assert '"local_id":"fr17-passthrough-session"' in log


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestSessionStartHookIdempotency:
    """TC-39: FR-19/NFR-6 — every `SessionStart` matcher dedupes to one row
    across repeated firings, invoked through the actual hook script."""

    @pytest.mark.parametrize(
        "matcher", ["startup", "resume", "clear", "compact", "fork"]
    )
    def test_all_five_matchers_dedupe_to_one_row(
        self, matcher: str, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """FR-19/NFR-6: invoked through the actual hook SCRIPT (not the
        library call — this is the ceiling-level counterpart to
        `test_observe_session.py`'s `TestIdempotencyAcrossFiveMatchers`),
        parametrized over each `SessionStart` matcher; one session
        produces exactly one row across repeated firings.

        Only `matcher="startup"` reflects a harness-observed payload shape
        (Chunk 1's probe); the other four matcher values are synthesised —
        see this module's docstring."""
        workspace, slug, profile = enabled_project
        payload = {
            "session_id": f"idempotency-{matcher}-session",
            "cwd": str(workspace),
            "hook_event_name": "SessionStart",
            "source": matcher,
        }

        for _ in range(3):
            result = _run_hook_script(
                skill_root=skill_root, payload=payload, cwd=workspace, profile_path=profile
            )
            assert result.returncode == 0, (
                f"hook exited {result.returncode} for matcher={matcher}\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

        log_lines = [
            line for line in _events_log(workspace, slug).splitlines() if line.strip()
        ]
        registered = [line for line in log_lines if '"type":"session_registered"' in line]
        assert len(registered) == 1, (
            f"expected exactly one session_registered row for matcher={matcher}, "
            f"found {len(registered)}"
        )


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestSessionStartHookLatency:
    """TC-40: NFR-2 — wall-clock latency of one real hook invocation stays
    under the hard ceiling; median is reported, not gated, per run noise."""

    def test_hook_latency_within_agreed_budget(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """NFR-2: wall-clock measurement of one hook invocation against the
        PM-set budget for this chunk — median target <= 1.0s, hard ceiling
        2.0s. This test asserts the hard ceiling on every run; the median
        is reported by the caller (see the chunk's status report) rather
        than asserted here, since a median over a handful of runs is noisy
        in a shared CI environment."""
        workspace, slug, profile = enabled_project
        samples = []
        for i in range(5):
            payload = {
                "session_id": f"latency-session-{i}",
                "cwd": str(workspace),
                "hook_event_name": "SessionStart",
                "source": "startup",
            }
            start = time.perf_counter()
            result = _run_hook_script(
                skill_root=skill_root, payload=payload, cwd=workspace, profile_path=profile
            )
            elapsed = time.perf_counter() - start
            assert result.returncode == 0
            samples.append(elapsed)

        samples.sort()
        median = samples[len(samples) // 2]
        maximum = samples[-1]
        assert maximum < 2.0, (
            f"hook latency exceeded the 2.0s hard ceiling: samples={samples!r}"
        )
        # Surfaced for the chunk's status report, not asserted as a hard gate
        # (NFR-2's median target is a budget to reach for, not a CI trip-wire).
        print(f"session-start hook latency: median={median:.3f}s max={maximum:.3f}s")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-native .cmd shim test")
class TestSessionStartHookWindowsShim:
    """TC-41: NFR-6 — the `.cmd` shim exists and actually delegates to the
    extensionless script via Git Bash on this machine."""

    def test_cmd_shim_present_and_delegates_to_sh_script(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """`templates/hooks/claude-code/session-start.cmd` exists, matches
        the existing `hooks/session-start.cmd` precedent's shape, and
        (on this machine, per NFR-6) actually delegates to the
        extensionless/`.sh` script via Git Bash."""
        cmd_path = _hook_cmd(skill_root)
        assert cmd_path.is_file(), f".cmd shim not found at {cmd_path}"
        assert _hook_script(skill_root).is_file(), "the .sh script the shim delegates to is missing"

        workspace, slug, profile = enabled_project
        payload = {
            "session_id": "cmd-shim-session",
            "cwd": str(workspace),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }
        env = os.environ.copy()
        env.pop("CLAUDE_PROJECT_DIR", None)
        env["SUPERHUMAN_PROFILE"] = str(profile)

        result = subprocess.run(
            ["cmd.exe", "/c", str(cmd_path)],
            input=json.dumps(payload),
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )

        assert result.returncode == 0, (
            f".cmd shim exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert '"type":"session_registered"' in _events_log(workspace, slug), (
            ".cmd shim did not delegate through to a registered row"
        )


class TestSessionStartAnchorRegression:
    """PM ruling (chunk 6 kickoff, ARCHITECTURE.md Addendum §A): `cli.py`'s
    `session-start` subparser gains a generically-named `--anchor` option.
    These two tests are the regression pair the ruling requires, at the
    CLI layer (not the hook subprocess) since `--anchor` is `cli.py`
    surface, not template surface."""

    def test_no_anchor_behavior_is_unchanged(
        self, enabled_project: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling `observe session-start --hook-payload -` with no
        `--anchor` given must behave exactly as it did before this chunk:
        the workspace/slug are derived from the payload's `cwd` alone. This
        is a regression test, not new-feature coverage — by construction,
        the new `--anchor` branch in `_cmd_observe_session_start` is only
        ever entered when `args.anchor` is truthy, so omitting the flag
        exercises the exact pre-chunk-6 code path."""
        workspace, slug, profile = enabled_project
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
        payload = json.dumps({"session_id": "no-anchor-session", "cwd": str(workspace)})
        monkeypatch.setattr("sys.stdin", _fake_stdin(payload))

        parser = build_parser()
        args = parser.parse_args(
            ["observe", "session-start", "--hook-payload", "-", "--harness", "portable"]
        )
        assert args.anchor is None  # default is None; the flag was not passed

        exit_code = args.func(args)

        assert exit_code == 0
        log = _events_log(workspace, slug)
        assert '"origination":"observed"' in log
        assert '"local_id":"no-anchor-session"' in log

    def test_anchor_preferred_over_a_drifted_cwd(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ARCHITECTURE.md Addendum §A: when `--anchor` is given and
        resolves, it is tried FIRST, ahead of the payload's `cwd` — the
        scenario `CLAUDE_PROJECT_DIR` exists to fix (a shell that `cd`'d
        into a nested docs clone; `cwd` names the inner clone, the anchor
        still names the outer root). Build workspace A (the anchor,
        resolvable) and workspace B (a DIFFERENT resolvable project named
        by the payload's `cwd`, standing in for the drifted value); assert
        the row lands under A, not B."""
        workspace_a = git_repo
        slug_a = "anchor-project"
        _write_project(workspace_a, slug_a)

        workspace_b = tmp_path / "drifted-repo"
        workspace_b.mkdir()
        _run_git(workspace_b, "init", "-q", "-b", "trunk")
        _run_git(workspace_b, "config", "user.email", "test@example.invalid")
        _run_git(workspace_b, "config", "user.name", "Test")
        (workspace_b / "README.md").write_text("hello\n", encoding="utf-8")
        _run_git(workspace_b, "add", "README.md")
        _run_git(workspace_b, "commit", "-q", "-m", "initial")
        slug_b = "drifted-project"
        _write_project(workspace_b, slug_b)

        profile = tmp_path / "profile.yaml"
        _write_profile(profile)
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        payload = json.dumps(
            {"session_id": "anchor-preferred-session", "cwd": str(workspace_b)}
        )
        monkeypatch.setattr("sys.stdin", _fake_stdin(payload))

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "session-start",
                "--hook-payload",
                "-",
                "--harness",
                "portable",
                "--anchor",
                str(workspace_a),
            ]
        )

        exit_code = args.func(args)

        assert exit_code == 0
        assert '"type":"session_registered"' in _events_log(workspace_a, slug_a), (
            "row did not land under the anchor's project"
        )
        assert _events_log(workspace_b, slug_b) == "", (
            "row leaked into the drifted cwd's project instead of the anchor's"
        )

    def test_anchor_falls_back_to_cwd_when_anchor_does_not_resolve(
        self, enabled_project: tuple[Path, str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half of the fallback contract: `--anchor` is GIVEN but
        does not resolve to a project (e.g. it names a directory outside
        any git repository — the exact shape CHUNK-1-FINDINGS.md finding 4
        measured for a real session launched at the home directory).
        `locate_project(payload.cwd)` must still be tried, and must still
        succeed — the anchor is a preference, not a replacement that can
        strand a resolvable session."""
        workspace, slug, profile = enabled_project
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        unresolvable_anchor = tmp_path / "not-a-git-repo"
        unresolvable_anchor.mkdir()

        payload = json.dumps(
            {"session_id": "anchor-fallback-session", "cwd": str(workspace)}
        )
        monkeypatch.setattr("sys.stdin", _fake_stdin(payload))

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "session-start",
                "--hook-payload",
                "-",
                "--harness",
                "portable",
                "--anchor",
                str(unresolvable_anchor),
            ]
        )

        exit_code = args.func(args)

        assert exit_code == 0
        log = _events_log(workspace, slug)
        assert '"type":"session_registered"' in log
        assert '"local_id":"anchor-fallback-session"' in log


# --- Chunk 7: SubagentStart hook — shared helpers -----------------------------------

_SUBAGENT_HOOK_SCRIPT_NAME = "subagent-start"
_SUBAGENT_HOOK_CMD_NAME = "subagent-start.cmd"


def _subagent_hook_script(skill_root: Path) -> Path:
    return skill_root / "templates" / "hooks" / "claude-code" / _SUBAGENT_HOOK_SCRIPT_NAME


def _subagent_hook_cmd(skill_root: Path) -> Path:
    return skill_root / "templates" / "hooks" / "claude-code" / _SUBAGENT_HOOK_CMD_NAME


def _subagent_payload(
    *,
    session_id: str,
    cwd: Path | str,
    agent_id: str = "test-agent-id",
    agent_type: str = "developer",
    transcript_path: Path | str | None = None,
) -> dict:
    """Build a synthetic `SubagentStart` payload — the 8-field shape measured
    in CHUNK-1-FINDINGS.md (`session_id`, `cwd`, `transcript_path`,
    `scratchpad_dir`, `hook_event_name`, `agent_id`, `agent_type`,
    `prompt_id`); this helper populates the fields this chunk's hook/filter
    actually consume."""
    payload: dict = {
        "session_id": session_id,
        "cwd": str(cwd),
        "hook_event_name": "SubagentStart",
        "agent_id": agent_id,
        "agent_type": agent_type,
    }
    if transcript_path is not None:
        payload["transcript_path"] = str(transcript_path)
    return payload


def _write_transcript(
    path: Path,
    *,
    message_id: str = "msg_test_dispatch",
    subagent_type: str = "developer",
    prompt: str,
    tool_name: str = "Agent",
) -> None:
    """Write a synthetic, minimal Claude-Code-shaped transcript JSONL with
    ONE dispatching assistant-message record — the shape confirmed by
    reading a real transcript on this machine (chunk 7 dispatch brief):
    the tool_use's `name` is `"Agent"`, and its `input` carries
    `subagent_type`/`prompt`, inside a `message` with `role: "assistant"`
    and an `id` (real transcripts split one message across several JSONL
    records sharing that `id`; one record suffices for these tests since
    the filter only needs the last dispatching message's candidates)."""
    record = {
        "type": "assistant",
        "message": {
            "id": message_id,
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_test",
                    "name": tool_name,
                    "input": {
                        "description": "test dispatch",
                        "subagent_type": subagent_type,
                        "prompt": prompt,
                    },
                }
            ],
        },
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _run_subagent_hook_script(
    *,
    skill_root: Path,
    stdin_text: str,
    cwd: Path,
    profile_path: Path | None = None,
    extra_env: dict | None = None,
    unset_claude_project_dir: bool = True,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess:
    """Invoke `templates/hooks/claude-code/subagent-start` as a real subprocess.

    Args:
        skill_root: the superhuman skill checkout.
        stdin_text: the raw text piped to the hook's stdin — deliberately a
            raw string rather than a payload dict, so fault-injection tests
            (corrupted/non-JSON/empty stdin) can pass arbitrary bytes.
        cwd: the subprocess's OWN OS working directory.
        profile_path: `SUPERHUMAN_PROFILE` override so fleet is enabled;
            omit for fault tests where the payload is never expected to
            reach a fleet write at all.
        extra_env: additional/overriding environment variables (e.g. a
            `PATH` override for the missing-interpreter fault).
        unset_claude_project_dir: pop `CLAUDE_PROJECT_DIR` first, matching
            `_run_hook_script`'s identical rationale.
        timeout: bounded wait.

    Returns:
        subprocess.CompletedProcess: with `text=True` stdout/stderr.
    """
    env = os.environ.copy()
    if unset_claude_project_dir:
        env.pop("CLAUDE_PROJECT_DIR", None)
    if profile_path is not None:
        env["SUPERHUMAN_PROFILE"] = str(profile_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(_subagent_hook_script(skill_root))],
        input=stdin_text,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


# --- Chunk 7: SubagentStart hook — the fault-injection matrix -----------------------


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestSubagentHookFaultInjectionMatrix:
    """FR-18's fault matrix. Every test's PRIMARY assertion is exit code 0
    — that is the correctness bar, because exit 2 on `SubagentStart` blocks
    the subagent outright.
    """

    def test_exits_0_on_corrupted_truncated_json_payload(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        """TC-42, fault #1: a syntactically-broken JSON string on stdin,
        e.g. `{"session_id": "abc", "cwd": "/x` (unterminated)."""
        result = _run_subagent_hook_script(
            skill_root=skill_root,
            stdin_text='{"session_id": "abc", "cwd": "/x',
            cwd=tmp_path,
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_exits_0_on_non_json_stdin(self, skill_root: Path, tmp_path: Path) -> None:
        """TC-43, fault #2: arbitrary non-JSON bytes (including a raw NUL
        and a raw 0xFF byte) on stdin."""
        result = _run_subagent_hook_script(
            skill_root=skill_root,
            stdin_text="not json at all\x00\xff",
            cwd=tmp_path,
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_exits_0_on_empty_stdin(self, skill_root: Path, tmp_path: Path) -> None:
        """TC-44, fault #3: stdin closed immediately / zero bytes."""
        result = _run_subagent_hook_script(
            skill_root=skill_root, stdin_text="", cwd=tmp_path
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_exits_0_when_interpreter_missing(self, skill_root: Path, tmp_path: Path) -> None:
        """TC-45, fault #4: `PATH` names a directory with no `python`/
        `python3` executable, simulating a moved/uninstalled interpreter.
        The shell wrapper's own `trap ... EXIT` must catch this, not just
        the Python layer — there IS no Python layer on this path."""
        empty_path_dir = tmp_path / "empty-path"
        empty_path_dir.mkdir()
        payload = _subagent_payload(session_id="tc45-session", cwd=tmp_path)
        result = _run_subagent_hook_script(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=tmp_path,
            extra_env={"PATH": str(empty_path_dir)},
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_exits_0_on_unreadable_transcript_path(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """TC-46, fault #5: `transcript_path` names a file that does not
        exist. The hook (via the D5 filter) must not read it unconditionally
        without a guard, and must still write no row and exit 0."""
        workspace, slug, profile = enabled_project
        missing_transcript = workspace / "does-not-exist.jsonl"
        payload = _subagent_payload(
            session_id="tc46-session",
            cwd=workspace,
            transcript_path=missing_transcript,
        )
        result = _run_subagent_hook_script(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert _events_log(workspace, slug) == "", (
            "an unreadable transcript_path must never register a row -- the "
            "D5 filter's zero-candidates case means 'no row', not a guess"
        )

    def test_exits_0_when_workspace_no_longer_exists(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        """TC-47, fault #6: the payload `cwd` names a directory deleted
        between payload construction and hook invocation."""
        deleted_workspace = tmp_path / "deleted-workspace"
        deleted_workspace.mkdir()
        shutil.rmtree(deleted_workspace)

        payload = _subagent_payload(session_id="tc47-session", cwd=deleted_workspace)
        result = _run_subagent_hook_script(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=tmp_path,  # the subprocess's OWN cwd must exist; the payload's need not
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_survives_a_hung_subprocess_under_the_harness_timeout_backstop(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        """TC-48, fault #7: the invoked `python` is replaced with a stub
        that sleeps indefinitely; this test's own bounded wait is shorter
        than the sleep, and it asserts the process TREE the hook wrapper
        spawned can be cleanly killed with nothing left running.

        DOCUMENTED LIMITATION (also true of the shipped hook itself): the
        wrapper's own `trap ... EXIT` cannot preempt a genuine hang — it
        only guarantees exit 0 on paths that DO return. The actual
        mitigation for a true hang is the harness's own per-hook
        `"timeout"` setting, written by the Chunk 8 installer as a backstop
        (NFR-2). This test proves the wrapper does not DEFEAT that backstop
        (e.g. by disabling signal delivery, backgrounding an untracked
        child, or swallowing `SIGTERM`) — it does not claim the hook
        self-terminates a hang.
        """
        stub_dir = tmp_path / "fake-python-bin"
        stub_dir.mkdir()
        marker_file = tmp_path / "stub.pid"
        stub_path = stub_dir / "python3"
        stub_path.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "-c" ]; then\n'
            "  exit 0\n"
            "fi\n"
            f'echo $$ > "{marker_file.as_posix()}"\n'
            "sleep 300\n",
            encoding="utf-8",
        )
        stub_path.chmod(0o755)

        workspace = tmp_path / "ws"
        workspace.mkdir()

        payload = _subagent_payload(session_id="tc48-session", cwd=workspace)

        env = os.environ.copy()
        env.pop("CLAUDE_PROJECT_DIR", None)
        env["PATH"] = str(stub_dir) + os.pathsep + env.get("PATH", "")

        proc = subprocess.Popen(
            [_BASH, str(_subagent_hook_script(skill_root))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workspace),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload))
            proc.stdin.close()

            with pytest.raises(subprocess.TimeoutExpired):
                proc.wait(timeout=3.0)

            # Give the stub a moment to have written its own pid -- it runs
            # concurrently with the bounded wait above.
            deadline = time.time() + 5.0
            stub_pid = None
            while time.time() < deadline:
                if marker_file.exists():
                    stub_pid = marker_file.read_text(encoding="utf-8").strip()
                    if stub_pid:
                        break
                time.sleep(0.1)

            # The harness-level backstop, simulated: kill the whole process
            # TREE the hook wrapper spawned, not just the top bash process
            # -- proving the wrapper did not detach the hung python from
            # that tree (no `disown`/`setsid`/backgrounding that would
            # defeat a real per-hook "timeout" backstop, Chunk 8/NFR-2).
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:
                import signal as _signal

                os.killpg(proc.pid, _signal.SIGKILL)
            proc.wait(timeout=10)

            if stub_pid:
                if sys.platform == "win32":
                    check = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {stub_pid}"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    survived = stub_pid in check.stdout
                else:
                    survived = os.path.exists(f"/proc/{stub_pid}")
                assert not survived, (
                    f"stub python process {stub_pid} survived the process-tree kill -- "
                    "the hook wrapper detached it from the tree the harness backstop "
                    "would have killed, defeating the timeout backstop"
                )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    def test_exits_0_on_simulated_disk_full(
        self, enabled_project: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-49, fault #8: at the PYTHON layer (mirroring the existing
        `test_journal_write_failure_falls_back_to_exactly_one_stderr_line`
        idiom in `tests/fleet/test_observe.py`), monkeypatch the write call
        to raise `OSError(errno.ENOSPC, "No space left on device")`. A true
        OS-level full-disk simulation is out of scope for a portable
        suite; this is the practical equivalent, since `observe.py`'s
        broad catch treats any `OSError` identically regardless of `errno`.

        This one test in the matrix exercises the CLI layer directly
        (`observe dispatch`'s own `args.func(args) == 0`) rather than the
        real shell subprocess, since the fault must be injected into THIS
        process's own `Path.mkdir`, which a subprocess cannot see.
        """
        workspace, slug, profile = enabled_project
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        def _raise_enospc(*args: object, **kwargs: object) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(Path, "mkdir", _raise_enospc)

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "dispatch",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--dispatch-id",
                "tc49-dispatch",
                "--harness",
                "subagent",
                "--local-id",
                "tc49-dispatch",
            ]
        )
        assert args.func(args) == 0


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestSubagentHookNeverPrintsATraceback:
    """TC-53: across every fault in TC-42..TC-49, stdout never carries a
    Python traceback."""

    @pytest.mark.parametrize(
        "fault",
        [
            "corrupted_json",
            "non_json",
            "empty_stdin",
            "missing_interpreter",
            "unreadable_transcript",
            "deleted_workspace",
        ],
    )
    def test_stdout_never_carries_a_python_traceback(
        self, fault: str, skill_root: Path, tmp_path: Path
    ) -> None:
        """Across every SUBPROCESS-level fault above (`disk_full` is
        covered separately below, at the CLI-function layer TC-49 itself
        runs at), stdout is either empty or the one sanctioned diagnostic
        line — never a Python traceback, which would itself be a symptom
        the trap failed to catch cleanly, independent of the exit code."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        extra_env: dict | None = None

        if fault == "corrupted_json":
            stdin_text = '{"session_id": "abc", "cwd": "/x'
        elif fault == "non_json":
            stdin_text = "not json at all\x00\xff"
        elif fault == "empty_stdin":
            stdin_text = ""
        elif fault == "missing_interpreter":
            empty_path_dir = tmp_path / "empty-path"
            empty_path_dir.mkdir()
            extra_env = {"PATH": str(empty_path_dir)}
            stdin_text = json.dumps(_subagent_payload(session_id=fault, cwd=workspace))
        elif fault == "unreadable_transcript":
            stdin_text = json.dumps(
                _subagent_payload(
                    session_id=fault,
                    cwd=workspace,
                    transcript_path=workspace / "does-not-exist.jsonl",
                )
            )
        else:  # "deleted_workspace"
            deleted = tmp_path / "deleted"
            deleted.mkdir()
            shutil.rmtree(deleted)
            stdin_text = json.dumps(_subagent_payload(session_id=fault, cwd=deleted))

        result = _run_subagent_hook_script(
            skill_root=skill_root, stdin_text=stdin_text, cwd=workspace, extra_env=extra_env
        )
        assert result.returncode == 0
        assert "Traceback (most recent call last)" not in result.stdout, (
            f"fault={fault}: a Python traceback leaked onto stdout:\n{result.stdout}"
        )

    def test_stdout_never_carries_a_python_traceback_disk_full(
        self,
        enabled_project: tuple[Path, str, Path],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The `disk_full` fault's counterpart to the parametrized test
        above. TC-49 exercises this fault at the CLI-function layer
        (`_cmd_observe_dispatch`), never spawning a subprocess whose stdout
        the parametrized test could capture — so this asserts the same
        "no traceback" property directly against `capsys`, the layer TC-49
        itself runs at."""
        workspace, slug, profile = enabled_project
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        def _raise_enospc(*args: object, **kwargs: object) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(Path, "mkdir", _raise_enospc)

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "dispatch",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--dispatch-id",
                "tc53-disk-full-dispatch",
                "--harness",
                "subagent",
                "--local-id",
                "tc53-disk-full-dispatch",
            ]
        )
        assert args.func(args) == 0
        captured = capsys.readouterr()
        assert "Traceback (most recent call last)" not in captured.out


class TestDecisionCGranularity:
    """D5: the SubagentStart hook applies Decision C's exact predicate —
    'a dispatch registers iff its prompt leads with a `roles/*.md`
    block' — read from `transcript_path`, per Chunk 1's probe finding and
    chunk 7's PM rulings (delta-report-002).
    """

    _ROLE_BLOCK_PROMPT = (
        "---\n"
        "name: developer\n"
        "tier: standard\n"
        "declared-references:\n"
        "  - references/test-driven-development/SKILL.md\n"
        "---\n"
        "\n"
        "# Developer role\n"
        "\n"
        "You are the Developer for this superhuman project...\n"
    )
    _PROSE_BRIEF_PROMPT = (
        "You are the Developer for **chunk 7** of the superhuman project "
        "`fleet-deterministic-seams`. Implement the SubagentStart hook...\n"
    )
    _ROLES_MENTIONED_MIDBODY_PROMPT = (
        "You are a general-purpose research agent dispatched to investigate "
        "how superhuman's role system works. Its role prompts live under "
        "roles/*.md (e.g. roles/developer.md) and are dispatched by the PM. "
        "Summarize how role tiers are declared.\n"
    )

    @pytest.mark.skipif(
        _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
    )
    def test_subagent_hook_registers_a_role_dispatch(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """TC-50: a synthetic payload whose `transcript_path` content leads
        with a `roles/*.md` block: the hook registers a dispatch row."""
        workspace, slug, profile = enabled_project
        transcript = workspace / "transcript.jsonl"
        _write_transcript(transcript, prompt=self._ROLE_BLOCK_PROMPT)
        payload = _subagent_payload(
            session_id="tc50-session", cwd=workspace, transcript_path=transcript
        )

        result = _run_subagent_hook_script(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )

        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        log = _events_log(workspace, slug)
        assert '"type":"session_registered"' in log
        assert '"origination":"spawned"' in log
        assert '"harness":"subagent"' in log

    @pytest.mark.skipif(
        _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
    )
    def test_subagent_hook_does_not_register_a_research_fanout(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """TC-51: same payload shape, but the dispatch prompt does NOT lead
        with a `roles/*.md` block (a general-purpose research fan-out). No
        row is written."""
        workspace, slug, profile = enabled_project
        transcript = workspace / "transcript.jsonl"
        _write_transcript(transcript, prompt=self._PROSE_BRIEF_PROMPT)
        payload = _subagent_payload(
            session_id="tc51-session", cwd=workspace, transcript_path=transcript
        )

        result = _run_subagent_hook_script(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )

        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert _events_log(workspace, slug) == ""

    @pytest.mark.skipif(
        _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
    )
    def test_decision_c_predicate_edge_case_roles_mentioned_but_not_leading(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """TC-52: a prompt that MENTIONS `roles/` mid-body (e.g. discussing
        the role system) but does not LEAD with it. Must not register —
        this is the precision case separating Decision C's exact predicate
        from a loose substring match."""
        workspace, slug, profile = enabled_project
        transcript = workspace / "transcript.jsonl"
        _write_transcript(
            transcript,
            subagent_type="general-purpose",
            prompt=self._ROLES_MENTIONED_MIDBODY_PROMPT,
        )
        payload = _subagent_payload(
            session_id="tc52-session",
            cwd=workspace,
            agent_type="general-purpose",
            transcript_path=transcript,
        )

        result = _run_subagent_hook_script(
            skill_root=skill_root,
            stdin_text=json.dumps(payload),
            cwd=workspace,
            profile_path=profile,
        )

        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert _events_log(workspace, slug) == ""


# --- Chunk 7a fix: hooks must decode stdin as UTF-8 -----------------------------------


def _run_hook_script_raw_bytes(
    *,
    skill_root: Path,
    stdin_bytes: bytes,
    cwd: Path,
    profile_path: Path | None = None,
    extra_env: dict | None = None,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess:
    """Invoke `templates/hooks/claude-code/session-start` with RAW BYTES on
    stdin — no `text=True`, no `encoding=`, and `PYTHONIOENCODING`/
    `PYTHONUTF8` stripped from the child environment — so the test exercises
    exactly what a real harness does: write UTF-8 bytes and nothing else.
    `_run_hook_script` above cannot do this (it always passes `text=True,
    encoding="utf-8"`, which pre-decodes/re-encodes symmetrically on this
    process's own side and hides the defect this chunk fixes).
    """
    env = os.environ.copy()
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    if profile_path is not None:
        env["SUPERHUMAN_PROFILE"] = str(profile_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(_hook_script(skill_root))],
        input=stdin_bytes,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        timeout=timeout,
    )


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestHookPayloadUTF8StdinDecoding:
    """Regression for the defect fixed in this chunk (chunk 7a fix): the
    harness writes the hook payload as UTF-8 bytes, but
    `scripts/fleet/hook_payload.py`'s `read_hook_payload` used to read
    stdin with `sys.stdin.read()`, which decodes using the process's
    locale-preferred encoding — cp1252 on this Windows runner, not UTF-8.
    Any non-ASCII byte anywhere in the payload (not just a role-file em
    dash — a `cwd` containing a non-ASCII path segment, for instance) was
    silently mangled, so the locator failed and the hook wrote no row at
    all, with no error surfaced anywhere (NFR-9's fail-soft posture makes
    this doubly silent). These tests feed RAW UTF-8 BYTES (see
    `_run_hook_script_raw_bytes`) through the REAL `session-start` hook —
    `read_hook_payload`'s own call site — using only synthetic content
    (never the captured live payload's text; NFR-8/publication guard).
    """

    def test_non_ascii_session_id_survives_the_utf8_round_trip(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """A `session_id` containing non-ASCII characters (an em dash and
        a character with no cp1252 representation) must land in the row
        UNCHANGED — proving `read_hook_payload` decodes stdin as UTF-8.

        This deliberately keeps the WORKSPACE path itself plain ASCII
        (unlike a non-ASCII `cwd`, which this module's own docstring notes
        as "latent") — `locate_project` (`scripts/fleet/locate.py`) reads
        `git rev-parse`'s stdout via `subprocess.run(text=True)` with no
        explicit `encoding=` either, the SAME defect class, but in a
        different file outside this chunk's three assigned sites. Until
        that is fixed too, a non-ASCII path segment in `cwd` fails to
        resolve for an independent reason and would confound this
        specific regression — reported to the PM as a new finding rather
        than fixed here (matching this chunk's own treatment of
        `scripts/superhuman_profile.py:2149`)."""
        workspace, slug, profile = enabled_project
        session_id = "utf8-sess-—-→"  # em dash, rightwards arrow
        payload = {
            "session_id": session_id,
            "cwd": str(workspace),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }
        # `ensure_ascii=False` matches a real harness (e.g. Node's
        # `JSON.stringify`), which emits non-ASCII characters as literal
        # UTF-8 bytes, never `\uXXXX` escapes — `json.dumps`'s own default
        # (`ensure_ascii=True`) would sidestep the byte-level defect
        # entirely by pre-escaping every non-ASCII character to plain
        # ASCII, which is exactly why `_run_hook_script`'s existing calls
        # never caught this.
        stdin_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        result = _run_hook_script_raw_bytes(
            skill_root=skill_root,
            stdin_bytes=stdin_bytes,
            cwd=workspace,
            profile_path=profile,
            # PM ruling R9 (chunk 9): this test was reproduced flaking
            # under deliberate concurrent subprocess load (24 background
            # `git` workers for 40-60s; see the chunk 9 status report for
            # exact counts) with the SAME "no row landed, log=''" symptom
            # this test itself would otherwise show for the wrong reason
            # -- `locate.py`'s own `_GIT_TIMEOUT_SECONDS` (0.25s, NFR-2)
            # racing `locate_project`'s `git rev-parse` calls inside this
            # REAL CHILD PROCESS. A parent-process `monkeypatch` cannot
            # reach a subprocess's module state at all, so this widens
            # `_resolved_git_timeout`'s test-only env var IN THE CHILD's
            # OWN environment instead; the production default (0.25,
            # unset) is untouched.
            extra_env={"SUPERHUMAN_FLEET_LOCATE_GIT_TIMEOUT_SECONDS": "5.0"},
        )

        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout!r}\nSTDERR:\n{result.stderr!r}"
        )
        log = _events_log(workspace, slug)
        assert '"type":"session_registered"' in log, (
            f"no row landed -- log={log!r}"
        )
        assert json.dumps(session_id) in log or session_id in log, (
            f"session_id was mangled in transit -- expected {session_id!r} to survive "
            f"verbatim; log={log!r}"
        )

    def test_invalid_utf8_stdin_writes_no_row_and_exits_0(
        self, skill_root: Path, enabled_project: tuple[Path, str, Path]
    ) -> None:
        """Bytes that are not valid UTF-8 at all (a lone `0xFF`/`0xFE`
        pair) must degrade to "no payload" — same as malformed JSON —
        never a decode exception reaching the harness."""
        workspace, slug, profile = enabled_project
        result = _run_hook_script_raw_bytes(
            skill_root=skill_root,
            stdin_bytes=b"\xff\xfe{\"session_id\": \"x\"",
            cwd=workspace,
            profile_path=profile,
        )
        assert result.returncode == 0, (
            f"hook exited {result.returncode}\nSTDOUT:\n{result.stdout!r}\nSTDERR:\n{result.stderr!r}"
        )
        assert _events_log(workspace, slug) == ""
