"""Tests for `templates/hooks/claude-code/{session-start,subagent-start}` —
the deterministic hook wrappers themselves (Chunks 6 and 7).

TDD scaffold only (Phase 2.1). Stubs with `pytest.mark.skip`; the Chunk 6/7
Developer implements each, TDD-first, per `TEST.md` TC-35..TC-53.

**FR-18 is the load-bearing requirement of this whole file for
`subagent-start`: exit code 2 on `SubagentStart` BLOCKS THE SUBAGENT.**
"Exits 0 under every injected fault" is therefore a correctness
requirement, not a quality goal — every fault-injection test below asserts
exit 0 as its primary assertion, not as an afterthought.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


# --- Chunk 6: SessionStart hook -----------------------------------------------------


class TestSessionStartHookValueDefinition:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 6, TC-35")
    def test_session_start_hook_produces_a_row_with_no_prose_instruction(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: build a resolvable workspace, invoke
        `templates/hooks/claude-code/session-start` directly (not through
        `cli.py`) with a synthetic stdin payload, and assert a manifest row
        exists afterward with NOTHING beyond the hook invocation having
        run. This is the literal acceptance demonstration named in
        PLAN.md's Chunk 6 — the value chunk."""


class TestSessionStartHookContent:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 6, TC-36")
    def test_no_replace_with_or_pwd_in_hook_templates(self) -> None:
        """Content test over every file under `templates/hooks/**`: zero
        occurrences of `REPLACE_WITH_` (FR-8) and zero occurrences of the
        literal string `$(pwd)` (FR-16 — the Phase 1.1 defect this chunk
        fixes)."""


class TestSessionStartHookUsesStdinCwd:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 6, TC-37")
    def test_hook_resolves_workspace_from_stdin_cwd_not_process_cwd(
        self, tmp_path: Path
    ) -> None:
        """Differential test: invoke the hook subprocess with its OWN OS
        working directory set to workspace A, but the stdin JSON payload's
        `cwd` field naming workspace B. Assert the resulting row lands
        under workspace B. This is the direct regression test for FR-16 —
        the subprocess's own working directory is undocumented and must
        never be trusted."""


class TestSessionStartHookSessionIdPassthrough:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 6, TC-38")
    def test_hook_passes_session_id_through_to_the_cli(self, tmp_path: Path) -> None:
        """FR-17: the payload's `session_id` reaches the registered
        fragment exactly, via `--session-id`, rather than being re-derived
        via the fuzzy `(cwd, branch)` anchors `observe launch` falls back
        to when no explicit id is available."""


class TestSessionStartHookIdempotency:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 6, TC-39")
    @pytest.mark.parametrize(
        "matcher", ["startup", "resume", "clear", "compact", "fork"]
    )
    def test_all_five_matchers_dedupe_to_one_row(
        self, matcher: str, tmp_path: Path
    ) -> None:
        """FR-19/NFR-6: invoked through the actual hook SCRIPT (not the
        library call — this is the ceiling-level counterpart to
        `test_observe_session.py`'s `TestIdempotencyAcrossFiveMatchers`),
        parametrized over each `SessionStart` matcher; one session
        produces exactly one row across repeated firings."""


class TestSessionStartHookLatency:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 6, TC-40")
    def test_hook_latency_within_agreed_budget(self, tmp_path: Path) -> None:
        """NFR-2: wall-clock measurement of one hook invocation against the
        budget Chunk 1 measures and G3 agrees
        (`fleet.observe_deadline_seconds`). Skip/xfail with a clear reason
        if Chunk 1's measured figure has not yet landed in DESIGN.md."""


class TestSessionStartHookWindowsShim:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 6, TC-41")
    def test_cmd_shim_present_and_delegates_to_sh_script(self) -> None:
        """`templates/hooks/claude-code/session-start.cmd` exists, matches
        the existing `hooks/session-start.cmd` precedent's shape, and
        (on this machine, per NFR-6) actually delegates to the
        extensionless/`.sh` script via Git Bash."""


# --- Chunk 7: SubagentStart hook — the fault-injection matrix -----------------------


class TestSubagentHookFaultInjectionMatrix:
    """FR-18's fault matrix. Every test's PRIMARY assertion is exit code 0
    — that is the correctness bar, because exit 2 on `SubagentStart` blocks
    the subagent outright.
    """

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-42, fault #1")
    def test_exits_0_on_corrupted_truncated_json_payload(
        self, tmp_path: Path
    ) -> None:
        """Simulation: pipe a syntactically-broken JSON string to stdin,
        e.g. `{"session_id": "abc", "cwd": "/x` (unterminated). Assert exit
        code 0 and no traceback on stdout."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-43, fault #2")
    def test_exits_0_on_non_json_stdin(self, tmp_path: Path) -> None:
        """Simulation: pipe arbitrary non-JSON bytes (including a raw NUL
        and a raw 0xFF byte) to stdin. Assert exit code 0."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-44, fault #3")
    def test_exits_0_on_empty_stdin(self, tmp_path: Path) -> None:
        """Simulation: close stdin immediately / pipe zero bytes. Assert
        exit code 0."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-45, fault #4")
    def test_exits_0_when_interpreter_missing(self, tmp_path: Path) -> None:
        """Simulation: invoke the hook with a `PATH` environment override
        (`subprocess.run(..., env={...})`) pointing at a directory
        containing no `python`/`python3` executable, simulating a
        moved/uninstalled interpreter. Assert exit code 0 — the shell
        wrapper's own `trap ... EXIT` must catch this, not just the Python
        layer."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-46, fault #5")
    def test_exits_0_on_unreadable_transcript_path(self, tmp_path: Path) -> None:
        """Simulation: payload's `transcript_path` names a file that does
        not exist (and, where the platform allows it, one with permissions
        revoked). The hook must not read it unconditionally without a
        guard. Assert exit code 0."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-47, fault #6")
    def test_exits_0_when_workspace_no_longer_exists(self, tmp_path: Path) -> None:
        """Simulation: create a directory, then `shutil.rmtree` it
        immediately before invoking the hook with a payload `cwd` naming
        the now-deleted path. Assert exit code 0."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-48, fault #7")
    def test_survives_a_hung_subprocess_under_the_harness_timeout_backstop(
        self, tmp_path: Path
    ) -> None:
        """Simulation: replace the invoked `python` with a stub script that
        sleeps indefinitely; invoke the hook under a TEST-LEVEL bounded
        wait shorter than the sleep, and assert the process is terminated
        cleanly (no orphan child) once the bounded wait's timeout fires.

        DOCUMENTED LIMITATION (state this in the implementation's
        docstring too): the hook's own `trap ... EXIT` cannot preempt a
        genuine hang — it only guarantees exit 0 on paths that DO return.
        The actual mitigation for a true hang is the harness's own
        per-hook `"timeout"` setting, written by the Chunk 8 installer as
        a backstop (NFR-2). This test proves the wrapper does not DEFEAT
        that backstop (e.g. by disabling signal delivery, backgrounding an
        untracked child, or swallowing `SIGTERM`) — it does not claim the
        hook self-terminates a hang.
        """

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-49, fault #8")
    def test_exits_0_on_simulated_disk_full(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulation: at the PYTHON layer (mirroring the existing
        `test_journal_write_failure_falls_back_to_exactly_one_stderr_line`
        idiom in `tests/fleet/test_observe.py`), monkeypatch the write call
        to raise `OSError(errno.ENOSPC, "No space left on device")`. A true
        OS-level full-disk simulation is out of scope for a portable
        suite; this is documented here as the practical equivalent, since
        `observe.py`'s broad catch treats any `OSError` identically
        regardless of `errno`. Assert exit code 0."""


class TestSubagentHookNeverPrintsATraceback:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-53")
    @pytest.mark.parametrize(
        "fault",
        [
            "corrupted_json",
            "non_json",
            "empty_stdin",
            "missing_interpreter",
            "unreadable_transcript",
            "deleted_workspace",
            "disk_full",
        ],
    )
    def test_stdout_never_carries_a_python_traceback(
        self, fault: str, tmp_path: Path
    ) -> None:
        """Across every fault above, stdout is either empty or the one
        sanctioned diagnostic line — never a Python traceback, which would
        itself be a symptom the trap failed to catch cleanly, independent
        of whatever the exit code turned out to be."""


class TestDecisionCGranularity:
    """D5: the SubagentStart hook applies Decision C's exact predicate —
    'a dispatch registers iff its prompt leads with a `roles/*.md`
    block' — read from `transcript_path`, per Chunk 1's probe finding.
    """

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-50")
    def test_subagent_hook_registers_a_role_dispatch(self, tmp_path: Path) -> None:
        """A synthetic payload whose `transcript_path` content leads with a
        `roles/*.md` block: the hook registers a dispatch row."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-51")
    def test_subagent_hook_does_not_register_a_research_fanout(
        self, tmp_path: Path
    ) -> None:
        """Same payload shape, but the dispatch prompt does NOT lead with a
        `roles/*.md` block (a general-purpose research fan-out). No row is
        written."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 7, TC-52")
    def test_decision_c_predicate_edge_case_roles_mentioned_but_not_leading(
        self, tmp_path: Path
    ) -> None:
        """A prompt that MENTIONS `roles/` mid-body (e.g. discussing the
        role system) but does not LEAD with it. Must not register — this
        is the precision case separating Decision C's exact predicate from
        a loose substring match."""
