"""Tests for `scripts.fleet.doctor` — the read-only estate-health scanner.

TDD scaffold only (Phase 2.1). Stubs with `pytest.mark.skip`; the Chunk 3
Developer implements each, TDD-first, per `TEST.md` TC-23/TC-24.

Not named in PLAN.md's file-structure table (which folds `fleet doctor`
into Chunk 3's acceptance criteria without naming a discrete test file);
QA scaffolds it separately because `doctor.py` is its own new module and
`conventions/testing.md` calls for one test file per source file. Flagged
in the QA return report as a QA naming choice, not an authoritative
PLAN.md path.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestHealthStates:
    """D6: `fleet doctor` reports each project record's writability as one
    of four states: ok / no Project-id / fleet disabled / unresolvable."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3")
    def test_reports_ok_for_a_fully_configured_record(self, tmp_path: Path) -> None:
        """A record with a valid `Project-id:`, fleet enabled, resolvable
        via the locator, reports `ok`."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3")
    def test_reports_no_project_id_when_the_field_is_absent(
        self, tmp_path: Path
    ) -> None:
        """A record that resolves but has no `**Project-id:**` line reports
        that state, matching the FR-10 acceptance scan's own definition of
        'missing the field'."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3")
    def test_reports_fleet_disabled_when_no_profile_enables_it(
        self, tmp_path: Path
    ) -> None:
        """A resolvable record in a workspace with no `fleet.enabled: true`
        profile reports the disabled state, not a false 'unresolvable'."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3")
    def test_reports_unresolvable_for_an_unparseable_or_ambiguous_record(
        self, tmp_path: Path
    ) -> None:
        """A candidate the locator would exclude or refuse on (malformed
        `SUPERHUMAN.md`, or genuine multi-candidate ambiguity) reports
        `unresolvable`, distinct from the other three states."""


class TestScanRoots:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 3")
    def test_scan_single_root_enumerates_all_records_under_it(
        self, tmp_path: Path
    ) -> None:
        """`scan([root])` walks down and finds every `SUPERHUMAN.md` under
        `root`, not just the one the cwd happens to be in — `doctor` is a
        DOWN enumeration tool, unlike the locator's UP-then-ladder
        resolution."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3")
    def test_scan_multiple_roots_aggregates_into_one_report(
        self, tmp_path: Path
    ) -> None:
        """`scan([root1, root2])` combines records from both roots into one
        `list[ProjectHealth]`, each entry stating which root it came from."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3")
    def test_scan_states_its_roots_in_the_report(self, tmp_path: Path) -> None:
        """The report itself names the roots that were scanned (open issue
        5 in DESIGN.md notes prior scans disagreed on scope because roots
        weren't recorded) — this is the regression test for that."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3")
    def test_cli_doctor_scan_prints_a_human_readable_summary(
        self, tmp_path: Path
    ) -> None:
        """`fleet doctor --scan <root>...` prints a readable summary line
        per record and exits 0 regardless of findings (read-only, never a
        fail-closed assertion)."""
