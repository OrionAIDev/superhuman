"""Tests for ``scripts.fleet.core.store`` — TC-7.

Covers NFR-1 (fragment-side correctness): temp-file + ``os.replace`` atomic
writes, so a fragment is never observed half-written.
"""

from __future__ import annotations

import random
import string
from pathlib import Path
from urllib.parse import quote

import pytest

from scripts.fleet.core.errors import ValidationError
from scripts.fleet.core.nodes import make_node_id
from scripts.fleet.core.schema import Fragment
from scripts.fleet.core.store import (
    _DIGEST_PREFIX,
    _MAX_READABLE_ENCODED_LEN,
    fragment_path,
    iter_fragments,
    read_fragment,
    write_fragment,
)

#: A safe, conservative bound the reviewer asked for: comfortably under both
#: the Windows MAX_PATH-adjacent risk and the NTFS 255-char component limit,
#: with headroom left for a real sessions_dir prefix.
_SAFE_FILENAME_LEN = 200


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

    def test_non_oserror_failure_still_cleans_up_the_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """G5 review nit: `except OSError` was narrower than the try body, so
        a non-`OSError` (e.g. a `json.dump` failure) leaked the `.tmp-*`
        file. The cleanup must run on *any* exception, not just `OSError`.
        """
        sessions_dir = tmp_path / "sessions"
        fragment = _fragment("portable/ws/proj/non-oserror-failure")

        def _boom(*args: object, **kwargs: object) -> None:
            raise ValueError("simulated non-OSError failure inside the write")

        monkeypatch.setattr("json.dump", _boom)
        with pytest.raises(ValueError):
            write_fragment(fragment, sessions_dir)
        monkeypatch.undo()

        assert read_fragment(fragment.node_id, sessions_dir) is None
        leftovers = [p for p in sessions_dir.glob("*") if p.name.startswith(".tmp-")]
        assert leftovers == [], f"temp file leaked on a non-OSError failure: {leftovers}"


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


class TestFragmentPathIsLengthSafeForPathologicalNodeIds:
    """Chunk-2 code-quality review follow-up: ``fragment_path`` double-encoded
    an already-percent-encoded ``node_id`` (``make_node_id`` encodes each of
    the 4 components once; ``fragment_path`` then re-quoted the whole,
    already-encoded string). Every special char in the original components
    cost ~5 extra characters, with no length cap — a long slug + a
    ``:``-heavy local_id blows straight past the Windows MAX_PATH (260) and
    the NTFS 255-char single-component limit, producing a filename
    ``write_fragment``/``os.replace`` cannot actually create.
    """

    def _pathological_node_id(self, slug_len: int = 89, colon_count: int = 88) -> str:
        return make_node_id(
            "portable",
            "ws",
            "s" * slug_len,
            ":" * colon_count,
        )

    def test_pathological_node_id_yields_a_length_safe_filename(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = self._pathological_node_id()

        path = fragment_path(node_id, sessions_dir)

        assert len(path.name) <= _SAFE_FILENAME_LEN, (
            f"fragment filename is {len(path.name)} chars — over the "
            f"{_SAFE_FILENAME_LEN}-char safe bound; the old double-encoding "
            f"bug produces filenames past the Windows MAX_PATH / NTFS "
            f"255-char component limit"
        )

    def test_pathological_node_id_round_trips_end_to_end(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id = self._pathological_node_id()
        fragment = _fragment(node_id)

        # Proves the digest filename actually works end-to-end, not just
        # that fragment_path() returns a short string — the write must
        # really land on disk and read back correctly.
        write_fragment(fragment, sessions_dir)
        read_back = read_fragment(node_id, sessions_dir)

        assert read_back == fragment
        # The node_id is recovered from the fragment's CONTENT, not the
        # filename — a digest filename is not required to be decodable.
        assert read_back.node_id == node_id

    def test_two_distinct_pathological_node_ids_get_distinct_files(
        self, tmp_path: Path
    ) -> None:
        sessions_dir = tmp_path / "sessions"
        node_id_a = self._pathological_node_id(colon_count=88)
        node_id_b = self._pathological_node_id(colon_count=87)  # one shorter, still pathological
        assert node_id_a != node_id_b

        path_a = fragment_path(node_id_a, sessions_dir)
        path_b = fragment_path(node_id_b, sessions_dir)
        assert path_a != path_b

        write_fragment(_fragment(node_id_a), sessions_dir)
        write_fragment(_fragment(node_id_b), sessions_dir)
        assert read_fragment(node_id_a, sessions_dir).node_id == node_id_a
        assert read_fragment(node_id_b, sessions_dir).node_id == node_id_b

    def test_short_node_id_still_uses_the_readable_encoded_filename(
        self, tmp_path: Path
    ) -> None:
        """Backward compatibility: a short node_id must keep producing the
        exact same readable filename format as before this fix — only
        pathologically long/special node_ids fall back to a digest name.
        """
        sessions_dir = tmp_path / "sessions"
        node_id = "portable/ws/proj/local-1"
        path = fragment_path(node_id, sessions_dir)
        assert path.name == f"{quote(node_id, safe='')}.json"

    def test_digest_prefix_is_disjoint_from_any_readable_encoded_name(self) -> None:
        """The collision-safety argument for `_DIGEST_PREFIX`, proven
        directly rather than only asserted in a comment: `quote(s, safe="")`
        never emits `%` followed by a lowercase letter — every literal `%`
        it produces starts a `%XX` escape with XX always two UPPERCASE hex
        digits. `_DIGEST_PREFIX` starts with `%` followed by a lowercase
        letter, so it can never be a prefix of `quote()`'s output, for any
        input, at any position.
        """
        assert _DIGEST_PREFIX[0] == "%"
        assert _DIGEST_PREFIX[1].islower(), (
            "the disjointness proof below depends on the prefix's second "
            "character being lowercase"
        )

        # Exhaustive over every single byte value...
        for i in range(256):
            encoded = quote(chr(i), safe="")
            assert _DIGEST_PREFIX not in encoded

        # ...plus a wide fuzz sweep over multi-character strings, including
        # ones deliberately built from characters `quote` must escape.
        rng = random.Random(0)  # deterministic — a flaky proof is no proof
        alphabet = string.printable
        for _ in range(5000):
            sample = "".join(rng.choices(alphabet, k=rng.randint(1, 60)))
            encoded = quote(sample, safe="")
            assert _DIGEST_PREFIX not in encoded, (
                f"quote({sample!r}) produced {encoded!r}, which contains "
                f"the supposedly-disjoint digest prefix {_DIGEST_PREFIX!r}"
            )

    def test_readable_threshold_constant_is_well_under_filesystem_limits(self) -> None:
        # The readable name is `<=_MAX_READABLE_ENCODED_LEN>` + ".json" (5)
        # — confirm that stays comfortably under the 255-char NTFS
        # single-component limit even before accounting for sessions_dir.
        assert _MAX_READABLE_ENCODED_LEN + len(".json") < 255
