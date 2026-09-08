"""Tests for `scripts.fleet.observe.observe_session_start` and the additive
`ObserveResult.error_class` field — the FR-5 / FR-13 / FR-17 / D2a / D6 seam.

TDD scaffold only (Phase 2.1). Stubs with `pytest.mark.skip`; the Chunk 3
Developer implements each, TDD-first, per `TEST.md` TC-15..TC-24.

Filename matches PLAN.md's file-structure table (`test_observe_session.py`),
not DESIGN.md's component table (`test_observe_session_start.py`) — the two
artifacts disagree on this one filename; QA followed PLAN.md as the more
authoritative "what gets built where" source. Flagged in the QA return
report; not a silent pick.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestOriginationObserved:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-15")
    def test_observe_session_start_registers_with_origination_observed(
        self, tmp_path: Path
    ) -> None:
        """Happy path: a resolvable workspace with fleet enabled and no
        pending handoff produces a row whose `origination == "observed"`."""


class TestNeverAFuzzyFlip:
    """D2a: `observe session-start` NEVER performs a fuzzy (cwd, branch)
    launch flip — that is an assertion (commission risk), not coverage."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-16")
    def test_observe_session_start_never_performs_a_fuzzy_flip(
        self, tmp_path: Path
    ) -> None:
        """Even when `(cwd, branch)` would uniquely match an existing
        `awaiting-launch` row, calling `observe_session_start` with NO
        `--handoff-id` must register a NEW session row — never silently
        flip the existing awaiting-launch row to active. This is the
        regression test for D2a's stated commission-exposure risk."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-17")
    def test_observe_session_start_flips_only_with_explicit_handoff_id(
        self, tmp_path: Path
    ) -> None:
        """With `--handoff-id` supplied and matching an awaiting-launch
        row, the id-anchored flip fires (the fuzzy path is never
        consulted for this call)."""


class TestIdempotencyAcrossFiveMatchers:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-18")
    @pytest.mark.parametrize(
        "matcher", ["startup", "resume", "clear", "compact", "fork"]
    )
    def test_observe_session_start_five_matchers_produce_one_row(
        self, matcher: str, tmp_path: Path
    ) -> None:
        """FR-19/NFR-5 at the API level: the same `session_id` fired 5
        times (once per matcher) via `observe_session_start` produces
        exactly one row in the manifest (`idempotency_key =
        register:<node_id>` dedupes)."""


class TestErrorClassAndFR13Surfacing:
    """FR-13: `identity_unresolved` stops being invisible. `error_class`
    lives on `ObserveResult`; the stdout line lives ONLY in `cli.py`."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-19")
    def test_observe_result_error_class_set_to_identity_unresolved(
        self, tmp_path: Path
    ) -> None:
        """A project that resolves via the locator but whose
        `SUPERHUMAN.md` has no `**Project-id:**` line produces an
        `ObserveResult` with `error_class == "identity_unresolved"`."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-20")
    def test_cli_prints_exactly_one_stdout_line_for_identity_unresolved(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Drive the CLI entrypoint (`args.func(args)`) for `observe
        session-start` against the identity_unresolved fixture; capture
        stdout via `capsys`; assert exactly one non-empty stdout line, and
        that it names the actionable condition (project resolved but
        unwritable)."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-21")
    def test_library_call_produces_zero_stdout_for_identity_unresolved(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The library-level companion to TC-20: call
        `observe.observe_session_start(...)` DIRECTLY (not through
        `cli.py`), same fixture as TC-20; assert
        `capsys.readouterr().out == ""`. TC-20 + TC-21 together prove the
        stdout line lives only in `cli.py`, never in `observe.py` — whose
        loudness tiers reserve stdout entirely for its CLI callers."""


class TestBroadCatchByteUnchanged:
    """NFR-3: `observe.py`'s single sanctioned broad catch is neither
    widened nor duplicated by this chunk's additions."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-22")
    def test_observe_py_broad_catch_is_byte_unchanged(self) -> None:
        """Golden-snippet test, mirroring `TestCoreUntouched` in the
        existing `test_observe.py`: capture the exact source text (or a
        SHA-256 of the byte range) of `observe.py`'s single `except
        Exception` block as it exists at Chunk 3 HEAD. Re-extract the same
        byte range and assert equality — fails loudly if a future edit
        widens, duplicates, or relocates the catch."""


class TestFleetDoctor:
    """D6: `fleet doctor` ships in this chunk and is the FR-10 acceptance
    scan Chunk 5 uses."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-23")
    @pytest.mark.parametrize(
        "state", ["ok", "no_project_id", "fleet_disabled", "unresolvable"]
    )
    def test_fleet_doctor_reports_all_four_health_states(
        self, state: str, tmp_path: Path
    ) -> None:
        """A fixture repo in the given state is reported with the matching
        `ProjectHealth` classification."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 3, TC-24")
    def test_fleet_doctor_scans_multiple_roots_and_aggregates(
        self, tmp_path: Path
    ) -> None:
        """`--scan <root1> <root2>` reports records discovered under both
        roots in one combined report."""
