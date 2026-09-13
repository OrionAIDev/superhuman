#!/usr/bin/env python3
"""Claude Code transcript parsing for D5's granularity filter (chunk 7,
`SubagentStart` hook; D4: harness-specific parsing lives here, never in
`scripts/fleet/`).

**Why this file exists, separately from `subagent-start`.** The shell
wrapper's job is best-effort process plumbing (find an interpreter, trap
every exit path, never propagate a non-zero exit). Deciding *whether* a
dispatch is a role dispatch needs real JSON/JSONL parsing over a
Claude-Code-shaped transcript record, which is far more naturally (and
safely) written in Python than bash. This script is that decision, called
once from `subagent-start` before it ever calls
`observe dispatch --hook-payload -`.

**The transcript record shape (confirmed by reading a real transcript on
this machine before writing this module — see the chunk 7 dispatch
brief).** Each content block of an assistant turn is its OWN JSONL record;
multiple records share the same `message.id` when they belong to the same
logical assistant turn. A dispatching tool_use looks like::

    {"type": "assistant",
     "message": {"id": "msg_...", "role": "assistant",
                 "content": [{"type": "tool_use", "name": "Agent",
                              "input": {"subagent_type": "developer",
                                        "prompt": "..."}}]}}

The tool name observed in practice is `"Agent"`; `"Task"` is also accepted
per DESIGN.md/PLAN.md's own references to an `Agent`/`Task` tool_use pair,
in case a future harness release renames or dual-ships the tool.

**Chunk 7 PM ruling 2 (delta-report-002), implemented verbatim:**
candidates are the `Agent`/`Task` tool_uses in the parent transcript's MOST
RECENT dispatching assistant message (grouped by `message.id`) whose
`subagent_type` equals the payload's `agent_type`. If every candidate gives
the same predicate verdict, use it. If there are zero candidates, or they
disagree, the decision is "skip" (write no row). A nested dispatch (a
subagent dispatching a further subagent) finds no candidate in ITS OWN
parent transcript — a stated limitation, not an error.

**Never raises, never prints anything but exactly one word to stdout**
(`"register"` or `"skip"`) — this is read by `subagent-start`'s shell
wrapper, which itself must exit 0 under every fault (FR-18). Every failure
mode here (missing file, corrupted JSON, an unreadable roles directory)
degrades to `"skip"`, never a traceback, never a non-zero exit from this
script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: This file lives at <skill_root>/templates/hooks/claude-code/subagent_dispatch_filter.py.
_SKILL_ROOT = Path(__file__).resolve().parents[3]

# So `from scripts.fleet... import ...` resolves when this script is run
# directly (not via `-m`) — it is invoked as a plain script by
# `subagent-start`, not as a package module, so `scripts.fleet` is not
# already importable without this.
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.fleet.dispatch_predicate import (  # noqa: E402
    leads_with_role_block,
    resolve_verdict,
)
from scripts.fleet.hook_payload import read_hook_payload  # noqa: E402

#: Bounded, tail-only read (D5/NFR-2). CHUNK-1-FINDINGS.md measured a real
#: `SubagentStart` transcript at 1.46 MB; 4 MB gives ~2.7x headroom for a
#: longer-running session while keeping the read (and the JSONL parse that
#: follows it) O(cap) rather than O(file size) — a multi-hundred-MB
#: transcript costs the same bounded read as a small one. Reading and
#: JSON-parsing a few MB of JSONL is on the order of tens of milliseconds,
#: negligible next to interpreter startup (CHUNK-1-FINDINGS.md's ~300ms
#: probe-alone latency), so this cap does not meaningfully move NFR-2's
#: budget.
DEFAULT_MAX_TAIL_BYTES = 4_000_000

_DISPATCH_TOOL_NAMES = ("Agent", "Task")


def _tail_bytes(path: Path, cap: int) -> bytes:
    """Read at most the last `cap` bytes of `path`, discarding a partial leading line.

    Args:
        path: the transcript file to read.
        cap: the maximum number of bytes to read from the end of the file.

    Returns:
        bytes: the tail of the file, with any partial first line (the
        remainder of a JSONL record split by the cap boundary) discarded.
        Returns `b""` for any I/O failure — a missing, unreadable, or
        vanished-mid-read file is treated as "no transcript", never an
        exception (FR-18's threat model; CHUNK-1-FINDINGS.md finding 3:
        `transcript_path` is frequently unreadable and must never be
        assumed present).
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > cap:
                fh.seek(size - cap)
                data = fh.read()
                newline_at = data.find(b"\n")
                data = data[newline_at + 1 :] if newline_at != -1 else b""
            else:
                data = fh.read()
    except OSError:
        return b""
    return data


def _iter_dispatch_tool_uses(lines: list[str]) -> list[tuple[str | None, str | None, str]]:
    """Yield `(message_id, subagent_type, prompt)` for every dispatching tool_use record.

    Args:
        lines: the tail-read transcript, split into individual JSONL lines
            (each line is one Claude Code transcript record).

    Returns:
        list[tuple[str | None, str | None, str]]: one entry per
        `Agent`/`Task` tool_use block found in an assistant-role record,
        in file order. A line that is not valid JSON, not an object, or
        does not carry the expected shape is silently skipped — this
        function never raises.
    """
    found: list[tuple[str | None, str | None, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record: Any = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        message_id = message.get("id") if isinstance(message.get("id"), str) else None
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name") not in _DISPATCH_TOOL_NAMES:
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                continue
            prompt = tool_input.get("prompt")
            if not isinstance(prompt, str):
                continue
            subagent_type = tool_input.get("subagent_type")
            if not isinstance(subagent_type, str):
                subagent_type = None
            found.append((message_id, subagent_type, prompt))
    return found


def decide(
    *,
    transcript_path: str | None,
    agent_type: str | None,
    roles_dir: Path,
    max_tail_bytes: int = DEFAULT_MAX_TAIL_BYTES,
) -> str:
    """Apply chunk 7 PM rulings 2/3 and return `"register"` or `"skip"`.

    Args:
        transcript_path: the payload's `transcript_path` — the PARENT
            transcript (chunk 7 PM ruling 1: the subagent's own transcript
            never exists at hook time). `None`/missing/unreadable all
            resolve to `"skip"`.
        agent_type: the payload's `agent_type` — only tool_uses dispatching
            this exact type are candidates.
        roles_dir: the directory holding `roles/*.md`, forwarded to
            `leads_with_role_block`.
        max_tail_bytes: bounds the transcript read (NFR-2).

    Returns:
        str: `"register"` if every candidate agrees the prompt leads with
        a role block; `"skip"` for zero candidates, disagreement, a
        missing/unreadable transcript, or any unexpected failure — this
        function never raises.
    """
    try:
        if not transcript_path or not agent_type:
            return "skip"
        data = _tail_bytes(Path(transcript_path), max_tail_bytes)
        if not data:
            return "skip"
        text = data.decode("utf-8", errors="replace")
        tool_uses = _iter_dispatch_tool_uses(text.splitlines())
        if not tool_uses:
            return "skip"

        # Chunk 7 PM ruling 2: candidates come from the MOST RECENT
        # dispatching assistant message — the message.id attached to the
        # last tool_use record found, regardless of that record's own
        # subagent_type (a message can dispatch several different types at
        # once; "most recent" is about the MESSAGE, not this dispatch's
        # own type).
        most_recent_message_id = tool_uses[-1][0]
        candidate_prompts = [
            prompt
            for message_id, subagent_type, prompt in tool_uses
            if message_id == most_recent_message_id and subagent_type == agent_type
        ]

        verdicts = [leads_with_role_block(prompt, roles_dir) for prompt in candidate_prompts]
        verdict = resolve_verdict(verdicts)
        return "register" if verdict else "skip"
    except Exception:  # noqa: BLE001 - this script's sole broad catch; see module docstring
        return "skip"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: read `--hook-payload`, print `"register"`/`"skip"`, exit 0.

    Args:
        argv: argument vector (defaults to `sys.argv[1:]`).

    Returns:
        int: always `0` — this script's own exit status carries no
        meaning to its caller; the decision is read from stdout. Never
        raises past this function (see `decide`'s docstring and this
        function's own broad catch).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hook-payload",
        default="-",
        help="read the harness hook JSON payload from this file, or '-' for stdin",
    )
    parser.add_argument(
        "--roles-dir",
        type=Path,
        default=_SKILL_ROOT / "roles",
        help="the directory holding roles/*.md (default: this skill's own roles/)",
    )
    parser.add_argument(
        "--max-tail-bytes",
        type=int,
        default=DEFAULT_MAX_TAIL_BYTES,
        help="bound the transcript read (NFR-2)",
    )
    try:
        args = parser.parse_args(argv)
        payload = read_hook_payload(args.hook_payload)
        if payload is None:
            decision = "skip"
        else:
            decision = decide(
                transcript_path=payload.transcript_path,
                agent_type=payload.agent_type,
                roles_dir=args.roles_dir,
                max_tail_bytes=args.max_tail_bytes,
            )
    except Exception:  # noqa: BLE001 - this script's sole broad catch; see module docstring
        decision = "skip"
    print(decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
