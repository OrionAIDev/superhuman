"""Tests for `scripts.fleet.project_id` — fail-closed minting + validation.

TDD scaffold only (Phase 2.1). Stubs with `pytest.mark.skip`; the Chunk 4
Developer implements each, TDD-first, per `TEST.md` TC-25..TC-34.

Deliberately a separate module and a separate test file from
`scripts/fleet/project.py` / `tests/fleet/test_project.py` — `project.py`'s
"never invents an id" read contract must stay literally true, so
`project_id.py` (mint/check) is never imported by `observe.py` and its
tests never import `project.py` internals either.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestMinting:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-25")
    def test_mint_project_id_is_16_hex_from_uuid4(self, tmp_path: Path) -> None:
        """A newly minted id is 16 lowercase hex characters
        (`uuid4().hex[:16]`), and contains neither the slug string nor any
        substring derived from a git remote URL."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-26")
    def test_mint_project_id_differs_for_same_slug_different_remote(
        self, tmp_path: Path
    ) -> None:
        """Direct regression against the forbidden 'hash of repo-remote +
        slug' scheme (D3's correction to `SUPERHUMAN.md.tpl`): two records
        sharing the SAME slug in two different repos (different remotes)
        must not collide, and neither id must be derivable from the
        other's remote+slug pair."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-27")
    def test_mint_is_a_noop_on_a_record_that_already_has_an_id(
        self, tmp_path: Path
    ) -> None:
        """Re-running `mint_project_id` against a `SUPERHUMAN.md` that
        already carries a `**Project-id:**` line leaves that value
        unchanged (FR-11: an id is minted once, never re-derived)."""


class TestFailClosedValidator:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-28")
    def test_check_project_id_exits_nonzero_when_absent(self, tmp_path: Path) -> None:
        """`fleet project check` against a record with no `Project-id:`
        exits with a NON-ZERO code — a fail-closed assertion, deliberately
        outside the `observe.py` fail-soft façade (FR-12)."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-29")
    def test_check_project_id_exits_zero_when_present(self, tmp_path: Path) -> None:
        """`fleet project check` against a fully-configured record exits 0
        and reports the id."""


class TestModuleIsolation:
    """D3: `project_id.py` is a strictly separate module from
    `project.py`, and `project.py` stays unmodified by this chunk."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-30")
    def test_project_py_is_unmodified(self) -> None:
        """Content-hash guard on `scripts/fleet/project.py`, matching the
        `TestCoreUntouched` idiom already used for `scripts/fleet/core/` —
        a golden SHA-256 of the file, asserted unchanged."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-31")
    def test_project_id_py_never_imported_by_observe_py(self) -> None:
        """Static source check: neither `import project_id` nor `from .
        import project_id` (nor any equivalent import spelling) appears
        anywhere in `scripts/fleet/observe.py`'s source text — the
        fail-soft façade must never gain a dependency on the fail-closed
        minting module."""


class TestKickoffSeam:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-32")
    def test_kickoff_seam_calls_check_then_mint_additively(self) -> None:
        """Content test on `phases/0-kickoff.md`: the new step text is
        present and additive — diffing the file against the merge-base
        with `origin/main` shows zero REMOVED lines (the same additive-edit
        idiom `test_seams.py` already uses for Phase 1.1's seams)."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-33")
    def test_superhuman_md_tpl_no_longer_suggests_remote_plus_slug_hash(
        self,
    ) -> None:
        """`templates/SUPERHUMAN.md.tpl` no longer contains the phrase
        suggesting 'a stable hash of repo-remote + slug' as a minting
        option — D3's correction, since that scheme is slug-derived and
        forbidden by FR-11."""


class TestThisProjectsOwnException:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 4, TC-34")
    def test_this_projects_own_project_id_is_not_reminted(self) -> None:
        """Regression anchor for PLAN.md Chunk 4's explicit note: this
        project's own `SUPERHUMAN.md` (`Project-id: 7124ce46ccd0a49a`,
        minted under the now-forbidden scheme before this rule existed)
        must NOT be silently re-minted by any mint/backfill pass — a
        `Project-id` is never re-minted once assigned (Phase 1 Decision
        F). Assert the value on disk stays byte-identical after running
        the mint path against this project's own record."""
