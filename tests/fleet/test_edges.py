"""Tests for ``scripts.fleet.core.edges`` (PLAN.md Chunk 4).

Covers the acceptance criteria in ``docs/superhuman/session-tracking/PLAN.md``
Chunk 4: a derived branch-contention edge appears automatically with
``source=derived`` + evidence; a declared edge records ``source=declared``;
introducing a cycle on the ordering edge types is recorded as a
``cycle_flagged`` event, excluded from the resolved graph, and never
silently stored (FR-4); the graph algorithms (``ready_set``,
``transitive_blockers``, ``related_cluster``, ``transitive_reduction``,
``detect_cycles``) return correct results on a fixture DAG (G3-2); and
adding the same edge twice dedupes via the idempotency key.
"""

from __future__ import annotations

from pathlib import Path

from scripts.fleet.core.edges import (
    AddEdgeResult,
    DependencyGraph,
    Edge,
    SessionGitSnapshot,
    add_edge,
    derive_edges,
    resolve_graph,
)
from scripts.fleet.core.events import append, read_all

_PROJECT_ID = "proj-edges"
_WRITER_ROLE = "Project Manager"


def _log(tmp_path: Path) -> Path:
    return tmp_path / "events.jsonl"


class TestAddEdgeDeclaredVsDerived:
    """Acceptance criteria 1 & 2: source=derived+evidence, source=declared."""

    def test_derived_branch_contention_edge_appears_with_evidence(
        self, tmp_path: Path
    ) -> None:
        log_path = _log(tmp_path)
        sessions = [
            SessionGitSnapshot(node_id="node/a", branch="feature/x"),
            SessionGitSnapshot(node_id="node/b", branch="feature/x"),
        ]

        results = derive_edges(
            sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path
        )

        contends = [r for r in results if r.edge.type == "contends-for"]
        assert len(contends) == 1
        result = contends[0]
        assert result.status == "added"
        assert result.edge.source == "derived"
        assert result.edge.evidence == {"branch": "feature/x"}

        graph = resolve_graph(log_path)
        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert edge.type == "contends-for"
        assert edge.source == "derived"
        assert edge.evidence == {"branch": "feature/x"}
        assert {edge.src, edge.dst} == {"node/a", "node/b"}

    def test_derived_worktree_contention_edge_appears_with_evidence(
        self, tmp_path: Path
    ) -> None:
        log_path = _log(tmp_path)
        sessions = [
            SessionGitSnapshot(node_id="node/a", worktree="/repo/checkout"),
            SessionGitSnapshot(node_id="node/b", worktree="/repo/checkout"),
        ]

        results = derive_edges(
            sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path
        )

        assert len(results) == 1
        assert results[0].edge.evidence == {"worktree": "/repo/checkout"}
        assert results[0].edge.source == "derived"

    def test_no_derivation_when_sessions_share_nothing(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        sessions = [
            SessionGitSnapshot(node_id="node/a", branch="feature/x"),
            SessionGitSnapshot(node_id="node/b", branch="feature/y"),
        ]

        results = derive_edges(
            sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path
        )

        assert results == []
        assert resolve_graph(log_path).edges == ()

    def test_declared_edge_records_source_declared(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)

        result = add_edge(
            "node/a",
            "contends-for",
            "node/b",
            source="declared",
            evidence={"note": "PM says so"},
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
        )

        assert result.status == "added"
        assert result.edge.source == "declared"

        graph = resolve_graph(log_path)
        assert len(graph.edges) == 1
        assert graph.edges[0].source == "declared"
        assert graph.edges[0].evidence == {"note": "PM says so"}

        events = [e for e in read_all(log_path) if e.type == "edge_declared"]
        assert len(events) == 1


class TestIdempotency:
    """Adding the same edge twice dedupes (idempotency key), no duplicate."""

    def test_repeat_add_edge_dedupes(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        kwargs = dict(
            source="declared",
            evidence={"note": "same edge"},
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
        )

        first = add_edge("node/a", "contends-for", "node/b", **kwargs)
        second = add_edge("node/a", "contends-for", "node/b", **kwargs)

        assert first.status == "added"
        assert second.status == "deduped"

        graph = resolve_graph(log_path)
        assert len(graph.edges) == 1

        events = [e for e in read_all(log_path) if e.type == "edge_declared"]
        assert len(events) == 1

    def test_repeat_derive_edges_dedupes(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        sessions = [
            SessionGitSnapshot(node_id="node/a", branch="feature/x"),
            SessionGitSnapshot(node_id="node/b", branch="feature/x"),
        ]

        derive_edges(sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path)
        second_results = derive_edges(
            sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path
        )

        assert [r.status for r in second_results] == ["deduped"]
        assert len(resolve_graph(log_path).edges) == 1


class TestCycleHandling:
    """Acceptance criterion 3 (FR-4): cycle flagged, excluded, never stored."""

    def test_cycle_closing_edge_is_flagged_not_stored(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)

        first = add_edge(
            "A",
            "blocked-by",
            "B",
            source="declared",
            evidence={},
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
        )
        assert first.status == "added"

        second = add_edge(
            "B",
            "blocked-by",
            "A",
            source="declared",
            evidence={},
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
        )
        assert second.status == "cycle_flagged"

        # The flag was emitted...
        cycle_events = [e for e in read_all(log_path) if e.type == "cycle_flagged"]
        assert len(cycle_events) == 1
        assert cycle_events[0].node_id == "B"
        assert cycle_events[0].payload["dst"] == "A"
        assert cycle_events[0].payload["edge_type"] == "blocked-by"

        # No edge_declared/edge_derived event was ever written for the
        # cycle-closing candidate — only the first, non-cycle-closing edge.
        edge_events = [
            e for e in read_all(log_path) if e.type in ("edge_declared", "edge_derived")
        ]
        assert len(edge_events) == 1

        # ...and resolve_graph() omits it.
        graph = resolve_graph(log_path)
        assert len(graph.edges) == 1
        kept = graph.edges[0]
        assert (kept.src, kept.type, kept.dst) == ("A", "blocked-by", "B")

    def test_cycle_flagging_is_idempotent(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        add_edge(
            "A", "blocked-by", "B", source="declared", evidence={},
            project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path,
        )
        first_flag = add_edge(
            "B", "blocked-by", "A", source="declared", evidence={},
            project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path,
        )
        second_flag = add_edge(
            "B", "blocked-by", "A", source="declared", evidence={},
            project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path,
        )

        assert first_flag.status == "cycle_flagged"
        assert second_flag.status == "cycle_flagged"
        cycle_events = [e for e in read_all(log_path) if e.type == "cycle_flagged"]
        assert len(cycle_events) == 1  # deduped by idempotency_key

    def test_resolve_graph_independently_excludes_a_cycle_closing_edge(
        self, tmp_path: Path
    ) -> None:
        # Defensive replay-time guard (module docstring "Cycle handling"):
        # even if a cycle-closing ordering edge somehow reached the log by a
        # path other than add_edge() (bypassing its precondition), resolve_graph
        # must still never surface a cycle.
        log_path = _log(tmp_path)
        append(
            log_path,
            {
                "schema_version": 1,
                "event_id": "e1",
                "idempotency_key": "edge:A:blocked-by:B",
                "ts": "2026-08-14T12:00:00.000000Z",
                "type": "edge_declared",
                "project_id": _PROJECT_ID,
                "node_id": "A",
                "writer_role": _WRITER_ROLE,
                "payload": {"dst": "B", "edge_type": "blocked-by", "evidence": {}},
            },
        )
        append(
            log_path,
            {
                "schema_version": 1,
                "event_id": "e2",
                "idempotency_key": "edge:B:blocked-by:A",
                "ts": "2026-08-14T12:00:01.000000Z",
                "type": "edge_declared",
                "project_id": _PROJECT_ID,
                "node_id": "B",
                "writer_role": _WRITER_ROLE,
                "payload": {"dst": "A", "edge_type": "blocked-by", "evidence": {}},
            },
        )

        graph = resolve_graph(log_path)

        # Only the first (non-cycle-closing) edge survives the replay.
        assert len(graph.edges) == 1
        assert (graph.edges[0].src, graph.edges[0].dst) == ("A", "B")

    def test_non_ordering_edges_never_gate_on_cycles(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        a = add_edge(
            "A", "contends-for", "B", source="declared", evidence={},
            project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path,
        )
        b = add_edge(
            "B", "contends-for", "A", source="declared", evidence={},
            project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path,
        )
        assert a.status == "added"
        assert b.status == "added"  # distinct idempotency key; not a "cycle"
        assert len(resolve_graph(log_path).edges) == 2


def _fixture_graph() -> DependencyGraph:
    """Build the G3-2 fixture DAG directly (bypassing the log/add_edge).

    Precedence (feeds-into keeps direction): A -> B -> C, A -> D, and a
    redundant direct A -> C (implied by A -> B -> C). `contends-for(D, E)`
    is a non-ordering edge connecting an otherwise-unconnected node E into
    the same weakly-connected cluster.
    """
    return DependencyGraph(
        edges=(
            Edge(src="A", type="feeds-into", dst="B", source="declared", evidence={}),
            Edge(src="B", type="feeds-into", dst="C", source="declared", evidence={}),
            Edge(src="A", type="feeds-into", dst="D", source="declared", evidence={}),
            Edge(src="A", type="feeds-into", dst="C", source="declared", evidence={}),
            Edge(src="D", type="contends-for", dst="E", source="declared", evidence={}),
        )
    )


class TestGraphAlgorithmsOnFixtureDag:
    """G3-2: ready_set/transitive_blockers/related_cluster on a fixture DAG."""

    def test_ready_set_is_nodes_with_no_incoming_precedence_edge(self) -> None:
        graph = _fixture_graph()
        assert graph.ready_set() == {"A", "E"}

    def test_transitive_blockers_of_c_is_a_and_b(self) -> None:
        graph = _fixture_graph()
        assert graph.transitive_blockers("C") == {"A", "B"}

    def test_transitive_blockers_of_d_is_a(self) -> None:
        graph = _fixture_graph()
        assert graph.transitive_blockers("D") == {"A"}

    def test_transitive_blockers_of_a_is_empty(self) -> None:
        graph = _fixture_graph()
        assert graph.transitive_blockers("A") == set()

    def test_related_cluster_spans_the_whole_weakly_connected_component(self) -> None:
        graph = _fixture_graph()
        assert graph.related_cluster("A") == {"A", "B", "C", "D", "E"}
        assert graph.related_cluster("E") == {"A", "B", "C", "D", "E"}

    def test_related_cluster_of_isolated_node_is_itself(self) -> None:
        graph = DependencyGraph(edges=())
        assert graph.related_cluster("Z") == {"Z"}

    def test_transitive_reduction_drops_the_implied_direct_edge(self) -> None:
        graph = _fixture_graph()
        reduced = graph.transitive_reduction()
        pairs = {(e.src, e.type, e.dst) for e in reduced}

        assert ("A", "feeds-into", "C") not in pairs  # implied by A->B->C
        assert ("A", "feeds-into", "B") in pairs
        assert ("B", "feeds-into", "C") in pairs
        assert ("A", "feeds-into", "D") in pairs
        assert ("D", "contends-for", "E") in pairs  # non-ordering: passthrough
        assert len(reduced) == 4

    def test_detect_cycles_on_an_acyclic_fixture_is_empty(self) -> None:
        assert _fixture_graph().detect_cycles() == []

    def test_detect_cycles_finds_a_manually_constructed_cycle(self) -> None:
        graph = DependencyGraph(
            edges=(
                Edge(src="X", type="feeds-into", dst="Y", source="declared", evidence={}),
                Edge(src="Y", type="feeds-into", dst="Z", source="declared", evidence={}),
                Edge(src="Z", type="feeds-into", dst="X", source="declared", evidence={}),
            )
        )
        cycles = graph.detect_cycles()
        assert len(cycles) == 1
        assert set(cycles[0]) == {"X", "Y", "Z"}

    def test_detect_cycles_ignores_non_ordering_edges(self) -> None:
        # Two contends-for edges that would form a "cycle" if ordering were
        # ignored must never be reported — contends-for never gates on
        # cycles (Decision B).
        graph = DependencyGraph(
            edges=(
                Edge(src="A", type="contends-for", dst="B", source="declared", evidence={}),
                Edge(src="B", type="contends-for", dst="A", source="declared", evidence={}),
            )
        )
        assert graph.detect_cycles() == []


class TestDeriveEdgesFeedsIntoAndServesGap:
    """feeds-into via merge_base+registered_at; serves' documented schema gap."""

    def test_feeds_into_derived_from_shared_merge_base_and_registration_order(
        self, tmp_path: Path
    ) -> None:
        log_path = _log(tmp_path)
        sessions = [
            SessionGitSnapshot(
                node_id="node/early",
                merge_base="deadbeef",
                registered_at="2026-08-14T10:00:00.000000Z",
            ),
            SessionGitSnapshot(
                node_id="node/late",
                merge_base="deadbeef",
                registered_at="2026-08-14T11:00:00.000000Z",
            ),
        ]

        results = derive_edges(
            sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path
        )

        feeds = [r for r in results if r.edge.type == "feeds-into"]
        assert len(feeds) == 1
        assert feeds[0].edge.src == "node/early"
        assert feeds[0].edge.dst == "node/late"
        assert feeds[0].edge.evidence == {"merge_base": "deadbeef"}

    def test_feeds_into_not_derived_without_registration_order(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        sessions = [
            SessionGitSnapshot(node_id="node/a", merge_base="deadbeef"),
            SessionGitSnapshot(node_id="node/b", merge_base="deadbeef"),
        ]

        results = derive_edges(
            sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path
        )

        assert [r for r in results if r.edge.type == "feeds-into"] == []

    def test_serves_never_derives_without_a_persisted_issue_ref(self, tmp_path: Path) -> None:
        # Documents the DONE_WITH_CONCERNS gap: no real caller populates
        # issue_ref today (nothing in the schema persists one), so this is
        # always None from real session facts and serves() never fires.
        log_path = _log(tmp_path)
        sessions = [SessionGitSnapshot(node_id="node/a"), SessionGitSnapshot(node_id="node/b")]

        results = derive_edges(
            sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path
        )

        assert [r for r in results if r.edge.type == "serves"] == []

    def test_serves_derives_when_issue_ref_is_supplied(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        sessions = [SessionGitSnapshot(node_id="node/a", issue_ref="roadmap#123")]

        results = derive_edges(
            sessions, project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path
        )

        serves = [r for r in results if r.edge.type == "serves"]
        assert len(serves) == 1
        assert serves[0].edge.dst == "roadmap#123"
        assert serves[0].edge.evidence == {"issue_ref": "roadmap#123"}


class TestAddEdgeValidation:
    """Basic input validation on add_edge()."""

    def test_unknown_edge_type_rejected(self, tmp_path: Path) -> None:
        try:
            add_edge(
                "A", "not-a-real-type", "B", source="declared", evidence={},
                project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=_log(tmp_path),
            )
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_unknown_source_rejected(self, tmp_path: Path) -> None:
        try:
            add_edge(
                "A", "contends-for", "B", source="not-a-real-source", evidence={},
                project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=_log(tmp_path),
            )
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_self_edge_rejected(self, tmp_path: Path) -> None:
        try:
            add_edge(
                "A", "contends-for", "A", source="declared", evidence={},
                project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=_log(tmp_path),
            )
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestAddEdgeResultShape:
    """AddEdgeResult always carries the candidate edge, regardless of status."""

    def test_result_edge_matches_input_regardless_of_status(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        kwargs = dict(
            source="declared", evidence={"k": "v"},
            project_id=_PROJECT_ID, writer_role=_WRITER_ROLE, log_path=log_path,
        )
        added: AddEdgeResult = add_edge("A", "contends-for", "B", **kwargs)
        deduped: AddEdgeResult = add_edge("A", "contends-for", "B", **kwargs)

        assert added.edge == deduped.edge
        assert added.edge.src == "A"
        assert added.edge.dst == "B"
        assert added.edge.type == "contends-for"
