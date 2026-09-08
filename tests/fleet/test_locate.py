"""Tests for `scripts.fleet.locate` — the (workspace, slug) project locator.

TDD scaffold only (Phase 2.1). Every test below is a stub: a name, a
docstring stating the behavior it proves, and `pytest.mark.skip` so the
suite stays green until the Chunk 2 Developer implements each one for real
per `TEST.md` TC-1..TC-14, watching each fail before writing `locate.py`.

Covers FR-1..FR-4, FR-2's refusal cases, FR-3's three real estate layouts,
D1's 4-rung disambiguation ladder, and NFR-4's confinement property (the
locator is a *selector* between two git-derived paths, never a path
*constructor*).
"""

from __future__ import annotations

from pathlib import Path

import pytest


# --- FR-3: the three real layouts -------------------------------------------------


class TestFR3Layouts:
    """One test per real-estate layout named in FR-3 / DESIGN.md D1."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-1")
    def test_locate_layout_plain_repo(self, tmp_path: Path) -> None:
        """A plain repo with exactly one `docs/superhuman/<slug>/SUPERHUMAN.md`
        resolves via the L4 singleton rung."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-2")
    def test_locate_layout_worktree_stale_copy_resolves_to_main_worktree(
        self, tmp_path: Path
    ) -> None:
        """The `gate-record-integrity` shape: a linked worktree's
        `docs/superhuman/<slug>/` directory exists but has NO
        `SUPERHUMAN.md` in it. The locator must retry once against the main
        working tree (via `git rev-parse --git-common-dir`'s parent) and
        resolve to the canonical record there — never silently resolve to
        the stale, id-less copy in the worktree."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-3")
    def test_locate_layout_gitignored_mount_point(self, tmp_path: Path) -> None:
        """`docs/superhuman/` is git-ignored (a mount point for a separate
        clone) but present as a plain directory on disk. The locator walks
        the filesystem, not the git index, so it must see it."""


# --- D1: the 4-rung disambiguation ladder -----------------------------------------


class TestDisambiguationLadder:
    """One test per rung, per DESIGN.md D1's normative ladder table."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-4")
    def test_locate_rung_l1_cwd_containment(self, tmp_path: Path) -> None:
        """Payload `cwd` is at/under `<root>/docs/superhuman/<slug>/`, with
        two OTHER candidates present elsewhere in the same repo — L1 alone
        must uniquely narrow to the containing candidate."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-5")
    def test_locate_rung_l2_declared_profile_slug(self, tmp_path: Path) -> None:
        """The resolved profile's `fleet.slug` names a candidate; cwd is the
        repo root (L1 does not fire); two other candidates exist."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-6")
    def test_locate_rung_l3_branch_name(self, tmp_path: Path) -> None:
        """The workspace's current git branch equals a candidate slug; L1
        and L2 do not fire."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-7")
    def test_locate_rung_l4_singleton(self, tmp_path: Path) -> None:
        """Exactly one well-formed candidate exists; no other rung is
        needed to reach a unique answer."""


# --- FR-2: refusal cases -----------------------------------------------------------


class TestRefusal:
    """FR-2: zero, several-with-no-unique-rung, or unparseable candidates
    each yield 'no result' — never a first-match-wins pick."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-8")
    def test_locate_refuses_on_zero_candidates(self, tmp_path: Path) -> None:
        """No `SUPERHUMAN.md` anywhere under `docs/superhuman/*/` returns
        `None` with a reason string."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-9")
    def test_locate_refuses_when_no_rung_produces_a_unique_answer(
        self, tmp_path: Path
    ) -> None:
        """Several well-formed candidates, none matched by cwd, profile, or
        branch — L1-L3 silent, L4 fails (not singleton). The reason string
        names every candidate considered."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-10")
    def test_locate_excludes_unparseable_record_without_blinding_the_others(
        self, tmp_path: Path
    ) -> None:
        """One candidate's `SUPERHUMAN.md` has no `**Slug:**` line (or it
        disagrees with its directory name) and is excluded from candidacy;
        the OTHER candidates in the same repo still resolve normally via
        their own rung. One malformed record must not blind an 11-record
        repo."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-11")
    def test_locate_candidacy_does_not_require_project_id(
        self, tmp_path: Path
    ) -> None:
        """D1 ruling 3: a candidate whose `SUPERHUMAN.md` has no
        `**Project-id:**` line is still selectable. Resolution behavior
        must be IDENTICAL before and after the Chunk 5 backfill — if
        Project-id were required for candidacy, the hook would silently
        change what it resolves to the moment ids are backfilled."""


# --- FR-4: CLI + API surface --------------------------------------------------------


class TestCliAndApiSurface:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-12")
    def test_cli_locate_prints_slug_or_nothing_exit_0(self, tmp_path: Path) -> None:
        """`fleet locate` prints the resolved slug on a resolvable
        workspace, prints nothing on an ambiguous one, and exits 0 in both
        cases."""


# --- NFR-4: confinement (the locator is a selector, never a constructor) -----------


class TestNFR4Confinement:
    """The locator may return ONLY one of two git-derived paths
    (`--show-toplevel` or `--git-common-dir`'s parent) and must never
    construct a path from a profile value, an env var, or a branch name.
    See TEST.md's 'How to test the NFR-4 negative' for the rationale behind
    the differential-oracle and trap-fixture approach below.
    """

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-13")
    def test_locate_confinement_returns_only_git_derived_paths(
        self, tmp_path: Path
    ) -> None:
        """Differential oracle: independently compute the two legitimate
        candidate paths via direct `subprocess` calls to `git rev-parse
        --show-toplevel` and `git rev-parse --git-common-dir` (run from the
        TEST's own process, not imported from `locate.py`). Run
        `locate_project(cwd)` across every fixture used in the layout and
        ladder tests above; assert `result.workspace` is path-EQUAL
        (`Path.samefile` / resolved-string equality — never a mere
        `.is_relative_to()` containment check) to one of the two
        independently-computed candidates, for every fixture."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 2, TC-14")
    def test_locate_confinement_rejects_a_profile_supplied_trap_path(
        self, tmp_path: Path
    ) -> None:
        """Trap fixture: the L2 rung's `fleet.slug` profile value (or a
        crafted branch name) is set to a path-shaped string that resolves
        to a directory OUTSIDE the two legitimate git-derived candidates,
        where that directory also contains a validly-named
        `SUPERHUMAN.md` (a planted trap, e.g.
        `<toplevel>/../trap/docs/superhuman/<slug>/SUPERHUMAN.md`). Assert
        the locator never resolves to the trap directory — it either
        refuses, or resolves to one of the two legitimate roots. This is
        the test that would fail if D1's 'selector, never a constructor'
        property were violated."""
