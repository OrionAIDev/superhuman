"""``SessionAdapter`` ABC — the sole harness seam (ARCHITECTURE "Cross-component contracts" item 4).

Any new harness implements this interface and nothing in ``core/`` changes.
The four methods are exactly the ones DESIGN's component-breakdown table
names for ``adapter/base.py``:

- ``current_session()`` — identity + facts for the session the adapter is
  running inside of.
- ``enumerate_sessions()`` — every other live session this adapter can see.
- ``git_facts()`` — git plumbing evidence for the adapter's working tree
  (Decision B's evidence source for edge derivation).
- ``emit_prompt(text, handoff_id)`` — embed a durable ``handoff_id`` in a
  prompt (Decision E).

The ABC is deliberately not instantiable (``abstractmethod`` on all four) —
TC-14 asserts ``SessionAdapter()`` raises ``TypeError``.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The literal marker line `emit_prompt` embeds (Decision E). Kept here, not
#: duplicated per adapter, so both implementations produce byte-identical
#: framing and a self-register step only ever has to look for one format.
HANDOFF_ID_LINE_PREFIX = "FLEET-HANDOFF-ID:"

#: Length of the stable digest `workspace_component` returns. 16 hex chars
#: (64 bits) is far more than enough to make an accidental collision between
#: two different real workspace paths on one host astronomically unlikely,
#: while staying short.
_WORKSPACE_COMPONENT_DIGEST_LEN = 16


def format_handoff_line(handoff_id: str) -> str:
    """Return the literal handoff-id line embedded in an emitted prompt.

    Args:
        handoff_id: the UUID minted for this handoff (Decision E).

    Returns:
        str: e.g. ``"FLEET-HANDOFF-ID: <uuid>"``, with no trailing newline.
    """
    return f"{HANDOFF_ID_LINE_PREFIX} {handoff_id}"


#: The self-register instruction `emit_prompt` embeds adjacent to the
#: `FLEET-HANDOFF-ID` line (fleet-wiring Chunk 3, Decision E). The prompt is
#: the only context guaranteed to reach a launched session that never loads
#: superhuman, so this is the strongest available carrier for the launch-flip
#: trigger — stronger than the `SKILL.md` first-action step or an
#: operator-installed hook, both of which are unreachable for a session that
#: skips superhuman entirely. Kept here, not duplicated per adapter, so both
#: implementations produce byte-identical framing (same rationale as
#: `format_handoff_line`). Deliberately generic prose, not a fully-formed
#: CLI invocation: `emit_prompt` has no `workspace`/`slug` to fill in, and
#: this step is best-effort by construction — never worth blocking on.
_LAUNCH_INSTRUCTION = (
    "If this project uses fleet session observation, run "
    "`fleet observe launch --workspace <this project's root> "
    "--slug <this project's slug> --handoff-id <the id above>` as your first "
    "action (see SKILL.md's first-action step). This is best-effort: if "
    "fleet observation is disabled, unavailable, or the command fails, skip "
    "it and proceed with the work above regardless — it never blocks."
)


def format_launch_instruction() -> str:
    """Return the self-register instruction embedded next to the handoff-id line.

    Returns:
        str: prose telling a launched session how to flip its own row to
        `active`, with no trailing newline.
    """
    return _LAUNCH_INSTRUCTION


def workspace_component(workspace: Path | str) -> str:
    """Return a short, filesystem-safe, stable node-id component for `workspace`.

    Node ids are namespaced `<harness>/<workspace>/<slug>/<local-session-id>`
    (CC-3), and `core.store.fragment_path` percent-encodes the *whole* node
    id a second time to build a fragment filename on disk. A real absolute
    Windows workspace path run through that double percent-encoding
    (`:` and `\\` each expand to 3 characters on the first pass; the `%`
    signs that produces expand *again* on the second pass) can push the
    fragment path well past the classic 260-character `MAX_PATH` — verified
    directly: this chunk's own `tests/fleet/test_register.py` reproduced a
    `FileNotFoundError` from `os.replace` on exactly this path, using
    nothing more exotic than a real pytest temp-repo fixture. Both adapters
    key node ids on this short stable digest instead of the raw path, so an
    id's filesystem footprint no longer scales with the workspace path's
    length. Nothing about the workspace's identity is lost — the true
    absolute path is still carried verbatim in `SessionInfo.workspace` and
    the `session_registered` event payload; only how it is spelled *inside
    the id* changes.

    Args:
        workspace: the working tree path to key on.

    Returns:
        str: a stable, lowercase hex digest of the resolved absolute path.
        The same `workspace` (even given as different relative spellings)
        always yields the same digest; different workspaces yield different
        digests with overwhelming probability.
    """
    resolved = str(Path(workspace).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:_WORKSPACE_COMPONENT_DIGEST_LEN]


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """One session's identity + origination facts, as known to an adapter.

    Attributes:
        node_id: the fully namespaced node id (`core.nodes.make_node_id`).
        harness: the harness this session runs under (e.g. "claude", "portable").
        workspace: absolute path to the working tree this session runs in.
        local_id: the harness-local session identifier.
        branch: the git branch checked out in `workspace`, if known.
        origination: which of FR-1's origination paths produced this session
            — "spawned" | "relayed" | "manual" | "unknown".
        raw: the adapter-specific source record this was built from, kept
            for debugging/audit only; `core/` never reads it.
    """

    node_id: str
    harness: str
    workspace: str
    local_id: str
    branch: str | None = None
    origination: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GitFacts:
    """Git plumbing facts for one working tree (Decision B's evidence source).

    Attributes:
        is_repo: whether the directory is inside a git working tree.
        branch: the currently checked-out branch, or None if detached,
            absent, or not a repo.
        toplevel: absolute path to the working tree root, if any.
        merge_base: merge-base sha against the best-guess default branch, if
            one was resolvable; None otherwise (e.g. a single-branch repo).
        dirty_files: count of entries reported by `git status --porcelain`.
    """

    is_repo: bool
    branch: str | None = None
    toplevel: str | None = None
    merge_base: str | None = None
    dirty_files: int = 0


class SessionAdapter(ABC):
    """The sole harness-aware seam ``core/`` code is ever allowed to cross.

    ``core/`` never imports this module or any of its implementations (the
    dependency arrow only ever points `adapter -> core`, never back) — see
    ARCHITECTURE "Dependency map". Implement all four methods to add support
    for a new harness; nothing in `core/` needs to change.
    """

    @abstractmethod
    def current_session(self) -> SessionInfo:
        """Return identity + facts for the session this adapter runs inside of.

        Returns:
            SessionInfo: this session's own identity and origination facts.
        """
        raise NotImplementedError

    @abstractmethod
    def enumerate_sessions(self) -> list[SessionInfo]:
        """Return every other live session this adapter can currently see.

        Returns:
            list[SessionInfo]: zero or more sessions. An adapter with no
            enumeration mechanism returns an empty list rather than raising
            or fabricating entries.
        """
        raise NotImplementedError

    @abstractmethod
    def git_facts(self) -> GitFacts:
        """Return git plumbing facts for this adapter's working tree.

        Returns:
            GitFacts: `is_repo=False` (with the rest defaulted) if the
            working tree is not a git repository, rather than raising.
        """
        raise NotImplementedError

    @abstractmethod
    def emit_prompt(self, text: str, handoff_id: str) -> str:
        """Return `text` with a durable `handoff_id` marker line embedded (Decision E).

        Args:
            text: the prompt body to emit.
            handoff_id: the UUID minted for this handoff.

        Returns:
            str: `text` plus a literal `FLEET-HANDOFF-ID: <uuid>` line, so a
            launched session's first action can recover `handoff_id` by
            grepping its own prompt even if everything else was edited.
        """
        raise NotImplementedError
