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

G6-002 correction: the live 35-record corpus surfaced two authored shapes
the original grammar rejected -- rejecting either one is a formatting
complaint dressed up as a well-formedness failure, which is exactly the
distinction this module exists to get right. Both are handled here, not by
the sweeper: (1) a fourth stamp precision, `YYYY-MM-DD HH:MM UTC`
(space-separated, `UTC`-suffixed, minute-precision); and (2) continuation
lines -- inside `parse_section`, a non-blank, non-comment line that does not
itself structurally attempt a `[stamp] label: text` shape folds into the
*preceding* entry's `text` rather than counting as malformed, as long as an
entry has already opened in that section. A line that DOES structurally
attempt the shape (an opening bracket followed eventually by a colon) but
carries an unrecognised stamp is deliberately NOT folded -- it stays
malformed and visible, because it is very likely a real, mistyped gate line,
and folding it away would silently drop the gate the way this project's own
motivating defect did.

G6-007 correction: the label grammar required the *whole* label to equal
`G<n>` and only *leading* emphasis was stripped (G4-R3), so a real, richly
worded label -- `**G8 ACCEPTANCE**`, `G5 (chunk 8, FINAL chunk)`, `G6
(moderate -- drift)` -- silently failed to register its gate. Measured on the
live corpus: 8 of 34 records under-reported their true highest gate this
way, three of them by four gates or more. This is worse than an empty gate
set: it looks like a real, in-range finding rather than a parse failure. Two
changes land together, because either alone leaves a hole:

1. `_GATE_LABEL` now matches a gate token at the *head* of the label rather
   than requiring whole-label equality, and trailing emphasis is stripped
   alongside leading emphasis. Qualifier text after the token no longer
   defeats the match.
2. A permissive disagreement detector runs alongside the grammar,
   permanently (it is not superseded by widening -- it is the standing
   safety net for the *next* spelling nobody has anticipated yet). It scans
   each entry's label -- never its free-text `text`, which routinely cites
   other gates in prose and would make this fire on nearly every entry --
   for any `G<n>` token, independent of where the strict grammar requires it
   to sit. Where that permissive scan finds a higher gate than the grammar
   resolved for the section, `SectionReading.disagreement` is set and
   `reported_highest_gate` reports `GATE_UNKNOWN` rather than the lower,
   confidently-parsed value. An unreadable record is not evidence of a lower
   state, and a parser that falls back to the best value it managed to read
   is asserting something it did not observe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
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

#: Matches a gate token at the HEAD of a label, after leading/trailing
#: markdown emphasis has been stripped -- not whole-label equality (G6-007a).
#: `G0`, `G10`, `G6-001`, `G0-pre` still match in full, but so do
#: `G8 ACCEPTANCE`, `G5 (chunk 8, FINAL chunk)` and `G6 (moderate -- drift)`:
#: only the token at the head is captured, and trailing qualifier text plays
#: no role in whether -- or which -- gate registers. The original
#: `$`-anchored version silently dropped every one of these (module
#: docstring, G6-007).
_GATE_LABEL = re.compile(r"^G(?P<gate>\d+)(?:-(?P<sub>\d+|pre))?\b")

#: Leading bullet/emphasis markers tolerated ahead of a label (G4-R3).
_LEADING_EMPHASIS = re.compile(r"^[*_]+")

#: Trailing markdown emphasis tolerated after a label (G6-007a) -- the other
#: half of G4-R3's leading strip. `**G8 ACCEPTANCE**` and `**G3` are both
#: real corpus shapes; only the leading run was stripped before this change.
_TRAILING_EMPHASIS = re.compile(r"[*_]+$")

#: Permissive scan for a gate token anywhere in a label, independent of
#: where `_GATE_LABEL` requires it to sit (G6-007b). This is the standing
#: safety net for an authored spelling neither the original grammar nor its
#: G6-007a widening anticipated -- deliberately scoped to `label` alone,
#: never `text`: free-text rationale routinely cites other gates by number
#: (this project's own decisions log does it constantly), and scanning it
#: would make disagreement fire on nearly every entry, defeating the point.
_PERMISSIVE_GATE_TOKEN = re.compile(r"\bG(\d+)\b")

#: Sentinel `SectionReading.reported_highest_gate` returns when the
#: permissive detector out-ranks the strict parse (G6-007b): the record
#: carries textual evidence of a gate the grammar could not confidently
#: resolve, and reporting the lower, confidently-parsed value would repeat
#: the exact failure this project exists to eliminate -- asserting a state
#: that was never actually observed. Distinct from `None` (genuinely no
#: gate reached): "I could not read this" and "this stopped here" are
#: different claims.
GATE_UNKNOWN = "UNKNOWN"

#: A line that opens the next markdown section, terminating the current one.
_NEXT_HEADING = re.compile(r"^##\s")

_ISO_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ISO_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Fourth precision, added at G6-002: space-separated, `UTC`-suffixed,
#: minute precision (e.g. `2026-06-23 22:09 UTC`) -- a legitimately authored
#: stamp shape, not corruption (see module docstring).
_SPACE_UTC_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")

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
            and leading/trailing markdown emphasis (`*`/`_`) stripped
            (G4-R3, and trailing as of G6-007a).
        text: the free-text portion following `label: `, verbatim -- an
            `UNKNOWN -- <note>` value is not parsed further.
        raw: the original line, unmodified, kept for diagnostics.
        permissive_gate: the highest gate token found anywhere in `label`
            by the permissive scan (G6-007b), independent of `gate`. `None`
            when the label carries no `G<n>` token at all. Used only to
            compute `SectionReading.disagreement`; never a substitute for
            `gate` in gate-presence logic.
    """

    gate: int | None
    sub: str | None
    timestamp: datetime | None
    precision: str
    label: str
    text: str
    raw: str
    permissive_gate: int | None


def _parse_stamp(raw: str) -> tuple[datetime | None, str | None]:
    """Classify and parse a stamp slot.

    Args:
        raw: the text captured between `[` and `]`.

    Returns:
        A `(timestamp, precision)` pair. `precision` is `None` when `raw`
        matches none of the sanctioned forms (`UNKNOWN`; an ISO timestamp at
        full/minute/date precision; or the space-separated, `UTC`-suffixed
        minute-precision form added at G6-002), signalling the caller to
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
        if _SPACE_UTC_MINUTE.match(raw):
            # Minute precision (G6-002): this shape carries exactly the same
            # information as `_ISO_MINUTE`, just spelled with a space and a
            # `UTC` suffix instead of `T...Z`. Reusing `_PRECISION_MINUTE`
            # rather than minting a fifth vocabulary value keeps "precision"
            # meaning what it says -- how much of the timestamp is known --
            # rather than which of two equivalent notations authored it.
            return (
                datetime.strptime(raw, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc),
                _PRECISION_MINUTE,
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
    label = _TRAILING_EMPHASIS.sub("", label).strip()
    gate_match = _GATE_LABEL.match(label)
    if gate_match is not None:
        gate: int | None = int(gate_match.group("gate"))
        sub: str | None = gate_match.group("sub")
    else:
        gate = None
        sub = None

    permissive_tokens = [int(token) for token in _PERMISSIVE_GATE_TOKEN.findall(label)]
    permissive_gate = max(permissive_tokens) if permissive_tokens else None

    return Entry(
        gate=gate,
        sub=sub,
        timestamp=timestamp,
        precision=precision,
        label=label,
        text=match.group("text"),
        raw=line,
        permissive_gate=permissive_gate,
    )


@dataclass(frozen=True, slots=True)
class SectionReading:
    """The result of scanning one named, heading-delimited section.

    Attributes:
        found: whether a line starting with the requested heading was
            located at all.
        entries: every line inside the section that parsed as an `Entry`,
            in document order. Blank lines and HTML comments are skipped
            silently -- neither counts as an entry nor as a failure. A
            continuation line (see below) is folded into the `text` of the
            entry it extends rather than appearing here as its own `Entry`.
        malformed: the raw text of every non-blank, non-comment line inside
            the section that failed to parse AND that could not be folded
            as a continuation, in document order. Callers that must fail
            closed (NFR-2) refuse on a non-empty tuple here rather than
            passing vacuously.

            G6-002: a line that does not itself structurally attempt the
            `[stamp] label: text` shape (no colon-delimited label at all --
            e.g. a hard-wrapped continuation of a long decision, or a
            bracketed freeform note with no `Label:`) is a **continuation
            line**. It folds into the *preceding* entry's `text` instead of
            landing here, as long as an entry has already opened in this
            section -- a continuation line before any entry has opened is
            still malformed (there is nothing for it to fold into). A line
            that DOES structurally attempt the shape but carries an
            unrecognised stamp is never folded: it stays here, visible,
            because it is very likely a real gate line with a typo'd
            timestamp, and folding it away would silently drop the gate.
        malformed_at: the 1-based document line number of each entry in
            `malformed`, in the same order -- so a caller can report
            "file:line: text" (DESIGN error table) without a second
            heading-scan implementation.
        gates: the set of gate ordinals contributed by `entries`.
        highest_gate: `max(gates)`, or None when no entry names a gate. This
            is always the *strict* value -- never downgraded to
            `GATE_UNKNOWN` even when `disagreement` is True. Use
            `reported_highest_gate` for the value that respects disagreement.
        disagreement: whether the permissive scan (G6-007b) found a gate
            token, in some entry's label, strictly higher than
            `highest_gate`. True means the section contains textual evidence
            of a gate the strict grammar did not confidently resolve to that
            value -- the standing safety net for a spelling neither the
            original grammar nor its G6-007a widening anticipated.
    """

    found: bool
    entries: tuple[Entry, ...]
    malformed: tuple[str, ...]
    malformed_at: tuple[int, ...]
    gates: frozenset[int]
    highest_gate: int | None
    disagreement: bool

    @property
    def well_formed(self) -> bool:
        """Whether the section was found and every non-blank line parsed."""
        return self.found and not self.malformed

    @property
    def reported_highest_gate(self) -> int | str | None:
        """`highest_gate`, downgraded to `GATE_UNKNOWN` on disagreement (G6-007b).

        Returns:
            `GATE_UNKNOWN` when `disagreement` is True; otherwise
            `highest_gate` unchanged (an `int`, or `None` when no gate was
            named at all).
        """
        return GATE_UNKNOWN if self.disagreement else self.highest_gate


def _fold_continuation(previous: Entry, continuation_text: str) -> Entry:
    """Fold one continuation line into the entry it extends (G6-002).

    Joins with a single space, not a newline: the corpus shape this exists
    for is a long decision hard-wrapped across physical lines purely for
    readability, so re-flowing it back into one prose line (rather than
    preserving the arbitrary wrap points as embedded newlines) is the
    faithful reconstruction of the author's intent. `raw` is deliberately
    left untouched -- it keeps its existing documented meaning (the
    entry's own opening line, for diagnostics) rather than growing to span
    every physical line folded into it.

    Args:
        previous: the most recently parsed `Entry` in the current section.
        continuation_text: the continuation line's text, already stripped
            of surrounding whitespace.

    Returns:
        A new `Entry`, identical to `previous` except `text`, which gains
        `continuation_text` appended after a single joining space (or is
        replaced outright if `previous.text` was empty).
    """
    joined = f"{previous.text} {continuation_text}" if previous.text else continuation_text
    return replace(previous, text=joined)


_NOT_FOUND = SectionReading(
    found=False,
    entries=(),
    malformed=(),
    malformed_at=(),
    gates=frozenset(),
    highest_gate=None,
    disagreement=False,
)


def parse_section(text: str, heading: str) -> SectionReading:
    """Read one heading-delimited section and parse its gate entries.

    Region-scoped, always (G4-R4): only lines strictly between the matched
    heading and the next `^## ` line are considered. A gate label anywhere
    else in the document -- front matter, prose, a different section --
    never registers. This is the property that a whole-file scan (the
    defect this module replaces) violates.

    A line inside the section that does not itself open a new entry folds
    into the *preceding* entry's text as a continuation line rather than
    counting as malformed, provided an entry has already opened (G6-002).
    See `SectionReading.malformed` for the exact rule and why a
    structurally-attempted-but-broken entry is never folded this way.

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
        if entry is not None:
            entries.append(entry)
            continue
        if entries and GATE_ENTRY.match(line.rstrip("\r\n")) is None:
            # G6-002: this line does not even structurally attempt a
            # `[stamp] label: text` shape (no colon-delimited label at all),
            # so it is a continuation of the entry most recently opened in
            # this section, not a new entry and not a malformed one. A line
            # that DOES match `GATE_ENTRY` but failed only because its stamp
            # is unrecognised falls through to `malformed` below -- it looks
            # enough like a real (if broken) gate line that swallowing it
            # silently would risk dropping a real gate (the module docstring
            # explains why).
            entries[-1] = _fold_continuation(entries[-1], stripped)
            continue
        malformed.append(line)
        malformed_at.append(line_no)

    gates = frozenset(entry.gate for entry in entries if entry.gate is not None)
    highest_gate = max(gates) if gates else None
    permissive_values = [entry.permissive_gate for entry in entries if entry.permissive_gate is not None]
    permissive_highest = max(permissive_values) if permissive_values else None
    disagreement = permissive_highest is not None and (
        highest_gate is None or permissive_highest > highest_gate
    )
    return SectionReading(
        found=True,
        entries=tuple(entries),
        malformed=tuple(malformed),
        malformed_at=tuple(malformed_at),
        gates=gates,
        highest_gate=highest_gate,
        disagreement=disagreement,
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


# ---------------------------------------------------------------------------
# Terminal state (FR-16, G6-005) -- "closed, but not completed" is a
# non-gate ledger entry, not a gate line, and not a front-matter field
# (G4-R4 keeps the parser scoped to the sections it already reads). No
# grammar change is required: both spellings below already parse under the
# existing entry grammar as legal entries; this only reads that parse.
# ---------------------------------------------------------------------------

_TERMINAL_CLOSED = "closed"
_TERMINAL_ABORT = "abort"


def _entry_terminal_marker(entry: Entry) -> str | None:
    """Whether one parsed entry is a terminal-state ledger marker (FR-16).

    `ABORT` is recognised both as a bare non-gate label (`ABORT: ...`) and
    in its existing gate-line spelling -- `G3: ABORT -- PROJECT CLOSED`, the
    real `memory-sync-evaluation` shape locked at G4-R5 -- so that record
    needs no rewrite to be understood. A gate-line `ABORT` entry still
    registers its own gate normally; the two facts (a gate fired, and the
    project is terminal) are independent and both true at once.

    Args:
        entry: a successfully parsed `Entry` from either target section.

    Returns:
        `"abort"`, `"closed"`, or `None` when the entry is neither.
    """
    if entry.label == "ABORT" or entry.text.startswith("ABORT"):
        return _TERMINAL_ABORT
    if entry.label.startswith("CLOSED"):
        return _TERMINAL_CLOSED
    return None


def _terminal_state(sections: tuple[SectionReading, ...]) -> str | None:
    """Scan a record's sections, in order, for the first terminal-state marker.

    Args:
        sections: the sections to scan, e.g. `(log, locked)`. Both are
            scanned regardless of the version gate -- terminal state is a
            project-level ledger event, not a presence-governed one (FR-3
            governs which section counts *gates*, not this).

    Returns:
        `"abort"`, `"closed"`, or `None` when no section carries a marker.
    """
    for section in sections:
        for entry in section.entries:
            marker = _entry_terminal_marker(entry)
            if marker is not None:
                return marker
    return None


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
        terminal_state: `"closed"`, `"abort"`, or `None` for an ordinary,
            non-terminal record (FR-16, G6-005). A closed project never
            reaches another gate, so a low `highest_gate` on a terminal
            record means finished, not stalled -- distinguishing the two
            without string-matching is this field's entire purpose. Neither
            the predecessor test nor the enforcement path reads this: a
            closed project has no caller left to exempt.
    """

    path: Path
    exists: bool
    error: str | None
    version: str | None
    version_gate: bool
    log: SectionReading
    locked: SectionReading
    terminal_state: str | None

    @property
    def gate_disagreement(self) -> bool:
        """Whether either section's permissive scan out-ranked its strict parse (G6-007b).

        A convenience OR over `log.disagreement` / `locked.disagreement` --
        the authoritative, per-section computation lives on
        `SectionReading`. True here means at least one section carries
        textual evidence of a gate higher than what it confidently parsed.
        """
        return self.log.disagreement or self.locked.disagreement


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
            terminal_state=None,
        )

    version = _extract_version(text)
    log_reading = parse_section(text, LOG_HEADING)
    locked_reading = parse_section(text, LOCKED_HEADING)
    return RecordReading(
        path=path,
        exists=True,
        error=None,
        version=version,
        version_gate=_version_at_least(version, _VERSION_GATE_FLOOR),
        log=log_reading,
        locked=locked_reading,
        terminal_state=_terminal_state((log_reading, locked_reading)),
    )
