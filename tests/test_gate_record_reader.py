"""Unit suite for record reading, the version gate, and the gate-record gap.

Covers FR-2, FR-3 (as amended at G6-001), FR-4, FR-5, FR-13, FR-14 at the
`gate_record_parser.read_record` / `superhuman_profile.gate_record_gap`
level. Test cases mirror TEST.md's "3. Test cases -- record reading & the
version gate" section (TC-14..TC-29, TC-B).

Every "open ruling" TEST.md flagged (TC-15, TC-19) is settled in
`DECISIONS.md` (R6, G4-R2) and implemented here as the locked behavior, not
the alternative QA floated.

Per NFR-4 (public repo): every fixture below is a synthetic record. None is
copied from a real corpus file -- the *shapes* (version gate boundaries,
compound labels, UNKNOWN sentinels, malformed lines) are drawn from TEST.md,
but the content is invented so no private repo/slug name, decision text, or
operator vocabulary is shipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gate_record_parser as grp  # noqa: E402
import superhuman_profile as sp  # noqa: E402


def _write(tmp_path: Path, text: str, name: str = "SUPERHUMAN.md") -> Path:
    """Write `text` to a fresh file under `tmp_path` and return its path."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# TC-14: version exactly 1.1.0, locked block present and well-formed
# ---------------------------------------------------------------------------


def test_v1_1_0_with_well_formed_locked_block_presence_reads_locked(tmp_path: Path) -> None:
    """At >= 1.1.0 the presence test reads `## Decisions locked`; G4 passes G5."""
    path = _write(
        tmp_path,
        "**Superhuman-version:** 1.1.0\n"
        "## Decisions locked\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[2026-08-05T00:00:00Z] G4: test plan\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
    )
    reading = grp.read_record(path)
    assert reading.version == "1.1.0"
    assert reading.version_gate is True
    gap = sp.gate_record_gap(reading, gate=5)
    assert gap is None


# ---------------------------------------------------------------------------
# TC-15: version exactly 1.1.0, locked block ABSENT -- fails closed (R6)
# ---------------------------------------------------------------------------


def test_v1_1_0_with_locked_block_absent_fails_closed(tmp_path: Path) -> None:
    """R6: absence at >= 1.1.0 is treated exactly like an empty block -- exit 5."""
    path = _write(
        tmp_path,
        "**Superhuman-version:** 1.1.0\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[2026-08-05T00:00:00Z] G4: test plan\n",
    )
    reading = grp.read_record(path)
    assert reading.version_gate is True
    assert reading.locked.found is False
    gap = sp.gate_record_gap(reading, gate=5)
    assert gap is not None
    assert gap.code == sp.EXIT_RECORD


def test_v1_1_0_with_locked_block_present_but_empty_fails_closed(tmp_path: Path) -> None:
    """G4-R1: an empty (found, zero-entry) locked block at >= 1.1.0 also fails closed."""
    path = _write(
        tmp_path,
        "**Superhuman-version:** 1.1.0\n"
        "## Decisions locked\n"
        "<!-- nothing locked yet -->\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n",
    )
    reading = grp.read_record(path)
    assert reading.locked.found is True
    assert reading.locked.entries == ()
    gap = sp.gate_record_gap(reading, gate=1)
    assert gap is not None
    assert gap.code == sp.EXIT_RECORD


# ---------------------------------------------------------------------------
# TC-16: version just below 1.1.0, no locked block -- the legacy-majority shape
# ---------------------------------------------------------------------------


def test_below_1_1_0_with_no_locked_block_passes(tmp_path: Path) -> None:
    """Presence reads `## Decisions log` below 1.1.0; a legitimately absent locked block passes."""
    path = _write(
        tmp_path,
        "**Superhuman-version:** 1.0.3\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[2026-08-05T00:00:00Z] G4: test plan\n",
    )
    reading = grp.read_record(path)
    assert reading.version_gate is False
    gap = sp.gate_record_gap(reading, gate=5)
    assert gap is None


# ---------------------------------------------------------------------------
# TC-17: version below 1.1.0 WITH a locked block -- ignore it for presence
# ---------------------------------------------------------------------------


def test_early_adopter_below_1_1_0_ignores_its_own_locked_block_for_presence(
    tmp_path: Path,
) -> None:
    """Below 1.1.0, presence reads the log regardless of an incidentally-present locked block."""
    path = _write(
        tmp_path,
        "**Superhuman-version:** 1.0.3\n"
        "## Decisions locked\n"
        "[2026-08-01T00:00:00Z] G0: baseline only, does not mirror the log\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[2026-08-05T00:00:00Z] G4: test plan\n",
    )
    reading = grp.read_record(path)
    assert reading.version_gate is False
    # G4 is on the log but NOT on the (incidentally present) locked block.
    assert 4 in reading.log.gates
    assert 4 not in reading.locked.gates
    gap = sp.gate_record_gap(reading, gate=5)
    assert gap is None


# ---------------------------------------------------------------------------
# TC-18: no version declared at all -- same bucket as < 1.1.0
# ---------------------------------------------------------------------------


def test_no_version_declared_is_the_below_1_1_0_bucket(tmp_path: Path) -> None:
    """No `Superhuman-version:` field at all is documented as the same bucket as < 1.1.0."""
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[2026-08-05T00:00:00Z] G4: test plan\n",
    )
    reading = grp.read_record(path)
    assert reading.version is None
    assert reading.version_gate is False
    gap = sp.gate_record_gap(reading, gate=5)
    assert gap is None


# ---------------------------------------------------------------------------
# TC-19: malformed `Superhuman-version` -- tolerant bucket, never raises (G4-R2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed_version",
    ["1.1", "v1.1.0", "1.1.0-beta", "latest", "banana"],
)
def test_malformed_version_falls_into_the_tolerant_bucket(
    tmp_path: Path, malformed_version: str
) -> None:
    """A malformed version string is treated as absent/below-1.1.0, never raises (G4-R2)."""
    path = _write(
        tmp_path,
        f"**Superhuman-version:** {malformed_version}\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[2026-08-05T00:00:00Z] G4: test plan\n",
    )
    reading = grp.read_record(path)
    assert reading.version_gate is False
    gap = sp.gate_record_gap(reading, gate=5)
    assert gap is None


def test_empty_version_field_falls_into_the_tolerant_bucket(tmp_path: Path) -> None:
    """An empty declared version resolves the same as an absent one -- never raises."""
    assert grp.read_record  # sanity: module import order
    from gate_record_parser import _version_at_least  # noqa: PLC0415

    assert _version_at_least("", (1, 1, 0)) is False
    assert _version_at_least(None, (1, 1, 0)) is False


# ---------------------------------------------------------------------------
# TC-20: FR-4 -- G0 exempt from the preceding-gate rule
# ---------------------------------------------------------------------------


def test_g0_only_record_passes_gate_1(tmp_path: Path) -> None:
    """A record with only a well-formed G0 line passes when gate=1 (predecessor(1) == 0)."""
    path = _write(
        tmp_path,
        "## Decisions log\n[2026-08-01T00:00:00Z] G0: baseline\n",
    )
    reading = grp.read_record(path)
    assert sp.gate_record_gap(reading, gate=1) is None


def test_malformed_line_in_locked_block_fails_even_when_log_is_clean(tmp_path: Path) -> None:
    """FR-3: well-formedness is checked over BOTH sections, wherever present.

    A malformed line inside `## Decisions locked` fails the record even
    though `## Decisions log` (the presence source below 1.1.0) is
    perfectly clean -- this is the case a checker that only inspected the
    presence section would miss.
    """
    path = _write(
        tmp_path,
        "## Decisions locked\n"
        "[not-a-stamp] G0: this locked line is broken\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n",
    )
    reading = grp.read_record(path)
    assert reading.log.well_formed is True
    assert reading.locked.malformed != ()
    gap = sp.gate_record_gap(reading, gate=1)
    assert gap is not None
    assert gap.code == sp.EXIT_RECORD
    assert "Decisions locked" in gap.message


def test_record_with_no_g0_fails_gate_1(tmp_path: Path) -> None:
    """A record missing G0 entirely fails at gate=1."""
    path = _write(
        tmp_path,
        "## Decisions log\n[2026-08-01T00:00:00Z] Constraint: no G0 line here\n",
    )
    reading = grp.read_record(path)
    gap = sp.gate_record_gap(reading, gate=1)
    assert gap is not None
    assert gap.code == sp.EXIT_RECORD


# ---------------------------------------------------------------------------
# TC-22: FR-13 end-to-end -- G7 passes with G5 on record and no G6
# ---------------------------------------------------------------------------


def test_g7_passes_with_g5_present_and_no_g6(tmp_path: Path) -> None:
    """DESIGN's own example: a record at G5 satisfies the predecessor test for G7."""
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[2026-08-05T00:00:00Z] G4: test plan\n"
        "[2026-08-06T00:00:00Z] G5: implementation\n",
    )
    reading = grp.read_record(path)
    gap = sp.gate_record_gap(reading, gate=7)
    assert gap is None


# ---------------------------------------------------------------------------
# TC-24/25/26: FR-14 -- G10 exempt from parse and predecessor tests, together
# ---------------------------------------------------------------------------


def test_g10_passes_on_a_record_that_fails_every_other_check(tmp_path: Path) -> None:
    """G10 passes despite a malformed record; the same record fails at any other gate."""
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[not-a-stamp] G1: this line is broken\n",
    )
    reading = grp.read_record(path)
    assert sp.gate_record_gap(reading, gate=10) is None
    gap_at_5 = sp.gate_record_gap(reading, gate=5)
    assert gap_at_5 is not None
    assert gap_at_5.code == sp.EXIT_RECORD


def test_g10_escapes_a_record_with_no_gates_at_all(tmp_path: Path) -> None:
    """A well-formed record with zero gate entries still permits a G10 escalation."""
    path = _write(
        tmp_path,
        "## Decisions log\n[2026-08-01T00:00:00Z] Constraint: no gate lines at all\n",
    )
    reading = grp.read_record(path)
    assert sp.gate_record_gap(reading, gate=10) is None


def test_g10_escapes_a_record_broken_in_every_way_at_once(tmp_path: Path) -> None:
    """The maximally-broken record (malformed AND gateless) still permits G10."""
    path = _write(
        tmp_path,
        "## Decisions log\n[not-a-stamp] not a valid entry at all\n",
    )
    reading = grp.read_record(path)
    assert sp.gate_record_gap(reading, gate=10) is None


def test_g10_passes_even_when_the_record_file_is_entirely_absent(tmp_path: Path) -> None:
    """G10 is exempt from every other test, including the record simply not existing."""
    reading = grp.read_record(tmp_path / "does-not-exist" / "SUPERHUMAN.md")
    assert reading.exists is False
    assert sp.gate_record_gap(reading, gate=10) is None


# ---------------------------------------------------------------------------
# TC-27/28: FR-5 -- the UNKNOWN sentinel satisfies presence
# ---------------------------------------------------------------------------


def test_unknown_sentinel_satisfies_presence_for_the_next_gate(tmp_path: Path) -> None:
    """`[UNKNOWN] G4: ...` counts as G4 present -- the predecessor test for G5 passes."""
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[UNKNOWN] G4: Enable per-agency wiki write gate\n",
    )
    reading = grp.read_record(path)
    assert sp.gate_record_gap(reading, gate=5) is None


def test_both_slots_unknown_still_satisfies_presence(tmp_path: Path) -> None:
    """Both the timestamp and the recovered-text note may read UNKNOWN; gate still counts."""
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-08-04T00:00:00Z] G3: design\n"
        "[UNKNOWN] G4: UNKNOWN -- no G4 line in git history; gate inferred from the G5 entry\n",
    )
    reading = grp.read_record(path)
    assert sp.gate_record_gap(reading, gate=5) is None


# ---------------------------------------------------------------------------
# TC-29: a fully swept record passes end-to-end, gate by gate
# ---------------------------------------------------------------------------


def test_swept_record_with_unknown_gates_passes_its_own_gate_check(tmp_path: Path) -> None:
    """A record with one UNKNOWN-stamped gate among full-precision ones passes every gate it claims.

    This is the direct regression test for FR-5's stated failure mode: "a
    sweep emitting records that fail the very check it exists to enable."
    """
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[UNKNOWN] G3: repaired from evidence, time unrecoverable\n"
        "[2026-08-05T00:00:00Z] G4: test plan\n"
        "[2026-08-06T00:00:00Z] G5: implementation\n",
    )
    reading = grp.read_record(path)
    for gate in range(0, 6):
        gap = sp.gate_record_gap(reading, gate=gate)
        assert gap is None, f"gate {gate} unexpectedly failed: {gap}"


# ---------------------------------------------------------------------------
# G6-002: the space-separated UTC stamp and continuation lines, end to end
# through `read_record` / `gate_record_gap` -- not just `parse_section`.
# ---------------------------------------------------------------------------


def test_space_separated_utc_stamp_record_passes_its_own_gates(tmp_path: Path) -> None:
    """A record entirely stamped `YYYY-MM-DD HH:MM UTC` (the `hello-cli` shape) reads correctly.

    This is the direct regression test for G6-002's motivating finding: a
    parser that fails to recognise this stamp shape reports a completed
    project's gate set as empty. Genericized shape of the real corpus record.
    """
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-06-23 22:09 UTC] G0: VISION approved; user decision: approve\n"
        "[2026-06-23 22:09 UTC] G1: workflow prefs set; user decision: approve\n"
        "[2026-06-23 22:13 UTC] G2: REQUIREMENTS approved; user decision: approve\n"
        "[2026-06-23 22:16 UTC] G3: DESIGN approved; user decision: approve\n"
        "[2026-06-23 22:19 UTC] G4: TEST plan approved; user decision: approve\n",
    )
    reading = grp.read_record(path)
    assert reading.log.well_formed is True
    assert reading.log.gates == frozenset({0, 1, 2, 3, 4})
    assert sp.gate_record_gap(reading, gate=5) is None


def test_hard_wrapped_continuation_record_passes_its_own_gates(tmp_path: Path) -> None:
    """A record whose long decisions hard-wrap across physical lines still passes cleanly.

    No gate is lost by a wrapped paragraph (G6-002); refusing the record over
    how its prose wraps would measure formatting, not well-formedness.
    """
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff approved after a long discussion that\n"
        "wraps across a second physical line with no bracket prefix at all\n"
        "[2026-08-03T00:00:00Z] G2: requirements approved\n",
    )
    reading = grp.read_record(path)
    assert reading.log.well_formed is True
    assert reading.log.gates == frozenset({0, 1, 2})
    assert sp.gate_record_gap(reading, gate=3) is None


# ---------------------------------------------------------------------------
# TC-B: a gate mentioned only in prose, outside any target section
# ---------------------------------------------------------------------------


def test_gate_mentioned_in_front_matter_prose_does_not_count_as_reached(tmp_path: Path) -> None:
    """A gate number named in front-matter prose (not inside a target section) never registers."""
    path = _write(
        tmp_path,
        "**Superhuman-version:** 1.1.0\n"
        "**Parallelism preference:** PM-decides (architecture-seam splits still surface as G9)\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n",
    )
    reading = grp.read_record(path)
    assert reading.log.gates == frozenset({0, 1})
    assert 9 not in reading.log.gates
    assert reading.log.highest_gate == 1
    # Sanity check that the record's TRUE state, not an inflated one, drives
    # the result: gate 3's predecessor is G2, which this record has not
    # actually reached (it stops at G1) -- a whole-file scan that let the
    # prose-only "G9" leak in would not manufacture a G2 either, but this
    # pins that the true, narrow gate set (not the inflated one) is what a
    # consumer's predecessor check actually sees.
    gap = sp.gate_record_gap(reading, gate=3)
    assert gap is not None
    assert gap.code == sp.EXIT_RECORD


# ---------------------------------------------------------------------------
# FR-16 / G6-005: terminal state does not touch the predecessor test or the
# enforcement path. A closed project never reaches another gate, so an
# exemption there would have no caller -- the reader field alone closes the
# gap (per DECISIONS.md's correction to the peer: highest_gate, not the
# predecessor test, is what makes a closed project read as stalled).
# ---------------------------------------------------------------------------


def test_terminal_record_still_fails_the_predecessor_test_for_an_unreached_gate(
    tmp_path: Path,
) -> None:
    """A `CLOSED` record with a low highest gate is NOT exempted from `gate_record_gap`.

    `terminal_state` is a reporting field only. Nothing about the
    enforcement path changes: a record closed at G2 (with no G3 on record)
    still fails at gate=4 exactly as an ordinary, non-terminal G2 record
    would -- predecessor(4) is 3, which this record never reached.
    """
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] G2: requirements\n"
        "[2026-09-06] CLOSED (not completed): closed early, requirement tracked elsewhere\n",
    )
    reading = grp.read_record(path)
    assert reading.terminal_state == "closed"
    gap = sp.gate_record_gap(reading, gate=4)
    assert gap is not None
    assert gap.code == sp.EXIT_RECORD


def test_gate_disagreement_does_not_change_which_gates_the_enforcement_path_sees(
    tmp_path: Path,
) -> None:
    """`gate_record_gap` reads `presence.gates`, never `highest_gate`/`disagreement` (G6-007b).

    A section in disagreement still enforces on exactly the strictly-parsed
    gate set -- the permissive detector is a reporting signal, not a second
    enforcement path.
    """
    path = _write(
        tmp_path,
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-08-03T00:00:00Z] Escalation to G8 noted: for quick reference only\n",
    )
    reading = grp.read_record(path)
    assert reading.log.disagreement is True
    assert reading.gate_disagreement is True
    # Gate 2's predecessor (1) is present, so gate 2 still passes -- the
    # disagreement on a LATER, unrelated entry does not block it.
    assert sp.gate_record_gap(reading, gate=2) is None
    # Gate 8 was never actually asserted (only mentioned in prose), so it
    # still correctly fails -- disagreement does not manufacture presence.
    gap_at_8 = sp.gate_record_gap(reading, gate=8)
    assert gap_at_8 is not None
    assert gap_at_8.code == sp.EXIT_RECORD
