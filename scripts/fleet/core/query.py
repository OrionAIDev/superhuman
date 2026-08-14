"""Read-side queries over fragments/graph (FR-7 backing, conformance query).

Read-only: nothing here ever writes the manifest. `list_sessions()` is fully
implemented in Chunk 1 (it is what proves `project_id` equality-grouping,
G3-1). `edges_of()` and `stale_handoffs()` are documented stubs — their real
implementations land with `core/edges.py` (Chunk 4) and `handoff.py`
(Chunk 3) respectively; Chunk 1 has no edge or handoff data to query yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def stale_handoffs(now: Any) -> list[dict[str, Any]]:
    """Return `awaiting-launch` handoff rows past their expiry.

    Args:
        now: the reference time to compare handoff expiry against.

    Raises:
        NotImplementedError: `handoff.py` (Chunk 3) has not landed yet. There
            is no handoff-expiry data for Chunk 1 to query.
    """
    raise NotImplementedError("stale_handoffs() is implemented in Chunk 3 (handoff.py)")
