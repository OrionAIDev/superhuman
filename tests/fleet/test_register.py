"""Tests for ``scripts.fleet.cli`` ``register`` — TC-15, plus LockTimeoutError handling.

TC-15: ``fleet register`` writes exactly one validated ``session_registered``
event, through the enforced ``core.events.append`` boundary, for both FR-1
origination paths this chunk covers — spawned (a parent registers a child it
just learned about via ``enumerate_sessions()``) and relayed (a session
self-registers via ``current_session()``, its own id supplied by the
orchestrator). Both must also work when driven straight off the
``PortableAdapter`` (no Claude harness at all), covering NFR-3 end to end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.fleet.adapter.claude import ClaudeAdapter
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.cli import build_parser, main, register_session
from scripts.fleet.core.errors import LockTimeoutError
from scripts.fleet.core.events import read_all
from scripts.fleet.core.nodes import parse_node_id
from scripts.fleet.core.query import list_sessions
from scripts.fleet.core.store import fragment_path


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


class TestRegisterSpawnedPath:
    """Fixture: a parent's Claude adapter, `enumerate_sessions()` returning a child."""

    def test_spawned_child_registers_a_valid_session_registered_event(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        adapter = ClaudeAdapter(
            git_repo,
            "demo-slug",
            sessions=[{"sessionId": "child-1", "cwd": str(git_repo), "branch": "trunk"}],
        )

        fragment = register_session(
            adapter,
            origination="spawned",
            project_id="proj-abc123",
            writer_role="Project Manager",
            log_path=log_path,
            sessions_dir=sessions_dir,
            target_session_id="child-1",
        )

        events = read_all(log_path)
        assert len(events) == 1
        event = events[0]
        # Every required field (schema.REQUIRED_EVENT_FIELDS) is present and
        # well-formed by construction — `validate_event` inside `append`
        # would have raised otherwise — but assert the ones TC-15 names
        # explicitly, plus the two identity-bearing ones.
        assert event.schema_version == 1
        assert event.event_id
        assert event.ts
        assert event.type == "session_registered"
        assert event.project_id == "proj-abc123"
        assert event.writer_role == "Project Manager"
        assert event.idempotency_key == f"register:{event.node_id}"
        harness, _workspace, _slug, local_id = parse_node_id(event.node_id)
        assert harness == "claude"
        assert local_id == "child-1"
        assert event.payload["origination"] == "spawned"

        assert fragment.node_id == event.node_id
        sessions = list_sessions(sessions_dir, project_id="proj-abc123")
        assert [s.node_id for s in sessions] == [event.node_id]


class TestRegisterRelayedPath:
    """Fixture: a launched session-relay handoff session self-registering."""

    def test_relayed_session_registers_a_valid_session_registered_event(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        adapter = ClaudeAdapter(git_repo, "demo-slug", current_session_id="relayed-session-9")

        fragment = register_session(
            adapter,
            origination="relayed",
            project_id="proj-abc123",
            writer_role="Project Manager",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )

        events = read_all(log_path)
        assert len(events) == 1
        event = events[0]
        assert event.schema_version == 1
        assert event.event_id
        assert event.ts
        assert event.type == "session_registered"
        assert event.project_id == "proj-abc123"
        assert event.writer_role == "Project Manager"
        assert event.idempotency_key == f"register:{event.node_id}"
        harness, _workspace, _slug, local_id = parse_node_id(event.node_id)
        assert harness == "claude"
        assert local_id == "relayed-session-9"
        assert event.payload["origination"] == "relayed"
        assert fragment.node_id == event.node_id


class TestRegisterPortablePathGracefulDegradation:
    """NFR-3 (TC-16, end to end): registration works with no Claude harness at all."""

    def test_portable_path_still_registers_a_valid_entry(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        adapter = PortableAdapter(git_repo, "demo-slug", local_id="portable-1")

        fragment = register_session(
            adapter,
            origination="manual",
            project_id="proj-abc123",
            writer_role="session",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )

        events = read_all(log_path)
        assert len(events) == 1
        assert events[0].type == "session_registered"
        harness, _workspace, _slug, local_id = parse_node_id(events[0].node_id)
        assert harness == "portable"
        assert local_id == "portable-1"
        assert fragment.node_id == events[0].node_id


class TestRegisterIsIdempotent:
    def test_registering_the_same_session_twice_does_not_duplicate_the_event(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        adapter = PortableAdapter(git_repo, "demo-slug", local_id="repeat-1")

        first = register_session(
            adapter,
            origination="manual",
            project_id="proj-abc123",
            writer_role="session",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )
        second = register_session(
            adapter,
            origination="manual",
            project_id="proj-abc123",
            writer_role="session",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )

        assert len(read_all(log_path)) == 1
        assert first.node_id == second.node_id

    def test_repeat_registration_recovers_from_corrupt_cached_fragment(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        """G6 (systematic sweep), site 4 (`cli.py:~261`): a repeat
        registration's dedupe read hits a corrupt cached fragment. Already
        hardened in G5 round-5 (the local `except FragmentCorrupt` falls
        through to `project_event`, whose own `FragmentCorrupt` handling
        rebuilds) — re-verified here as part of this round's systematic
        sweep, since the earlier round had no direct test for this specific
        call site."""
        log_path, sessions_dir = fleet_dir
        adapter = PortableAdapter(git_repo, "demo-slug", local_id="repeat-corrupt-1")

        first = register_session(
            adapter,
            origination="manual",
            project_id="proj-abc123",
            writer_role="session",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )

        # Deliberately corrupt the cached fragment (truncated JSON) so the
        # dedupe path's `read_fragment` at cli.py:~261 hits `FragmentCorrupt`.
        path = fragment_path(first.node_id, sessions_dir)
        path.write_text('{"node_id": "' + first.node_id + '", "lifecy', encoding="utf-8")

        second = register_session(
            adapter,
            origination="manual",
            project_id="proj-abc123",
            writer_role="session",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )

        assert len(read_all(log_path)) == 1
        assert second.node_id == first.node_id
        assert second.lifecycle == "active"


class TestRegisterHandlesLockTimeout:
    def test_lock_timeout_is_surfaced_not_swallowed(
        self,
        git_repo: Path,
        fleet_dir: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path, sessions_dir = fleet_dir
        adapter = PortableAdapter(git_repo, "demo-slug", local_id="locked-1")

        calls = {"n": 0}

        def _always_times_out(*_args: Any, **_kwargs: Any) -> None:
            calls["n"] += 1
            raise LockTimeoutError("simulated contention")

        monkeypatch.setattr("scripts.fleet.cli.append", _always_times_out)

        with pytest.raises(LockTimeoutError):
            register_session(
                adapter,
                origination="manual",
                project_id="proj-abc123",
                writer_role="session",
                log_path=log_path,
                sessions_dir=sessions_dir,
                lock_retry_attempts=3,
                lock_retry_backoff=0.0,
            )

        # Bounded retry: attempted exactly `lock_retry_attempts` times, not
        # once and not unboundedly.
        assert calls["n"] == 3
        # Never proceeds as if written: no log file, no fragment.
        assert not log_path.exists()
        assert not sessions_dir.exists() or not any(sessions_dir.iterdir())


class TestCliRegisterSubcommand:
    def test_register_subcommand_writes_a_valid_entry_via_the_cli(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        fleet_dir = tmp_path / "fleet"
        parser = build_parser()
        argv = [
            "register",
            "--project-id",
            "proj-abc123",
            "--slug",
            "demo-slug",
            "--workspace",
            str(git_repo),
            "--harness",
            "portable",
            "--origination",
            "manual",
            "--writer-role",
            "session",
            "--fleet-dir",
            str(fleet_dir),
        ]
        args = parser.parse_args(argv)
        exit_code = args.func(args)

        assert exit_code == 0
        events = read_all(fleet_dir / "events.jsonl")
        assert len(events) == 1
        assert events[0].type == "session_registered"

    def test_claude_harness_register_without_session_id_fails_closed_via_cli(
        self, git_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """GPT-5 round-9 preflight, BLOCKING, PM-reproduced: `--harness
        claude` with no `--session-id` and no `--target-session-id` used to
        fall through to `current_session()`'s `id(self)` fallback, minting a
        non-deterministic phantom id. Now `current_session()` fails closed
        with `SessionIdentityUnresolved`, and the CLI must render that as a
        clean nonzero exit with an actionable stderr message — never an
        unhandled traceback."""
        fleet_dir = tmp_path / "fleet"
        exit_code = main(
            [
                "register",
                "--project-id",
                "proj-abc123",
                "--slug",
                "demo-slug",
                "--workspace",
                str(git_repo),
                "--harness",
                "claude",
                "--origination",
                "relayed",
                "--writer-role",
                "session",
                "--fleet-dir",
                str(fleet_dir),
            ]
        )

        assert exit_code != 0
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "session-id" in captured.err
        assert not (fleet_dir / "events.jsonl").exists()

    def test_claude_harness_register_with_blank_session_id_fails_closed_via_cli(
        self, git_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """10th-round preflight, BLOCKING, PM-reproduced (R10-3): round-9's
        fix only caught a missing `--session-id`; `--session-id ""` (e.g. an
        unset shell var interpolated into the flag) passed the `is None`
        check outright and used to mint `node_id="claude/<ws>/demo-slug/"`
        — an EMPTY trailing `local_id` component. Must now fail closed the
        same way absence does: nonzero exit, actionable stderr, no
        traceback, no events.jsonl written."""
        fleet_dir = tmp_path / "fleet"
        exit_code = main(
            [
                "register",
                "--project-id",
                "proj-abc123",
                "--slug",
                "demo-slug",
                "--workspace",
                str(git_repo),
                "--harness",
                "claude",
                "--origination",
                "relayed",
                "--writer-role",
                "session",
                "--fleet-dir",
                str(fleet_dir),
                "--session-id",
                "",
            ]
        )

        assert exit_code != 0
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "session-id" in captured.err
        assert not (fleet_dir / "events.jsonl").exists()

    def test_main_returns_nonzero_on_lock_timeout(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _always_times_out(*_args: Any, **_kwargs: Any) -> None:
            raise LockTimeoutError("simulated contention")

        monkeypatch.setattr("scripts.fleet.cli.append", _always_times_out)

        fleet_dir = tmp_path / "fleet"
        exit_code = main(
            [
                "register",
                "--project-id",
                "proj-abc123",
                "--slug",
                "demo-slug",
                "--workspace",
                str(git_repo),
                "--harness",
                "portable",
                "--origination",
                "manual",
                "--writer-role",
                "session",
                "--fleet-dir",
                str(fleet_dir),
                "--lock-retry-attempts",
                "1",
            ]
        )

        assert exit_code == 1
        assert not (fleet_dir / "events.jsonl").exists()
