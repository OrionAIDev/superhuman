"""Parses the generic harness hook-payload contract (D2b, FR-15/FR-16/FR-17).

`--hook-payload <file|->` is how `fleet observe session-start` (and, later,
`fleet observe dispatch`) reads a harness's stdin JSON payload so the hook
and the portable prose floor call the *identical* verb (FR-15) — the only
difference is where the arguments came from. This module documents the
payload shape **generically**: any harness emitting `session_id`/`cwd` (plus
whichever of the other measured fields it happens to carry) works
unchanged. No harness name appears anywhere in this module (D4).

**The measured contract (CHUNK-1-FINDINGS.md).** A real `SessionStart`
payload carries 6 fields, `SubagentStart` carries 8:
`session_id`, `cwd`, `transcript_path`, `scratchpad_dir`, `hook_event_name`
(both events); `source` (`SessionStart` only — the matcher that fired);
`agent_id`, `agent_type`, `prompt_id` (`SubagentStart` only). Three of these
are undocumented by the harness (`scratchpad_dir`, `agent_id`, `source`) but
real and load-bearing (`agent_id` is D5/D7's per-dispatch `--local-id`
anchor) — this module reads them the same way as every documented field, no
differently.

**Never raises (FR-18's threat model starts here).** A hostile or corrupted
stdin payload — truncated JSON, non-JSON bytes, a JSON array instead of an
object, a missing `session_id`/`cwd` — all resolve to `None`, never an
exception propagating up to the hook wrapper. `session_id` and `cwd` are the
two fields every downstream caller (the locator, `FR-17`'s pass-through)
requires; a payload missing either is treated as no payload at all rather
than handed on half-populated.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class HookPayload:
    """One parsed harness hook payload (`SessionStart` or `SubagentStart` shape).

    Every field beyond `session_id`/`cwd` is optional and payload-shape
    dependent (see the module docstring's measured contract); a field
    absent from the raw payload is `None` here, never fabricated.

    Attributes:
        session_id: the harness's session identifier — the same namespace
            as fleet's existing `local_id` (Chunk 1 finding 10), passed
            through unchanged rather than fuzzy-matched (FR-17).
        cwd: the session's working directory at hook-fire time (FR-16) —
            the sole anchor `locate.locate_project` resolves from; never
            the hook subprocess's own OS working directory.
        hook_event_name: `"SessionStart"` | `"SubagentStart"` | other.
        transcript_path: frequently names a file that does not exist yet
            at `SessionStart` time (CHUNK-1-FINDINGS.md finding 3) — never
            assume it is readable.
        scratchpad_dir: undocumented but real (CHUNK-1-FINDINGS.md).
        source: the `SessionStart` matcher that fired (`startup`, `resume`,
            `clear`, `compact`, `fork`); absent on `SubagentStart`.
        agent_id: undocumented, `SubagentStart`-only per-dispatch
            identifier (CHUNK-1-FINDINGS.md finding 1 — D5's `--local-id`
            anchor).
        agent_type: `SubagentStart`-only.
        prompt_id: `SubagentStart`-only.
    """

    session_id: str
    cwd: str
    hook_event_name: str | None = None
    transcript_path: str | None = None
    scratchpad_dir: str | None = None
    source: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    prompt_id: str | None = None


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    """Return `data[key]` as a non-empty `str`, or `None`.

    Args:
        data: the parsed JSON payload.
        key: the field to read.

    Returns:
        str | None: the value if it is a non-empty string; `None` for a
        missing key, a blank string, or any non-string JSON value (a
        hostile/malformed payload must never raise a `TypeError` further
        down the caller chain).
    """
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def read_hook_payload(source: Path | str) -> HookPayload | None:
    """Parse a harness hook payload from a file path or stdin.

    Never raises (see the module docstring). A payload missing
    `session_id` or `cwd` also resolves to `None` rather than a
    partially-populated `HookPayload`.

    Args:
        source: `"-"` to read stdin; any other value is treated as a file
            path to read.

    Returns:
        HookPayload | None: the parsed payload, or `None` on any failure —
        missing/empty input, invalid JSON, a non-object top level, or a
        missing/blank `session_id`/`cwd`.
    """
    try:
        if str(source) == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(source).read_text(encoding="utf-8")
    except (OSError, ValueError):
        # `ValueError` covers `UnicodeDecodeError` on a non-UTF-8 file.
        return None

    if not raw.strip():
        return None

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    session_id = _optional_str(data, "session_id")
    cwd = _optional_str(data, "cwd")
    if session_id is None or cwd is None:
        return None

    return HookPayload(
        session_id=session_id,
        cwd=cwd,
        hook_event_name=_optional_str(data, "hook_event_name"),
        transcript_path=_optional_str(data, "transcript_path"),
        scratchpad_dir=_optional_str(data, "scratchpad_dir"),
        source=_optional_str(data, "source"),
        agent_id=_optional_str(data, "agent_id"),
        agent_type=_optional_str(data, "agent_type"),
        prompt_id=_optional_str(data, "prompt_id"),
    )
