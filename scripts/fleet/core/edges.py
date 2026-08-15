"""Typed dependency edges + in-memory digraph + graph algorithms (Decision B, CC-3/FR-4, G3 comment 2).

An edge is `(src, type, dst, source, evidence)`: `type` is one of the four
namespaced kinds (`contends-for`, `feeds-into`, `serves`, `blocked-by`),
`source` is `"declared"` (human/PM) or `"derived"` (auto, from git + phase
facts), and `evidence` is a small dict recording *why* the edge exists (e.g.
`{"branch": ...}`). Edges are persisted as events (`edge_declared` /
`edge_derived`), exactly like every other fact in this package — there is no
separate edge store. `blocked-by` and `feeds-into` are the **ordering** edge
types (they carry precedence); `contends-for` and `serves` never gate on
cycles.

**Precedence direction.** Both ordering edge types are folded into one
internal "u must happen before v" precedence relation so cycle detection and
the graph algorithms below operate over a single consistent DAG:
`feeds-into(src, dst)` already reads as precedence order (`src` precedes
`dst`); `blocked-by(src, dst)` means `src` is blocked *by* `dst`, i.e. `dst`
must resolve first — the reverse of the edge's own direction. See
`_precedence_pair`.

**Cycle handling (FR-4).** `add_edge()` checks a *candidate* ordering edge
against the current resolved graph using `core.events.append`'s
`precondition` hook — evaluated under the log lock, against the fresh log,
mirroring `handoff.self_register`'s own use of the same mechanism. If the
candidate would close a cycle, a `cycle_flagged` event is appended **instead
of** the edge event; the edge itself is never persisted. `resolve_graph()`
additionally re-derives the same exclusion independently while replaying:
it tracks the ordering precedence pairs it has accepted so far and skips any
edge that would close a cycle against *that* replay-so-far state, not just
edges added through `add_edge()`. Under normal operation (every edge going
through `add_edge()`) this is redundant with the write-time check, since a
cycle-closing ordering edge is never actually written as `edge_declared`/
`edge_derived` in the first place — but it makes "never silently store a
cycle" a property of *reading* the log, not just of one write path, which
matches `core/projection.py`'s own "fragments are a rebuildable projection,
never independent truth" philosophy applied to the edge graph.

**`derive_edges` signature (chosen, documented per the Chunk 4 brief).**
DESIGN's component table names the intent as `derive_edges(git_facts,
phase_state)`, but `core/edges.py` may import nothing from `adapter/`
(NFR-2) — so it cannot take `adapter.base.GitFacts` directly, and deriving
`contends-for`/`feeds-into` is inherently a *pairwise* comparison across
every known session, not a single git-facts-plus-phase-state pair. This
module instead defines a small core-local record, `SessionGitSnapshot`
(plain primitives only — `str | None` fields), and `derive_edges()` takes an
iterable of them: one snapshot per known session, built by the
adapter/CLI layer from `adapter.git_facts()` + whatever phase state it has,
never imported here. Concretely:

- `contends-for`: two snapshots share a non-empty `branch` **or** a
  non-empty `worktree` (Decision B) — both facts are already persisted in
  every `session_registered` event's payload (`core/events.py` /
  `cli.build_session_registered_event`), so this fires from real data today.
- `feeds-into`: two snapshots share a non-empty `merge_base` (Decision B's
  "shares a merge-base" clause). DESIGN's other clause — "A's PR merges
  before B's, or B branched from A" — needs data this manifest does not
  persist (no PR-merge-timestamp tracking, no branch-tip ancestry check).
  Absent that, this derivation uses `registered_at` (each session's own
  `session_registered` timestamp, i.e. real persisted data) as the ordering
  proxy: the earlier-registered session is taken to feed into the later one.
  This is a documented interpretation, not DESIGN's literal PR-merge-order
  signal — flagged as a design call in the Chunk 4 report, not a schema gap.
- `serves`: DESIGN's rule needs "the issue ref captured at register/handoff
  time" mapped to "a roadmap issue node" — **neither exists yet**: no event
  payload or `Fragment` field records an issue/phase reference for a
  session, and there is no roadmap-issue node-id scheme in `core/nodes.py`.
  `SessionGitSnapshot.issue_ref` and the derivation branch below exist so the
  edge *type* and its wiring are real and tested, but with every real caller
  leaving `issue_ref=None` (nothing populates it), zero `serves` edges are
  ever derived from actual manifest data today. This is the schema gap the
  Chunk 4 brief asks to surface as DONE_WITH_CONCERNS rather than invent a
  field for.

**Graph representation.** `DependencyGraph` is a lightweight immutable
wrapper around a tuple of `Edge`s (adjacency lists are built on demand inside
each algorithm, not cached) — stdlib only, no graph library, per Decision B.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from .errors import PreconditionUnmet
from .events import append, read_all
from .schema import Event

#: The four typed edge kinds (Decision B).
EDGE_TYPES: Final[frozenset[str]] = frozenset(
    {"contends-for", "feeds-into", "serves", "blocked-by"}
)

#: Edge types that carry precedence; cycle detection and the precedence-DAG
#: algorithms below run only on these (Decision B / G3 comment 2).
ORDERING_EDGE_TYPES: Final[frozenset[str]] = frozenset({"blocked-by", "feeds-into"})

#: Edge provenance classes.
EDGE_SOURCES: Final[frozenset[str]] = frozenset({"declared", "derived"})


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix.

    Returns:
        str: e.g. `"2026-08-14T12:00:00.000000Z"`.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class Edge:
    """One typed, namespaced dependency edge.

    Attributes:
        src: the edge's source node id.
        type: one of `EDGE_TYPES`.
        dst: the edge's destination node id (or, for `serves`, an issue
            reference — see the module docstring's `serves` gap note).
        source: `"declared"` (human/PM) or `"derived"` (auto).
        evidence: why this edge exists, e.g. `{"branch": "feature/x"}`.
    """

    src: str
    type: str
    dst: str
    source: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AddEdgeResult:
    """The outcome of `add_edge()`.

    Attributes:
        status: `"added"` (a new `edge_declared`/`edge_derived` event was
            appended), `"deduped"` (an event with this edge's idempotency
            key already existed — no-op, not an error), or `"cycle_flagged"`
            (the candidate would close a cycle on the ordering edge types; a
            `cycle_flagged` event was appended instead and the edge itself
            was never persisted — FR-4).
        edge: for `"added"` and `"cycle_flagged"`, the candidate `Edge`
            (nothing else was ever persisted for it). For `"deduped"`, the
            **already-persisted** edge for this `(src, type, dst)` — its
            `source`/`evidence` are whatever the pre-existing event actually
            stored, which may differ from the caller's own candidate values
            (those were never written); the candidate is used only as a
            fallback if the persisted edge cannot be found (G5 F2b).
    """

    status: str
    edge: Edge


def _precedence_pair(edge_type: str, src: str, dst: str) -> tuple[str, str]:
    """Fold one ordering edge into a `(u, v)` "u must happen before v" pair.

    Args:
        edge_type: one of `ORDERING_EDGE_TYPES`.
        src: the edge's source node id.
        dst: the edge's destination node id.

    Returns:
        tuple[str, str]: `(src, dst)` for `feeds-into` (already precedence
        order); `(dst, src)` for `blocked-by` (reversed — see module
        docstring "Precedence direction").

    Raises:
        ValueError: if `edge_type` is not an ordering edge type.
    """
    if edge_type == "feeds-into":
        return src, dst
    if edge_type == "blocked-by":
        return dst, src
    raise ValueError(f"{edge_type!r} is not an ordering edge type ({sorted(ORDERING_EDGE_TYPES)})")


def _reachable_from(adjacency: dict[str, list[str]], start: str) -> set[str]:
    """Return every node reachable from `start` via `adjacency`.

    Args:
        adjacency: `node -> [direct successors]`.
        start: the node to search from.

    Returns:
        set[str]: nodes reachable from `start`, excluding `start` itself
        unless a path loops back to it (i.e. a cycle through `start`).
    """
    seen: set[str] = set()
    stack = list(adjacency.get(start, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adjacency.get(node, ()))
    return seen


def _would_close_cycle(existing_pairs: Iterable[tuple[str, str]], u: str, v: str) -> bool:
    """Return whether adding precedence edge `u -> v` would close a cycle.

    Standard DAG-plus-one-edge check: adding `u -> v` closes a cycle iff `v`
    can already reach `u`.

    Args:
        existing_pairs: the precedence pairs already accepted.
        u: the candidate edge's "before" node.
        v: the candidate edge's "after" node.

    Returns:
        bool: True iff `u` is reachable from `v` in `existing_pairs`.
    """
    adjacency: dict[str, list[str]] = defaultdict(list)
    for a, b in existing_pairs:
        adjacency[a].append(b)
    return u in _reachable_from(adjacency, v)


def _no_cycle_precondition(u: str, v: str) -> Callable[[list[Event]], bool]:
    """Build an `append()` precondition rejecting a cycle-closing edge (FR-4).

    Args:
        u: the candidate edge's "before" node (see `_precedence_pair`).
        v: the candidate edge's "after" node.

    Returns:
        Callable[[list[Event]], bool]: a predicate over the fresh event list
        `core.events.append` reads under the lock — `False` (refuse the
        write) iff the candidate `u -> v` would close a cycle against the
        ordering edges already in that fresh list.
    """

    def _check(existing: list[Event]) -> bool:
        return not _would_close_cycle(_ordering_pairs_from_events(existing), u, v)

    return _check


def _ordering_pairs_from_events(events: Iterable[Event]) -> list[tuple[str, str]]:
    """Extract every ordering-edge precedence pair recorded in `events`.

    Args:
        events: event-log entries, e.g. as read by `core.events.read_all`.

    Returns:
        list[tuple[str, str]]: one `(u, v)` precedence pair per
        `edge_declared`/`edge_derived` event whose `edge_type` is an
        ordering type. A malformed/unrecognized payload — including a
        self edge (`dst == node_id`), which can never be a real
        dependency — is skipped rather than raised (matching
        `core.events.read_all`'s own tolerance; G5 F3).
    """
    pairs: list[tuple[str, str]] = []
    for event in events:
        if event.type not in ("edge_declared", "edge_derived"):
            continue
        edge_type = event.payload.get("edge_type")
        dst = event.payload.get("dst")
        if (
            edge_type not in ORDERING_EDGE_TYPES
            or not isinstance(dst, str)
            or not dst
            or dst == event.node_id
        ):
            continue  # malformed/self edge — skip defensively, never raise on read (G5 F3)
        pairs.append(_precedence_pair(edge_type, event.node_id, dst))
    return pairs


def add_edge(
    src: str,
    edge_type: str,
    dst: str,
    *,
    source: str,
    evidence: dict[str, Any] | None,
    project_id: str,
    writer_role: str,
    log_path: Path | str,
) -> AddEdgeResult:
    """Record one dependency edge, refusing anything that would close a cycle (FR-4).

    Appends an `edge_declared` (`source="declared"`) or `edge_derived`
    (`source="derived"`) event via `core.events.append`, idempotency key
    `edge:<src>:<type>:<dst>` (Decision F). For an ordering edge type
    (`ORDERING_EDGE_TYPES`), the write is additionally guarded by an
    `append()` `precondition` that resolves the current ordering graph
    *under the lock, against the fresh log* and refuses the write if the
    candidate would close a cycle — the same pattern `handoff.self_register`
    uses for its own atomic terminal-state check. When that happens, a
    `cycle_flagged` event is appended instead and the edge is never
    persisted.

    Args:
        src: the edge's source node id.
        edge_type: one of `EDGE_TYPES`.
        dst: the edge's destination node id.
        source: `"declared"` or `"derived"` (`EDGE_SOURCES`).
        evidence: why this edge exists; stored on the event payload. `None`
            is treated as `{}`.
        project_id: the owning project's stable id.
        writer_role: a role name, never a model/vendor string (NFR-6).
            Edges are not an owned field (`core.schema.FIELD_OWNERS` has no
            entry for any edge type), so any valid role may write one.
        log_path: path to the project's event log.

    Returns:
        AddEdgeResult: see the class docstring for the status vocabulary.

    Raises:
        ValueError: if `edge_type`/`source` is not recognized, or `src`/
            `dst` is empty or identical (an edge cannot connect a node to
            itself).
        ValidationError: if the built event fails schema validation.
        OwnershipError: if `writer_role` may not write `event_type` (edges
            are unowned today, so this never fires for the edge types
            themselves, but is not suppressed either).
        LockTimeoutError: if the shared log lock could not be acquired in
            time. Nothing was written.
    """
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"unknown edge type: {edge_type!r} (expected one of {sorted(EDGE_TYPES)})")
    if source not in EDGE_SOURCES:
        raise ValueError(f"unknown edge source: {source!r} (expected one of {sorted(EDGE_SOURCES)})")
    if not src or not dst:
        raise ValueError("src and dst must be non-empty node ids")
    if src == dst:
        raise ValueError(f"an edge cannot connect a node to itself ({src!r})")

    evidence_dict = dict(evidence or {})
    candidate = Edge(src=src, type=edge_type, dst=dst, source=source, evidence=evidence_dict)
    event_type = "edge_declared" if source == "declared" else "edge_derived"

    event_dict: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": f"edge:{src}:{edge_type}:{dst}",
        "ts": _now_iso(),
        "type": event_type,
        "project_id": project_id,
        "node_id": src,
        "writer_role": writer_role,
        "payload": {"dst": dst, "edge_type": edge_type, "evidence": evidence_dict},
    }

    precondition = None
    if edge_type in ORDERING_EDGE_TYPES:
        u, v = _precedence_pair(edge_type, src, dst)
        precondition = _no_cycle_precondition(u, v)

    try:
        appended = append(log_path, event_dict, precondition=precondition)
    except PreconditionUnmet:
        _flag_cycle(
            src,
            edge_type,
            dst,
            evidence=evidence_dict,
            project_id=project_id,
            writer_role=writer_role,
            log_path=log_path,
        )
        return AddEdgeResult(status="cycle_flagged", edge=candidate)

    if appended is None:
        persisted = _find_persisted_edge(log_path, src, edge_type, dst)
        return AddEdgeResult(status="deduped", edge=persisted if persisted is not None else candidate)

    return AddEdgeResult(status="added", edge=candidate)


def _find_persisted_edge(
    log_path: Path | str, src: str, edge_type: str, dst: str
) -> Edge | None:
    """Look up the actually-persisted edge for one `(src, type, dst)` triple.

    Used by `add_edge()`'s dedupe branch (G5 F2b): when `append()` reports
    an idempotency-key collision, the caller's own candidate `source`/
    `evidence` were never written — only the pre-existing event's were. This
    scans the log for that event so the dedupe result can report what was
    actually persisted.

    Args:
        log_path: path to the project's event log.
        src: the edge's source node id.
        edge_type: one of `EDGE_TYPES`.
        dst: the edge's destination node id.

    Returns:
        Edge | None: the persisted edge (`source`/`evidence` taken from the
        matching event), or `None` if no `edge_declared`/`edge_derived`
        event with idempotency key `edge:<src>:<type>:<dst>` is found —
        should not happen on a genuine dedupe, but handled defensively so
        `add_edge()` can fall back to the candidate rather than raise.
    """
    key = f"edge:{src}:{edge_type}:{dst}"
    for event in read_all(log_path):
        if event.type not in ("edge_declared", "edge_derived"):
            continue
        if event.idempotency_key != key:
            continue
        source = "declared" if event.type == "edge_declared" else "derived"
        raw_evidence = event.payload.get("evidence")
        evidence = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
        return Edge(src=src, type=edge_type, dst=dst, source=source, evidence=evidence)
    return None


def _flag_cycle(
    src: str,
    edge_type: str,
    dst: str,
    *,
    evidence: dict[str, Any],
    project_id: str,
    writer_role: str,
    log_path: Path | str,
) -> Event | None:
    """Append a `cycle_flagged` event for a rejected cycle-closing candidate.

    Args:
        src: the candidate edge's source node id.
        edge_type: the candidate edge's type.
        dst: the candidate edge's destination node id.
        evidence: the candidate edge's evidence dict.
        project_id: the owning project's stable id.
        writer_role: a role name, never a model/vendor string (NFR-6).
        log_path: path to the project's event log.

    Returns:
        Event | None: the appended `cycle_flagged` event, or `None` if an
        identical flag (same `src`/`edge_type`/`dst`) was already recorded
        (idempotency-key dedupe — a repeat attempt to add the same
        cycle-closing edge flags the same fact twice, not two facts).
    """
    event_dict: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": f"cycle:{src}:{edge_type}:{dst}",
        "ts": _now_iso(),
        "type": "cycle_flagged",
        "project_id": project_id,
        "node_id": src,
        "writer_role": writer_role,
        "payload": {
            "dst": dst,
            "edge_type": edge_type,
            "evidence": evidence,
            "reason": "would close a cycle on the ordering edge types (blocked-by/feeds-into)",
        },
    }
    return append(log_path, event_dict)


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """An immutable, in-memory digraph of every currently-valid dependency edge.

    Built by `resolve_graph()`; never constructed by mutating an existing
    instance. Adjacency lists are computed on demand inside each algorithm
    below rather than cached, since a `DependencyGraph` is typically built
    once per query and discarded (Decision B: "no weighting/centrality/
    pathfinding until a phase actually needs them" — same scope discipline
    applies to premature caching).

    Attributes:
        edges: every edge currently in effect, deduplicated by
            `(src, type, dst)`. Never includes an ordering edge that would
            close a cycle (FR-4) — see `resolve_graph`.
    """

    edges: tuple[Edge, ...]

    def _nodes(self) -> set[str]:
        """Return every node id touched by at least one edge.

        Returns:
            set[str]: the union of every edge's `src` and `dst`.
        """
        nodes: set[str] = set()
        for e in self.edges:
            nodes.add(e.src)
            nodes.add(e.dst)
        return nodes

    def _ordering_edges(self) -> tuple[Edge, ...]:
        """Return only the edges whose type carries precedence.

        Returns:
            tuple[Edge, ...]: the subset of `self.edges` with
            `type in ORDERING_EDGE_TYPES`.
        """
        return tuple(e for e in self.edges if e.type in ORDERING_EDGE_TYPES)

    def _precedence_pairs(self) -> list[tuple[str, str]]:
        """Return every ordering edge folded into a `(u, v)` precedence pair.

        Returns:
            list[tuple[str, str]]: see `_precedence_pair`.
        """
        return [_precedence_pair(e.type, e.src, e.dst) for e in self._ordering_edges()]

    def _precedence_adjacency(self) -> dict[str, list[str]]:
        """Return the precedence DAG's forward adjacency list.

        Returns:
            dict[str, list[str]]: `u -> [v, ...]` for every accepted
            precedence pair.
        """
        adjacency: dict[str, list[str]] = defaultdict(list)
        for u, v in self._precedence_pairs():
            adjacency[u].append(v)
        return adjacency

    def detect_cycles(self) -> list[tuple[str, ...]]:
        """DFS back-edge cycle detection over the ordering edge types (FR-4).

        A resolved graph built by `resolve_graph()` never actually contains
        a cycle (cycle-closing edges are excluded at write and read time
        alike) — this exists as the underlying primitive `add_edge()`'s
        precondition and `resolve_graph()`'s replay-time guard both build
        on, and as a directly-testable algorithm in its own right (Decision
        B's algorithm set) against a `DependencyGraph` constructed some
        other way (e.g. a test fixture).

        Implemented as an explicit-stack (iterative) DFS rather than a
        recursive one, so a long ordering chain (thousands of `feeds-into`
        edges) cannot blow Python's recursion limit (G5 F5) — the
        white/gray/black coloring, deterministic sorted node iteration
        order, and returned cycle shape are otherwise unchanged from the
        recursive formulation.

        Returns:
            list[tuple[str, ...]]: one tuple of node ids per cycle found (the
            cycle's nodes in traversal order, starting and ending at the
            back-edge's target); empty if the ordering-edge subgraph is
            acyclic. Traversal order is deterministic (nodes visited in
            sorted order) so results are reproducible across runs.
        """
        adjacency = self._precedence_adjacency()
        white, gray, black = 0, 1, 2
        color: dict[str, int] = defaultdict(lambda: white)
        cycles: list[tuple[str, ...]] = []
        path: list[str] = []

        for start in sorted(self._nodes()):
            if color[start] != white:
                continue

            # Each stack frame is (node, iterator over its neighbors),
            # mirroring one level of the recursive call stack. Breaking out
            # of the inner `for` after pushing a white neighbor's frame —
            # then resuming the same (already-partially-consumed) iterator
            # once that neighbor's subtree finishes — reproduces the exact
            # depth-first visit order the recursive version had.
            color[start] = gray
            path.append(start)
            stack: list[tuple[str, Iterator[str]]] = [(start, iter(adjacency.get(start, ())))]

            while stack:
                node, neighbors = stack[-1]
                descended = False
                for nxt in neighbors:
                    if color[nxt] == gray:
                        idx = path.index(nxt)
                        cycles.append(tuple(path[idx:]))
                    elif color[nxt] == white:
                        color[nxt] = gray
                        path.append(nxt)
                        stack.append((nxt, iter(adjacency.get(nxt, ()))))
                        descended = True
                        break
                if not descended:
                    stack.pop()
                    path.pop()
                    color[node] = black

        return cycles

    def transitive_blockers(self, node: str) -> set[str]:
        """Return every node that transitively blocks `node`.

        Reachability over the reversed precedence DAG: every ancestor of
        `node` (a node that must happen before it, directly or through a
        chain of ordering edges) — "what transitively blocks X".

        Args:
            node: the node to query.

        Returns:
            set[str]: transitive blockers of `node`; empty if nothing
            blocks it (including if `node` is not in the graph at all).
        """
        reverse_adjacency: dict[str, list[str]] = defaultdict(list)
        for u, v in self._precedence_pairs():
            reverse_adjacency[v].append(u)
        return _reachable_from(reverse_adjacency, node)

    def ready_set(self) -> set[str]:
        """Return the topological frontier — nodes with no unresolved blocker.

        Every node in the graph (from any edge type, not only ordering
        edges) that has zero *incoming* precedence edges: nothing in this
        resolved graph must happen before it, i.e. "what can start now".
        This is a purely structural computation over the resolved graph, per
        Decision B ("pure functions over the resolved graph") — it has no
        notion of `done_level` (Chunk 5's separate state machine).

        Returns:
            set[str]: the ready node ids.
        """
        has_incoming = {v for _, v in self._precedence_pairs()}
        return self._nodes() - has_incoming

    def transitive_reduction(self) -> tuple[Edge, ...]:
        """Drop ordering edges implied by a longer path, for a legible view.

        Only ordering edges (`ORDERING_EDGE_TYPES`) are candidates for
        reduction — `contends-for`/`serves` are not a composable precedence
        relation, so they pass through unchanged (Decision B). An ordering
        edge folding to precedence pair `(u, v)` is dropped iff `v` remains
        reachable from `u` using every *other* accepted precedence pair
        (i.e. a path of length >= 2 already implies it).

        Returns:
            tuple[Edge, ...]: every non-ordering edge, plus every ordering
            edge whose precedence pair is not implied by a longer path.
            Order is not significant.
        """
        ordering = self._ordering_edges()
        non_ordering = tuple(e for e in self.edges if e.type not in ORDERING_EDGE_TYPES)

        by_pair: dict[tuple[str, str], list[Edge]] = defaultdict(list)
        for e in ordering:
            by_pair[_precedence_pair(e.type, e.src, e.dst)].append(e)

        full_adjacency: dict[str, list[str]] = defaultdict(list)
        for u, v in by_pair:
            full_adjacency[u].append(v)

        kept_edges: list[Edge] = []
        for (u, v), edges_for_pair in by_pair.items():
            reduced_adjacency = {
                node: [nxt for nxt in neighbors if not (node == u and nxt == v)]
                for node, neighbors in full_adjacency.items()
            }
            if v in _reachable_from(reduced_adjacency, u):
                continue  # implied by a longer path — drop
            kept_edges.extend(edges_for_pair)

        return tuple(kept_edges) + non_ordering

    def related_cluster(self, node: str) -> set[str]:
        """Return the weakly-connected component containing `node`.

        Ignores edge direction and considers every edge type (Decision B /
        G3 comment 2) — the structural "related sessions" grouping,
        deliberately distinct from `project_id` (which groups by ownership,
        not dependency structure).

        Args:
            node: the node to query.

        Returns:
            set[str]: every node reachable from `node` via any edge, in
            either direction, including `node` itself. `{node}` if `node`
            has no edges in this graph.
        """
        undirected_adjacency: dict[str, list[str]] = defaultdict(list)
        for e in self.edges:
            undirected_adjacency[e.src].append(e.dst)
            undirected_adjacency[e.dst].append(e.src)
        seen: set[str] = {node}
        stack = list(undirected_adjacency.get(node, ()))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(undirected_adjacency.get(current, ()))
        return seen


def resolve_graph(log_path: Path | str) -> DependencyGraph:
    """Replay the log's edge events into a `DependencyGraph`.

    Pure/rebuildable, same philosophy as `core/projection.py`: this reads
    only `events.jsonl` (the source of truth), never any cached graph. See
    the module docstring's "Cycle handling" note for why a cycle-closing
    ordering edge is excluded here independently of `add_edge()`'s own
    write-time guard, not merely trusted to never have been written.

    Args:
        log_path: path to the project's event log.

    Returns:
        DependencyGraph: every currently-valid edge, deduplicated by
        `(src, type, dst)` (later duplicate events for the same triple are
        a no-op — `core.events.append`'s idempotency dedupe already prevents
        them from existing at all under normal operation). A self edge
        (`dst == node_id`), however it reached the log, is skipped
        defensively (G5 F3) — never a real dependency.
    """
    edges: dict[tuple[str, str, str], Edge] = {}
    accepted_pairs: list[tuple[str, str]] = []

    for event in read_all(log_path):
        if event.type not in ("edge_declared", "edge_derived"):
            continue
        edge_type = event.payload.get("edge_type")
        dst = event.payload.get("dst")
        src = event.node_id
        if edge_type not in EDGE_TYPES or not isinstance(dst, str) or not dst or dst == src:
            continue  # malformed/legacy/self-edge payload — skip defensively, never raise on read (G5 F3)
        source = "declared" if event.type == "edge_declared" else "derived"
        raw_evidence = event.payload.get("evidence")
        evidence = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}

        if edge_type in ORDERING_EDGE_TYPES:
            u, v = _precedence_pair(edge_type, src, dst)
            if _would_close_cycle(accepted_pairs, u, v):
                continue  # never let a cycle-closing edge into the resolved graph (FR-4)
            accepted_pairs.append((u, v))

        edges[(src, edge_type, dst)] = Edge(
            src=src, type=edge_type, dst=dst, source=source, evidence=evidence
        )

    return DependencyGraph(edges=tuple(edges.values()))


@dataclass(frozen=True, slots=True)
class SessionGitSnapshot:
    """Primitive git/phase facts for one known session (`derive_edges`' input).

    A core-local record — plain primitives only (NFR-2: `core/` may not
    import `adapter.base.GitFacts`/`SessionInfo`) — built by the caller
    (adapter/CLI layer) from whatever it can observe about one session.

    Attributes:
        node_id: the session's node id.
        branch: the checked-out branch, or `None` if unknown. Matches the
            `branch` field already persisted in every `session_registered`
            event's payload.
        worktree: the resolved working-tree root path, or `None` if unknown.
        merge_base: merge-base sha against the default branch, or `None`
            (mirrors `adapter.base.GitFacts.merge_base`).
        registered_at: this session's `session_registered` event timestamp
            (ISO-8601 string), or `None` if unknown. Used as the ordering
            proxy for `feeds-into` derivation — see the module docstring.
        issue_ref: the roadmap issue/phase reference this session owns, or
            `None`. Not persisted anywhere in the manifest today (see the
            module docstring's `serves` gap note) — every real caller leaves
            this `None`.
    """

    node_id: str
    branch: str | None = None
    worktree: str | None = None
    merge_base: str | None = None
    registered_at: str | None = None
    issue_ref: str | None = None


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    """Return `(a, b)` in a stable, deterministic order.

    Used for the symmetric `contends-for` relation so deriving it from the
    same pair of sessions always produces the same `(src, dst)` — and
    therefore the same idempotency key — regardless of iteration order.

    Args:
        a: one node id.
        b: the other node id.

    Returns:
        tuple[str, str]: `(a, b)` if `a <= b`, else `(b, a)`.
    """
    return (a, b) if a <= b else (b, a)


def _unordered_pairs(
    items: list[SessionGitSnapshot],
) -> Iterable[tuple[SessionGitSnapshot, SessionGitSnapshot]]:
    """Yield every unordered pair of distinct items exactly once.

    Args:
        items: the snapshots to pair up.

    Yields:
        tuple[SessionGitSnapshot, SessionGitSnapshot]: each unordered pair.
    """
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            yield a, b


def derive_edges(
    sessions: Iterable[SessionGitSnapshot],
    *,
    project_id: str,
    writer_role: str,
    log_path: Path | str,
) -> list[AddEdgeResult]:
    """Derive `contends-for`/`feeds-into`/`serves` edges from known session facts (Decision B).

    Every derived edge is written via `add_edge(source="derived", ...)`, so
    it gets the same idempotency dedupe and cycle protection as a declared
    edge. See the module docstring for the exact per-type rule and the
    `serves` schema gap.

    Two snapshots that share the same `node_id` are the same session
    observed twice (e.g. a stale/duplicate registration), never a real
    dependency between two sessions — such a pair is skipped defensively
    for every derivation rule (`contends-for`, `feeds-into`), and a
    snapshot whose `issue_ref` equals its own `node_id` is likewise skipped
    for `serves`, rather than reaching `add_edge()`'s `src == dst` guard and
    raising mid-loop after earlier pairs in the same call were already
    persisted (G5 F1).

    Args:
        sessions: primitive git/phase facts for every session to consider,
            as `SessionGitSnapshot`s (core-local, no adapter import).
        project_id: the owning project's stable id.
        writer_role: a role name, never a model/vendor string (NFR-6).
        log_path: path to the project's event log.

    Returns:
        list[AddEdgeResult]: one result per edge this call attempted to
        write (added, deduped, or cycle-flagged) — never includes a
        non-derivation (a pair that didn't match any rule, or a same-
        `node_id` pair/self `serves` reference, produces no entry at all).
    """
    snapshots = list(sessions)
    results: list[AddEdgeResult] = []

    for a, b in _unordered_pairs(snapshots):
        if a.node_id == b.node_id:
            continue  # same session counted twice — never a real dependency (G5 F1)
        if a.branch and b.branch and a.branch == b.branch:
            src, dst = _canonical_pair(a.node_id, b.node_id)
            results.append(
                add_edge(
                    src,
                    "contends-for",
                    dst,
                    source="derived",
                    evidence={"branch": a.branch},
                    project_id=project_id,
                    writer_role=writer_role,
                    log_path=log_path,
                )
            )
        if a.worktree and b.worktree and a.worktree == b.worktree:
            src, dst = _canonical_pair(a.node_id, b.node_id)
            results.append(
                add_edge(
                    src,
                    "contends-for",
                    dst,
                    source="derived",
                    evidence={"worktree": a.worktree},
                    project_id=project_id,
                    writer_role=writer_role,
                    log_path=log_path,
                )
            )

    for a, b in _unordered_pairs(snapshots):
        if a.node_id == b.node_id:
            continue  # same session counted twice — never a real dependency (G5 F1)
        if not (a.merge_base and b.merge_base and a.merge_base == b.merge_base):
            continue
        if not a.registered_at or not b.registered_at or a.registered_at == b.registered_at:
            continue  # no resolvable order — see module docstring's feeds-into note
        earlier, later = (
            (a, b) if a.registered_at < b.registered_at else (b, a)
        )
        results.append(
            add_edge(
                earlier.node_id,
                "feeds-into",
                later.node_id,
                source="derived",
                evidence={"merge_base": a.merge_base},
                project_id=project_id,
                writer_role=writer_role,
                log_path=log_path,
            )
        )

    for s in snapshots:
        if s.issue_ref and s.issue_ref == s.node_id:
            continue  # a session cannot serve its own node id (G5 F1)
        if s.issue_ref:
            results.append(
                add_edge(
                    s.node_id,
                    "serves",
                    s.issue_ref,
                    source="derived",
                    evidence={"issue_ref": s.issue_ref},
                    project_id=project_id,
                    writer_role=writer_role,
                    log_path=log_path,
                )
            )

    return results
