"""Tests for ``scripts.fleet.core.store`` — TC-7.

Covers NFR-1 (fragment-side correctness): temp-file + ``os.replace`` atomic
writes, so a fragment is never observed half-written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fleet.core.errors import ValidationError
from scripts.fleet.core.schema import Fragment
from scripts.fleet.core.store import (
    fragment_path,
    iter_fragments,
    read_fragment,
    write_fragment,
)


def _fragment(node_id: str = "portable/ws/proj/local-1") -> Fragment:
    return Fragment(
        node_id=node_id,
        project_id="proj-abc",
        lifecycle="active",
        block_state="unblocked",
        review_state="none",
        adoption_state="normal",
        done_level="D0-code",
    )


class TestRoundTrip:
    """TC-7(a)."""

    def test_write_then_read_round_trips_exactly(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        fragment = _fragment()
        write_fragment(fragment, sessions_dir)
        read_back = read_fragment(fragment.node_id, sessions_dir)
        assert read_back == fragment

    def test_read_missing_fragment_returns_none(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        assert read_fragment("no/such/node/id", sessions_dir) is None

    def test_write_creates_sessions_dir_if_absent(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "does-not-exist-yet"
        write_fragment(_fragment(), sessions_dir)
        assert sessions_dir.is_dir()

    def test_different_node_ids_get_distinct_files(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        write_fragment(_fragment("portable/ws/proj/a"), sessions_dir)
        write_fragment(_fragment("portable/ws/proj/b"), sessions_dir)
        assert fragment_path("portable/ws/proj/a", sessions_dir) != fragment_path(
            "portable/ws/proj/b", sessions_dir
        )
        assert read_fragment("portable/ws/proj/a", sessions_dir).node_id == "portable/ws/proj/a"
        assert read_fragment("portable/ws/proj/b", sessions_dir).node_id == "portable/ws/proj/b"


class TestAtomicWrite:
    """TC-7(b): an interrupted rename never leaves a partial file."""

    def test_replace_failure_leaves_old_content_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sessions_dir = tmp_path / "sessions"
        old = _fragment()
        write_fragment(old, sessions_dir)

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated crash between temp-write and rename")

        monkeypatch.setattr("os.replace", _boom)

        new = _fragment()
        object.__setattr__(new, "lifecycle", "awaiting-launch")
        with pytest.raises(OSError):
            write_fragment(new, sessions_dir)

        monkeypatch.undo()
        # The target is untouched — still the old, complete content.
        read_back = read_fragment(old.node_id, sessions_dir)
        assert read_back == old

    def test_replace_failure_leaves_no_target_when_none_existed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sessions_dir = tmp_path / "sessions"
        fragment = _fragment("portable/ws/proj/brand-new")

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated crash between temp-write and rename")

        monkeypatch.setattr("os.replace", _boom)
        with pytest.raises(OSError):
            write_fragment(fragment, sessions_dir)
        monkeypatch.undo()

        assert read_fragment(fragment.node_id, sessions_dir) is None
        # No stray temp file left behind either.
        leftovers = [p for p in sessions_dir.glob("*") if p.name.startswith(".tmp-")]
        assert leftovers == []


class TestIterFragments:
    def test_missing_sessions_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert iter_fragments(tmp_path / "does-not-exist") == []

    def test_lists_every_readable_fragment(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        write_fragment(_fragment("portable/ws/proj/a"), sessions_dir)
        write_fragment(_fragment("portable/ws/proj/b"), sessions_dir)
        node_ids = {f.node_id for f in iter_fragments(sessions_dir)}
        assert node_ids == {"portable/ws/proj/a", "portable/ws/proj/b"}

    def test_corrupt_fragment_is_skipped_by_default(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        write_fragment(_fragment("portable/ws/proj/good"), sessions_dir)
        (sessions_dir / "zzz-corrupt.json").write_text("{not valid json", encoding="utf-8")

        fragments = iter_fragments(sessions_dir)
        assert {f.node_id for f in fragments} == {"portable/ws/proj/good"}

    def test_corrupt_fragment_raises_when_skip_corrupt_is_false(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        write_fragment(_fragment("portable/ws/proj/good"), sessions_dir)
        (sessions_dir / "zzz-corrupt.json").write_text("{not valid json", encoding="utf-8")

        with pytest.raises((ValidationError, ValueError)):
            iter_fragments(sessions_dir, skip_corrupt=False)
