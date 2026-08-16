"""``ClaudeAdapter`` — native session tools + ``session-relay``, honestly bounded.

**Read this before changing what this adapter claims to know.** Claude's
native session tools (``list_sessions``, ``get_session``, ``spawn_task``,
``archive_session``, ``send_message``, ...) are agent/MCP tools invoked by
the orchestrating LLM turn — they are **not** Python-importable functions.
Nothing running inside a plain ``python`` process (this module included) can
call them directly. Two facts a Claude-aware adapter would naturally want
are consequently **not obtainable from Python on Claude at all**:

- *"which session am I"* — there is no Claude-Code-exposed environment
  variable, file, or Python API carrying the native session id. (Checked:
  ``session-relay``'s own reference docs — ``references/inventory.md``,
  ``references/handoff.md`` — describe only agent-tool-mediated access.)
- *"what other sessions currently exist"* — only the ``list_sessions`` tool
  knows this; there is no on-disk session index this adapter can read
  instead.

Both are therefore accepted as **orchestrator-supplied constructor
parameters** (``current_session_id``, ``sessions``) rather than invented,
defaulted to something plausible-looking, or silently left to fail later.
The orchestrator — the agent turn that instantiates this adapter — already
has these facts from its own agent-tool context (it just called
``list_sessions``, or it knows its own session id) and passes them in. This
keeps the method *signatures* identical to the ABC (`current_session()` and
`enumerate_sessions()` take no arguments, matching TC-14) while never
fabricating a fact this process cannot honestly know.

What genuinely **is** Python-accessible on Claude, and what this adapter
does with it:

- **Git facts** for the adapter's own working tree — real subprocess `git`
  calls, identical mechanism to `adapter.portable.collect_git_facts`.
- **Enrichment of an already-obtained session list** — ``session-relay``
  ships ``scripts/session_scan.py``, which (per its own module docstring)
  "[r]eads the JSON array produced by the `list_sessions` tool" from a file
  and enriches it with live git facts via subprocess; it never calls
  `list_sessions` itself either. This adapter can invoke that same script
  the same way (subprocess, given the *already-fetched* session records) if
  the caller supplies ``session_relay_script``. It is optional and degrades
  silently (NFR-3's spirit, even though NFR-3 is formally proven by
  `PortableAdapter`) — a missing or failing script just means the sessions
  are used as supplied, unenriched.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..core.errors import SessionIdentityUnresolved
from ..core.nodes import make_node_id
from .base import GitFacts, SessionAdapter, SessionInfo, format_handoff_line, workspace_component
from .portable import collect_git_facts

#: Bounded so a hung/misbehaving session_scan.py can never wedge registration.
_SCRIPT_TIMEOUT_SECONDS = 30


class ClaudeAdapter(SessionAdapter):
    """`SessionAdapter` for the Claude harness — see the module docstring for
    the honest boundary between what this adapter can determine itself and
    what it must be told by its orchestrator.

    Attributes:
        workspace: the working tree this adapter reports git facts for.
        slug: the owning superhuman project's slug (used to namespace node ids).
    """

    def __init__(
        self,
        workspace: Path | str,
        slug: str,
        *,
        current_session_id: str | None = None,
        sessions: list[dict[str, Any]] | None = None,
        session_relay_script: Path | str | None = None,
    ) -> None:
        """Initialize a ClaudeAdapter.

        Args:
            workspace: the working tree this adapter reports git facts for.
            slug: the owning superhuman project's slug.
            current_session_id: THIS session's native Claude session id, as
                known by the orchestrating agent turn. There is no
                Python-accessible source for this on Claude (see module
                docstring) — `None` means "not supplied", not "unknown but
                guessable".
            sessions: the raw JSON records the orchestrator already obtained
                from the `list_sessions` agent tool. There is no
                Python-accessible source for this on Claude either (see
                module docstring). `None`/empty means "not supplied";
                `enumerate_sessions()` then returns `[]` rather than
                raising or fabricating entries.
            session_relay_script: path to `session-relay`'s
                `scripts/session_scan.py`. If given and the file exists,
                `sessions` are enriched (git-derived facts/disposition) via
                subprocess before being turned into `SessionInfo` — mirrors
                how `session-relay` invokes that script itself. If `None`,
                or the subprocess call fails for any reason, `sessions` are
                used exactly as supplied — a missing/broken enrichment path
                is never a hard failure.
        """
        self.workspace = Path(workspace)
        self.slug = slug
        self._current_session_id = current_session_id
        self._sessions = list(sessions) if sessions else []
        self._session_relay_script = (
            Path(session_relay_script) if session_relay_script is not None else None
        )

    def current_session(self) -> SessionInfo:
        """Return this session's identity from the orchestrator-supplied id.

        Fails closed (GPT-5 round-9 preflight, BLOCKING, PM-reproduced)
        rather than fabricating an id: `id(self)` is a per-object memory
        address, so two adapters minted for one real session (each
        constructed without `current_session_id`) used to resolve to two
        different node_ids — duplicate `session_registered` rows for the
        same session, breaking NFR-1 idempotency on the primary harness.

        10th-round preflight, BLOCKING, PM-reproduced (R10-3): round 9's
        guard only tested `current_session_id is None` — `--session-id ""`
        (e.g. an unset shell var interpolated into the flag) is not `None`,
        so it passed straight through and minted a node id with an EMPTY
        trailing `local_id` component (`node_id="claude/<ws>/demo/"`). A
        blank (empty or whitespace-only) id is exactly as unresolved as a
        missing one for this method's purposes and must fail the same way.
        This check alone is defense-in-depth, not the sole guard: even if
        a blank slipped past it, `core.nodes.make_node_id` itself now
        structurally rejects any blank component (R10-3(b)), so no adapter
        can ever mint a node id with an empty segment.

        Returns:
            SessionInfo: always `origination="relayed"` — reaching this
            point proves `current_session_id` was supplied and non-blank.

        Raises:
            SessionIdentityUnresolved: if `current_session_id` was never
                supplied at construction, or was supplied but is empty or
                whitespace-only. The caller must supply the real, non-blank
                id (`--session-id` on the CLI, or `current_session_id=` on
                the constructor) — this adapter does not guess.
        """
        if self._current_session_id is None:
            raise SessionIdentityUnresolved(
                "ClaudeAdapter has no current_session_id to register with — "
                "supply the real Claude session id via --session-id (CLI) "
                "or current_session_id= (constructor); it cannot be "
                "determined automatically on the Claude harness."
            )
        if not self._current_session_id.strip():
            raise SessionIdentityUnresolved(
                f"ClaudeAdapter's current_session_id is blank ({self._current_session_id!r}) "
                "— supply the real, non-empty Claude session id via "
                "--session-id (CLI) or current_session_id= (constructor); "
                "a blank id is treated the same as a missing one."
            )
        facts = self.git_facts()
        local_id = self._current_session_id
        node_id = make_node_id(
            "claude", workspace_component(self.workspace), self.slug, local_id
        )
        return SessionInfo(
            node_id=node_id,
            harness="claude",
            workspace=str(self.workspace),
            local_id=local_id,
            branch=facts.branch,
            origination="relayed",
            raw={"current_session_id": self._current_session_id},
        )

    def enumerate_sessions(self) -> list[SessionInfo]:
        """Return `SessionInfo` for every orchestrator-supplied session record.

        Returns:
            list[SessionInfo]: one entry per supplied session record that
            carries a non-empty `sessionId`/`session_id`; records without
            one are skipped rather than assigned a fabricated id. `[]` if no
            `sessions` were supplied at construction.
        """
        if not self._sessions:
            return []

        records = self._enrich_sessions_via_script()
        result: list[SessionInfo] = []
        for record in records:
            local_id = str(record.get("sessionId") or record.get("session_id") or "").strip()
            if not local_id:
                continue
            cwd = str(record.get("cwd") or self.workspace)
            node_id = make_node_id("claude", workspace_component(cwd), self.slug, local_id)
            result.append(
                SessionInfo(
                    node_id=node_id,
                    harness="claude",
                    workspace=cwd,
                    local_id=local_id,
                    branch=record.get("branch"),
                    origination="relayed",
                    raw=record,
                )
            )
        return result

    def _enrich_sessions_via_script(self) -> list[dict[str, Any]]:
        """Best-effort git-fact enrichment of `self._sessions` via `session_scan.py`.

        Returns:
            list[dict[str, Any]]: the script's enriched session records, or
            `self._sessions` unchanged if no script was configured, it does
            not exist, or the subprocess call fails or returns something
            unparseable — every failure mode degrades to "use what was
            supplied," never a raised exception.
        """
        if self._session_relay_script is None or not self._session_relay_script.is_file():
            return list(self._sessions)

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(self._sessions, tmp)
                tmp_path = Path(tmp.name)

            proc = subprocess.run(
                [sys.executable, str(self._session_relay_script), str(tmp_path), "--json"],
                capture_output=True,
                text=True,
                timeout=_SCRIPT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return list(self._sessions)
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        if proc.returncode != 0:
            return list(self._sessions)
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return list(self._sessions)

        enriched = parsed.get("sessions")
        if not isinstance(enriched, list):
            return list(self._sessions)
        return enriched

    def git_facts(self) -> GitFacts:
        """Return real git plumbing facts for `self.workspace`.

        Returns:
            GitFacts: as `adapter.portable.collect_git_facts(self.workspace)`
            — identical mechanism to `PortableAdapter`, since git plumbing is
            equally Python-accessible on either harness.
        """
        return collect_git_facts(self.workspace)

    def emit_prompt(self, text: str, handoff_id: str) -> str:
        """Append the literal handoff-id marker line to `text`.

        Args:
            text: the prompt body to emit.
            handoff_id: the UUID minted for this handoff.

        Returns:
            str: `text`, a blank line, then the `FLEET-HANDOFF-ID:` line.
        """
        return f"{text}\n\n{format_handoff_line(handoff_id)}\n"
