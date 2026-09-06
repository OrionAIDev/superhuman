"""Unit suite for the FR-6 canonical-record enumerator.

Covers TEST.md's "4. Test cases -- census" section (TC-30..TC-33). Per R8,
the count assertion here runs against a synthetic, checked-in-shaped tree
built fresh in a `tmp_path` fixture -- never against the live, growing
`~/.claude/skills` / `~/dev` roots. The live-corpus run is a separate,
manually-invoked smoke check (see the chunk report), not a pytest case.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gate_record_census as census_mod  # noqa: E402


def _make_record(root: Path, slug: str) -> Path:
    """Create a minimal, well-formed `SUPERHUMAN.md` under `root` for `slug`."""
    record_dir = root / "docs" / "superhuman" / slug
    record_dir.mkdir(parents=True, exist_ok=True)
    path = record_dir / "SUPERHUMAN.md"
    path.write_text(
        "## Decisions log\n[2026-08-01T00:00:00Z] G0: baseline\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# TC-30: enumeration matches the documented count (synthetic, frozen tree)
# ---------------------------------------------------------------------------


def test_census_count_matches_the_number_of_canonical_records_created(tmp_path: Path) -> None:
    """The census count is a formula over what exists, never a hardcoded number (R8)."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_record(repo_a, "alpha")
    _make_record(repo_a, "beta")
    _make_record(repo_b, "gamma")

    records = census_mod.census([tmp_path])
    assert len(records) == 3


def test_iter_canonical_records_yields_repo_root_slug_and_path(tmp_path: Path) -> None:
    """Each yielded `RecordRef` carries the repo root, slug, and the file path."""
    repo = tmp_path / "repo-a"
    expected_path = _make_record(repo, "alpha")

    refs = list(census_mod.iter_canonical_records([tmp_path]))
    assert len(refs) == 1
    ref = refs[0]
    assert ref.repo_root == repo
    assert ref.slug == "alpha"
    assert ref.path == expected_path


def test_census_over_nonexistent_root_yields_nothing_for_that_root(tmp_path: Path) -> None:
    """A root that does not exist on disk contributes no records but does not error."""
    repo = tmp_path / "real-repo"
    _make_record(repo, "alpha")
    missing = tmp_path / "does-not-exist"

    refs = list(census_mod.iter_canonical_records([repo, missing]))
    assert len(refs) == 1


# ---------------------------------------------------------------------------
# TC-31: exclusion rule -- worktrees and tests directories never enumerated
# ---------------------------------------------------------------------------


def test_worktree_records_are_excluded(tmp_path: Path) -> None:
    """A `SUPERHUMAN.md` under `.claude/worktrees/` is never enumerated."""
    repo = tmp_path / "repo-a"
    _make_record(repo, "real-project")
    _make_record(repo / ".claude" / "worktrees" / "some-branch", "real-project")

    refs = list(census_mod.iter_canonical_records([tmp_path]))
    assert len(refs) == 1
    assert ".claude" not in refs[0].path.parts or "worktrees" not in refs[0].path.parts


def test_tests_directory_records_are_excluded(tmp_path: Path) -> None:
    """A `SUPERHUMAN.md` under a `tests/` directory is never enumerated."""
    repo = tmp_path / "repo-a"
    _make_record(repo, "real-project")
    _make_record(repo / "tests" / "fixtures", "real-project")

    refs = list(census_mod.iter_canonical_records([tmp_path]))
    assert len(refs) == 1


def test_this_fixtures_own_tree_cannot_pollute_the_census(tmp_path: Path) -> None:
    """The census excludes `tests/`, so this project's own fixtures never inflate the count."""
    repo = tmp_path / "superhuman"
    _make_record(repo, "gate-record-integrity")
    _make_record(repo / "tests" / "fixtures" / "gate_records", "decoy")

    refs = list(census_mod.iter_canonical_records([tmp_path]))
    assert len(refs) == 1
    assert refs[0].slug == "gate-record-integrity"


# ---------------------------------------------------------------------------
# TC-32: an empty census is a hard failure, never a silent empty result
# ---------------------------------------------------------------------------


def test_empty_census_raises_rather_than_returning_an_empty_list(tmp_path: Path) -> None:
    """Zero canonical records under the roots is a bug, not a finding (regen_core_manifest precedent)."""
    empty_dir = tmp_path / "nothing-here"
    empty_dir.mkdir()
    with pytest.raises(RuntimeError, match="(?i)empty"):
        census_mod.census([empty_dir])


def test_iter_canonical_records_does_not_raise_on_empty_input() -> None:
    """`iter_canonical_records` itself is a lazy enumerator; only `census` enforces non-emptiness."""
    assert list(census_mod.iter_canonical_records([])) == []


# ---------------------------------------------------------------------------
# TC-33: NFR-7 -- duplicate slug across two repos is never collapsed
# ---------------------------------------------------------------------------


def test_duplicate_slug_across_repos_is_never_collapsed(tmp_path: Path) -> None:
    """The same slug in two different repos yields two distinct `RecordRef`s, never one."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _make_record(repo_a, "shared-slug")
    _make_record(repo_b, "shared-slug")

    records = census_mod.census([tmp_path])
    assert len(records) == 2
    repo_roots = {ref.repo_root for ref in records}
    assert repo_roots == {repo_a, repo_b}

    # A type-level regression guard: the return type is a list of RecordRef,
    # never a slug-keyed mapping that would silently collapse this pair.
    assert isinstance(records, list)
    assert not isinstance(records, dict)
