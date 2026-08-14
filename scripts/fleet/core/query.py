"""Read-side queries over fragments/graph (FR-7 backing, conformance query).

Read-only: nothing here ever writes the manifest. `list_sessions()` is fully
implemented in Chunk 1 (it is what proves `project_id` equality-grouping,
G3-1). `edges_of()` is a documented stub — its real implementation lands
with `core/edges.py` (Chunk 4); Chunk 1 has no edge data to query yet.

`stale_handoffs()` lands in this chunk (Chunk 3). It answers "which
`awaiting-launch` rows are past expiry" from the manifest **alone** — no
adapter, no session enumeration, no profile/config loading (FR-3): it takes
an already-resolved `now` and `expiry_seconds` and joins two manifest
sources only — the current fragments (for `lifecycle`) and the log (for the
`handoff_emitted` event's `ts`, since `Fragment` carries no timestamp field
of its own). Resolving `expiry_seconds` from `~/.superhuman/profile.yaml`
(NFR-5 — operator-specific, config-driven, not hardcoded) is `handoff.py`'s
job (`handoff.stale_report()`), not this module's — keeping this a pure
function of its explicit inputs, with no profile/config dependency of its
own.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import read_all
from .schema import Fragment
from .store import iter_fragments


def list_sessions(sessions_dir: Path | str, project_id: str | None = None) -> list[Fragment]:
    """List every tracked session, optionally filtered to one project.

    Grouping is by `project_id` **equality** — never a `<slug>` substring
    match (G3-1) — so two projects whose slugs happen to share text never
    bleed into each other's session list.

    Args:
        sessions_dir: the directory holding per-session fragment files.
        project_id: if given, only sessions with this exact `project_id` are
            returned; otherwise every tracked session is returned.

    Returns:
        list[Fragment]: the matching fragments. Corrupt fragments are
        skipped (best-effort read-only listing; see `core/store.iter_fragments`).
    """
    fragments = iter_fragments(sessions_dir)
    if project_id is None:
        return fragments
    return [f for f in fragments if f.project_id == project_id]


def edges_of(node_id: str) -> list[dict[str, Any]]:
    """Return the dependency edges touching `node_id`.

    Args:
        node_id: the node to query.

    Raises:
        NotImplementedError: `core/edges.py` (Chunk 4) has not landed yet.
            There is no edge data for Chunk 1 to query.
    """
    raise NotImplementedError("edges_of() is implemented in Chunk 4 (core/edges.py)")


def _parse_ts(ts: str) -> datetime:
    """Parse an event's `ts` field back into an aware UTC `datetime`.

    Args:
        ts: an ISO-8601 timestamp string, as produced by any writer in this
            package (all of which emit UTC, `Z`-suffixed or otherwise
            offset-bearing strings — `core.schema._ISO_8601_RE` is the
            format contract every stored `ts` already satisfies).

    Returns:
        datetime: an aware `datetime` in UTC. A naive result from
        `fromisoformat` (no offset in the string) is treated as UTC rather
        than the local zone, matching every writer's actual behavior.
    """
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stale_handoffs(
    log_path: Path | str,
    sessions_dir: Path | str,
    *,
    now: datetime,
    expiry_seconds: float,
) -> list[dict[str, Any]]:
    """Return `awaiting-launch` handoff rows older than `expiry_seconds`.

    Computed from the manifest alone (FR-3): the current fragments (for
    `lifecycle`) and the event log (for the originating `handoff_emitted`
    event's `ts`, which is where "how long has this been open" actually
    lives — `Fragment` has no timestamp field). No adapter, no session
    enumeration, no config/profile lookup — both `now` and `expiry_seconds`
    are supplied by the caller (`handoff.stale_report()` resolves the latter
    from `~/.superhuman/profile.yaml`, per NFR-5).

    A cancelled row is never "stale": `handoff_cancelled` flips `lifecycle`
    away from `"awaiting-launch"` (see `handoff.cancel()`), so it is excluded
    here by the same `lifecycle` filter that selects candidates at all — the
    same invariant `handoff.self_register()`'s fuzzy-match pool relies on.

    Args:
        log_path: path to the project's event log.
        sessions_dir: path to the project's fragment directory.
        now: the reference time to compare handoff age against. A naive
            value is treated as UTC.
        expiry_seconds: how old (in seconds) an `awaiting-launch` row must be
            to count as stale.

    Returns:
        list[dict[str, Any]]: one entry per stale row — `node_id`,
        `handoff_id`, `emitted_ts`, `age_seconds` — sorted by `node_id` for a
        deterministic, reproducible report. A row whose `handoff_emitted`
        event cannot be found (a fragment with no matching log entry — not
        expected in normal operation) is skipped rather than guessed at.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    awaiting = {
        f.node_id: f for f in iter_fragments(sessions_dir) if f.lifecycle == "awaiting-launch"
    }
    if not awaiting:
        return []

    emitted_by_node: dict[str, Any] = {}
    for event in read_all(log_path):
        if event.type == "handoff_emitted" and event.node_id in awaiting:
            emitted_by_node[event.node_id] = event

    stale: list[dict[str, Any]] = []
    for node_id, event in emitted_by_node.items():
        emitted_at = _parse_ts(event.ts)
        age_seconds = (now - emitted_at).total_seconds()
        if age_seconds > expiry_seconds:
            stale.append(
                {
                    "node_id": node_id,
                    "handoff_id": event.payload.get("handoff_id"),
                    "emitted_ts": event.ts,
                    "age_seconds": age_seconds,
                }
            )

    stale.sort(key=lambda row: row["node_id"])
    return stale
