"""Canonical parser for superhuman's structured gate-decision lines (FR-1).

This is the single source of truth for the `[<stamp>] <label>: <text>` line
shape used in every project's `## Decisions log` / `## Decisions locked`
section. It replaces the ad hoc, whole-file-scanning regex that previously
lived only in a downstream consumer -- that regex both matched a single
digit after `G` (silently dropping `G10` and higher, FR-15) and was applied
to entire documents rather than to the section it claimed to read (G4-R4).

The module is deliberately stdlib-only and small: it is vendored byte-for-
byte into at least one other, private repo (see DESIGN.md D-2), so its
surface area is a design constraint, not an accident.

Scope note: this module implements the grammar only -- one line
(`parse_entry`) and one section (`parse_section`). Combining sections across
a whole record file, resolving which section governs presence under the
`Superhuman-version` gate (FR-2/FR-3), enumerating the canonical record set
(FR-6), and the predecessor-gate rule (FR-13) are later chunks' concerns and
are not implemented here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

#: The one regex for this line format in the estate (FR-1). Deliberately
#: `\d+`, not `\d`: the retired pattern this replaces (`G(\d)\s*:`, which
#: lived in a different, private repo) captured a single digit, so against
#: `G10:` it matched only `G1` with no colon immediately after -- the
#: anchored `\s*:` check then failed and the WHOLE match failed. That
#: dropped gate 10 silently; it did not misread it as gate 1 (FR-15; see
#: `tests/test_gate_record_parser.py::test_legacy_pattern_dropped_g10_did_not_misread_it_as_g1`
#: for the regression anchor, kept local to the test file on purpose).
GATE_ENTRY = re.compile(
    r"""
    ^
    (?:-\s+)?                  # optional leading bullet marker (G4-R3)
    \[(?P<stamp>[^\]]+)\]      # the timestamp slot
    \s*
    (?P<rawlabel>[^:]+)        # the label, up to the first colon
    :
    \s?
    (?P<text>.*)
    $
    """,
    re.VERBOSE,
)

#: Matches a gate label after any leading markdown emphasis has been
#: stripped: `G0`, `G10`, `G6-001`, `G0-pre`.
_GATE_LABEL = re.compile(r"^G(?P<gate>\d+)(?:-(?P<sub>\d+|pre))?$")

#: Leading bullet/emphasis markers tolerated ahead of a label (G4-R3).
_LEADING_EMPHASIS = re.compile(r"^[*_]+")

#: A line that opens the next markdown section, terminating the current one.
_NEXT_HEADING = re.compile(r"^##\s")

_ISO_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ISO_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: How much of an entry's timestamp is known. Recorded, never enforced
#: (DESIGN "The grammar, concretely") -- the gate check ignores precision
#: entirely.
_PRECISION_FULL = "full"
_PRECISION_MINUTE = "minute"
_PRECISION_DATE = "date"
_PRECISION_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Entry:
    """One well-formed line inside a gate-record section.

    Attributes:
        gate: gate ordinal this entry contributes, or None for a non-gate
            entry (e.g. `BRIEF:`, `Constraint:`). A non-gate entry is a
            legal line, not a parse failure.
        sub: sub-gate suffix, e.g. `"001"` from `G6-001` or `"pre"` from
            `G0-pre`, or None when the label names no sub-gate.
        timestamp: parsed UTC timestamp, or None when the stamp slot reads
            `UNKNOWN` (D-1). Presence of a gate is keyed on `gate` alone and
            must never depend on this field.
        precision: one of `"full"`, `"minute"`, `"date"`, `"unknown"` --
            how much of the timestamp is known.
        label: the label token as written, with any leading bullet marker
            and leading markdown emphasis (`*`/`_`) stripped (G4-R3).
        text: the free-text portion following `label: `, verbatim -- an
            `UNKNOWN -- <note>` value is not parsed further.
        raw: the original line, unmodified, kept for diagnostics.
    """

    gate: int | None
    sub: str | None
    timestamp: datetime | None
    precision: str
    label: str
    text: str
    raw: str


def _parse_stamp(raw: str) -> tuple[datetime | None, str | None]:
    """Classify and parse a stamp slot.

    Args:
        raw: the text captured between `[` and `]`.

    Returns:
        A `(timestamp, precision)` pair. `precision` is `None` when `raw`
        matches none of the sanctioned forms (`UNKNOWN`, or an ISO
        timestamp at full/minute/date precision), signalling the caller to
        reject the whole entry.
    """
    if raw == "UNKNOWN":
        return None, _PRECISION_UNKNOWN
    try:
        if _ISO_FULL.match(raw):
            return (
                datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                _PRECISION_FULL,
            )
        if _ISO_MINUTE.match(raw):
            return (
                datetime.strptime(raw, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc),
                _PRECISION_MINUTE,
            )
        if _ISO_DATE.match(raw):
            return (
                datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc),
                _PRECISION_DATE,
            )
    except ValueError:
        # Syntactically ISO-shaped but a calendar-invalid value (e.g. month
        # 13). Fail the whole entry rather than raise out of a parse-or-None
        # contract.
        return None, None
    return None, None


def parse_entry(line: str) -> Entry | None:
    """Parse one gate-record line.

    Args:
        line: a single line, optionally bullet-prefixed, from a target
            section. May carry a trailing `\\r`/`\\n`.

    Returns:
        The parsed `Entry`, or `None` if the line does not match the
        grammar: an unterminated bracket, a missing colon, or a stamp that
        is neither a recognised ISO precision nor `UNKNOWN`.
    """
    candidate = line.rstrip("\r\n")
    match = GATE_ENTRY.match(candidate)
    if match is None:
        return None

    timestamp, precision = _parse_stamp(match.group("stamp"))
    if precision is None:
        return None

    raw_label = match.group("rawlabel").strip()
    label = _LEADING_EMPHASIS.sub("", raw_label).strip()
    gate_match = _GATE_LABEL.match(label)
    if gate_match is not None:
        gate: int | None = int(gate_match.group("gate"))
        sub: str | None = gate_match.group("sub")
    else:
        gate = None
        sub = None

    return Entry(
        gate=gate,
        sub=sub,
        timestamp=timestamp,
        precision=precision,
        label=label,
        text=match.group("text"),
        raw=line,
    )


@dataclass(frozen=True, slots=True)
class SectionReading:
    """The result of scanning one named, heading-delimited section.

    Attributes:
        found: whether a line starting with the requested heading was
            located at all.
        entries: every line inside the section that parsed as an `Entry`,
            in document order. Blank lines and HTML comments are skipped
            silently -- neither counts as an entry nor as a failure.
        malformed: the raw text of every non-blank, non-comment line inside
            the section that failed to parse, in document order. Callers
            that must fail closed (NFR-2) refuse on a non-empty tuple here
            rather than passing vacuously.
        gates: the set of gate ordinals contributed by `entries`.
        highest_gate: `max(gates)`, or None when no entry names a gate.
    """

    found: bool
    entries: tuple[Entry, ...]
    malformed: tuple[str, ...]
    gates: frozenset[int]
    highest_gate: int | None

    @property
    def well_formed(self) -> bool:
        """Whether the section was found and every non-blank line parsed."""
        return self.found and not self.malformed


_NOT_FOUND = SectionReading(
    found=False, entries=(), malformed=(), gates=frozenset(), highest_gate=None
)


def parse_section(text: str, heading: str) -> SectionReading:
    """Read one heading-delimited section and parse its gate entries.

    Region-scoped, always (G4-R4): only lines strictly between the matched
    heading and the next `^## ` line are considered. A gate label anywhere
    else in the document -- front matter, prose, a different section --
    never registers. This is the property that a whole-file scan (the
    defect this module replaces) violates.

    Args:
        text: the full document text, or any substring containing the
            target section. Any line-ending style is accepted uniformly
            (NFR-3): `str.splitlines()` already treats `\\r\\n`, lone `\\r`,
            and `\\n` as equivalent boundaries, so no separate line-ending
            fold is implemented here.
        heading: the heading to match, e.g. `"## Decisions locked"`.
            Matched by prefix, not equality, so a real variant like
            `"## Decisions locked -- do not relitigate"` is still found.

    Returns:
        A `SectionReading`. When no line starts with `heading`, `found` is
        False and every other field is empty/None.
    """
    lines = text.splitlines()

    start = None
    for index, line in enumerate(lines):
        if line.startswith(heading):
            start = index + 1
            break
    if start is None:
        return _NOT_FOUND

    end = len(lines)
    for index in range(start, len(lines)):
        if _NEXT_HEADING.match(lines[index]):
            end = index
            break

    entries: list[Entry] = []
    malformed: list[str] = []
    in_comment = False
    for line in lines[start:end]:
        stripped = line.strip()
        if not stripped:
            continue
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_comment = True
            continue
        entry = parse_entry(line)
        if entry is None:
            malformed.append(line)
        else:
            entries.append(entry)

    gates = frozenset(entry.gate for entry in entries if entry.gate is not None)
    highest_gate = max(gates) if gates else None
    return SectionReading(
        found=True,
        entries=tuple(entries),
        malformed=tuple(malformed),
        gates=gates,
        highest_gate=highest_gate,
    )
