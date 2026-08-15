"""Tests for ``scripts.fleet.view`` (PLAN.md Chunk 6, FR-7/CC-7).

Covers: the CLI table renderer shows every tracked session with its five
decomposed status fields plus dependency edges (`TestStatusTable`); the
`FLEET.md` generator produces DESIGN "Decision A"'s standalone-file structure
deterministically (`TestFleetMdGenerator`); and — the chunk's key contract —
neither render path, nor the `fleet status`/`fleet gen-view` CLI subcommands
built on them, ever writes the manifest (`TestReadOnlyManifest`). Fixtures
build a small real manifest via the existing register/edge/done write paths
(``cli.register_session``, ``core.edges.add_edge``, ``core.done.advance``),
matching ``test_handoff.py``/``test_done.py``'s own "drive the real write
path, then assert on the read side" convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.cli import build_parser, register_session
from scripts.fleet.core.edges import add_edge
from scripts.fleet.core.events import read_all
from scripts.fleet.core.store import iter_fragments
from scripts.fleet import view

_PROJECT_ID = "proj-view"
_SLUG = "view-proj"
_WRITER_ROLE = "Project Manager"


def _fleet_dir(workspace: Path) -> Path:
    return workspace / "docs" / "superhuman" / _SLUG / "fleet"


def _register(workspace: Path, local_id: str) -> str:
    """Register one session via the real registrar, return its node_id."""
    fleet_dir = _fleet_dir(workspace)
    adapter = PortableAdapter(workspace, _SLUG, local_id=local_id)
    fragment = register_session(
        adapter,
        origination="manual",
        project_id=_PROJECT_ID,
        writer_role=_WRITER_ROLE,
        log_path=fleet_dir / "events.jsonl",
        sessions_dir=fleet_dir / "sessions",
    )
    return fragment.node_id


class TestStatusTable:
    """Table render shows every session's five status fields + edges."""

    def test_empty_manifest_renders_cleanly(self, tmp_path: Path) -> None:
        fleet_dir = _fleet_dir(tmp_path)
        table = view.render_status_table(fleet_dir / "sessions", fleet_dir / "events.jsonl")
        assert "no tracked sessions" in table.lower()

    def test_shows_five_status_fields_for_a_registered_session(self, tmp_path: Path) -> None:
        node_id = _register(tmp_path, "sess-a")
        fleet_dir = _fleet_dir(tmp_path)

        table = view.render_status_table(fleet_dir / "sessions", fleet_dir / "events.jsonl")

        assert node_id in table
        for header in ("LIFECYCLE", "BLOCK_STATE", "REVIEW_STATE", "ADOPTION_STATE", "DONE_LEVEL"):
            assert header in table
        # Freshly-registered defaults should appear somewhere in the row.
        assert "active" in table or "unblocked" in table

    def test_shows_dependency_edges_for_a_session(self, tmp_path: Path) -> None:
        node_a = _register(tmp_path, "sess-a")
        node_b = _register(tmp_path, "sess-b")
        fleet_dir = _fleet_dir(tmp_path)
        log_path = fleet_dir / "events.jsonl"

        add_edge(
            node_a,
            "blocked-by",
            node_b,
            source="declared",
            evidence={"reason": "waiting on b"},
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=log_path,
        )

        table = view.render_status_table(fleet_dir / "sessions", log_path)
        rows = {
            line.split()[0]: line for line in table.splitlines() if node_a in line or node_b in line
        }
        assert "blocked-by" in rows[node_a]
        assert node_b in rows[node_a]
        assert "blocked-by" in rows[node_b]
        assert node_a in rows[node_b]

    def test_deterministic_row_order(self, tmp_path: Path) -> None:
        _register(tmp_path, "sess-z")
        _register(tmp_path, "sess-a")
        _register(tmp_path, "sess-m")
        fleet_dir = _fleet_dir(tmp_path)

        first = view.render_status_table(fleet_dir / "sessions", fleet_dir / "events.jsonl")
        second = view.render_status_table(fleet_dir / "sessions", fleet_dir / "events.jsonl")
        assert first == second


class TestFleetMdGenerator:
    """FLEET.md generation follows Decision A and is deterministic."""

    def test_writes_docs_superhuman_slug_fleet_md(self, tmp_path: Path) -> None:
        node_id = _register(tmp_path, "sess-a")
        fleet_dir = _fleet_dir(tmp_path)

        target = view.write_fleet_md(
            fleet_dir / "sessions",
            fleet_dir / "events.jsonl",
            slug=_SLUG,
            workspace=tmp_path,
        )

        assert target == tmp_path / "docs" / "superhuman" / _SLUG / "FLEET.md"
        assert target.is_file()
        content = target.read_text(encoding="utf-8")
        assert node_id in content
        assert _SLUG in content

    def test_generating_twice_is_byte_identical(self, tmp_path: Path) -> None:
        _register(tmp_path, "sess-a")
        _register(tmp_path, "sess-b")
        fleet_dir = _fleet_dir(tmp_path)

        first = view.render_fleet_md(
            fleet_dir / "sessions", fleet_dir / "events.jsonl", slug=_SLUG
        )
        second = view.render_fleet_md(
            fleet_dir / "sessions", fleet_dir / "events.jsonl", slug=_SLUG
        )
        assert first == second

    def test_empty_manifest_produces_clean_doc(self, tmp_path: Path) -> None:
        fleet_dir = _fleet_dir(tmp_path)
        content = view.render_fleet_md(
            fleet_dir / "sessions", fleet_dir / "events.jsonl", slug=_SLUG
        )
        assert _SLUG in content
        assert "no tracked sessions" in content.lower() or "no sessions" in content.lower()

    def test_fleet_md_is_a_standalone_file_not_a_superhuman_md_section(
        self, tmp_path: Path
    ) -> None:
        """Decision A: a standalone `FLEET.md`, never spliced into SUPERHUMAN.md."""
        superhuman_md = tmp_path / "docs" / "superhuman" / _SLUG / "SUPERHUMAN.md"
        superhuman_md.parent.mkdir(parents=True, exist_ok=True)
        superhuman_md.write_text("# Decisions log\n\nhand-authored content\n", encoding="utf-8")
        before = superhuman_md.read_bytes()

        _register(tmp_path, "sess-a")
        fleet_dir = _fleet_dir(tmp_path)
        view.write_fleet_md(
            fleet_dir / "sessions", fleet_dir / "events.jsonl", slug=_SLUG, workspace=tmp_path
        )

        assert superhuman_md.read_bytes() == before


class TestReadOnlyManifest:
    """FR-7/CC-7: neither `status` nor `gen-view` ever writes the manifest."""

    def _manifest_snapshot(self, fleet_dir: Path) -> dict:
        log_path = fleet_dir / "events.jsonl"
        sessions_dir = fleet_dir / "sessions"
        log_bytes = log_path.read_bytes() if log_path.is_file() else None
        fragment_bytes = {
            f.node_id: (fleet_dir / "sessions").exists() for f in iter_fragments(sessions_dir)
        }
        # Byte-for-byte snapshot of every fragment file on disk, keyed by
        # filename (not node_id) so a rename/corruption would also show up.
        fragment_files = {}
        if sessions_dir.is_dir():
            for path in sorted(sessions_dir.glob("*.json")):
                fragment_files[path.name] = path.read_bytes()
        return {"log_bytes": log_bytes, "fragment_files": fragment_files}

    def test_render_status_table_never_writes_manifest(self, tmp_path: Path) -> None:
        _register(tmp_path, "sess-a")
        node_b = _register(tmp_path, "sess-b")
        fleet_dir = _fleet_dir(tmp_path)
        add_edge(
            node_b,
            "feeds-into",
            node_b + "-other",  # dst need not be a registered node for this check
            source="declared",
            evidence={},
            project_id=_PROJECT_ID,
            writer_role=_WRITER_ROLE,
            log_path=fleet_dir / "events.jsonl",
        )

        before = self._manifest_snapshot(fleet_dir)
        view.render_status_table(fleet_dir / "sessions", fleet_dir / "events.jsonl")
        after = self._manifest_snapshot(fleet_dir)

        assert after == before

    def test_gen_view_writes_only_the_doc_never_the_manifest(self, tmp_path: Path) -> None:
        _register(tmp_path, "sess-a")
        fleet_dir = _fleet_dir(tmp_path)

        before = self._manifest_snapshot(fleet_dir)
        view.write_fleet_md(
            fleet_dir / "sessions", fleet_dir / "events.jsonl", slug=_SLUG, workspace=tmp_path
        )
        after = self._manifest_snapshot(fleet_dir)

        assert after == before

    def test_cli_status_subcommand_never_writes_manifest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _register(tmp_path, "sess-a")
        fleet_dir = _fleet_dir(tmp_path)
        before = self._manifest_snapshot(fleet_dir)

        parser = build_parser()
        args = parser.parse_args(
            ["status", "--workspace", str(tmp_path), "--slug", _SLUG, "--project-id", _PROJECT_ID]
        )
        rc = args.func(args)
        out = capsys.readouterr().out

        assert rc == 0
        assert "sess-a" in out or _PROJECT_ID in out or out  # something was printed
        after = self._manifest_snapshot(fleet_dir)
        assert after == before

    def test_cli_gen_view_subcommand_never_writes_manifest(self, tmp_path: Path) -> None:
        _register(tmp_path, "sess-a")
        fleet_dir = _fleet_dir(tmp_path)
        before = self._manifest_snapshot(fleet_dir)

        parser = build_parser()
        args = parser.parse_args(
            [
                "gen-view",
                "--workspace",
                str(tmp_path),
                "--slug",
                _SLUG,
                "--project-id",
                _PROJECT_ID,
            ]
        )
        rc = args.func(args)

        assert rc == 0
        assert (tmp_path / "docs" / "superhuman" / _SLUG / "FLEET.md").is_file()
        after = self._manifest_snapshot(fleet_dir)
        assert after == before

    def test_view_module_imports_nothing_that_writes(self) -> None:
        """Static guard: `view.py`'s actual import statements never name a
        manifest write path. Parsed via `ast` (not a source-text substring
        scan) so the module's own explanatory docstring — which necessarily
        *names* the forbidden functions to document why it avoids them — can
        never produce a false positive."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(view))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imported.add(f"{module}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)

        forbidden = {
            "core.events.append",
            ".core.events.append",
            "core.store.write_fragment",
            ".core.store.write_fragment",
            "core.projection.project_event",
            ".core.projection.project_event",
            "core.projection.rebuild",
            ".core.projection.rebuild",
        }
        assert not (imported & forbidden), f"view.py imports a write path: {imported & forbidden}"
