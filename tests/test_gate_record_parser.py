"""Unit suite for the canonical gate-record line parser (chunk 1).

Covers FR-1 (the grammar itself), FR-15 (multi-digit gate ordinals), and
NFR-3 (CRLF/CR/LF equivalence). Test cases mirror TEST.md's
"2. Test cases -- grammar (gate_record_parser.py)" section (TC-1..TC-13,
TC-A) at the `parse_entry`/`parse_section` level, since `read_record` and
the version-gated presence test (FR-2/FR-3) are out of this chunk's scope
per PLAN.md chunk 1.

Per NFR-4 (this is a public repo), corpus-sourced fixture lines are
reproduced with their *shape* only -- bullet markers, timestamp precision,
label form, bold-wrap, and line length are preserved, but real private repo
names, slugs, and identifying decision text are replaced with generic
stand-ins.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gate_record_parser as grp  # noqa: E402


# ---------------------------------------------------------------------------
# TC-1: well-formed full-precision entry
# ---------------------------------------------------------------------------


def test_well_formed_full_precision_entry_parses() -> None:
    """A canonical `[<iso>] G<n>: <text>` line parses in full."""
    entry = grp.parse_entry(
        "[2026-08-26T11:19:54Z] G6: The guard is content-based, not string-based"
    )
    assert entry is not None
    assert entry.gate == 6
    assert entry.sub is None
    assert entry.precision == "full"
    assert entry.timestamp is not None
    assert entry.timestamp.year == 2026
    assert entry.timestamp.hour == 11
    assert entry.label == "G6"
    assert entry.text == "The guard is content-based, not string-based"


# ---------------------------------------------------------------------------
# TC-2: bullet-prefixed entry
# ---------------------------------------------------------------------------


def test_bullet_prefixed_entry_parses_identically_to_unprefixed() -> None:
    """A leading `- ` bullet is tolerated and does not change the parse (G4-R3)."""
    bulleted = grp.parse_entry(
        "- [2026-08-23T14:26:37Z] G0: Form = Claude Code skill, "
        "home ~/.claude/skills/example-skill/"
    )
    unbulleted = grp.parse_entry(
        "[2026-08-23T14:26:37Z] G0: Form = Claude Code skill, "
        "home ~/.claude/skills/example-skill/"
    )
    assert bulleted is not None
    assert unbulleted is not None
    assert bulleted.gate == unbulleted.gate == 0
    assert bulleted.text == unbulleted.text
    assert bulleted.timestamp == unbulleted.timestamp
    assert bulleted.precision == unbulleted.precision == "full"


# ---------------------------------------------------------------------------
# TC-3: date-only precision, non-gate label
# ---------------------------------------------------------------------------


def test_date_only_precision_is_a_legal_non_gate_entry() -> None:
    """A date-only stamp on a non-gate label parses; it is not a rejection."""
    entry = grp.parse_entry(
        "[2026-09-05] BRIEF: This project is a customer-facing product for "
        "managing widget orders end to end"
    )
    assert entry is not None
    assert entry.precision == "date"
    assert entry.label == "BRIEF"
    assert entry.gate is None
    assert entry.timestamp is not None
    assert entry.timestamp.year == 2026 and entry.timestamp.month == 9 and entry.timestamp.day == 5


# ---------------------------------------------------------------------------
# TC-4: compound sub-gate label G6-001
# ---------------------------------------------------------------------------


def test_compound_subgate_label_contributes_parent_gate() -> None:
    """`G6-001` parses as gate 6, sub `001` -- it counts as gate 6 fired."""
    entry = grp.parse_entry(
        "[2026-09-06T00:24:00Z] G6-001: The gate presence test is version-gated"
    )
    assert entry is not None
    assert entry.gate == 6
    assert entry.sub == "001"


# ---------------------------------------------------------------------------
# TC-5: G<n>-pre compound form
# ---------------------------------------------------------------------------


def test_g_n_pre_compound_form_parses() -> None:
    """`G0-pre` parses as gate 0, sub `pre`."""
    entry = grp.parse_entry("[2026-08-16T21:08Z] G0-pre: early framing note")
    assert entry is not None
    assert entry.gate == 0
    assert entry.sub == "pre"
    assert entry.precision == "minute"


# ---------------------------------------------------------------------------
# TC-6: legitimate non-gate labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected_label",
    [
        ("[2026-08-16T21:08Z] Pre-project: repo history strategy decided up front", "Pre-project"),
        ("[2026-08-20T10:00:00Z] Constraint: no budget for paid tools", "Constraint"),
        ("[2026-08-21T09:00:00Z] Chunk 1: canonical parser lands first", "Chunk 1"),
        ("[2026-09-05] BRIEF: customer-facing product for order management", "BRIEF"),
        (
            "[2026-08-22T08:00:00Z] Step 0.5 (pre-existing-code drift check): baseline captured",
            "Step 0.5 (pre-existing-code drift check)",
        ),
    ],
)
def test_legitimate_non_gate_labels_parse_as_valid_entries(line: str, expected_label: str) -> None:
    """A non-gate label is a valid entry, not a parse failure.

    "The block parses" means every line is a valid entry; it does not mean
    every line names a gate (DESIGN section "The grammar, concretely").
    """
    entry = grp.parse_entry(line)
    assert entry is not None
    assert entry.gate is None
    assert entry.label == expected_label


# ---------------------------------------------------------------------------
# TC-7: G(\d+), the FR-15 fix, positive case
# ---------------------------------------------------------------------------


def test_g10_parses_as_gate_ten_not_gate_one() -> None:
    """`G10:` parses as gate 10, never as gate 1."""
    entry = grp.parse_entry("[2026-08-24T09:00:00Z] G10: BLOCKED -- escalating to a human")
    assert entry is not None
    assert entry.gate == 10


# ---------------------------------------------------------------------------
# TC-8: multi-digit generalization beyond G10
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gate_number", [11, 23, 100])
def test_multi_digit_gate_generalization_beyond_g10(gate_number: int) -> None:
    """The fix is `\\d+` in general, not a `G10` special case."""
    entry = grp.parse_entry(f"[2026-08-24T09:00:00Z] G{gate_number}: synthetic gate")
    assert entry is not None
    assert entry.gate == gate_number


# ---------------------------------------------------------------------------
# TC-9: regression anchor for the retired single-digit pattern
# ---------------------------------------------------------------------------


def test_legacy_pattern_dropped_g10_did_not_misread_it_as_g1() -> None:
    """Anchors the corrected historical finding.

    The retired pattern this project's canonical parser replaces (it lived in
    a different, private repo -- never imported here) captured a single
    digit after `G`. Against `G10:` it therefore matched `G1` with no
    trailing colon immediately following the captured digit, so the anchored
    `\\s*:` check failed and the WHOLE match failed -- gate 10 was silently
    DROPPED, not misread as gate 1. This local, clearly-labeled copy exists
    only to pin that corrected understanding; it must never be imported from
    production code.
    """
    legacy_pattern = re.compile(r"G(\d)\s*:")
    assert legacy_pattern.search("G10: BLOCKED") is None


# ---------------------------------------------------------------------------
# TC-10: heading prefix match, not equality
# ---------------------------------------------------------------------------


def test_heading_prefix_match_finds_a_variant_heading() -> None:
    """`## Decisions locked -- do not relitigate` is found by prefix match."""
    text = (
        "## Decisions locked -- do not relitigate\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "## Next section\n"
        "[2026-08-02T00:00:00Z] G1: unrelated\n"
    )
    reading = grp.parse_section(text, "## Decisions locked")
    assert reading.found is True
    assert reading.gates == frozenset({0})
    assert reading.highest_gate == 0


# ---------------------------------------------------------------------------
# TC-11: multi-line HTML comment stripping
# ---------------------------------------------------------------------------


def test_multiline_html_comment_is_stripped() -> None:
    """A comment whose continuation lines don't start with `<!--` is excluded whole."""
    text = (
        "## Decisions locked\n"
        "<!-- Changing a locked item requires\n"
        "     a surfaced gate/drift event -- never\n"
        "     a silent edit. -->\n"
        "[2026-08-23T14:26:37Z] G0: Form = Claude Code skill\n"
    )
    reading = grp.parse_section(text, "## Decisions locked")
    assert reading.found is True
    assert len(reading.entries) == 1
    assert reading.entries[0].gate == 0
    assert reading.malformed == ()


def test_single_line_html_comment_is_stripped() -> None:
    """A comment opened and closed on one line is excluded, not parsed as an entry."""
    text = (
        "## Decisions locked\n"
        "<!-- inline note -->\n"
        "[2026-08-23T14:26:37Z] G0: Form = Claude Code skill\n"
    )
    reading = grp.parse_section(text, "## Decisions locked")
    assert len(reading.entries) == 1
    assert reading.malformed == ()


# ---------------------------------------------------------------------------
# TC-A: markdown-emphasis-wrapped gate label
# ---------------------------------------------------------------------------


def test_bold_wrapped_gate_label_still_registers() -> None:
    """Leading `**`/`_` emphasis around a gate label is stripped before matching (G4-R3).

    Real corpus shape (genericized per NFR-4): a bulleted entry whose gate
    label is wrapped in markdown bold, e.g.
    `- [<iso>] **G3: ABORT -- PROJECT CLOSED.** <further prose>`. The label
    slot captured before the first colon is `**G3`; stripping the leading
    emphasis run yields `G3`, which must register as gate 3 -- the
    alternative (silently falling through to a non-gate entry) would
    under-report a project's true furthest gate.
    """
    entry = grp.parse_entry(
        "- [2026-07-27T22:50:39Z] **G3: ABORT -- PROJECT CLOSED.** "
        "PM presented the assessment; user decision: close the project."
    )
    assert entry is not None
    assert entry.gate == 3
    assert entry.label == "G3"


# ---------------------------------------------------------------------------
# Region-scoped scanning (G4-R4) -- the defect this parser must not inherit
# ---------------------------------------------------------------------------


def test_gate_mentioned_outside_target_section_does_not_count() -> None:
    """A gate label appearing outside the target section never registers.

    This is the direct regression test for the defect DESIGN's F-2 found in
    a different (private) repo's `_read_gates`: its docstring claimed it read
    only the locked-decisions block, but its code scanned the whole
    document. `parse_section` must only ever look between the matched
    heading and the next `^## ` line.
    """
    text = (
        "---\n"
        "Parallelism preference: PM-decides (architecture-seam splits still "
        "surface as G9)\n"
        "---\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff approved\n"
        "## Resume packet\n"
        "[2026-08-03T00:00:00Z] G9: this must not count -- outside the section\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.found is True
    assert reading.gates == frozenset({0, 1})
    assert reading.highest_gate == 1
    assert 9 not in reading.gates


def test_section_stops_at_next_heading() -> None:
    """Scanning terminates at the next `^## ` line, even mid-file."""
    text = (
        "## Decisions locked\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "## Something else entirely\n"
        "[2026-08-02T00:00:00Z] G5: must not be read as part of the first section\n"
    )
    reading = grp.parse_section(text, "## Decisions locked")
    assert reading.gates == frozenset({0})


def test_section_not_found_returns_empty_reading() -> None:
    """A heading that never occurs yields `found=False` and an empty reading."""
    reading = grp.parse_section("## Some other heading\ntext\n", "## Decisions locked")
    assert reading.found is False
    assert reading.entries == ()
    assert reading.malformed == ()
    assert reading.gates == frozenset()
    assert reading.highest_gate is None
    assert reading.well_formed is False


# ---------------------------------------------------------------------------
# TC-12: CRLF / CR / LF equivalence (NFR-3)
# ---------------------------------------------------------------------------


_NFR3_FIXTURE_LF = (
    "## Decisions locked\n"
    "- [2026-08-23T14:26:37Z] G0: Form decided\n"
    "[2026-09-05] BRIEF: date-only non-gate entry\n"
    "[2026-08-26T11:19:54Z] G6-001: sub-gate entry\n"
)


def _entry_fingerprints(reading: "grp.SectionReading") -> list[tuple]:
    """Return a comparable summary of a reading's entries, excluding `raw`.

    `raw` is expected to differ across line-ending variants of the same
    logical content, so it is deliberately excluded from the comparison.
    """
    return [
        (entry.gate, entry.sub, entry.timestamp, entry.precision, entry.label, entry.text)
        for entry in reading.entries
    ]


@pytest.mark.parametrize(
    "line_ending",
    ["\n", "\r\n", "\r"],
    ids=["lf", "crlf", "cr"],
)
def test_crlf_cr_lf_parse_identically(line_ending: str) -> None:
    """CRLF, CR and LF records parse to the same result (NFR-3).

    Do not hash raw bytes to prove this -- compare the parsed structure.
    `parse_section` relies on `str.splitlines()`, which already treats
    `\\r\\n`, lone `\\r`, and `\\n` as equivalent line boundaries, so no
    second line-ending-folding implementation is written here (per D-2's
    instruction to reuse the existing fold approach rather than duplicate
    it).
    """
    variant = _NFR3_FIXTURE_LF.replace("\n", line_ending)
    reading = grp.parse_section(variant, "## Decisions locked")
    baseline = grp.parse_section(_NFR3_FIXTURE_LF, "## Decisions locked")

    assert reading.found == baseline.found
    assert reading.gates == baseline.gates
    assert reading.highest_gate == baseline.highest_gate
    assert reading.well_formed == baseline.well_formed
    assert _entry_fingerprints(reading) == _entry_fingerprints(baseline)


# ---------------------------------------------------------------------------
# The UNKNOWN sentinel (D-1 / FR-5) -- parses; presence keys on the ordinal
# ---------------------------------------------------------------------------


def test_unknown_stamp_parses_with_no_timestamp() -> None:
    """`[UNKNOWN] G<n>: <text>` parses; the timestamp slot is None."""
    entry = grp.parse_entry("[UNKNOWN] G4: Enable per-agency wiki write gate")
    assert entry is not None
    assert entry.gate == 4
    assert entry.timestamp is None
    assert entry.precision == "unknown"
    assert entry.text == "Enable per-agency wiki write gate"


def test_unknown_stamp_and_unknown_text_both_parse() -> None:
    """Both slots may independently read UNKNOWN; the free-text note is preserved verbatim."""
    entry = grp.parse_entry(
        "[UNKNOWN] G4: UNKNOWN -- no G4 line in git history; "
        "gate inferred from the G5 entry in Decisions log"
    )
    assert entry is not None
    assert entry.gate == 4
    assert entry.timestamp is None
    assert entry.text == (
        "UNKNOWN -- no G4 line in git history; "
        "gate inferred from the G5 entry in Decisions log"
    )


def test_unknown_entry_still_contributes_to_the_gate_set() -> None:
    """Presence keys on the gate ordinal alone, never the timestamp (D-1)."""
    text = (
        "## Decisions locked\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[UNKNOWN] G4: repaired from evidence, time unrecoverable\n"
    )
    reading = grp.parse_section(text, "## Decisions locked")
    assert reading.gates == frozenset({0, 4})
    assert reading.highest_gate == 4


# ---------------------------------------------------------------------------
# Malformed lines: rejected, and reported, not silently dropped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "[2026-08-01T00:00:00Z G0: missing closing bracket",
        "[2026-08-01T00:00:00Z] G0 missing the colon entirely",
        "[not-a-recognised-stamp] G1: unparseable timestamp slot",
        "[2026-13-40T99:99:99Z] G1: calendar-invalid full-precision stamp",
        "just some prose with no structure at all",
    ],
)
def test_malformed_lines_do_not_parse(line: str) -> None:
    """A line that fails the entry grammar returns None, not a best-effort guess."""
    assert grp.parse_entry(line) is None


def test_malformed_line_inside_a_section_is_recorded_not_dropped() -> None:
    """A malformed line inside a target section is reported via `malformed`, not silently skipped."""
    text = (
        "## Decisions locked\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[not-a-stamp] G1: this line is broken\n"
    )
    reading = grp.parse_section(text, "## Decisions locked")
    assert reading.gates == frozenset({0})
    assert len(reading.malformed) == 1
    assert "this line is broken" in reading.malformed[0]
    assert reading.well_formed is False


def test_blank_lines_inside_a_section_are_skipped() -> None:
    """Blank lines are neither entries nor malformed lines."""
    text = (
        "## Decisions locked\n"
        "\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "\n"
    )
    reading = grp.parse_section(text, "## Decisions locked")
    assert reading.well_formed is True
    assert len(reading.entries) == 1
    assert reading.malformed == ()


# ---------------------------------------------------------------------------
# No second regex for this format exists anywhere in this repo (FR-1)
# ---------------------------------------------------------------------------


def test_gate_entry_pattern_is_exposed_for_reuse() -> None:
    """`GATE_ENTRY` is the one public pattern for this line shape (DESIGN component table)."""
    assert isinstance(grp.GATE_ENTRY, re.Pattern)
