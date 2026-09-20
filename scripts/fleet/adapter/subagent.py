"""``SubagentAdapter`` — identity for a PM-minted dispatch unit (W-FR-1, Decision C, Q3).

**Read this before changing what this adapter claims to know.** A subagent
dispatch (the Agent-tool / Task-style mechanism a PM uses to run a role
subagent) is **not an addressable harness session** — DESIGN's Decision C
"Identity note" verifies this directly from `adapter/claude.py`'s own module
docstring: neither "which session am I" nor a native session id is
obtainable from Python on the primary harness, and a dispatched subagent does
not appear in `list_sessions` as a resumable session at all. There is
consequently nothing this adapter could poll or enumerate to discover a
dispatch's identity on its own — the identity comes from the PM that issued
the dispatch, exactly the way `handoff.emit` already mints a synthetic
`harness="handoff"` node for a row that is not a real harness session either
(the precedent this adapter follows).

This adapter therefore registers the **dispatch unit itself**, keyed on a
PM-minted `local_id` (the dispatch id), reporting `harness="subagent"` so
`fleet status` and the Phase-2 CTO can tell a subagent dispatch apart from a
`PortableAdapter`-registered OS process or a `ClaudeAdapter`-registered
harness session — never `harness="portable"` (Decision C's rejected
alternative: reusing `PortableAdapter` would mislabel a dispatch as "this OS
process, git+fs+env only", which it is not).

**Git facts are derived from the dispatching workspace, not from an
independent working tree of the dispatch's own** — a subagent dispatch has
no working tree distinct from the one the PM dispatched it from (it runs
inside the same checkout). Mechanically this reuses
`adapter.portable.collect_git_facts` verbatim (identical to how
`ClaudeAdapter` reuses it for exactly the same reason: git plumbing is
equally Python-accessible regardless of which harness dispatched the
process), so this file adds zero new git-facts logic.

**Manual smoke procedure for a real dispatch (recorded here for Chunk 7's
`docs/fleet-observation.md`, per PLAN.md Chunk 5's accepted G3 residual —
W-FR-1's acceptance criterion is only partly automatable):**

1. From a real superhuman project with `fleet.enabled: true` in
   `.superhuman/profile.yaml`, have PM dispatch one role subagent whose
   prompt leads with a `roles/*.md` block (per the granularity rule in
   `roles/pm.md`'s "Fleet dispatch observation (spawned path)" subsection).
2. Immediately after the dispatch call returns, run (from the superhuman
   skill root — the checkout holding ``SKILL.md``, which is where ``-m``
   resolves ``scripts.fleet``; ``<ws>`` is the dispatching workspace, and is
   normally a different directory):
   ``python -m scripts.fleet.cli observe dispatch --harness subagent
   --workspace <ws> --slug <slug> --dispatch-id <role>-<chunk>-<n>
   --local-id <role>-<chunk>-<n> --writer-role pm``
3. Run ``python -m scripts.fleet.cli observe status --workspace <ws> --slug
   <slug>`` and confirm it reports "last write for this project succeeded"
   (not "zero writes").
4. Run ``python -m scripts.fleet.cli status --workspace <ws> --slug <slug>``
   and confirm the new row appears with ``harness=subagent`` and
   ``origination=spawned``.
5. Repeat once for a PM research/read-only fan-out (a prompt that does NOT
   lead with a `roles/*.md` block, e.g. an `Explore` dispatch) and confirm
   PM does *not* call `observe dispatch` for it — the granularity rule
   holds. This half (a live orchestrating model actually following the
   prose instruction) is exactly what TC-21 records as manual-only; no
   automated test can prove a model's own compliance.
"""

from __future__ import annotations

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
from .portable import collect_git_facts

#: Bounded so a hung/misbehaving `git` process can never wedge registration
#: (identical default to `adapter.portable`'s own — see
#: `collect_git_facts`'s docstring for the additive-passthrough contract).
_GIT_TIMEOUT_SECONDS = 30


class SubagentAdapter(SessionAdapter):
    """`SessionAdapter` for a PM-minted subagent dispatch unit — see the
    module docstring for why this is a dispatch identity, not a live
    harness-session identity, and why `git_facts()` reads the dispatching
    workspace rather than an independent working tree.

    Attributes:
        workspace: the *dispatching* project's working tree root — the
            subagent has no working tree of its own to report on. NOT
            necessarily where this dispatch's own facts should be read from
            (see `git_facts_root`).
        slug: the owning superhuman project's slug (used to namespace node ids).
    """

    def __init__(
        self,
        workspace: Path | str,
        slug: str,
        *,
        local_id: str,
        git_timeout: float | None = None,
        git_facts_root: Path | str | None = None,
    ) -> None:
        """Initialize a SubagentAdapter.

        Args:
            workspace: the dispatching project's working tree root.
            slug: the owning superhuman project's slug.
            local_id: the PM-minted dispatch id identifying this dispatch
                unit (e.g. `"<role>-<chunk>-<n>"`). Unlike `PortableAdapter`,
                there is no honest process-level fallback to default to — a
                dispatch has no OS process of its own that this Python
                interpreter can observe — so this is required, not optional.
            git_timeout: seconds allowed per git subprocess call in
                `git_facts()` (additive passthrough, fleet-wiring Chunk 1).
                `None` (the default) uses `collect_git_facts`'s own default
                (30.0s).
            git_facts_root: the directory `git_facts()` actually queries for
                branch/commit/dirty state. Defaults to `workspace` when
                omitted -- byte-identical to every caller before this
                parameter existed. Mirrors `ClaudeAdapter`'s identically-named
                parameter and exists for the identical reason: a `--hook-
                payload` consumer (`SubagentStart`, `cli._cmd_observe_
                dispatch`) resolves the dispatching PM's own working tree
                (possibly a linked worktree) BEFORE `locate.locate_project`
                hops outward to find the project's `SUPERHUMAN.md`, and that
                outward-hopped `workspace` can be a different repository's
                worth of git state -- reporting `workspace`'s branch there
                answers a different question (chunk-6's measured defect,
                reproduced for this adapter at chunk 7: `cli._build_adapter`
                computed `args.git_facts_root` correctly but only forwarded
                it to `ClaudeAdapter`, silently dropping it for `--harness
                subagent`).
        """
        self.workspace = Path(workspace)
        self.slug = slug
        self._local_id = local_id
        self._git_timeout = git_timeout
        self._git_facts_root = (
            Path(git_facts_root) if git_facts_root is not None else self.workspace
        )

    def current_session(self) -> SessionInfo:
        """Return this dispatch unit's identity, enriched with the dispatching workspace's git facts.

        Returns:
            SessionInfo: `harness="subagent"`, `origination="spawned"` — a
            subagent dispatch is, by construction, always the spawned path
            (W-FR-1); it is never self-registering (`"relayed"`) or a manual
            registration, unlike `PortableAdapter`/`ClaudeAdapter`, which can
            serve more than one origination.
        """
        facts = self.git_facts()
        node_id = make_node_id(
            "subagent", workspace_component(self.workspace), self.slug, self._local_id
        )
        return SessionInfo(
            node_id=node_id,
            harness="subagent",
            workspace=str(self.workspace),
            local_id=self._local_id,
            branch=facts.branch,
            origination="spawned",
            raw={},
        )

    def enumerate_sessions(self) -> list[SessionInfo]:
        """Return this dispatch unit's own session — the only one this adapter can see.

        Mirrors `PortableAdapter.enumerate_sessions()` exactly: a subagent
        dispatch has no live-session-enumeration mechanism of its own (per
        the module docstring's identity note), so the sole entry this
        adapter can honestly report is itself. This is also what makes
        `cli._resolve_target_session`'s `target_session_id` lookup succeed
        for the spawned path: the caller passes the same id as both this
        adapter's `local_id` and `register_session`'s `target_session_id`.

        Returns:
            list[SessionInfo]: a single-element list containing
            `current_session()`.
        """
        return [self.current_session()]

    def git_facts(self) -> GitFacts:
        """Return real git plumbing facts for `self._git_facts_root`.

        Returns:
            GitFacts: as `adapter.portable.collect_git_facts(self._git_facts_root)`
            — identical mechanism to `PortableAdapter`/`ClaudeAdapter`.
            `self._git_facts_root` defaults to `self.workspace` when no
            override was given at construction, so this is byte-identical to
            querying `self.workspace` directly for every caller that predates
            `git_facts_root` — see the constructor's `git_facts_root`
            parameter for why the two differ whenever a `--hook-payload`
            consumer resolved a dispatching working tree distinct from the
            (possibly outward-hopped) project `workspace`. Honors this
            adapter's `git_timeout` override if one was given at
            construction.
        """
        if self._git_timeout is None:
            return collect_git_facts(self._git_facts_root)
        return collect_git_facts(self._git_facts_root, git_timeout=self._git_timeout)

    def emit_prompt(self, text: str, handoff_id: str) -> str:
        """Append the literal handoff-id marker line to `text`.

        Not part of the spawned dispatch's own flow (a dispatch registers
        via `fleet observe dispatch`, never `handoff-emit`) — implemented for
        ABC completeness and conformance-suite parity with `PortableAdapter`/
        `ClaudeAdapter`, in case a future caller ever emits a handoff through
        a subagent-dispatch identity.

        Args:
            text: the prompt body to emit.
            handoff_id: the UUID minted for this handoff.

        Returns:
            str: `text`, a blank line, the `FLEET-HANDOFF-ID:` line, then the
            self-register instruction — byte-identical framing to the other
            two adapters (both reuse the same `adapter.base` helpers).
        """
        return (
            f"{text}\n\n{format_handoff_line(handoff_id)}\n\n"
            f"{format_launch_instruction()}\n"
        )
