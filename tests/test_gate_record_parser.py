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
# G6-002 (1): a fourth recognised stamp precision -- `YYYY-MM-DD HH:MM UTC`
# ---------------------------------------------------------------------------


def test_space_separated_utc_minute_stamp_parses() -> None:
    """`[2026-06-23 22:09 UTC]` is a legitimately authored stamp, not corruption.

    Real corpus shape (genericized): `hello-cli`'s entire `## Decisions log`
    uses this space-separated, `UTC`-suffixed form instead of ISO `T...Z`.
    Rejecting it silently dropped all seven of that project's recorded gates
    (G6-002).
    """
    entry = grp.parse_entry("[2026-06-23 22:09 UTC] G7: docs sync approved")
    assert entry is not None
    assert entry.gate == 7
    assert entry.precision == "minute"
    assert entry.timestamp is not None
    assert entry.timestamp.year == 2026
    assert entry.timestamp.month == 6
    assert entry.timestamp.day == 23
    assert entry.timestamp.hour == 22
    assert entry.timestamp.minute == 9
    assert entry.timestamp.tzinfo is not None


def test_space_separated_utc_minute_stamp_on_non_gate_label_parses() -> None:
    """The fourth precision is recognised on non-gate labels too, not just `G<n>:`."""
    entry = grp.parse_entry(
        "[2026-06-23 22:16 UTC] Foundation decision: single chunk, no preceding foundation chunk"
    )
    assert entry is not None
    assert entry.gate is None
    assert entry.precision == "minute"
    assert entry.timestamp is not None


def test_space_separated_utc_calendar_invalid_stamp_does_not_parse() -> None:
    """A calendar-invalid value in the fourth precision's shape still fails closed."""
    assert grp.parse_entry("[2026-13-40 99:99 UTC] G1: bad calendar values") is None


# ---------------------------------------------------------------------------
# G6-002 (2): continuation lines -- a hard-wrapped paragraph is not malformed
# ---------------------------------------------------------------------------


def test_wrapped_continuation_line_folds_into_preceding_entrys_text() -> None:
    """A non-blank line with no `[stamp]` opening folds into the prior entry's text.

    Real corpus shape (genericized): a long decision line hard-wrapped across
    physical lines with no bracket on the continuation lines. No gate is lost
    -- the entry's own opening line already parsed -- so refusing the record
    over how its prose wraps would be measuring formatting, not content
    (G6-002, continuous with G4-R3).
    """
    text = (
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G3: DESIGN approved with a long rationale that\n"
        "wraps onto a second physical line with no bracket at all\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.well_formed is True
    assert reading.malformed == ()
    assert len(reading.entries) == 1
    assert reading.entries[0].gate == 3
    assert reading.entries[0].text == (
        "DESIGN approved with a long rationale that "
        "wraps onto a second physical line with no bracket at all"
    )


def test_multiple_consecutive_continuation_lines_all_fold_in_document_order() -> None:
    """Several wrapped lines in a row all fold into the same preceding entry, in order."""
    text = (
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G4: TEST plan approved with rationale that\n"
        "spans three physical lines total, each one wrapped by hand\n"
        "with no bracket prefix anywhere in the continuation.\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.well_formed is True
    assert len(reading.entries) == 1
    assert reading.entries[0].text == (
        "TEST plan approved with rationale that "
        "spans three physical lines total, each one wrapped by hand "
        "with no bracket prefix anywhere in the continuation."
    )


def test_continuation_line_does_not_register_a_gate_of_its_own() -> None:
    """A `G<n>:` token inside folded continuation prose must NOT register as that gate.

    This is the direct regression test for reintroducing G4-R4's whole-file-
    scan defect at paragraph scale: a wrapped sentence that happens to
    mention `G7:` mid-paragraph is prose, not a second entry, and must not
    contribute to the gate set.
    """
    text = (
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G3: DESIGN approved, superseding the earlier\n"
        "G7: reference in the retired draft, which was never itself a gate line\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.well_formed is True
    assert reading.gates == frozenset({3})
    assert 7 not in reading.gates
    assert reading.highest_gate == 3


def test_orphan_continuation_line_before_any_entry_is_still_malformed() -> None:
    """A non-blank, bracket-less line before any entry has opened is malformed, not swallowed.

    Leading orphan prose (e.g. a heading immediately followed by free text
    with no `[stamp] label:` line at all) must not be silently accepted --
    there is no preceding entry for it to fold into (G6-002).
    """
    text = (
        "## Decisions log\n"
        "stray prose with no stamp at all, appearing before any entry opens\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.well_formed is False
    assert len(reading.malformed) == 1
    assert "stray prose" in reading.malformed[0]
    assert reading.gates == frozenset({0})


def test_bracketed_but_unrecognized_stamp_line_stays_malformed_not_continuation() -> None:
    """A line that structurally attempts a new entry (bracket + label + colon) stays malformed.

    This is the regression anchor distinguishing an attempted-but-broken gate
    line (visible, reported) from ordinary wrapped prose (folded silently):
    a bad stamp on what is otherwise a `label: text` shape must never
    disappear into the preceding entry's text, or a real gate label could be
    silently lost -- the exact failure mode G6-002 exists to eliminate.
    """
    text = (
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[not-a-stamp] G1: this line is broken\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.well_formed is False
    assert len(reading.malformed) == 1
    assert "this line is broken" in reading.malformed[0]
    assert reading.gates == frozenset({0})
    assert 1 not in reading.gates


def test_bracketed_prose_with_no_colon_folds_as_continuation() -> None:
    """A bracketed, validly-stamped line with no `Label:` delimiter at all is a continuation.

    Real corpus shape (genericized): `[2026-07-01] PLAN.md extended with...` --
    a validly-stamped freeform note with no colon anywhere. It never matches
    the entry grammar (no `label:` to find), so -- like unbracketed prose --
    it folds into the preceding entry rather than blocking well-formedness.
    """
    text = (
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] Note: baseline note\n"
        "[2026-07-01] a freeform dated note with no colon delimiter at all\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.well_formed is True
    assert reading.malformed == ()
    assert len(reading.entries) == 1
    assert reading.entries[0].text == (
        "baseline note [2026-07-01] a freeform dated note with no colon delimiter at all"
    )


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


# ---------------------------------------------------------------------------
# `malformed_at` -- line numbers for malformed lines, so a caller can report
# "file:line: text" (DESIGN error table: "message names file, line number,
# and the offending text"), without a second heading-scan implementation.
# ---------------------------------------------------------------------------


def test_malformed_at_reports_one_based_document_line_numbers() -> None:
    """`malformed_at` pairs 1:1 with `malformed`, giving each a document line number."""
    text = (
        "# Title\n"
        "front matter\n"
        "## Decisions locked\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[not-a-stamp] G1: this line is broken\n"
        "\n"
        "[also-not-a-stamp] G2: this one too\n"
    )
    reading = grp.parse_section(text, "## Decisions locked")
    assert reading.malformed == (
        "[not-a-stamp] G1: this line is broken",
        "[also-not-a-stamp] G2: this one too",
    )
    # Line 5 is `[not-a-stamp] ...`; line 7 (after the blank line 6) is the second.
    assert reading.malformed_at == (5, 7)


def test_malformed_at_is_empty_when_nothing_is_malformed() -> None:
    """A well-formed section reports an empty `malformed_at`, paired with `malformed`."""
    reading = grp.parse_section(
        "## Decisions locked\n[2026-08-01T00:00:00Z] G0: baseline\n",
        "## Decisions locked",
    )
    assert reading.malformed == ()
    assert reading.malformed_at == ()


def test_section_not_found_has_empty_malformed_at() -> None:
    """A missing heading reports `malformed_at=()`, matching every other empty field."""
    reading = grp.parse_section("no heading here\n", "## Decisions locked")
    assert reading.malformed_at == ()


# ---------------------------------------------------------------------------
# `MANDATORY_GATES` / `predecessor()` -- FR-13, the non-contiguous predecessor
# rule. Pure functions, no I/O (PLAN chunk 2 step 1).
# ---------------------------------------------------------------------------


def test_mandatory_gates_is_the_documented_non_contiguous_tuple() -> None:
    """G6, G9 and G10 are conditional gates and are absent from the mandatory set."""
    assert grp.MANDATORY_GATES == (0, 1, 2, 3, 4, 5, 7, 8)


# TC-21: the non-contiguous predecessor table, every mandatory gate
@pytest.mark.parametrize(
    "gate,expected",
    [
        (0, None),
        (1, 0),
        (2, 1),
        (3, 2),
        (4, 3),
        (5, 4),
        (6, None),
        (7, 5),
        (8, 7),
        (9, None),
        (10, None),
    ],
)
def test_predecessor_table_g7_predecessor_is_g5_not_g6(gate: int, expected: int | None) -> None:
    """FR-13's whole point in one row: G7's predecessor is G5, not G6 (naive n-1 fails here)."""
    assert grp.predecessor(gate) == expected


# TC-23 (pure-function half): predecessor() never raises on an out-of-range gate
@pytest.mark.parametrize("gate", [-1, 11, 999])
def test_predecessor_returns_none_for_out_of_range_gate_and_never_raises(gate: int) -> None:
    """An out-of-range gate resolves to `None` deterministically -- it must never raise."""
    assert grp.predecessor(gate) is None


# ---------------------------------------------------------------------------
# G6-007a: the label grammar matches a gate token at the HEAD of the label,
# not whole-label equality, and strips trailing emphasis as well as leading.
# Real corpus shapes (genericized per NFR-4) that the original grammar
# silently failed to register -- measured on the live corpus at 8 of 34
# records under-reporting their true highest gate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected_gate,expected_label",
    [
        (
            "[2026-07-02] **G8 ACCEPTANCE**: user accepts the delivered project",
            8,
            "G8 ACCEPTANCE",
        ),
        (
            "[2026-08-16T14:00:00Z] **G8 - ACCEPTANCE. Project accepted; "
            "user decision: accept.**",
            8,
            "G8 - ACCEPTANCE. Project accepted; user decision",
        ),
        ("[2026-09-01] G5 (chunk 8, FINAL chunk): shipped", 5, "G5 (chunk 8, FINAL chunk)"),
        ("[2026-09-01] G6 (moderate - drift): raised", 6, "G6 (moderate - drift)"),
    ],
)
def test_gate_token_at_head_of_label_resolves_despite_trailing_qualifier_text(
    line: str, expected_gate: int, expected_label: str
) -> None:
    """A gate token at the head of a richly worded label registers (G6-007a).

    Only a clean `**G3:**`-shaped label resolved under the original,
    whole-label-equality grammar; trailing qualifier text -- a word, a
    parenthetical, an entire sentence -- silently defeated it. This is the
    direct regression test for the defect measured at 8 of 34 live records.
    """
    entry = grp.parse_entry(line)
    assert entry is not None
    assert entry.gate == expected_gate
    assert entry.label == expected_label


@pytest.mark.parametrize(
    "line",
    [
        "[2026-09-01] Pre-G5 discussion: the token is not at the head",
        "[2026-09-01] Chunk 8 G5 review: the token is not at the head",
        "[2026-09-01] BRIEF: no gate token at all",
        "[2026-09-01] Constraint: no gate token at all",
        "[2026-09-01] Step 0.5: no gate token at all",
        "[2026-09-01] CLOSED: must remain a non-gate entry (FR-16)",
        "[UNKNOWN] LD-2 (G0): the locked block contributes no gates (G6-006)",
    ],
)
def test_head_match_does_not_over_match_a_label_naming_a_gate_off_head(line: str) -> None:
    """A gate token NOT at the head of the label must still yield `gate=None` (G6-007a).

    Widening the grammar to a head match must not become "any gate token
    anywhere": `Pre-G5`/`Chunk 8 G5` name a gate mid-label, and `LD-2 (G0)`
    is the real `fidelity-provider-setup` locked-block shape whose gate
    contribution G6-006 already ruled is none -- a head match must not
    accidentally start resolving it to gate 0.
    """
    entry = grp.parse_entry(line)
    assert entry is not None
    assert entry.gate is None


def test_trailing_emphasis_is_stripped_alongside_leading() -> None:
    """Trailing `**`/`_` after a label is stripped, not just leading (G6-007a, G4-R3's other half)."""
    entry = grp.parse_entry("[2026-08-01T00:00:00Z] **G4**: test plan approved")
    assert entry is not None
    assert entry.gate == 4
    assert entry.label == "G4"


# ---------------------------------------------------------------------------
# G6-007b: the permissive disagreement detector -- a standing safety net for
# the next authored spelling the grammar does not anticipate, independent of
# and in addition to the G6-007a widening.
# ---------------------------------------------------------------------------


def test_disagreement_fires_when_a_label_names_a_higher_gate_than_the_grammar_resolved() -> None:
    """A label the strict grammar cannot resolve, but which names a higher gate, forces disagreement.

    Constructed case, not a live corpus shape: this is the standing safety
    net for a spelling neither the original grammar nor the G6-007a
    widening anticipated. `well_formed` and `gates` are unaffected --
    disagreement is reported alongside them, not in place of them.
    """
    text = (
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G3: baseline reached\n"
        "[2026-08-02T00:00:00Z] Escalation to G8 noted: for quick reference only\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.well_formed is True
    assert reading.gates == frozenset({3})
    assert reading.highest_gate == 3
    assert reading.disagreement is True
    assert reading.reported_highest_gate == grp.GATE_UNKNOWN


def test_disagreement_never_fires_when_the_strict_grammar_already_covers_every_label() -> None:
    """No disagreement when every entry's label names no gate higher than what strict parsing found."""
    text = (
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.disagreement is False
    assert reading.reported_highest_gate == 1


def test_disagreement_scans_only_the_label_never_the_free_text() -> None:
    """A gate number cited in free-text `text` must never trigger disagreement (G6-007b).

    Decision rationale routinely cites other gates by number -- this
    project's own decisions log does it constantly. Scanning `text` would
    make disagreement fire on nearly every entry, defeating the point of a
    *safety net* by turning it into permanent noise.
    """
    text = (
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G3: DESIGN approved, superseding the earlier "
        "reference to G9 in the retired draft\n"
    )
    reading = grp.parse_section(text, "## Decisions log")
    assert reading.disagreement is False
    assert reading.reported_highest_gate == 3


def test_reported_highest_gate_is_none_when_no_gate_and_no_disagreement() -> None:
    """`reported_highest_gate` stays `None` (not `GATE_UNKNOWN`) for a genuinely gateless section.

    `None` ("no gate reached") and `GATE_UNKNOWN` ("could not confidently
    read this") are different claims and must never collapse into each
    other.
    """
    reading = grp.parse_section(
        "## Decisions log\n[2026-08-01T00:00:00Z] Constraint: no gate lines at all\n",
        "## Decisions log",
    )
    assert reading.disagreement is False
    assert reading.highest_gate is None
    assert reading.reported_highest_gate is None


# ---------------------------------------------------------------------------
# FR-16 / G6-005: terminal state is a non-gate ledger entry, surfaced as a
# reader field on `RecordReading`. No grammar change -- both spellings below
# already parse under the existing entry grammar; this only reads that
# parse. Kept in this file (vendorable, G6-005-b): stdlib + pytest only, no
# other superhuman module imported.
# ---------------------------------------------------------------------------


def test_read_record_reports_none_terminal_state_for_an_ordinary_record(tmp_path: Path) -> None:
    """A record with no CLOSED/ABORT marker reports `terminal_state=None`."""
    path = tmp_path / "SUPERHUMAN.md"
    path.write_text(
        "## Decisions log\n[2026-08-01T00:00:00Z] G0: baseline\n",
        encoding="utf-8",
    )
    reading = grp.read_record(path)
    assert reading.terminal_state is None


def test_read_record_recognises_bare_closed_entry(tmp_path: Path) -> None:
    """`[<stamp>] CLOSED: <reason>` -- the canonical form -- reports `terminal_state == 'closed'`."""
    path = tmp_path / "SUPERHUMAN.md"
    path.write_text(
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-09-06] CLOSED: requirement kept, tracked elsewhere\n",
        encoding="utf-8",
    )
    reading = grp.read_record(path)
    assert reading.terminal_state == "closed"


def test_read_record_recognises_closed_with_a_parenthetical_qualifier(tmp_path: Path) -> None:
    """`CLOSED (not completed): ...` -- the real corpus shape -- also reports `'closed'`.

    Genericized shape of `superhuman-init` and `export-markdown-render-deps`
    (G6-005): both were closed short of G8 and both use this spelling.
    """
    path = tmp_path / "SUPERHUMAN.md"
    path.write_text(
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] G1: kickoff\n"
        "[2026-09-06] CLOSED (not completed): the project record was closed early\n",
        encoding="utf-8",
    )
    reading = grp.read_record(path)
    assert reading.terminal_state == "closed"


def test_read_record_recognises_bare_abort_label(tmp_path: Path) -> None:
    """A bare, non-gate `ABORT: <reason>` label reports `terminal_state == 'abort'`."""
    path = tmp_path / "SUPERHUMAN.md"
    path.write_text(
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[2026-08-02T00:00:00Z] ABORT: project canceled by user decision\n",
        encoding="utf-8",
    )
    reading = grp.read_record(path)
    assert reading.terminal_state == "abort"


def test_read_record_recognises_the_gate_line_abort_spelling(tmp_path: Path) -> None:
    """`G3: ABORT -- PROJECT CLOSED` -- the real `memory-sync-evaluation` shape -- reports `'abort'`.

    G4-R5 already ruled this line's project finished, not stalled; this
    pins that `read_record` understands it structurally, with no rewrite
    needed. The gate itself (3) still registers independently -- a gate
    firing and the project being terminal are separate, simultaneous facts.
    """
    path = tmp_path / "SUPERHUMAN.md"
    path.write_text(
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "- [2026-07-27T22:50:39Z] **G3: ABORT -- PROJECT CLOSED.** "
        "PM presented the assessment; user decision: close the project.\n",
        encoding="utf-8",
    )
    reading = grp.read_record(path)
    assert reading.terminal_state == "abort"
    assert reading.log.gates == frozenset({0, 3})


def test_malformed_closed_line_does_not_register_as_terminal_state(tmp_path: Path) -> None:
    """A `CLOSED`-labeled line that fails the entry grammar never becomes a terminal marker.

    It never became a parsed `Entry` in the first place, so there is
    nothing for `_terminal_state` to read; the record is reported as
    malformed, not as silently terminal.
    """
    path = tmp_path / "SUPERHUMAN.md"
    path.write_text(
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[not-a-stamp] CLOSED: this line fails the entry grammar\n",
        encoding="utf-8",
    )
    reading = grp.read_record(path)
    assert reading.terminal_state is None
    assert reading.log.well_formed is False
