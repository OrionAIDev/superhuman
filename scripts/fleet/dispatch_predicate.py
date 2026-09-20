"""Decision C's exact granularity predicate (D5, DECISIONS.md D5, chunk 7 PM
ruling 3: "the predicate, verbatim Decision C").

**Portable, harness-agnostic (D4).** This module names no harness and is
never invoked with a Claude Code transcript record shape — it operates on a
single already-extracted prompt string and a directory of `roles/*.md`
files. The Claude-Code-specific work of *finding* that prompt string inside
a transcript (JSONL record shape, `message.id` grouping, tool name `Agent`
vs `Task`) is harness knowledge and lives in
`templates/hooks/claude-code/subagent_dispatch_filter.py`, which imports
this module rather than duplicating the predicate (chunk 7 PM ruling 4: "no
new harness name enters `scripts/`").

**The predicate, verbatim (chunk 7 PM ruling 3):** the prompt, after
stripping leading whitespace or a BOM, opens with a `---` frontmatter block
whose `name:` is the basename of an existing `roles/*.md` file under the
skill root. A prompt that only *mentions* `roles/` mid-body does not match
(TC-52) — this module only ever looks at the leading frontmatter block. The
role set is read from `roles_dir` at call time, never hardcoded (so a role
added or removed on disk is picked up with no code change here).
"""

from __future__ import annotations

import re
from pathlib import Path

#: A `---`-delimited frontmatter block at the very start of the (stripped)
#: prompt. `[ \t]*` tolerates trailing whitespace on the delimiter lines;
#: `\r?\n` tolerates CRLF-authored prompts. `.*?` is non-greedy so the FIRST
#: closing `---` line ends the block, matching how real role files
#: (`roles/*.md`) are structured.
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)

#: The frontmatter's `name:` line, e.g. `name: developer`. Matched anywhere
#: within the frontmatter body (real role files carry it first, but nothing
#: in Decision C's predicate requires that).
_NAME_LINE_RE = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)


def _existing_role_basenames(roles_dir: Path) -> set[str]:
    """Return the set of role basenames (no `.md`) actually present under `roles_dir`.

    Args:
        roles_dir: the directory holding `roles/*.md`.

    Returns:
        set[str]: one entry per `*.md` file's stem. Never raises — a
        missing or unreadable `roles_dir` yields an empty set, which makes
        `leads_with_role_block` return `False` for every prompt (the safe,
        "do not register" default), rather than propagating an `OSError`
        into a `SubagentStart` hook, where FR-18 forbids a non-zero exit.
    """
    try:
        return {p.stem for p in roles_dir.glob("*.md") if p.is_file()}
    except OSError:
        return set()


def leads_with_role_block(prompt_text: str, roles_dir: Path) -> bool:
    """Decision C's exact predicate (chunk 7 PM ruling 3).

    Args:
        prompt_text: the dispatching `Agent`/`Task` tool_use's `prompt`
            input, exactly as read from a transcript — never pre-processed
            by the caller beyond extraction.
        roles_dir: the directory holding `roles/*.md`; the role set is read
            from disk each call, never hardcoded.

    Returns:
        bool: `True` iff the prompt leads with a frontmatter block whose
        `name:` names an existing role. Never raises: a malformed or empty
        prompt, a frontmatter with no `name:` line, or an unreadable
        `roles_dir` all resolve to `False` — the safe "do not register"
        default under FR-18's threat model.
    """
    if not prompt_text:
        return False
    stripped = prompt_text.lstrip("﻿").lstrip()
    match = _FRONTMATTER_RE.match(stripped)
    if match is None:
        return False
    name_match = _NAME_LINE_RE.search(match.group("body"))
    if name_match is None:
        return False
    role_name = name_match.group(1)
    return role_name in _existing_role_basenames(roles_dir)


def resolve_verdict(verdicts: list[bool]) -> bool | None:
    """Chunk 7 PM ruling 2's aggregation rule over one dispatching message's candidates.

    Args:
        verdicts: one `leads_with_role_block` result per candidate
            `Agent`/`Task` tool_use found in the most recent dispatching
            assistant message (grouped by `message.id`) whose
            `subagent_type` matches this dispatch's `agent_type`.

    Returns:
        bool | None: the unanimous verdict when every candidate agrees;
        `None` for zero candidates or any disagreement — both mean "write
        no row" (fail soft on coverage, fail closed on assertion; a nested
        dispatch, which finds no candidate at all, is exactly the zero-
        candidates case and is a stated limitation, not an error).
    """
    if not verdicts:
        return None
    first = verdicts[0]
    if all(v == first for v in verdicts):
        return first
    return None
