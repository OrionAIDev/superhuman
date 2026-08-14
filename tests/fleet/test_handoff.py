"""Tests for ``scripts.fleet.handoff`` — TC-17, TC-18, TC-19, TC-20, TC-21.

Chunk 3: the manual-handoff intent row (FR-1's 3rd origination path),
``handoff_id`` self-registration (FR-2), fuzzy ``(cwd, branch)``
reconciliation with ambiguity refusal (FR-2 / DESIGN's named "false-merge
risk" mitigation), and the stale/cancel report (FR-3).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.fleet.adapter.base import HANDOFF_ID_LINE_PREFIX
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.cli import build_parser
from scripts.fleet.core.errors import LockTimeoutError, OwnershipError
from scripts.fleet.core.events import append as core_append
from scripts.fleet.core.events import read_all
from scripts.fleet.core.query import list_sessions
from scripts.fleet.core.schema import Fragment
from scripts.fleet.core.store import fragment_path, write_fragment
from scripts.fleet import handoff
from scripts.fleet.handoff import _resolve_handoff_expiry_seconds


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


def _emit(
    git_repo: Path,
    fleet_dir: tuple[Path, Path],
    *,
    branch: str = "feature/x",
    writer_role: str = "Project Manager",
    handoff_id: str | None = None,
) -> handoff.HandoffEmission:
    log_path, sessions_dir = fleet_dir
    adapter = PortableAdapter(git_repo, "demo-slug")
    return handoff.emit(
        adapter,
        slug="demo-slug",
        project_id="proj-abc123",
        prompt_text="Please continue the work.",
        cwd=git_repo,
        branch=branch,
        writer_role=writer_role,
        log_path=log_path,
        sessions_dir=sessions_dir,
        handoff_id=handoff_id,
    )


class TestHandoffEmit:
    """TC-17."""

    def test_emit_writes_awaiting_launch_row_with_embedded_handoff_id(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir

        emission = _emit(git_repo, fleet_dir)

        events = read_all(log_path)
        assert len(events) == 1
        event = events[0]
        assert event.type == "handoff_emitted"
        assert event.payload["lifecycle"] == "awaiting-launch"
        assert event.payload["handoff_id"] == emission.handoff_id
        assert event.node_id == emission.node_id

        sessions = list_sessions(sessions_dir, project_id="proj-abc123")
        assert [s.node_id for s in sessions] == [emission.node_id]
        assert sessions[0].lifecycle == "awaiting-launch"

        expected_line = f"{HANDOFF_ID_LINE_PREFIX} {emission.handoff_id}"
        assert expected_line in emission.prompt_text

    def test_emit_is_rejected_for_a_ceo_class_writer_role(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        # lifecycle is superhuman-owned (FIELD_OWNERS); a "ceo"-class writer
        # must not be able to mint an awaiting-launch row. ("CEO" is the
        # literal role string `core.ownership._role_class` recognizes —
        # matching `tests/fleet/test_ownership.py`'s own fixtures.)
        with pytest.raises(OwnershipError):
            _emit(git_repo, fleet_dir, writer_role="CEO")

        log_path, _sessions_dir = fleet_dir
        assert not log_path.exists()


class TestSelfRegisterExactMatch:
    """TC-18."""

    def test_exact_handoff_id_flips_awaiting_launch_to_active(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emission = _emit(git_repo, fleet_dir)

        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=emission.handoff_id,
        )

        assert result.status == "launched"
        assert result.node_id == emission.node_id
        assert result.match_method == "exact"

        events = read_all(log_path)
        launch_events = [e for e in events if e.type == "handoff_launched"]
        assert len(launch_events) == 1
        assert launch_events[0].idempotency_key == f"launch:{emission.handoff_id}"
        assert launch_events[0].payload["match_method"] == "exact"

        sessions = list_sessions(sessions_dir, project_id="proj-abc123")
        assert sessions[0].lifecycle == "active"

    def test_second_call_with_same_handoff_id_does_not_duplicate_the_flip(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emission = _emit(git_repo, fleet_dir)

        first = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=emission.handoff_id,
        )
        second = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=emission.handoff_id,
        )

        assert first.status == "launched"
        assert second.status == "already_launched"
        events = read_all(log_path)
        assert len([e for e in events if e.type == "handoff_launched"]) == 1

    def test_unknown_handoff_id_raises(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        with pytest.raises(ValueError):
            handoff.self_register(
                log_path=log_path,
                sessions_dir=sessions_dir,
                writer_role="session",
                handoff_id="does-not-exist",
            )


class TestSelfRegisterFuzzyMatch:
    """TC-19: edited-prompt fuzzy (cwd, branch) reconciliation — no false 'dropped'."""

    def test_fuzzy_match_flips_and_records_match_method_fuzzy(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emission = _emit(git_repo, fleet_dir, branch="feature/x")

        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=None,
            cwd=git_repo,
            branch="feature/x",
        )

        assert result.status == "launched"
        assert result.node_id == emission.node_id
        assert result.match_method == "fuzzy"

        events = read_all(log_path)
        launch_events = [e for e in events if e.type == "handoff_launched"]
        assert len(launch_events) == 1
        assert launch_events[0].payload["match_method"] == "fuzzy"

    def test_fuzzy_match_tolerates_trailing_slash_and_refs_heads_prefix(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emission = _emit(git_repo, fleet_dir, branch="feature/x")

        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=None,
            cwd=str(git_repo) + "/",
            branch="refs/heads/feature/x",
        )

        assert result.status == "launched"
        assert result.node_id == emission.node_id
        assert result.match_method == "fuzzy"

    def test_fuzzy_match_never_reports_a_false_dropped_for_the_real_match(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        # A negative-fixture sibling with a different branch must not
        # interfere with (or be mistaken for) the real match.
        log_path, sessions_dir = fleet_dir
        _emit(git_repo, fleet_dir, branch="feature/other", handoff_id="hid-other")
        emission = _emit(git_repo, fleet_dir, branch="feature/x", handoff_id="hid-target")

        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=None,
            cwd=git_repo,
            branch="feature/x",
        )

        assert result.status == "launched"
        assert result.node_id == emission.node_id


class TestSelfRegisterAmbiguousFuzzyMatch:
    """TC-20: >1 open candidate sharing (cwd, branch) refuses to auto-flip."""

    def _seed_two_open_candidates(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> tuple[handoff.HandoffEmission, handoff.HandoffEmission]:
        first = _emit(git_repo, fleet_dir, branch="feature/x", handoff_id="hid-1")
        second = _emit(git_repo, fleet_dir, branch="feature/x", handoff_id="hid-2")
        return first, second

    def test_ambiguous_match_refuses_to_auto_flip_either_candidate(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        first, second = self._seed_two_open_candidates(git_repo, fleet_dir)

        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=None,
            cwd=git_repo,
            branch="feature/x",
        )

        assert result.status == "ambiguous"
        assert result.node_id is None
        assert set(result.candidates) == {first.node_id, second.node_id}

        # No handoff_launched event was written for either candidate.
        events = read_all(log_path)
        assert not [e for e in events if e.type == "handoff_launched"]

        sessions = {s.node_id: s for s in list_sessions(sessions_dir, project_id="proj-abc123")}
        assert sessions[first.node_id].lifecycle == "awaiting-launch"
        assert sessions[second.node_id].lifecycle == "awaiting-launch"

    def test_ambiguous_refusal_is_deterministic_across_repeated_runs(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        first, second = self._seed_two_open_candidates(git_repo, fleet_dir)
        expected_candidates = tuple(sorted([first.node_id, second.node_id]))

        for _ in range(10):
            result = handoff.self_register(
                log_path=log_path,
                sessions_dir=sessions_dir,
                writer_role="session",
                handoff_id=None,
                cwd=git_repo,
                branch="feature/x",
            )
            assert result.status == "ambiguous"
            assert result.candidates == expected_candidates

        # Still zero handoff_launched events after 10 repeated ambiguous
        # attempts — the refusal never non-deterministically picks a winner.
        events = read_all(log_path)
        assert not [e for e in events if e.type == "handoff_launched"]


class TestStaleReportAndCancel:
    """TC-21."""

    def test_stale_report_lists_a_row_once_now_passes_its_expiry(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emitted = _emit(git_repo, fleet_dir, handoff_id="hid-becomes-stale")

        far_future = datetime.now(timezone.utc) + timedelta(hours=48)
        rows = handoff.stale_report(
            log_path=log_path, sessions_dir=sessions_dir, now=far_future, expiry_seconds=3600
        )

        assert emitted.node_id in {r["node_id"] for r in rows}

    def test_stale_report_excludes_a_row_within_expiry(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        fresh = _emit(git_repo, fleet_dir, handoff_id="hid-fresh-2")

        now = datetime.now(timezone.utc)
        rows = handoff.stale_report(
            log_path=log_path, sessions_dir=sessions_dir, now=now, expiry_seconds=3600
        )

        assert fresh.node_id not in {r["node_id"] for r in rows}

    def test_cancel_writes_cancelled_and_excludes_from_stale_report_and_fuzzy_pool(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emission = _emit(git_repo, fleet_dir, branch="feature/x", handoff_id="hid-cancel-me")

        fragment = handoff.cancel(
            emission.node_id,
            project_id="proj-abc123",
            writer_role="Project Manager",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )
        assert fragment.lifecycle == "cancelled"

        events = read_all(log_path)
        cancel_events = [e for e in events if e.type == "handoff_cancelled"]
        assert len(cancel_events) == 1

        # No longer stale-reportable, however old the `now` given.
        far_future = datetime.now(timezone.utc) + timedelta(days=365)
        rows = handoff.stale_report(
            log_path=log_path, sessions_dir=sessions_dir, now=far_future, expiry_seconds=1
        )
        assert emission.node_id not in {r["node_id"] for r in rows}

        # No longer a live candidate for fuzzy self-register either.
        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=None,
            cwd=git_repo,
            branch="feature/x",
        )
        assert result.status == "not_found"

        # Nor for the exact-id path — cancelled means dead, both ways in.
        exact = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=emission.handoff_id,
        )
        assert exact.status == "not_launchable"

    def test_stale_report_resolves_expiry_from_a_profile_file(
        self, git_repo: Path, fleet_dir: tuple[Path, Path], tmp_path: Path
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emitted = _emit(git_repo, fleet_dir, handoff_id="hid-profile-driven")

        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text(
            "version: 1\nfleet:\n  handoff_expiry_seconds: 5\n", encoding="utf-8"
        )

        now = datetime.now(timezone.utc) + timedelta(seconds=30)
        rows = handoff.stale_report(
            log_path=log_path,
            sessions_dir=sessions_dir,
            now=now,
            profile_path=profile_path,
        )
        assert emitted.node_id in {r["node_id"] for r in rows}

    def test_stale_report_falls_back_to_the_default_when_no_profile_present(
        self, git_repo: Path, fleet_dir: tuple[Path, Path], tmp_path: Path
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emitted = _emit(git_repo, fleet_dir, handoff_id="hid-no-profile")

        missing_profile = tmp_path / "no-such-profile.yaml"
        now = datetime.now(timezone.utc) + timedelta(seconds=5)
        rows = handoff.stale_report(
            log_path=log_path,
            sessions_dir=sessions_dir,
            now=now,
            profile_path=missing_profile,
        )
        # 5 seconds is nowhere near the (generic, non-operator) default
        # expiry, so the row must not be reported stale.
        assert emitted.node_id not in {r["node_id"] for r in rows}


class TestHandoffCliSubcommands:
    def test_emit_cancel_stale_subcommands_round_trip(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        fleet_dir = tmp_path / "fleet"
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("Please continue.", encoding="utf-8")

        parser = build_parser()

        emit_args = parser.parse_args(
            [
                "handoff",
                "emit",
                "--project-id",
                "proj-abc123",
                "--slug",
                "demo-slug",
                "--workspace",
                str(git_repo),
                "--branch",
                "feature/x",
                "--prompt-file",
                str(prompt_file),
                "--writer-role",
                "Project Manager",
                "--fleet-dir",
                str(fleet_dir),
            ]
        )
        assert emit_args.func(emit_args) == 0

        events = read_all(fleet_dir / "events.jsonl")
        assert len(events) == 1
        node_id = events[0].node_id

        stale_args = parser.parse_args(
            [
                "handoff",
                "stale",
                "--workspace",
                str(git_repo),
                "--slug",
                "demo-slug",
                "--fleet-dir",
                str(fleet_dir),
                "--expiry-seconds",
                "0",
            ]
        )
        assert stale_args.func(stale_args) == 0

        cancel_args = parser.parse_args(
            [
                "handoff",
                "cancel",
                "--node-id",
                node_id,
                "--project-id",
                "proj-abc123",
                "--writer-role",
                "Project Manager",
                "--workspace",
                str(git_repo),
                "--slug",
                "demo-slug",
                "--fleet-dir",
                str(fleet_dir),
            ]
        )
        assert cancel_args.func(cancel_args) == 0

        events = read_all(fleet_dir / "events.jsonl")
        assert [e.type for e in events] == ["handoff_emitted", "handoff_cancelled"]


class TestSelfRegisterEdgeCases:
    """Closes coverage on `self_register`'s less-common branches."""

    def test_fuzzy_self_register_requires_both_cwd_and_branch(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        with pytest.raises(ValueError):
            handoff.self_register(
                log_path=log_path,
                sessions_dir=sessions_dir,
                writer_role="session",
                handoff_id=None,
                cwd=git_repo,
                branch=None,
            )

    def test_exact_match_with_no_fragment_on_disk_returns_not_found(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        # The `handoff_emitted` event exists (so the exact-id lookup
        # succeeds), but the fragment file itself is gone — an edge case
        # (e.g. a corrupt/deleted fragment) `self_register` must not crash
        # on.
        log_path, sessions_dir = fleet_dir
        emission = _emit(git_repo, fleet_dir)
        fragment_path(emission.node_id, sessions_dir).unlink()

        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=emission.handoff_id,
        )
        assert result.status == "not_found"
        assert result.node_id == emission.node_id

    def test_open_awaiting_launch_rows_skips_events_for_closed_or_other_handoffs(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        # One handoff stays open, a sibling is cancelled — the log then
        # contains a handoff_emitted for a node no longer in the open set,
        # plus a handoff_cancelled event, both of which the join in
        # `_open_awaiting_launch_rows` must skip over on the way to finding
        # the still-open row.
        log_path, sessions_dir = fleet_dir
        open_emission = _emit(git_repo, fleet_dir, branch="feature/x", handoff_id="hid-stays-open")
        closed_emission = _emit(
            git_repo, fleet_dir, branch="feature/y", handoff_id="hid-gets-cancelled"
        )
        handoff.cancel(
            closed_emission.node_id,
            project_id="proj-abc123",
            writer_role="Project Manager",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )

        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=None,
            cwd=git_repo,
            branch="feature/x",
        )
        assert result.status == "launched"
        assert result.node_id == open_emission.node_id

    def test_true_concurrent_dedupe_path_returns_already_launched(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        # Simulates the race `tests/fleet/test_concurrency.py`'s
        # double-launch worker exercises at the `core.events.append` layer:
        # the launch event already landed (same idempotency key), but the
        # fragment this process reads is a stale "awaiting-launch" snapshot
        # taken just before that — proving the *second* dedupe path (the
        # `append()` call itself returning None), not just the early
        # already-active short-circuit any sequential repeat call takes.
        log_path, sessions_dir = fleet_dir
        emission = _emit(git_repo, fleet_dir, handoff_id="hid-race")

        core_append(
            log_path,
            {
                "schema_version": 1,
                "event_id": "eid-race-launch",
                "idempotency_key": f"launch:{emission.handoff_id}",
                "ts": "2026-08-14T12:00:00.000000Z",
                "type": "handoff_launched",
                "project_id": "proj-abc123",
                "node_id": emission.node_id,
                "writer_role": "session",
                "payload": {"lifecycle": "active", "match_method": "exact"},
            },
        )
        write_fragment(
            Fragment(
                node_id=emission.node_id,
                project_id="proj-abc123",
                lifecycle="awaiting-launch",
                block_state="unblocked",
                review_state="none",
                adoption_state="normal",
                done_level="D0-code",
            ),
            sessions_dir,
        )

        result = handoff.self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role="session",
            handoff_id=emission.handoff_id,
        )
        assert result.status == "already_launched"

    def test_emit_lock_timeout_is_surfaced_not_swallowed(
        self, git_repo: Path, fleet_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_path, sessions_dir = fleet_dir
        adapter = PortableAdapter(git_repo, "demo-slug")

        calls = {"n": 0}

        def _always_times_out(*_args: object, **_kwargs: object) -> None:
            calls["n"] += 1
            raise LockTimeoutError("simulated contention")

        monkeypatch.setattr("scripts.fleet.handoff.append", _always_times_out)

        with pytest.raises(LockTimeoutError):
            handoff.emit(
                adapter,
                slug="demo-slug",
                project_id="proj-abc123",
                prompt_text="hi",
                cwd=git_repo,
                writer_role="Project Manager",
                log_path=log_path,
                sessions_dir=sessions_dir,
                lock_retry_attempts=3,
                lock_retry_backoff=0.0,
            )

        assert calls["n"] == 3
        assert not log_path.exists()

    def test_cancel_is_idempotent_on_repeat_call(
        self, git_repo: Path, fleet_dir: tuple[Path, Path]
    ) -> None:
        log_path, sessions_dir = fleet_dir
        emission = _emit(git_repo, fleet_dir)

        first = handoff.cancel(
            emission.node_id,
            project_id="proj-abc123",
            writer_role="Project Manager",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )
        second = handoff.cancel(
            emission.node_id,
            project_id="proj-abc123",
            writer_role="Project Manager",
            log_path=log_path,
            sessions_dir=sessions_dir,
        )

        assert first.lifecycle == second.lifecycle == "cancelled"
        events = read_all(log_path)
        assert len([e for e in events if e.type == "handoff_cancelled"]) == 1


class TestResolveHandoffExpirySeconds:
    """Coverage for the profile-driven (NFR-5) expiry resolver's edge cases."""

    def test_no_profile_path_and_no_profile_found_returns_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "scripts.superhuman_profile.find_profile", lambda cwd: None
        )
        result = _resolve_handoff_expiry_seconds(profile_path=None)
        assert result == handoff._DEFAULT_HANDOFF_EXPIRY_SECONDS

    def test_explicit_profile_path_that_does_not_exist_returns_default(
        self, tmp_path: Path
    ) -> None:
        result = _resolve_handoff_expiry_seconds(profile_path=tmp_path / "missing.yaml")
        assert result == handoff._DEFAULT_HANDOFF_EXPIRY_SECONDS

    def test_malformed_yaml_returns_default(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text("fleet: [unterminated\n", encoding="utf-8")
        result = _resolve_handoff_expiry_seconds(profile_path=profile_path)
        assert result == handoff._DEFAULT_HANDOFF_EXPIRY_SECONDS

    def test_non_mapping_yaml_root_returns_default(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        result = _resolve_handoff_expiry_seconds(profile_path=profile_path)
        assert result == handoff._DEFAULT_HANDOFF_EXPIRY_SECONDS

    def test_fleet_key_not_a_mapping_returns_default(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text("version: 1\nfleet: not-a-mapping\n", encoding="utf-8")
        result = _resolve_handoff_expiry_seconds(profile_path=profile_path)
        assert result == handoff._DEFAULT_HANDOFF_EXPIRY_SECONDS

    def test_missing_handoff_expiry_seconds_key_returns_default(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text("version: 1\nfleet: {}\n", encoding="utf-8")
        result = _resolve_handoff_expiry_seconds(profile_path=profile_path)
        assert result == handoff._DEFAULT_HANDOFF_EXPIRY_SECONDS

    def test_negative_handoff_expiry_seconds_returns_default(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text(
            "version: 1\nfleet:\n  handoff_expiry_seconds: -5\n", encoding="utf-8"
        )
        result = _resolve_handoff_expiry_seconds(profile_path=profile_path)
        assert result == handoff._DEFAULT_HANDOFF_EXPIRY_SECONDS

    def test_valid_override_is_used(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text(
            "version: 1\nfleet:\n  handoff_expiry_seconds: 120\n", encoding="utf-8"
        )
        result = _resolve_handoff_expiry_seconds(profile_path=profile_path)
        assert result == 120.0

    def test_profile_path_none_uses_found_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text(
            "version: 1\nfleet:\n  handoff_expiry_seconds: 42\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "scripts.superhuman_profile.find_profile", lambda cwd: profile_path
        )
        result = _resolve_handoff_expiry_seconds(profile_path=None)
        assert result == 42.0
