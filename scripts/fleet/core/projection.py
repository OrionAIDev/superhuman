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

**Write-boundary invariant (HARDEN #3, GPT-5 review):** `core/events.append`
is the *sole enforced write entry* for the log — schema validation and
ownership enforcement both live there. `rebuild()` trusts that invariant: it
sources only from `read_all(log_path)` (the log itself, which nothing can
enter without having passed `append`'s check when written) and does not
re-check ownership per replayed event. `project_event()` cannot make that
same assumption — it accepts an arbitrary `Event`, not necessarily one that
came through `append` — so it re-checks ownership itself before writing a
fragment, closing what would otherwise be a back door around `append`'s
enforcement.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ValidationError
from .events import read_all
from .ownership import assert_writer_may
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

    HARDEN #3 (GPT-5 review): `core/events.append` is the sole *enforced*
    write entry — it is where `validate_event` and `assert_writer_may` run.
    This function takes an arbitrary `Event`, not necessarily one that
    passed through `append`, so it re-checks ownership before writing
    anything. For the intended call pattern (an event just returned by
    `append`, or one read back via `read_all` — which only ever returns
    already-validated events), this re-check is a cheap no-op, since the
    event already passed the same check once. It exists specifically so a
    directly-constructed, ownership-violating `Event` handed to this
    function cannot materialize a forged fragment as a back door around
    `append`'s enforcement. `rebuild()` below intentionally does *not*
    repeat this check on every replayed event — it sources exclusively from
    `read_all(log_path)`, i.e. the log itself, and nothing can get into that
    log without having passed `append`'s check when it was written; a
    caller with direct filesystem write access to the log file bypasses
    every in-process check by definition and is out of this scope.

    Args:
        event: the event to project. Must already be validated/appended.
        sessions_dir: the directory holding per-session fragment files.

    Returns:
        Fragment: the fragment after applying `event`, already written to
        disk. A corrupt existing fragment is treated as absent rather than
        raised — the same non-fatal-corruption guarantee `rebuild()` gives,
        applied to the incremental path.

    Raises:
        OwnershipError: if `event.writer_role` may not write `event.type`
            or one of the owned fields present in `event.payload` (FR-8).
            Nothing is written.
    """
    assert_writer_may(event.type, event.writer_role)
    for field in event.payload:
        assert_writer_may(field, event.writer_role)

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
