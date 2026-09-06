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
from pathlib import Path

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
        malformed_at: the 1-based document line number of each entry in
            `malformed`, in the same order -- so a caller can report
            "file:line: text" (DESIGN error table) without a second
            heading-scan implementation.
        gates: the set of gate ordinals contributed by `entries`.
        highest_gate: `max(gates)`, or None when no entry names a gate.
    """

    found: bool
    entries: tuple[Entry, ...]
    malformed: tuple[str, ...]
    malformed_at: tuple[int, ...]
    gates: frozenset[int]
    highest_gate: int | None

    @property
    def well_formed(self) -> bool:
        """Whether the section was found and every non-blank line parsed."""
        return self.found and not self.malformed


_NOT_FOUND = SectionReading(
    found=False,
    entries=(),
    malformed=(),
    malformed_at=(),
    gates=frozenset(),
    highest_gate=None,
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
    malformed_at: list[int] = []
    in_comment = False
    for line_no, line in enumerate(lines[start:end], start=start + 1):
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
            malformed_at.append(line_no)
        else:
            entries.append(entry)

    gates = frozenset(entry.gate for entry in entries if entry.gate is not None)
    highest_gate = max(gates) if gates else None
    return SectionReading(
        found=True,
        entries=tuple(entries),
        malformed=tuple(malformed),
        malformed_at=tuple(malformed_at),
        gates=gates,
        highest_gate=highest_gate,
    )


# ---------------------------------------------------------------------------
# The predecessor rule (FR-13) -- superhuman's mandatory gate sequence is not
# contiguous: G6 (drift), G9 (parallelism) and G10 (BLOCKED) are conditional
# and fire ad hoc, so a literal `n - 1` would demand a G6 that most projects
# correctly never have (DESIGN "The predecessor rule").
# ---------------------------------------------------------------------------

#: The eight phase gates every project passes through, in order. G6, G9 and
#: G10 are conditional and are deliberately absent from this tuple.
MANDATORY_GATES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 7, 8)


def predecessor(gate: int) -> int | None:
    """Return the highest mandatory gate strictly below `gate`.

    A conditional gate (6, 9, 10) has no predecessor of its own -- it is
    exempt from the predecessor test, though not (G10 aside) from the parse
    test. Any `gate` outside `MANDATORY_GATES` -- including a conditional
    gate or an out-of-range value such as -1 or 999 -- resolves to `None`
    uniformly and this function never raises (R7): a bad `--gate` at the CLI
    layer is a usage error there, not a crash here.

    Args:
        gate: the gate ordinal being requested.

    Returns:
        The nearest lower mandatory gate, or None when there isn't one (G0,
        a conditional gate, or an out-of-range value).
    """
    if gate not in MANDATORY_GATES:
        return None
    lower = [g for g in MANDATORY_GATES if g < gate]
    return max(lower) if lower else None


# ---------------------------------------------------------------------------
# Record reading (FR-2, FR-3 as amended at G6-001) -- combines both sections
# of one `SUPERHUMAN.md` under the version gate. This is the only place the
# `Superhuman-version` field is read; the caller (`gate_record_gap` in
# `scripts/superhuman_profile.py`) decides what the reading means for one
# requested gate.
# ---------------------------------------------------------------------------

#: The two section headings this module reads. Exported so a caller never
#: needs to spell the heading string a second time.
LOG_HEADING = "## Decisions log"
LOCKED_HEADING = "## Decisions locked"

#: The version at and above which the presence test reads `LOCKED_HEADING`
#: instead of `LOG_HEADING` (FR-3, amended G6-001).
_VERSION_GATE_FLOOR = (1, 1, 0)

#: Matches the front-matter `Superhuman-version:` field, tolerating the
#: markdown-bold wrapping every real record uses (`**Superhuman-version:**`)
#: as well as a plain, unwrapped field.
_VERSION_FIELD = re.compile(
    r"^\*{0,2}Superhuman-version:\*{0,2}\s*(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

#: A strictly well-formed `major.minor.patch` version. Anything else --
#: `"1.1"`, `"v1.1.0"`, `"1.1.0-beta"`, `"latest"`, `""`, `"banana"` -- falls
#: into the tolerant (< 1.1.0) bucket rather than raising (G4-R2).
_STRICT_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _extract_version(text: str) -> str | None:
    """Read the declared `Superhuman-version:` field, verbatim.

    Args:
        text: the full record text.

    Returns:
        The field's value exactly as written, or None when the field is
        absent.
    """
    match = _VERSION_FIELD.search(text)
    return match.group(1) if match else None


def _version_at_least(version: str | None, floor: tuple[int, int, int]) -> bool:
    """Whether a declared version resolves to `>= floor`.

    A missing or malformed version is deliberately never an error here (G4-R2):
    it resolves False, placing the record in the tolerant, pre-1.1.0 bucket
    rather than raising or defaulting to the stricter branch.

    Args:
        version: the raw field value, or None when undeclared.
        floor: the `(major, minor, patch)` floor to compare against.

    Returns:
        True only when `version` is a strict `major.minor.patch` string at
        or above `floor`.
    """
    if version is None:
        return False
    match = _STRICT_VERSION.match(version)
    if match is None:
        return False
    return tuple(int(part) for part in match.groups()) >= floor


@dataclass(frozen=True, slots=True)
class RecordReading:
    """Everything FR-2/FR-3 need to know about one project's gate record.

    Attributes:
        path: the `SUPERHUMAN.md` path that was read.
        exists: whether the file could be found and read as text. False
            covers both "no such file" and an unreadable file (e.g. a
            permissions error); `error` distinguishes them for the message.
        error: a human-readable reason `exists` is False, or None when the
            read succeeded.
        version: the declared `Superhuman-version` field, verbatim, or None
            when front matter has no such field.
        version_gate: whether `version` resolves to `>= 1.1.0` (FR-3,
            amended G6-001). A missing or malformed version resolves False
            -- the tolerant bucket (G4-R2) -- and this never raises.
        log: reading of `## Decisions log` -- present in every real record
            and, per FR-3, always the presence source below the version
            gate.
        locked: reading of `## Decisions locked` -- present only in some
            records; the presence source at or above the version gate.
    """

    path: Path
    exists: bool
    error: str | None
    version: str | None
    version_gate: bool
    log: SectionReading
    locked: SectionReading


def read_record(path: Path) -> RecordReading:
    """Read one project's gate record.

    Reuses `parse_section` for both sections -- this function never
    re-derives the entry grammar (FR-1 is the single owner of that shape).
    An absent or unreadable file is not a parse failure: it is reported via
    `exists`/`error` so the caller can fail closed (NFR-2) with an
    actionable message, rather than raising out of a read-and-report
    contract.

    Args:
        path: path to a `SUPERHUMAN.md` file.

    Returns:
        A `RecordReading`. When the file cannot be read, `log` and `locked`
        are both the empty "not found" reading -- there is no text to
        search.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        empty = parse_section("", LOG_HEADING)
        return RecordReading(
            path=path,
            exists=False,
            error=str(exc),
            version=None,
            version_gate=False,
            log=empty,
            locked=parse_section("", LOCKED_HEADING),
        )

    version = _extract_version(text)
    return RecordReading(
        path=path,
        exists=True,
        error=None,
        version=version,
        version_gate=_version_at_least(version, _VERSION_GATE_FLOOR),
        log=parse_section(text, LOG_HEADING),
        locked=parse_section(text, LOCKED_HEADING),
    )
