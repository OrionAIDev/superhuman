"""Replay the event log into fragments — rebuild/reconcile (CC-1).

Fragments are a *rebuildable projection* of the log, never independent truth.
`rebuild()` replays the log from scratch and overwrites whatever is on disk,
so a corrupt or missing fragment is never fatal — it is simply replaced.
`project_event()` is the fast incremental path used after a single
`core/events.append()`: it folds one new event onto the current fragment
(defaulting to a fresh one if none exists yet) without re-reading the whole
log.

Any event payload key that matches one of `schema.STATUS_FIELDS` is applied
to the fragment directly — projection does not hardcode a table of event
types to fields, so new event types introduced by later chunks (handoff,
edges, done_level) project correctly without changes here, as long as their
payload uses the same field names.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ValidationError
from .events import read_all
from .schema import STATUS_FIELDS, Event, Fragment
from .store import read_fragment, write_fragment

#: Defaults for a freshly registered node, before any payload override.
_DEFAULT_STATUS: dict[str, str] = {
    "lifecycle": "active",
    "block_state": "unblocked",
    "review_state": "none",
    "adoption_state": "normal",
    "done_level": "D0-code",
}


def _fresh_fragment(event: Event) -> Fragment:
    """Build the default fragment for a node's first-seen event.

    Args:
        event: the event establishing this node (usually `session_registered`).

    Returns:
        Fragment: defaults overridden by any status-field keys in the
        event's payload.
    """
    fields = dict(_DEFAULT_STATUS)
    for key in STATUS_FIELDS:
        if key in event.payload:
            fields[key] = event.payload[key]
    return Fragment(node_id=event.node_id, project_id=event.project_id, **fields)


def _apply(fragment: Fragment, event: Event) -> Fragment:
    """Return a new Fragment with `event`'s status-field payload keys applied.

    Args:
        fragment: the fragment to update.
        event: the event to fold in; only keys in `STATUS_FIELDS` are applied.

    Returns:
        Fragment: `fragment` with any matching payload values overwritten.
    """
    overrides = {k: v for k, v in event.payload.items() if k in STATUS_FIELDS}
    if not overrides:
        return fragment
    fields = {
        "node_id": fragment.node_id,
        "project_id": fragment.project_id,
        "lifecycle": fragment.lifecycle,
        "block_state": fragment.block_state,
        "review_state": fragment.review_state,
        "adoption_state": fragment.adoption_state,
        "done_level": fragment.done_level,
        **overrides,
    }
    return Fragment(**fields)


def project_event(event: Event, sessions_dir: Path | str) -> Fragment:
    """Fold one already-appended event onto its node's fragment and persist it.

    This is the fast incremental path: it does not re-read the whole log,
    only the one fragment being updated (or none, if this is the node's
    first event).

    Args:
        event: the event to project. Must already be validated/appended.
        sessions_dir: the directory holding per-session fragment files.

    Returns:
        Fragment: the fragment after applying `event`, already written to
        disk. A corrupt existing fragment is treated as absent rather than
        raised — the same non-fatal-corruption guarantee `rebuild()` gives,
        applied to the incremental path.
    """
    try:
        current = read_fragment(event.node_id, sessions_dir)
    except ValidationError:
        current = None
    fragment = _fresh_fragment(event) if current is None else _apply(current, event)
    write_fragment(fragment, sessions_dir)
    return fragment


def rebuild(
    log_path: Path | str,
    sessions_dir: Path | str,
    project_id: str | None = None,
) -> dict[str, Fragment]:
    """Replay the whole log and overwrite every fragment it touches.

    Reads only `events.jsonl` (the source of truth) — never the existing
    fragment files — so a corrupt or missing fragment on disk has no effect
    on the result; it is simply overwritten. Use this for recovery, and
    whenever a from-scratch reconciliation is wanted (e.g. after a crash).

    Args:
        log_path: path to the event log.
        sessions_dir: the directory holding per-session fragment files.
        project_id: if given, only replay events for this project — other
            projects' events (and nodes) are ignored entirely.

    Returns:
        dict[str, Fragment]: every rebuilt fragment, keyed by `node_id`.
    """
    fragments: dict[str, Fragment] = {}
    for event in read_all(log_path):
        if project_id is not None and event.project_id != project_id:
            continue
        current = fragments.get(event.node_id)
        fragments[event.node_id] = (
            _fresh_fragment(event) if current is None else _apply(current, event)
        )

    for fragment in fragments.values():
        write_fragment(fragment, sessions_dir)

    return fragments
