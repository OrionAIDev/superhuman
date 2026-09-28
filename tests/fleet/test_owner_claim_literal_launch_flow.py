"""O14-e proof test #1: the launch instruction's two commands, run literally,
as SEPARATE subprocesses, against a real fixture project.

The O3 code review that produced the O14 addendum ran the launch
instruction's `observe session-start` and `owner claim` lines literally in
separate processes (the way a launched session actually experiences them --
each an independent `python -m scripts.fleet.cli ...` invocation, never the
same Python process) and found two defects, both fixed by O14-a/b:

1. The bare `observe session-start` line (no `--harness`) registered
   `portable/<ws>/<slug>/<pid>` -- the SESSION-START process's own pid. A
   LATER `owner claim` process (a genuinely different pid) resolved a
   DIFFERENT node under the default `--harness portable` path, so `owner
   claim` hit `not_registered` on every default flow.
2. `--harness claude` with no `--session-id` had no honest way to resolve
   "which session am I" across two separate processes.

This module reproduces the fix directly, but -- unlike an earlier version of
this file -- never hand-writes the argv it runs. It extracts the literal,
backtick-quoted `observe session-start` and `owner claim` command SHAPES
straight out of `adapter.base.format_launch_instruction()` (the actual text
a launched session reads), mechanically substitutes the placeholders for a
real fixture project, and runs the result as genuinely separate
`subprocess.run` calls from the superhuman skill root (`C:\\shfo`), for both
`--harness claude` (identity via `CLAUDE_CODE_SESSION_ID` in the subprocess
environment) and `--harness portable` (identity via a stable `--local-id`,
reused across both commands). Because every token traces back to a literal
span the instruction text actually contains, a future edit that drops
`--harness claude` from the claim sentence, or reshapes the session-start
sentence, breaks this module's own placeholder-extraction helpers (or the
resulting subprocess call) instead of silently passing -- unlike the
hand-written-argv version this replaces, which could drift from the real
instruction text without any test noticing.

The `observe session-start` command, as the instruction actually spells it,
carries no `--harness` flag at all (it defaults to `portable`) -- this
module deliberately does not add one for the claude variant either; doing
so would test a flag combination the instruction never tells a launched
session to run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.fleet.adapter.base import format_launch_instruction

_REPO_ROOT = Path(__file__).resolve().parents[2]

_BACKTICK_RE = re.compile(r"`([^`]*)`")


def _backtick_commands(text: str) -> list[str]:
    """Return every backtick-quoted command literal in `text`, in order."""
    return _BACKTICK_RE.findall(text)


def _session_start_command_template() -> str:
    """Return the launch instruction's literal `observe session-start` command.

    Placeholders (`<this project's root>` etc.) are left intact for the
    caller to substitute.

    Raises:
        AssertionError: if the instruction no longer names a backtick-quoted
            `observe session-start` invocation at all -- a session-start
            sentence reshaped out of existence should fail loudly here,
            not silently skip the flow this module exists to reproduce.
    """
    for span in _backtick_commands(format_launch_instruction()):
        if span.startswith("python -m scripts.fleet.cli observe session-start"):
            return span
    raise AssertionError(
        "format_launch_instruction() names no backtick-quoted "
        "`observe session-start` command -- the launch instruction's "
        "session-start sentence may have been reshaped or removed"
    )


def _owner_claim_claude_command_template() -> str:
    """Return the launch instruction's literal claude-harness `owner claim` command.

    Raises:
        AssertionError: if no backtick-quoted `owner claim ... --harness
            claude` command is present -- in particular if a future edit
            drops `--harness claude` from the claim sentence, this raises
            (and every caller test goes red) instead of silently running
            some other, weaker command.
    """
    for span in _backtick_commands(format_launch_instruction()):
        if span.startswith("python -m scripts.fleet.cli owner claim") and span.endswith(
            "--harness claude"
        ):
            return span
    raise AssertionError(
        "format_launch_instruction() names no backtick-quoted claude-harness "
        "`owner claim ... --harness claude` command -- the claim sentence "
        "may have dropped `--harness claude` or been reshaped"
    )


def _owner_claim_other_harness_suffix_template() -> str:
    """Return the literal `--harness <h> --local-id <...>` suffix for a non-claude harness.

    Raises:
        AssertionError: if the instruction no longer names this suffix.
    """
    for span in _backtick_commands(format_launch_instruction()):
        if span.startswith("--harness <h> --local-id"):
            return span
    raise AssertionError(
        "format_launch_instruction() names no backtick-quoted non-claude "
        "`--harness <h> --local-id ...` suffix"
    )


_WORKSPACE_SENTINEL = "__FLEET_TEST_WORKSPACE__"


def _fill(
    template: str,
    *,
    workspace: Path,
    slug: str,
    handoff_id: str | None = None,
    harness: str | None = None,
    local_id: str | None = None,
) -> str:
    """Substitute the launch instruction's placeholders with real values, mechanically.

    Every argv this module runs traces back through this function to a
    literal span `format_launch_instruction()` actually emits -- nothing
    here is hand-written command shape.

    Args:
        template: a literal command/suffix returned by one of the
            `_..._template` helpers above.
        workspace: the fixture project's root, substituted for
            `<this project's root>`.
        slug: substituted for `<this project's slug>`.
        handoff_id: substituted for `<the id above>`, when present.
        harness: substituted for `<h>`, when present.
        local_id: substituted for `<one stable name you choose, reused for
            the matching stand-down>`, when present.

    Returns:
        str: `template` with every applicable placeholder replaced.
    """
    text = template
    # A space-free sentinel, mapped back per token by `_argv_from_command`,
    # so a workspace path containing a space is never re-split.
    text = text.replace("<this project's root>", _WORKSPACE_SENTINEL)
    text = text.replace("<this project's slug>", slug)
    if handoff_id is not None:
        text = text.replace("<the id above>", handoff_id)
    if harness is not None:
        text = text.replace("<h>", harness)
    if local_id is not None:
        text = text.replace(
            "<one stable name you choose, reused for the matching stand-down>", local_id
        )
    return text


def _argv_from_command(command: str, *, workspace: Path) -> list[str]:
    """Split a filled-in `python -m scripts.fleet.cli ...` literal into subprocess argv.

    `python` is dropped (the caller substitutes `sys.executable` instead, so
    the call resolves regardless of what `python` means on `PATH` -- the
    only departure from the literal text). `_fill` leaves the workspace as a
    space-free sentinel; the template is split on whitespace first and the
    sentinel token is then mapped to the real path, so a path containing a
    space stays one argv token.

    Args:
        command: a command returned by `_fill`.
        workspace: the real workspace path the sentinel stands for.

    Returns:
        list[str]: the argv after `python`.

    Raises:
        AssertionError: if `command` does not literally start with `python`
            (guards against running something other than the instruction's
            own text), or if a `<...>` placeholder was left unfilled.
    """
    tokens = [
        str(workspace) if token == _WORKSPACE_SENTINEL else token for token in command.split()
    ]
    assert tokens and tokens[0] == "python", (
        f"expected the literal command to start with 'python', got {tokens[:1]!r}"
    )
    unfilled = [token for token in tokens if "<" in token or _WORKSPACE_SENTINEL in token]
    assert not unfilled, f"unfilled placeholder(s) in the launch command: {unfilled!r}"
    return tokens[1:]


class TestArgvHelperSurvivesSpacesInPaths:
    """A workspace path containing a space (a Windows user profile such as
    `C:\\Users\\First Last\\...`, where pytest's tmp dir lives) must stay ONE
    argv token -- plain whitespace splitting of the filled command broke it."""

    def test_spaced_workspace_is_a_single_argv_token(self) -> None:
        workspace = Path("C:/Users/First Last/tmp/ws")
        command = _fill(
            _owner_claim_claude_command_template(), workspace=workspace, slug="demo"
        )
        argv = _argv_from_command(command, workspace=workspace)
        assert argv[argv.index("--workspace") + 1] == str(workspace)
        assert not any("<" in token for token in argv), argv


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def fixture_project(tmp_path: Path) -> tuple[Path, str, Path]:
    """A real git working tree with a resolvable `SUPERHUMAN.md` identity and
    fleet enabled -- built with real subprocess `git`, not faked, matching
    the probe script's own fixture shape.

    Returns:
        tuple[Path, str, Path]: `(workspace, slug, profile_path)`.
    """
    slug = "literal-launch-flow"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _run_git(workspace, "init", "-q", "-b", "trunk")
    _run_git(workspace, "config", "user.email", "test@example.invalid")
    _run_git(workspace, "config", "user.name", "Test")

    project_dir = workspace / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(
        f"**Slug:** {slug}\n**Project-id:** proj-literal-flow\n", encoding="utf-8"
    )
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(workspace, "add", ".")
    _run_git(workspace, "commit", "-q", "-m", "initial")

    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")

    return workspace, slug, profile


def _run_cli(
    *args: str, profile: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `<sys.executable> <args>` as a genuinely separate process.

    Args:
        args: the argv after the interpreter (e.g. `"-m",
            "scripts.fleet.cli", "observe", "session-start", ...`), as
            produced by `_argv_from_command` or hand-built for a
            verification-only call (e.g. `owner show`) that is not itself
            part of the literal launch instruction.
        profile: path to the `fleet.enabled: true` profile file.
        extra_env: additional environment variables for THIS call only
            (e.g. a synthetic `CLAUDE_CODE_SESSION_ID`) -- never leaked to
            other calls, so each subprocess genuinely only knows what a
            real launched process would know.

    Returns:
        subprocess.CompletedProcess[str]: the finished process.
    """
    env = dict(os.environ)
    # This test module itself runs inside a live harness session, which may
    # have a REAL CLAUDE_CODE_SESSION_ID in its own environment. Pop it
    # before layering in extra_env, so a fixture project's event log can
    # only ever pick up a synthetic id this module chose, never this
    # session's real one.
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env["SUPERHUMAN_PROFILE"] = str(profile)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, *args],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _fleet_log_path(workspace: Path, slug: str) -> Path:
    return workspace / "docs" / "superhuman" / slug / "fleet" / "events.jsonl"


def _events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestLiteralLaunchFlowClaudeHarness:
    """The literal session-start-then-claim flow for a Claude session:
    identity comes from `CLAUDE_CODE_SESSION_ID` in the subprocess
    environment, never a `--session-id` flag (matching the launch
    instruction's own claude-harness wording, O14-d)."""

    def test_session_start_then_claim_as_separate_processes_succeeds(
        self, fixture_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = fixture_project
        session_id = "claude-session-literal-1"
        handoff_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        session_start_cmd = _fill(
            _session_start_command_template(),
            workspace=workspace,
            slug=slug,
            handoff_id=handoff_id,
        )
        session_start = _run_cli(
            *_argv_from_command(session_start_cmd, workspace=workspace),
            profile=profile,
            extra_env={"CLAUDE_CODE_SESSION_ID": session_id},
        )
        assert session_start.returncode == 0, session_start.stderr

        claim_cmd = _fill(
            _owner_claim_claude_command_template(), workspace=workspace, slug=slug
        )
        claim = _run_cli(
            *_argv_from_command(claim_cmd, workspace=workspace),
            profile=profile,
            extra_env={"CLAUDE_CODE_SESSION_ID": session_id},
        )
        assert claim.returncode == 0, (
            f"literal claude session-start -> owner claim flow failed: "
            f"stdout={claim.stdout!r} stderr={claim.stderr!r}"
        )

        # O14-a: the claim register-if-absent path must have written the
        # claimant's own `session_registered` event, origination "manual".
        log_path = _fleet_log_path(workspace, slug)
        registrations = [
            e
            for e in _events(log_path)
            if e.get("type") == "session_registered"
            and str(e.get("node_id", "")).endswith(f"/{session_id}")
            and e.get("payload", {}).get("origination") == "manual"
        ]
        assert registrations, (
            "O14-a: expected a `session_registered` event with "
            f"origination=manual for the claimant ({session_id!r}) in "
            f"{log_path}, found none"
        )

        show = _run_cli(
            "-m",
            "scripts.fleet.cli",
            "owner",
            "show",
            "--workspace",
            str(workspace),
            "--slug",
            slug,
            "--json",
            profile=profile,
        )
        assert show.returncode == 0
        payload = json.loads(show.stdout)
        assert payload["owner"] is not None
        assert payload["owner"].endswith(f"/{session_id}")


class TestLiteralLaunchFlowPortableHarness:
    """The literal session-start-then-claim flow for a non-claude harness:
    identity comes from an explicit, STABLE `--local-id` reused across both
    commands (never a bare process id, O14-b/d)."""

    def test_session_start_then_claim_as_separate_processes_succeeds(
        self, fixture_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = fixture_project
        stable_local_id = "portable-session-literal-1"
        handoff_id = "11111111-2222-3333-4444-555555555555"

        session_start_cmd = _fill(
            _session_start_command_template(),
            workspace=workspace,
            slug=slug,
            handoff_id=handoff_id,
        )
        session_start = _run_cli(*_argv_from_command(session_start_cmd, workspace=workspace), profile=profile)
        assert session_start.returncode == 0, session_start.stderr

        claude_claim_cmd = _fill(
            _owner_claim_claude_command_template(), workspace=workspace, slug=slug
        )
        # The instruction's own prose: "...or add `--harness <h> --local-id
        # ...>` for any other harness" -- drop the claude-only tail and
        # append the literal non-claude suffix, exactly as described.
        claim_base, claude_tail = claude_claim_cmd.rsplit(" --harness claude", 1)
        assert claude_tail == "", "unexpected trailing text after '--harness claude'"
        harness_suffix = _fill(
            _owner_claim_other_harness_suffix_template(),
            workspace=workspace,
            slug=slug,
            harness="portable",
            local_id=stable_local_id,
        )
        claim_cmd = f"{claim_base} {harness_suffix}"

        claim = _run_cli(*_argv_from_command(claim_cmd, workspace=workspace), profile=profile)
        assert claim.returncode == 0, (
            f"literal portable session-start -> owner claim flow failed: "
            f"stdout={claim.stdout!r} stderr={claim.stderr!r}"
        )

        stand_down = _run_cli(
            "-m",
            "scripts.fleet.cli",
            "owner",
            "stand-down",
            "--workspace",
            str(workspace),
            "--slug",
            slug,
            "--harness",
            "portable",
            "--local-id",
            stable_local_id,
            profile=profile,
        )
        assert stand_down.returncode == 0, stand_down.stderr


class TestLiteralFlowWithoutSessionStartStillWorks:
    """O14-a's register-if-absent means the session-start step is no longer
    load-bearing for a claim to succeed -- a claim with no prior
    `observe session-start` call still lands (it just also registers)."""

    def test_claim_with_no_prior_session_start_still_succeeds(
        self, fixture_project: tuple[Path, str, Path]
    ) -> None:
        workspace, slug, profile = fixture_project

        claim_cmd = _fill(
            _owner_claim_claude_command_template(), workspace=workspace, slug=slug
        )
        claim = _run_cli(
            *_argv_from_command(claim_cmd, workspace=workspace),
            profile=profile,
            extra_env={"CLAUDE_CODE_SESSION_ID": "never-session-started"},
        )
        assert claim.returncode == 0, claim.stderr
