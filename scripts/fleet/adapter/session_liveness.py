"""Adapter-layer prior-owner liveness resolution from supplied records (O-FR-5, DESIGN O.4).

`core/project_owner.decide_claim` needs to know whether a prior owner is
still active, but `core/` never imports a harness-specific module (W-NFR-2)
— it takes a plain `liveness` string, resolved elsewhere. This module is
that "elsewhere": it reads the SAME kind of already-fetched session records
`adapter.claude.ClaudeAdapter` consumes (the `--sessions-json` dump), never
calling a harness tool itself, and answers exactly one question — "is this
node id archived, active, or unknown" — as conservatively as possible.

**Conservative by construction (O-FR-5).** Every case this module cannot
positively resolve — no records supplied, a harness this module does not
know how to read, zero matches, two or more matches, or a non-boolean
`isArchived` flag — resolves to `"unknown"`, which `decide_claim` treats
exactly like `"active"`: coordination (an attestation) is still required.
`"deleted"` is never returned here: no supplied record format carries a
positive "this session was deleted" signal today (DESIGN O.4, OQ-3) — the
value exists in the ownership-event schema for a harness that later
supplies one, but this resolver has no path to it.

**Exact-id match only (OQ-4).** A node's `local_id` must equal a supplied
record's `sessionId`/`session_id` exactly, using the SAME extraction rule
`adapter.claude.ClaudeAdapter.enumerate_sessions` applies
(`adapter.base.extract_claude_session_local_id`) — refactored into one
shared helper so the two cannot drift. A hook-registered node's local id is
the Claude Code transcript id, which a Desktop `list_sessions` dump does not
carry (session-relay's own resolver docstring: "Neither id appears in the
other's records"), so such nodes resolve `"unknown"` here — safe, since
`"unknown"` never skips the coordination step. Resolver-backed
transcript-correlation matching (DESIGN O.4 option B) is a named follow-up,
not attempted here.
"""

from __future__ import annotations

from typing import Any, Final

from ..core.nodes import parse_node_id
from .base import extract_claude_session_local_id

#: Liveness result vocabulary (O-FR-5 / DESIGN O.1's `prior_owner_liveness`
#: payload field). `DELETED` is part of the vocabulary this module's callers
#: may see written to the manifest by some future resolver, but this
#: module never returns it itself (see module docstring, OQ-3).
ACTIVE: Final[str] = "active"
UNKNOWN: Final[str] = "unknown"
ARCHIVED: Final[str] = "archived"
DELETED: Final[str] = "deleted"

#: The one harness this resolver knows how to read supplied records for.
_KNOWN_HARNESS: Final[str] = "claude"


def resolve_liveness(node_id: str, sessions: list[dict[str, Any]] | None) -> str:
    """Resolve one node's liveness from supplied harness session records (O-FR-5).

    Args:
        node_id: the node to resolve, as `core.nodes.make_node_id` built it
            (typically the prior owner in an ownership claim).
        sessions: the raw session records from `--sessions-json` (or
            equivalent orchestrator-supplied records), or `None`/empty if
            none were supplied.

    Returns:
        str: `"archived"` iff exactly one supplied record's extracted id
        matches `node_id`'s `local_id` and that record's `isArchived` is
        literally `True`; `"active"` iff exactly one matches and
        `isArchived` is literally `False`; `"unknown"` for every other
        case — a malformed `node_id`, a non-`"claude"` harness, no records
        supplied, zero or multiple matches, or a non-boolean `isArchived`
        (TC-O16). Never `"deleted"` (see module docstring).
    """
    try:
        harness, _workspace, _slug, local_id = parse_node_id(node_id)
    except ValueError:
        return UNKNOWN

    if harness != _KNOWN_HARNESS:
        return UNKNOWN
    if not sessions:
        return UNKNOWN

    matches = [record for record in sessions if extract_claude_session_local_id(record) == local_id]
    if len(matches) != 1:
        return UNKNOWN

    is_archived = matches[0].get("isArchived")
    if is_archived is True:
        return ARCHIVED
    if is_archived is False:
        return ACTIVE
    return UNKNOWN
