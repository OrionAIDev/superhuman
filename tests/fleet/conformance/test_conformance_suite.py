"""Chunk 7: harness-neutral conformance suite (NFR-2, DESIGN Decision D).

Behavioral companion to ``test_core_is_harness_neutral.py``: that test is a
static ``ast`` import scan proving ``scripts/fleet/core/`` never *imports*
anything harness-specific. It cannot prove the surface actually *works* end
to end with no Claude harness present — that is this module's job, and is
exactly the "runtime half of NFR-2" that module's docstring names as
"TC-35/TC-32, landing with ... Chunk 7's conformance suite."

Per Decision D (recommendation A), the suite is bound to ``PortableAdapter``
— the one adapter implementation with zero Claude-specific dependency to
even accidentally reach for (git + filesystem + env only, no
``session-relay``, no native session tools). It drives the full
create -> update -> validate -> query lifecycle through the same enforced
write path production uses (``core.events.append`` -> ``validate_event`` ->
``ownership.assert_writer_may`` -> ``core.projection.project_event``) —
nothing here bypasses validation to make the fixture simpler, since that
would defeat the point of a *conformance* suite. It needs nothing beyond
``git`` on ``PATH`` and stdlib, and runs green anywhere Python does.

Proof that the flow never reaches for a Claude adapter is by construction,
not a ``sys.modules`` scan: ``scripts.fleet.cli`` (this suite's only
non-``core`` import) legitimately imports ``ClaudeAdapter`` at module scope
to support its own ``--harness claude`` option, so a module-presence check
would be a false positive against the very entry point NFR-3's degradation
path also drives. What actually matters — and what is asserted below — is
that every ``SessionAdapter`` instance this suite constructs and passes
through the lifecycle is a ``PortableAdapter``, and every resulting node id
carries ``harness="portable"`` end to end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.cli import register_session
from scripts.fleet.core.done import advance as done_advance
from scripts.fleet.core.done import event_for as done_event_for
from scripts.fleet.core.edges import add_edge, resolve_graph
from scripts.fleet.core.events import append, read_all
from scripts.fleet.core.nodes import parse_node_id
from scripts.fleet.core.projection import project_event
from scripts.fleet.core.query import edges_of, list_sessions

_PROJECT_ID = "proj-conformance"
_WRITER_ROLE = "Project Manager"


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "trunk")
    _run(repo, "config", "user.email", "test@example.invalid")
    _run(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run(repo, "add", "README.md")
    _run(repo, "commit", "-q", "-m", "initial")
    return repo


@pytest.fixture
def fleet_dir(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "fleet"
    return root / "events.jsonl", root / "sessions"


class TestFullLifecycleUnderPortableAdapter:
    """create -> update -> validate -> query, driven entirely by PortableAdapter."""

    def test_register_update_advance_edge_and_query_round_trip(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir

        # --- create: two sessions register through the Portable path only ---
        primary_adapter = PortableAdapter(git_repo, "conformance-slug", local_id="primary-1")
        dependent_adapter = PortableAdapter(git_repo, "conformance-slug", local_id="dependent-1")
        # No Claude adapter anywhere in this flow (NFR-2, behaviorally) —
        # both adapters driving the lifecycle really are PortableAdapter.
        assert isinstance(primary_adapter, PortableAdapter)
        assert isinstance(dependent_adapter, PortableAdapter)

        primary = register_session(
            primary_adapter,
            origination="manual",
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
            sessions_dir=sessions_dir,
        )
        dependent = register_session(
            dependent_adapter,
            origination="manual",
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
            sessions_dir=sessions_dir,
        )

        # Every node id was minted by the Portable path — never claude/*.
        for fragment in (primary, dependent):
            harness, _workspace, _slug, _local_id = parse_node_id(fragment.node_id)
            assert harness == "portable"
        assert primary.lifecycle == "active"
        assert primary.done_level == "D0-code"

        # --- update: decomposed status fields (FR-5), through the enforced
        # append -> validate -> project path, never a collapsed single enum ---
        lifecycle_event = {
            "schema_version": 1,
            "event_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "idempotency_key": f"lifecycle:{primary.node_id}:blocked",
            "ts": "2026-08-15T12:00:00.000000Z",
            "type": "lifecycle_changed",
            "project_id": _PROJECT_ID,
            "node_id": primary.node_id,
            "writer_role": _WRITER_ROLE,
            "payload": {"lifecycle": "blocked"},
        }
        block_event = {
            "schema_version": 1,
            "event_id": "aaaaaaaa-0000-0000-0000-000000000002",
            "idempotency_key": f"block:{primary.node_id}:waiting-on-review",
            "ts": "2026-08-15T12:01:00.000000Z",
            "type": "block_changed",
            "project_id": _PROJECT_ID,
            "node_id": primary.node_id,
            "writer_role": _WRITER_ROLE,
            "payload": {"block_state": "waiting-on-review"},
        }
        appended_lifecycle = append(log_path, lifecycle_event)
        assert appended_lifecycle is not None
        project_event(appended_lifecycle, sessions_dir)
        appended_block = append(log_path, block_event)
        assert appended_block is not None
        updated_primary = project_event(appended_block, sessions_dir)

        assert updated_primary.lifecycle == "blocked"
        assert updated_primary.block_state == "waiting-on-review"
        # The other status fields are untouched by these two targeted writes.
        assert updated_primary.review_state == "none"
        assert updated_primary.adoption_state == "normal"

        # --- update: advance done_level one rung, with merge evidence (FR-6) ---
        advance_result = done_advance(
            primary.node_id,
            "D1-merged",
            evidence={"commit": "abc123", "pr": "1"},
            approver=None,
            ceiling="D4-prod",
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
        )
        assert advance_result.status == "advanced"
        assert advance_result.level == "D1-merged"
        done_event = done_event_for(primary.node_id, "D1-merged", log_path)
        assert done_event is not None
        advanced_primary = project_event(done_event, sessions_dir)
        assert advanced_primary.done_level == "D1-merged"
        # Advancing done_level alone must not disturb the fields just set above.
        assert advanced_primary.lifecycle == "blocked"
        assert advanced_primary.block_state == "waiting-on-review"

        # --- create: a declared dependency edge between the two sessions ---
        edge_result = add_edge(
            dependent.node_id,
            "blocked-by",
            primary.node_id,
            source="declared",
            evidence={"reason": "waits on primary's merge"},
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
        )
        assert edge_result.status == "added"
        assert edge_result.edge.source == "declared"

        # --- query: read every write back through the query surface ---
        sessions = list_sessions(sessions_dir, project_id=_PROJECT_ID)
        assert {s.node_id for s in sessions} == {primary.node_id, dependent.node_id}
        queried_primary = next(s for s in sessions if s.node_id == primary.node_id)
        assert queried_primary.lifecycle == "blocked"
        assert queried_primary.block_state == "waiting-on-review"
        assert queried_primary.done_level == "D1-merged"

        primary_edges = edges_of(primary.node_id, log_path)
        assert len(primary_edges) == 1
        assert primary_edges[0]["src"] == dependent.node_id
        assert primary_edges[0]["type"] == "blocked-by"
        assert primary_edges[0]["dst"] == primary.node_id
        assert primary_edges[0]["source"] == "declared"

        dependent_edges = edges_of(dependent.node_id, log_path)
        assert dependent_edges == primary_edges  # same edge, either endpoint

        graph = resolve_graph(log_path)
        assert any(
            e.src == dependent.node_id and e.dst == primary.node_id and e.type == "blocked-by"
            for e in graph.edges
        )

        # --- validate: the whole written log is schema-valid on replay ---
        events = read_all(log_path)
        assert len(events) == 6  # 2 registrations, lifecycle, block, done advance, edge
        assert {e.node_id for e in events} >= {primary.node_id, dependent.node_id}
