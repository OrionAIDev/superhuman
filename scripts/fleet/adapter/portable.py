"""``PortableAdapter`` — git + filesystem + env only (NFR-2/NFR-3).

This is both the graceful-degradation path (a Claude harness with
``session-relay`` unavailable still has *this* to fall back to) and the
conformance path (Decision D binds the conformance suite to this adapter,
since it is the one implementation with zero Claude-specific dependencies to
even accidentally reach for). It needs nothing beyond `git` on `PATH` — no
``session-relay``, no native session tools, no network.

Because it has no live-session-enumeration mechanism of its own,
`enumerate_sessions()` can only ever report the one session it *is*
(`current_session()`) — there is no portable way to discover sibling
processes, and inventing one would mean guessing (DP#5 disallows exactly
that for anything feeding a persisted record).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..core.nodes import make_node_id
from .base import (
    GitFacts,
    SessionAdapter,
    SessionInfo,
    format_handoff_line,
    format_launch_instruction,
    workspace_component,
)

#: Local default-branch names tried when no remote-tracking ref exists —
#: mirrors `session-relay`'s own `session_scan.default_branch` fallback list,
#: reimplemented here (not imported) so this adapter has zero dependency on
#: `session-relay` even being installed.
_FALLBACK_DEFAULT_BRANCHES = ("main", "master")

#: Bounded so a hung/misbehaving `git` process can never wedge registration.
_GIT_TIMEOUT_SECONDS = 30


def run_git(cwd: Path, args: list[str], *, timeout: float = _GIT_TIMEOUT_SECONDS) -> str | None:
    """Run one git plumbing command, returning stripped stdout or None on failure.

    Args:
        cwd: directory to run the command in.
        args: git arguments, excluding the `git` executable itself.
        timeout: seconds to allow the subprocess before killing it. Additive
            passthrough (fleet-wiring Chunk 1, W-NFR-7) — defaults to
            `_GIT_TIMEOUT_SECONDS` (30.0s) unchanged for every existing
            caller; only `observe.py`'s own bounded calls pass a smaller
            value explicitly.

    Returns:
        str | None: stripped stdout on success; None if git exited non-zero,
        timed out, or is unavailable at all (no exception is raised — a
        missing/failing git degrades this adapter's facts, it never crashes
        registration, per NFR-3).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def collect_git_facts(cwd: Path, *, git_timeout: float = _GIT_TIMEOUT_SECONDS) -> GitFacts:
    """Gather real git plumbing facts for `cwd`.

    Args:
        cwd: the working tree to inspect.
        git_timeout: seconds allowed per subprocess call (additive
            passthrough, fleet-wiring Chunk 1 — defaults to
            `_GIT_TIMEOUT_SECONDS` (30.0s) unchanged for every existing
            caller). Up to 7 subprocess calls may be made in the worst
            case, so the observation façade passes a much smaller value
            (0.25s) to stay inside its own wall-clock budget (W-NFR-7).

    Returns:
        GitFacts: `is_repo=False` if `cwd` does not exist or is not inside a
        git working tree, rather than raising.
    """
    if not cwd.is_dir():
        return GitFacts(is_repo=False)

    toplevel = run_git(cwd, ["rev-parse", "--show-toplevel"], timeout=git_timeout)
    if toplevel is None:
        return GitFacts(is_repo=False)

    branch = run_git(cwd, ["branch", "--show-current"], timeout=git_timeout) or None

    status = run_git(cwd, ["status", "--porcelain"], timeout=git_timeout)
    dirty = len([line for line in status.splitlines() if line.strip()]) if status else 0

    merge_base = None
    default = run_git(
        cwd, ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], timeout=git_timeout
    )
    if default is None:
        for name in _FALLBACK_DEFAULT_BRANCHES:
            if name != branch and run_git(
                cwd, ["rev-parse", "--verify", "--quiet", name], timeout=git_timeout
            ):
                default = name
                break
    if default and branch and default != branch:
        merge_base = run_git(cwd, ["merge-base", default, branch], timeout=git_timeout)

    return GitFacts(
        is_repo=True,
        branch=branch,
        toplevel=toplevel,
        merge_base=merge_base,
        dirty_files=dirty,
    )


class PortableAdapter(SessionAdapter):
    """git+fs+env-only `SessionAdapter` — the degradation and conformance path.

    Attributes:
        workspace: the working tree this adapter reports on.
        slug: the owning superhuman project's slug (used to namespace node ids).
    """

    def __init__(
        self,
        workspace: Path | str,
        slug: str,
        *,
        local_id: str | None = None,
        git_timeout: float | None = None,
    ) -> None:
        """Initialize a PortableAdapter.

        Args:
            workspace: the working tree this adapter reports on.
            slug: the owning superhuman project's slug.
            local_id: override for this process's local session id. Defaults
                to `str(os.getpid())` — the only portable, non-fabricated
                identity a bare Python process has for itself with no
                harness support.
            git_timeout: seconds allowed per git subprocess call in
                `git_facts()` (additive passthrough, fleet-wiring Chunk 1).
                `None` (the default, unchanged for every existing caller)
                uses `collect_git_facts`'s own default (30.0s).
        """
        self.workspace = Path(workspace)
        self.slug = slug
        self._local_id = local_id if local_id is not None else str(os.getpid())
        self._git_timeout = git_timeout

    def current_session(self) -> SessionInfo:
        """Return this process's own identity, enriched with real git facts.

        Returns:
            SessionInfo: `harness="portable"`, `origination="manual"` (the
            portable path has no origination signal beyond "this process
            registered itself").
        """
        facts = self.git_facts()
        node_id = make_node_id(
            "portable", workspace_component(self.workspace), self.slug, self._local_id
        )
        return SessionInfo(
            node_id=node_id,
            harness="portable",
            workspace=str(self.workspace),
            local_id=self._local_id,
            branch=facts.branch,
            origination="manual",
            raw={},
        )

    def enumerate_sessions(self) -> list[SessionInfo]:
        """Return this process's own session — the only one Portable can see.

        Returns:
            list[SessionInfo]: a single-element list containing
            `current_session()`.
        """
        return [self.current_session()]

    def git_facts(self) -> GitFacts:
        """Return real git plumbing facts for `self.workspace`.

        Returns:
            GitFacts: as `collect_git_facts(self.workspace)`, honoring this
            adapter's `git_timeout` override if one was given at
            construction.
        """
        if self._git_timeout is None:
            return collect_git_facts(self.workspace)
        return collect_git_facts(self.workspace, git_timeout=self._git_timeout)

    def emit_prompt(self, text: str, handoff_id: str) -> str:
        """Append the literal handoff-id marker line to `text`.

        Args:
            text: the prompt body to emit.
            handoff_id: the UUID minted for this handoff.

        Returns:
            str: `text`, a blank line, the `FLEET-HANDOFF-ID:` line, then the
            self-register instruction (Chunk 3, Decision E).
        """
        return (
            f"{text}\n\n{format_handoff_line(handoff_id)}\n\n"
            f"{format_launch_instruction()}\n"
        )
