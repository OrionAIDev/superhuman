"""Tests for `scripts/fleet/role_block.py` (chunk 7a, D7.2/D7.3/D7.7).

Unit-level coverage of the portable verbatim-role-block predicate
(TC-77..TC-81) and the decision-log primitive (feeding TC-89). The
harness-specific `PreToolUse` adapter that FEEDS this predicate — payload
parsing, scope, the locator, the deny JSON — is exercised separately, at
the subprocess level, in `tests/fleet/test_role_gate_hook.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.fleet.role_block import (
    NON_ROLE_LINE,
    Verdict,
    check_role_block,
    read_role_gate_log,
    record_role_gate_decision,
    role_gate_log_path,
)

_DEVELOPER_ROLE_CONTENT = (
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


@pytest.fixture
def roles_dir(tmp_path: Path) -> Path:
    """A synthetic `roles/` directory carrying two well-formed role files."""
    directory = tmp_path / "roles"
    directory.mkdir()
    (directory / "developer.md").write_text(_DEVELOPER_ROLE_CONTENT, encoding="utf-8")
    (directory / "pm.md").write_text("---\nname: pm\n---\n\n# PM role\n", encoding="utf-8")
    return directory


class TestCheckRoleBlockRole:
    """TC-77: the verbatim role file yields ROLE; a BOM, CRLF, and leading
    whitespace still yield ROLE."""

    def test_verbatim_role_file_yields_role(self, roles_dir: Path) -> None:
        result = check_role_block(_DEVELOPER_ROLE_CONTENT, roles_dir)
        assert result.verdict == Verdict.ROLE
        assert result.role == "developer"
        assert result.role_file == roles_dir / "developer.md"

    def test_verbatim_role_file_plus_trailing_task_brief_yields_role(
        self, roles_dir: Path
    ) -> None:
        """A role dispatch is the role file's content FOLLOWED by more text
        (declared references / conventions / task brief) — the role file's
        content need not be the entire prompt, just its leading block."""
        prompt = _DEVELOPER_ROLE_CONTENT + "\n## Task brief\n\nImplement chunk 7a.\n"
        result = check_role_block(prompt, roles_dir)
        assert result.verdict == Verdict.ROLE

    def test_bom_and_leading_whitespace_still_role(self, roles_dir: Path) -> None:
        prompt = "﻿  \n\n" + _DEVELOPER_ROLE_CONTENT
        assert check_role_block(prompt, roles_dir).verdict == Verdict.ROLE

    def test_crlf_line_endings_still_role(self, roles_dir: Path) -> None:
        prompt = _DEVELOPER_ROLE_CONTENT.replace("\n", "\r\n")
        assert check_role_block(prompt, roles_dir).verdict == Verdict.ROLE

    def test_role_file_trailing_whitespace_is_stripped_before_comparison(
        self, roles_dir: Path
    ) -> None:
        """The role FILE's trailing whitespace is stripped (D7.3) — a
        role file ending in a blank line must still match a prompt that
        pastes it without that trailing blank line."""
        (roles_dir / "developer.md").write_text(
            _DEVELOPER_ROLE_CONTENT + "\n\n  \n", encoding="utf-8"
        )
        assert check_role_block(_DEVELOPER_ROLE_CONTENT, roles_dir).verdict == Verdict.ROLE


class TestCheckRoleBlockMismatch:
    """TC-78: frontmatter plus an edited body yields MISMATCH with the
    first differing line, for each measured shape (delta-report-002 /
    D7 fact 2)."""

    def test_dropped_declared_reference_yields_mismatch(self, roles_dir: Path) -> None:
        edited = (
            "---\n"
            "name: developer\n"
            "tier: standard\n"
            "---\n"
            "\n"
            "# Developer role\n"
            "\n"
            "You are the Developer for this superhuman project...\n"
        )
        result = check_role_block(edited, roles_dir)
        assert result.verdict == Verdict.MISMATCH
        assert result.role == "developer"
        assert result.prompt_mismatch_line is not None
        assert result.file_mismatch_line is not None
        assert result.prompt_mismatch_line != result.file_mismatch_line

    def test_edited_tier_line_yields_mismatch(self, roles_dir: Path) -> None:
        edited = _DEVELOPER_ROLE_CONTENT.replace("tier: standard", "tier: cheap")
        result = check_role_block(edited, roles_dir)
        assert result.verdict == Verdict.MISMATCH
        assert result.prompt_mismatch_line == "tier: cheap"
        assert result.file_mismatch_line == "tier: standard"

    def test_paraphrase_yields_mismatch(self, roles_dir: Path) -> None:
        edited = _DEVELOPER_ROLE_CONTENT.replace(
            "You are the Developer for this superhuman project...",
            "You're this project's Developer for the current chunk...",
        )
        result = check_role_block(edited, roles_dir)
        assert result.verdict == Verdict.MISMATCH
        assert result.role == "developer"


class TestCheckRoleBlockNonRole:
    """TC-79: `superhuman-dispatch: non-role` as the first line, optionally
    followed by text, yields NON_ROLE. Misspelt, or not on the first line,
    yields UNMARKED."""

    def test_non_role_line_alone_yields_non_role(self, roles_dir: Path) -> None:
        assert check_role_block(NON_ROLE_LINE, roles_dir).verdict == Verdict.NON_ROLE

    def test_non_role_line_followed_by_a_task_brief_yields_non_role(
        self, roles_dir: Path
    ) -> None:
        prompt = NON_ROLE_LINE + "\n\nInvestigate how the fleet manifest resolves slugs.\n"
        assert check_role_block(prompt, roles_dir).verdict == Verdict.NON_ROLE

    def test_bom_and_leading_whitespace_still_non_role(self, roles_dir: Path) -> None:
        prompt = "﻿  \n" + NON_ROLE_LINE + "\n"
        assert check_role_block(prompt, roles_dir).verdict == Verdict.NON_ROLE

    def test_misspelt_non_role_line_yields_unmarked(self, roles_dir: Path) -> None:
        prompt = "superhuman-dispatch: nonrole\n\nDo some research.\n"
        assert check_role_block(prompt, roles_dir).verdict == Verdict.UNMARKED

    def test_non_role_line_not_on_first_line_yields_unmarked(self, roles_dir: Path) -> None:
        prompt = "Some preamble.\n" + NON_ROLE_LINE + "\n"
        assert check_role_block(prompt, roles_dir).verdict == Verdict.UNMARKED


class TestCheckRoleBlockUnmarked:
    """TC-80: a prose-brief opener, or a prompt that only mentions
    `roles/`, yields UNMARKED (the TC-52 analogue)."""

    def test_prose_brief_yields_unmarked(self, roles_dir: Path) -> None:
        prompt = "You are the Developer for **chunk 7a**. Implement the role gate...\n"
        assert check_role_block(prompt, roles_dir).verdict == Verdict.UNMARKED

    def test_roles_mentioned_midbody_not_leading_yields_unmarked(self, roles_dir: Path) -> None:
        prompt = (
            "You are a general-purpose research agent. Our role prompts live under "
            "roles/*.md, e.g. roles/developer.md.\n"
        )
        assert check_role_block(prompt, roles_dir).verdict == Verdict.UNMARKED

    def test_empty_prompt_yields_unmarked(self, roles_dir: Path) -> None:
        assert check_role_block("", roles_dir).verdict == Verdict.UNMARKED


class TestCheckRoleBlockRoleSetFromDisk:
    """TC-81: the role set is read from the directory. A temporary
    `roles/foo.md` is recognised; an unknown `name:` yields UNMARKED."""

    def test_temporary_custom_role_is_recognised(self, roles_dir: Path) -> None:
        custom_content = "---\nname: totally-custom-role\n---\n\nBody.\n"
        (roles_dir / "totally-custom-role.md").write_text(custom_content, encoding="utf-8")
        result = check_role_block(custom_content, roles_dir)
        assert result.verdict == Verdict.ROLE
        assert result.role == "totally-custom-role"

    def test_unknown_role_name_yields_unmarked(self, roles_dir: Path) -> None:
        prompt = "---\nname: not-a-real-role\n---\n\nBody.\n"
        assert check_role_block(prompt, roles_dir).verdict == Verdict.UNMARKED

    def test_a_role_file_removed_from_disk_stops_matching(self, roles_dir: Path) -> None:
        role_file = roles_dir / "developer.md"
        assert check_role_block(_DEVELOPER_ROLE_CONTENT, roles_dir).verdict == Verdict.ROLE
        role_file.unlink()
        assert check_role_block(_DEVELOPER_ROLE_CONTENT, roles_dir).verdict == Verdict.UNMARKED


class TestCheckRoleBlockFaultPosture:
    """D7.5: an unreadable/empty `roles/` directory, or an unreadable role
    file, is a FAULT — never a verdict a caller could deny on. Also feeds
    TC-85's fault matrix (the adapter-level test asserts the WHOLE hook's
    behavior; this asserts the underlying predicate's contribution to it)."""

    def test_missing_roles_dir_is_fault(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        result = check_role_block(_DEVELOPER_ROLE_CONTENT, missing)
        assert result.verdict == Verdict.FAULT

    def test_empty_roles_dir_is_fault(self, tmp_path: Path) -> None:
        empty = tmp_path / "roles"
        empty.mkdir()
        result = check_role_block(_DEVELOPER_ROLE_CONTENT, empty)
        assert result.verdict == Verdict.FAULT

    def test_roles_dir_raising_oserror_on_glob_is_fault(
        self, roles_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(self: Path, pattern: str):  # noqa: ANN001 - matches Path.glob's signature
            raise OSError("simulated permission error enumerating roles_dir")

        monkeypatch.setattr(Path, "glob", _raise)
        result = check_role_block(_DEVELOPER_ROLE_CONTENT, roles_dir)
        assert result.verdict == Verdict.FAULT

    def test_unreadable_role_file_is_fault_not_mismatch(
        self, roles_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ONE role file this prompt names is unreadable, even though
        `roles_dir` itself is otherwise healthy (has `pm.md`) — must still
        be FAULT, never a confident MISMATCH/UNMARKED deny with no
        evidence behind it (D7.5's precise concern: a false denial caused
        by infrastructure, not by the prompt's actual content)."""
        real_read_text = Path.read_text

        def _raise_for_developer(self: Path, *args: object, **kwargs: object) -> str:
            if self.name == "developer.md":
                raise OSError("simulated permission error reading role file")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _raise_for_developer)
        result = check_role_block(_DEVELOPER_ROLE_CONTENT, roles_dir)
        assert result.verdict == Verdict.FAULT
        assert result.role == "developer"

    def test_non_string_prompt_is_fault(self, roles_dir: Path) -> None:
        assert check_role_block(None, roles_dir).verdict == Verdict.FAULT  # type: ignore[arg-type]

    def test_never_raises_on_a_hostile_roles_dir_type(self, tmp_path: Path) -> None:
        """Belt-and-suspenders: even an entirely unexpected internal
        failure degrades to FAULT rather than propagating (NFR-9's "never
        raises" contract, exercised via the module's own broad catch)."""
        result = check_role_block(_DEVELOPER_ROLE_CONTENT, object())  # type: ignore[arg-type]
        assert result.verdict == Verdict.FAULT


class TestRecordRoleGateDecision:
    """Feeds TC-89: the six-field decision log, bounded tail, silent no-op
    when fleet is disabled/unconfigured or the write itself fails, and a
    failed write never raising past the caller."""

    def _write_profile(self, profile_path: Path) -> None:
        profile_path.write_text("fleet:\n  enabled: true\n", encoding="utf-8")

    def test_records_the_six_fields_and_no_prompt_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        profile = tmp_path / "profile.yaml"
        self._write_profile(profile)
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        record_role_gate_decision(
            workspace,
            "demo-slug",
            session_id="sess-1",
            verdict=Verdict.MISMATCH,
            role="developer",
            subagent_type="developer",
            mismatch_line="tier: cheap",
        )

        fleet_dir = workspace / "docs" / "superhuman" / "demo-slug" / "fleet"
        rows = read_role_gate_log(fleet_dir)
        assert len(rows) == 1
        row = rows[0]
        assert set(row) == {"ts", "session_id", "verdict", "role", "subagent_type", "mismatch_line"}
        assert row["verdict"] == "MISMATCH"
        assert row["role"] == "developer"
        assert row["subagent_type"] == "developer"
        assert row["mismatch_line"] == "tier: cheap"
        assert row["session_id"] == "sess-1"

    def test_disabled_workspace_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        # An explicit profile with NO `fleet:` block, not merely an absent
        # env var -- this machine's own `~/.superhuman/profile.yaml` sets
        # `fleet.enabled: true` machine-wide, so relying on env-var absence
        # alone would silently pick that up instead of testing "disabled".
        disabled_profile = tmp_path / "disabled-profile.yaml"
        disabled_profile.write_text("version: 1\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(disabled_profile))

        record_role_gate_decision(
            workspace,
            "demo-slug",
            session_id="sess-1",
            verdict=Verdict.NON_ROLE,
            role=None,
            subagent_type=None,
            mismatch_line=None,
        )

        fleet_dir = workspace / "docs" / "superhuman" / "demo-slug" / "fleet"
        assert not role_gate_log_path(fleet_dir).exists()

    def test_bounded_tail_rotation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        profile = tmp_path / "profile.yaml"
        self._write_profile(profile)
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        fleet_dir = workspace / "docs" / "superhuman" / "demo-slug" / "fleet"
        fleet_dir.mkdir(parents=True)
        path = role_gate_log_path(fleet_dir)
        # Pre-seed one line over the 500-line bound so the very next write
        # must roll the tail.
        path.write_text(
            "\n".join(json.dumps({"ts": f"seed-{i}"}) for i in range(500)) + "\n",
            encoding="utf-8",
        )

        record_role_gate_decision(
            workspace,
            "demo-slug",
            session_id="sess-1",
            verdict=Verdict.UNMARKED,
            role=None,
            subagent_type="general-purpose",
            mismatch_line=None,
        )

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 500
        assert json.loads(lines[-1])["verdict"] == "UNMARKED"
        assert json.loads(lines[0])["ts"] == "seed-1"  # the oldest seed line was dropped

    def test_write_failure_is_silently_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        profile = tmp_path / "profile.yaml"
        self._write_profile(profile)
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        def _raise(*args: object, **kwargs: object) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr(Path, "mkdir", _raise)

        # Must not raise past this call (TC-89: "a failed write leaves the
        # verdict unchanged" -- there is nothing left for the caller to do
        # but proceed exactly as if this call had never been made).
        record_role_gate_decision(
            workspace,
            "demo-slug",
            session_id="sess-1",
            verdict=Verdict.NON_ROLE,
            role=None,
            subagent_type=None,
            mismatch_line=None,
        )


class TestReadRoleGateLog:
    def test_missing_log_returns_empty_list(self, tmp_path: Path) -> None:
        assert read_role_gate_log(tmp_path / "fleet") == []

    def test_malformed_line_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        fleet_dir = tmp_path / "fleet"
        fleet_dir.mkdir()
        path = role_gate_log_path(fleet_dir)
        path.write_text(
            "not json\n" + json.dumps({"ts": "x", "verdict": "NON_ROLE"}) + "\n",
            encoding="utf-8",
        )
        rows = read_role_gate_log(fleet_dir)
        assert len(rows) == 1
        assert rows[0]["verdict"] == "NON_ROLE"

    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        fleet_dir = tmp_path / "fleet"
        fleet_dir.mkdir()
        path = role_gate_log_path(fleet_dir)
        path.write_text(
            "\n   \n" + json.dumps({"ts": "x", "verdict": "NON_ROLE"}) + "\n\n",
            encoding="utf-8",
        )
        rows = read_role_gate_log(fleet_dir)
        assert len(rows) == 1

    def test_unreadable_log_file_returns_empty_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fleet_dir = tmp_path / "fleet"
        fleet_dir.mkdir()
        path = role_gate_log_path(fleet_dir)
        path.write_text(json.dumps({"ts": "x", "verdict": "NON_ROLE"}) + "\n", encoding="utf-8")

        real_read_text = Path.read_text

        def _raise_for_log(self: Path, *args: object, **kwargs: object) -> str:
            if self == path:
                raise OSError("simulated permission error")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _raise_for_log)
        assert read_role_gate_log(fleet_dir) == []


class TestFrontmatterRoleNameInternals:
    """White-box coverage of `_frontmatter_role_name`'s own early returns —
    unreachable from `check_role_block` (which only calls it once
    `leads_with_role_block` has already proven a frontmatter/name match
    exists), but exercised directly here rather than left unreachable."""

    def test_no_frontmatter_returns_none(self) -> None:
        from scripts.fleet.role_block import _frontmatter_role_name

        assert _frontmatter_role_name("You are the Developer...\n") is None

    def test_frontmatter_with_no_name_line_returns_none(self) -> None:
        from scripts.fleet.role_block import _frontmatter_role_name

        assert _frontmatter_role_name("---\ntier: standard\n---\n") is None


class TestRoleBlockCheckCli:
    """TC-86: `role-block check` exits 0 on ROLE/NON_ROLE and 1 otherwise,
    printing one verdict line."""

    def _run(self, argv: list[str]) -> tuple[int, str]:
        import io

        from scripts.fleet.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(argv)
        buf = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(buf):
            exit_code = args.func(args)
        return exit_code, buf.getvalue()

    def test_role_prompt_exits_0(self, roles_dir: Path, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(_DEVELOPER_ROLE_CONTENT, encoding="utf-8")
        exit_code, out = self._run(
            ["role-block", "check", "--prompt-file", str(prompt_file), "--roles-dir", str(roles_dir)]
        )
        assert exit_code == 0
        assert out.strip() == "ROLE"

    def test_non_role_prompt_exits_0(self, roles_dir: Path, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(NON_ROLE_LINE, encoding="utf-8")
        exit_code, out = self._run(
            ["role-block", "check", "--prompt-file", str(prompt_file), "--roles-dir", str(roles_dir)]
        )
        assert exit_code == 0
        assert out.strip() == "NON_ROLE"

    def test_prose_brief_exits_1(self, roles_dir: Path, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("You are the Developer for chunk 7a...\n", encoding="utf-8")
        exit_code, out = self._run(
            ["role-block", "check", "--prompt-file", str(prompt_file), "--roles-dir", str(roles_dir)]
        )
        assert exit_code == 1
        assert out.strip() == "UNMARKED"

    def test_mismatch_exits_1(self, roles_dir: Path, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(
            _DEVELOPER_ROLE_CONTENT.replace("tier: standard", "tier: cheap"), encoding="utf-8"
        )
        exit_code, out = self._run(
            ["role-block", "check", "--prompt-file", str(prompt_file), "--roles-dir", str(roles_dir)]
        )
        assert exit_code == 1
        assert out.strip() == "MISMATCH"

    def test_missing_roles_dir_exits_1_fault(self, tmp_path: Path) -> None:
        """A FAULT is "not proven compliant" for this fail-closed verb — it
        exits 1 exactly like a genuine mismatch, unlike the hook's own
        fail-soft posture."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(_DEVELOPER_ROLE_CONTENT, encoding="utf-8")
        missing_roles_dir = tmp_path / "does-not-exist"
        exit_code, out = self._run(
            [
                "role-block",
                "check",
                "--prompt-file",
                str(prompt_file),
                "--roles-dir",
                str(missing_roles_dir),
            ]
        )
        assert exit_code == 1
        assert out.strip() == "FAULT"

    def test_stdin_dash_reads_from_stdin(
        self, roles_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io

        # The CLI reads `sys.stdin.buffer` (raw bytes, then decodes as
        # UTF-8 itself — see the chunk 7a-fix module docstring); a plain
        # `io.StringIO` has no `.buffer`, so the fake stdin needs one.
        fake_stdin = io.StringIO(NON_ROLE_LINE)
        fake_stdin.buffer = io.BytesIO(NON_ROLE_LINE.encode("utf-8"))
        monkeypatch.setattr("sys.stdin", fake_stdin)
        exit_code, out = self._run(
            ["role-block", "check", "--prompt-file", "-", "--roles-dir", str(roles_dir)]
        )
        assert exit_code == 0
        assert out.strip() == "NON_ROLE"


class TestRoleBlockCheckCliUTF8StdinDecoding:
    """Regression for the defect fixed in this chunk (chunk 7a fix):
    `_cmd_role_block_check`'s `--prompt-file -` branch used to read stdin
    with `sys.stdin.read()`, which decodes using the process's
    locale-preferred encoding — cp1252 on this Windows runner, not UTF-8,
    even though a verbatim role dispatch piped in from a real harness
    arrives as UTF-8 bytes. `TestRoleBlockCheckCli.test_stdin_dash_reads_
    from_stdin` above tests the in-process, monkeypatched-`sys.stdin`
    path — it cannot exercise the actual OS-level decode this fix changes,
    because a real interpreter's `sys.stdin.encoding` is only meaningful
    for a REAL pipe. These tests run the CLI as a genuine subprocess,
    feeding RAW UTF-8 BYTES: no `text=True`, no `encoding=`, and
    `PYTHONIOENCODING`/`PYTHONUTF8` stripped from the child environment.
    """

    def _run_cli_raw_bytes(
        self, *, skill_root: Path, argv: list[str], stdin_bytes: bytes
    ) -> "subprocess.CompletedProcess[bytes]":
        import os
        import subprocess
        import sys

        env = os.environ.copy()
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        return subprocess.run(
            [sys.executable, "-m", "scripts.fleet.cli", *argv],
            input=stdin_bytes,
            cwd=str(skill_root),
            env=env,
            capture_output=True,
            timeout=15,
        )

    def test_verbatim_utf8_role_prompt_exits_0(
        self, roles_dir: Path, skill_root: Path
    ) -> None:
        # A synthetic role file carrying an em dash — the shipped
        # `_DEVELOPER_ROLE_CONTENT` fixture has none, so this test builds
        # its own to exercise the actual defect (a byte cp1252 cannot
        # round-trip).
        content = _DEVELOPER_ROLE_CONTENT.rstrip("\n") + " — end of role.\n"
        (roles_dir / "developer.md").write_text(content, encoding="utf-8")
        result = self._run_cli_raw_bytes(
            skill_root=skill_root,
            argv=["role-block", "check", "--prompt-file", "-", "--roles-dir", str(roles_dir)],
            stdin_bytes=content.encode("utf-8"),
        )
        assert result.returncode == 0, (
            f"exited {result.returncode}\nSTDOUT:\n{result.stdout!r}\nSTDERR:\n{result.stderr!r}"
        )
        assert result.stdout.strip() == b"ROLE"

    def test_invalid_utf8_stdin_exits_1_with_fault_verdict(
        self, roles_dir: Path, skill_root: Path
    ) -> None:
        """Bytes that are not valid UTF-8 at all must exit 1 with exactly
        one verdict line (`FAULT`) — this is a fail-CLOSED assertion tool
        (unlike the hook's fail-soft posture): a fault cannot certify
        compliance."""
        result = self._run_cli_raw_bytes(
            skill_root=skill_root,
            argv=["role-block", "check", "--prompt-file", "-", "--roles-dir", str(roles_dir)],
            stdin_bytes=b"\xff\xfe not valid utf-8",
        )
        assert result.returncode == 1, (
            f"exited {result.returncode}\nSTDOUT:\n{result.stdout!r}\nSTDERR:\n{result.stderr!r}"
        )
        assert result.stdout.strip() == b"FAULT"


class TestD4NoHarnessVocabularyInScripts:
    """TC-87: `scripts/` contains no `hookSpecificOutput`, `permissionDecision`
    or `tool_input`. The adapter and the CLI verb route through the SAME
    `check_role_block` function."""

    def test_scripts_fleet_carries_no_harness_vocabulary(self) -> None:
        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        forbidden = ("hookSpecificOutput", "permissionDecision", "tool_input")
        offenders: list[str] = []
        for path in scripts_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path}: {token}")
        assert not offenders, f"harness vocabulary leaked into scripts/: {offenders}"

    def test_adapter_and_cli_verb_call_the_same_function(self) -> None:
        skill_root = Path(__file__).resolve().parents[2]
        adapter_text = (
            skill_root / "templates" / "hooks" / "claude-code" / "pre_tool_use_role_gate.py"
        ).read_text(encoding="utf-8")
        cli_text = (skill_root / "scripts" / "fleet" / "cli.py").read_text(encoding="utf-8")
        assert "check_role_block(" in adapter_text
        assert "check_role_block(" in cli_text


class TestDoctorRoleGateHealth:
    """TC-90: `fleet doctor` counts correctly, and with an absent or empty
    log reports UNKNOWN, never OK or zero."""

    def _write_profile(self, profile_path: Path) -> None:
        profile_path.write_text("fleet:\n  enabled: true\n", encoding="utf-8")

    def test_absent_log_is_unknown(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.fleet.doctor import role_gate_health

        workspace = tmp_path / "ws"
        workspace.mkdir()
        profile = tmp_path / "profile.yaml"
        self._write_profile(profile)
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        health = role_gate_health(workspace, "demo-slug")
        assert health.state == "unknown"
        assert health.role_rows is None
        assert health.non_role_count is None
        assert health.denial_count is None

    def test_empty_log_is_unknown(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.fleet.doctor import role_gate_health

        workspace = tmp_path / "ws"
        workspace.mkdir()
        profile = tmp_path / "profile.yaml"
        self._write_profile(profile)
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        fleet_dir = workspace / "docs" / "superhuman" / "demo-slug" / "fleet"
        fleet_dir.mkdir(parents=True)
        role_gate_log_path(fleet_dir).write_text("", encoding="utf-8")

        health = role_gate_health(workspace, "demo-slug")
        assert health.state == "unknown"

    def test_populated_log_counts_correctly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.doctor import role_gate_health

        workspace = tmp_path / "ws"
        workspace.mkdir()
        profile = tmp_path / "profile.yaml"
        self._write_profile(profile)
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        record_role_gate_decision(
            workspace,
            "demo-slug",
            session_id="s1",
            verdict=Verdict.NON_ROLE,
            role=None,
            subagent_type="general-purpose",
            mismatch_line=None,
        )
        record_role_gate_decision(
            workspace,
            "demo-slug",
            session_id="s2",
            verdict=Verdict.MISMATCH,
            role="developer",
            subagent_type="developer",
            mismatch_line="tier: cheap",
        )
        record_role_gate_decision(
            workspace,
            "demo-slug",
            session_id="s3",
            verdict=Verdict.UNMARKED,
            role=None,
            subagent_type="developer",
            mismatch_line=None,
        )

        fleet_dir = workspace / "docs" / "superhuman" / "demo-slug" / "fleet"
        (fleet_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": "e1",
                    "idempotency_key": "register:node-1",
                    "ts": "2026-09-13T00:00:00.000000Z",
                    "type": "session_registered",
                    "project_id": "proj-1",
                    "node_id": "node-1",
                    "writer_role": "pm",
                    "payload": {
                        "harness": "subagent",
                        "workspace": str(workspace),
                        "local_id": "dispatch-1",
                        "branch": "",
                        "origination": "spawned",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        health = role_gate_health(workspace, "demo-slug")
        assert health.state == "ok"
        assert health.role_rows == 1
        assert health.non_role_count == 1
        assert health.denial_count == 2


class TestDoctorCliRoleGateSection:
    """`fleet doctor`'s CLI output (`_cmd_doctor`) prints the role-gate
    section for each `"ok"` record — UNKNOWN when absent/empty, the counted
    summary otherwise."""

    def _init_repo(self, repo: Path) -> None:
        import subprocess

        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)

    def _write_project(self, repo: Path, slug: str) -> None:
        project_dir = repo / "docs" / "superhuman" / slug
        project_dir.mkdir(parents=True)
        (project_dir / "SUPERHUMAN.md").write_text(
            f"**Slug:** {slug}\n**Project-id:** fleet-{slug}\n", encoding="utf-8"
        )

    def test_unknown_role_gate_printed_for_ok_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from scripts.fleet.cli import build_parser

        repo = tmp_path / "repo"
        self._init_repo(repo)
        slug = "doctor-demo"
        self._write_project(repo, slug)
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        parser = build_parser()
        args = parser.parse_args(["doctor", "--scan", str(repo)])
        exit_code = args.func(args)
        assert exit_code == 0

        out = capsys.readouterr().out
        assert f"{repo}  {slug}  ok" in out
        assert "role gate: UNKNOWN" in out

    def test_populated_role_gate_printed_for_ok_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from scripts.fleet.cli import build_parser

        repo = tmp_path / "repo"
        self._init_repo(repo)
        slug = "doctor-demo"
        self._write_project(repo, slug)
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        record_role_gate_decision(
            repo,
            slug,
            session_id="s1",
            verdict=Verdict.NON_ROLE,
            role=None,
            subagent_type="general-purpose",
            mismatch_line=None,
        )

        parser = build_parser()
        args = parser.parse_args(["doctor", "--scan", str(repo)])
        exit_code = args.func(args)
        assert exit_code == 0

        out = capsys.readouterr().out
        assert "role gate: role_rows=0 non_role=1 denials=0" in out
