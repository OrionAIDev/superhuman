"""FR-7/FR-8/FR-9/FR-10 gate-record sweeper: propose repairs, and (chunk 4+) apply them.

This module reconstructs each gate line's real time from the record's own
`git log -p` history (FR-7) and proposes a normalised `## Decisions
locked` block: the leading bullet is always stripped, but the stamp is
rewritten only when doing so is a strict improvement (G6-004). **Stamp
precision may only increase:**

- Git evidence strictly more precise than what is written -- upgrade
  (e.g. `2026-08-16` -> `2026-08-16T21:08:00Z`). This is FR-7's real value.
- No git evidence, or evidence no more precise than what is written --
  keep the existing stamp byte-unchanged.
- `[UNKNOWN]` is written only where no parseable stamp exists at all: the
  stamp slot already read `UNKNOWN` and no evidence was found to fill it
  in (D-1). It is never written in place of a real, human-recorded
  timestamp merely because *git* has no evidence for it -- a chunk-3
  defect that conflated "no git evidence" with "no evidence at all" and
  proposed downgrading every real, already-stamped gate line in this
  repo's own records to `[UNKNOWN]` (`DECISIONS.md` G6-004;
  `delta-report-G6-004.md`). A record whose locked block never carried a
  stamp at all (the OI-3 `LD-n` shape, G6-003-b) still reports `[UNKNOWN]`
  when it has no git evidence either -- that is not a downgrade, because
  there was never a written timestamp to protect.

The resulting property is named and tested (`test_sweep_never_downgrades_a_stamp`):
the sweep is monotone -- it can only ever add information, never remove
it -- which is what makes it safe to run repeatedly and safe to run
against a population nobody has audited line by line. It never rewrites a
decision's wording, because wording is not something git evidence can
adjudicate (DESIGN's Sweep data flow).

:func:`propose` never writes to disk -- it is the pure half of this module,
used by both `--dry-run` and `--apply`. :func:`apply` is the write path,
wired to the CLI's `--apply` flag: it backs up the record, verifies the
backup is readable and byte-identical to the source, and only then
overwrites the record -- failing closed (no write at all) if the backup
step fails for any reason (`DECISIONS.md` G6-004-d). For this repo's own
six records the backup is not belt-and-braces -- they are `.gitignore`d, so
there is no git history to revert to, and the backup under
`~/.superhuman/gate-record-backups/<run-id>/` is the only recovery
mechanism that exists. `apply()` on a record with nothing to change (not
applicable, already normalised, or aborted under FR-9) is a no-op: no
backup is written and the record is never touched, because there is
nothing to back up.

FR-9 is a hard invariant, not a warning: every byte outside the matched
`## Decisions locked` block must be identical before and after. This
module guarantees that structurally -- the text before and after the block
is copied verbatim into the proposal and never touched by any rewrite
logic -- and then verifies the guarantee by reconstructing the original
from the same slices before proposing any change, aborting the record
(proposing no change at all) if that reconstruction ever disagrees with
what was actually read from disk.

Two shape classes the tolerant grammar deliberately leaves malformed are
repaired here instead, per `DECISIONS.md` G6-003-b: an OI-3 `LD-n (...):`
block (`fidelity-provider-setup`'s style) is rewritten into canonical gate
lines when it names a gate in parentheses, and left as a canonical
non-gate entry otherwise. A line this module cannot recognise under any
known repair heuristic is left completely untouched, byte-for-byte --
FR-8 forbids guessing at a repair as much as it forbids guessing at a
timestamp.

This module owns no new entry-line grammar (FR-1's single owner is
`gate_record_parser.py`): every gate-shaped line here is still parsed with
`gate_record_parser.parse_entry`/`GATE_ENTRY`. What this module adds is
section-*boundary* detection that is fence-aware -- a real corpus record
can contain an example `## Decisions locked` heading quoted inside a
fenced code block ahead of the genuine section, and a naive first-match
scan (which is all `parse_section` needs for its own, narrower job) would
mistake the quoted example for the real boundary. Getting the boundary
right is this module's job because it is what "outside the block" means
for FR-9.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

try:
    # Package context: `from .. import gate_record_sweeper` (mirrors
    # `superhuman_profile.py`'s own dual-import convention).
    from . import gate_record_parser
except ImportError:
    # Flat-script context: `scripts/` is on `sys.path` directly, as every
    # test module in this repo already assumes.
    import gate_record_parser

#: The sanctioned sentinel for an unevidenced stamp (D-1). Presence of a
#: gate keys on the ordinal alone, never on this token.
UNKNOWN_STAMP = "UNKNOWN"

#: A markdown heading, terminating the current section (mirrors
#: `gate_record_parser`'s private `_NEXT_HEADING`; duplicated here because
#: this module's boundary search is fence-aware and the parser's is not --
#: see the module docstring).
_NEXT_HEADING = re.compile(r"^##\s")

#: A fenced-code-block delimiter. Toggled, not matched exactly, since a
#: fence closes with the same three backticks it opens with.
_FENCE = re.compile(r"^\s*```")

#: The OI-3 `LD-n (...):` shape (`fidelity-provider-setup`'s style,
#: G6-003-b) -- a bullet- and bold-wrapped label with NO `[stamp]` slot at
#: all, so it never matches `GATE_ENTRY` and stays malformed until this
#: repair runs. Not a second entry-grammar: this recognises a documented,
#: *non*-conforming shape in order to rewrite it into the canonical one.
_LD_LINE = re.compile(
    r"""
    ^
    (?:-\s+)?                      # optional bullet
    \*{0,2}                        # optional leading emphasis
    LD-(?P<num>\d+)
    \s*
    (?:\((?P<paren>[^)]*)\))?      # optional gate reference, e.g. "(G0)"
    \s*
    :\*{0,2}
    \s?
    (?P<text>.*)
    $
    """,
    re.VERBOSE,
)

#: Matches a parenthetical that names exactly one gate, e.g. `"G0"`.
_PAREN_GATE = re.compile(r"^G(\d+)$")


# ---------------------------------------------------------------------------
# Fence-aware section-boundary detection (see module docstring)
# ---------------------------------------------------------------------------


def _find_section_bounds(lines: Sequence[str], heading: str) -> tuple[int, int] | None:
    """Locate the `[start, end)` line-index span of the real `heading` section.

    Skips any occurrence of `heading` enclosed in a fenced code block
    (```` ``` ````...```` ``` ````), which would otherwise be mistaken for
    the section boundary -- a real corpus record quotes the heading inside
    a fence as a worked example ahead of the genuine section (TEST.md
    TC-43). A plain first-match scan, which is all `parse_section` needs
    for its own job, gets this wrong.

    Args:
        lines: the full document, split with `str.splitlines(keepends=True)`
            so each element retains its original line-ending bytes.
        heading: the heading to match, e.g. `"## Decisions locked"`.
            Matched by prefix, like `parse_section` (a real variant such as
            `"## Decisions locked -- do not relitigate"` must still be found).

    Returns:
        `(start, end)`: `start` is the index of the heading line itself;
        `end` is the exclusive index where the section stops (the next
        real, non-fenced `^## ` line, or `len(lines)`). `None` when no
        non-fenced occurrence of `heading` exists at all.
    """
    in_fence = False
    start = None
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith(heading):
            start = index
            break
    if start is None:
        return None

    in_fence = False
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _NEXT_HEADING.match(line):
            end = index
            break
    return start, end


def _line_ending(line: str) -> str:
    """Return the trailing line-ending substring of `line`, or `""`.

    Recognises `"\\r\\n"`, lone `"\\n"`, and lone `"\\r"` (NFR-3) -- the same
    three forms `str.splitlines()` already treats as equivalent boundaries.

    Args:
        line: one element of a `str.splitlines(keepends=True)` split.

    Returns:
        The line-ending substring, or `""` for the file's final line when
        it carries no trailing newline at all.
    """
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


# ---------------------------------------------------------------------------
# Git evidence (FR-7, FR-8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Commit:
    """One commit's evidence for a single file's history.

    Attributes:
        sha: the commit hash, for diagnostics only.
        committer_date: the commit's committer date, exactly as `git`
            reports it via `%cI` (ISO 8601 with a UTC offset) -- FR-7
            specifies the committer date, not the author date.
        added_lines: every line this commit's diff added to the file, in
            patch order, with the leading `+` already stripped.
    """

    sha: str
    committer_date: str
    added_lines: tuple[str, ...]


def _extract_added_lines(patch: str) -> tuple[str, ...]:
    """Pull every added content line out of one commit's unified diff.

    Args:
        patch: the patch text following one commit's formatted header.

    Returns:
        Added lines (the `+` prefix stripped), excluding the `+++ b/...`
        file-header line, in the order they appear in the patch.
    """
    added: list[str] = []
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
    return tuple(added)


def _parse_log_patch(output: str) -> list[_Commit]:
    """Parse `git log --follow --reverse -p --pretty=format:'%x01%H%x01%cI'` output.

    `\\x01` is used as a field separator because it cannot appear in a
    commit SHA, an ISO-8601 date, or ordinary patch text, so splitting on
    it is unambiguous without a second parsing pass.

    Args:
        output: the raw stdout of the `git log` invocation.

    Returns:
        One `_Commit` per commit touching the file, in the order `git`
        emitted them (oldest first, given `--reverse`).
    """
    if not output:
        return []
    parts = output.split("\x01")
    commits: list[_Commit] = []
    for sha, rest in zip(parts[1::2], parts[2::2]):
        date_str, _, patch = rest.partition("\n")
        commits.append(
            _Commit(sha=sha, committer_date=date_str.strip(), added_lines=_extract_added_lines(patch))
        )
    return commits


def _git_log_for_evidence(path: Path) -> list[_Commit]:
    """Run `git log -p --follow` for one file's full history, oldest first.

    Args:
        path: absolute path to the file (does not need to be relative to
            the repository root -- `git` resolves an absolute pathspec
            against whatever repository `cwd` is inside).

    Returns:
        Every commit touching `path`, oldest first, or `[]` when `path` is
        not inside a git repository, has no history, or `git` is
        unavailable -- all three are "no evidence", not an error (FR-8's
        caller treats an empty result as UNKNOWN, never as a crash).
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--follow",
                "--reverse",
                "-p",
                "--pretty=format:%x01%H%x01%cI",
                "--",
                str(path),
            ],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return _parse_log_patch(result.stdout)


def _to_canonical_stamp(committer_date: str) -> str:
    """Convert a git ISO-8601 committer date to the full-precision UTC stamp.

    Args:
        committer_date: e.g. `"2026-08-23T14:26:37-04:00"` (git's `%cI`).

    Returns:
        The same instant rendered as `YYYY-MM-DDTHH:MM:SSZ`.
    """
    parsed = datetime.fromisoformat(committer_date)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class _Evidence:
    """Git-derived evidence for one record's gate lines.

    Attributes:
        by_content: maps `(gate, sub, label, text)` -- a well-formed
            entry's content, ignoring its stamp -- to the canonical stamp
            of the earliest commit that added a matching line. The stamp
            slot is excluded from the key deliberately: it is the very
            thing being reconstructed, so matching on it would beg the
            question.
        by_raw_text: maps a malformed line's stripped raw text (used for
            shapes `parse_entry` cannot parse at all, e.g. the OI-3 `LD-n`
            lines) to the same earliest-commit stamp.
        has_differentiation: `True` only when the file's history has two
            or more commits. With fewer than two, every current line
            necessarily traces back to the *same* single commit, which
            gives no way to tell "gate 0 was decided here" apart from
            "gate 4 was decided here" -- exactly the squashed/rewritten-
            history trap FR-8 exists to refuse (TEST.md TC-39; superhuman's
            own history was rewritten in 2026-08, REQUIREMENTS Assumption
            1). When this is `False`, every line is reported `[UNKNOWN]`
            regardless of what `by_content`/`by_raw_text` happen to
            contain.
    """

    by_content: dict[tuple[int | None, str | None, str, str], str]
    by_raw_text: dict[str, str]
    has_differentiation: bool


_NO_EVIDENCE = _Evidence(by_content={}, by_raw_text={}, has_differentiation=False)


def _collect_evidence(path: Path) -> _Evidence:
    """Build the git-derived evidence set for one record's gate lines.

    Args:
        path: the `SUPERHUMAN.md` path being swept.

    Returns:
        An `_Evidence` reflecting that file's full commit history, or
        `_NO_EVIDENCE` when there is none or it does not differentiate
        (fewer than two commits).
    """
    commits = _git_log_for_evidence(path)
    if len(commits) < 2:
        return _NO_EVIDENCE

    by_content: dict[tuple[int | None, str | None, str, str], str] = {}
    by_raw_text: dict[str, str] = {}
    for commit in commits:
        stamp = _to_canonical_stamp(commit.committer_date)
        for added_line in commit.added_lines:
            stripped = added_line.strip()
            if not stripped:
                continue
            by_raw_text.setdefault(stripped, stamp)
            entry = gate_record_parser.parse_entry(added_line)
            if entry is not None:
                key = (entry.gate, entry.sub, entry.label, entry.text)
                by_content.setdefault(key, stamp)
    return _Evidence(by_content=by_content, by_raw_text=by_raw_text, has_differentiation=True)


# ---------------------------------------------------------------------------
# Line-level repair (bullet + stamp only; FR-9's "wording is never touched")
# ---------------------------------------------------------------------------


#: `Entry.precision` value denoting the maximum precision `gate_record_parser`
#: recognises. Reconstructed git evidence (`_to_canonical_stamp`'s output) is
#: always this precision, so it can never be "strictly more precise" than an
#: already-full-precision written stamp (G6-004) -- comparing against this
#: constant, rather than re-deriving a precision ordering, is what keeps this
#: module from re-implementing `gate_record_parser`'s stamp grammar.
_MAX_PRECISION = "full"


def _normalise_entry_line(content: str, entry: gate_record_parser.Entry, evidence: _Evidence) -> tuple[str, bool]:
    """Render one already-well-formed entry with its bullet stripped and its stamp normalised.

    Stamp precision may only increase (G6-004). The reconstructed value,
    when git evidence exists, is always full precision -- so it upgrades
    the stamp only when the written stamp is not already at full
    precision. Every other case -- no evidence at all, or a written stamp
    already at full precision -- keeps the existing stamp byte-unchanged:
    overwriting it for lack of *git* evidence would delete a timestamp a
    human actually recorded, which is itself the only evidence that gate
    will ever have (`DECISIONS.md` G6-004). `[UNKNOWN]` is rendered here
    only when the stamp as written was already `UNKNOWN` and no evidence
    was found to fill it in -- never as a downgrade of a real timestamp.

    Only the bullet and the stamp change. The label is rendered exactly as
    written -- including any leading markdown emphasis -- because chunk 3's
    mandate is "normalises only the bullet and the stamp"; stripping
    emphasis from a label is a *reading* concern
    (`gate_record_parser.parse_entry`'s job), not a rewrite this module is
    charged with making.

    Args:
        content: the original line, with any trailing newline already
            removed.
        entry: the line's already-parsed `Entry`.
        evidence: this record's git evidence.

    Returns:
        `(rendered_line, is_unknown)` where `rendered_line` carries no
        trailing newline and `is_unknown` reports whether the rendered
        stamp is the `[UNKNOWN]` sentinel.
    """
    match = gate_record_parser.GATE_ENTRY.match(content)
    raw_label = match.group("rawlabel").strip() if match else entry.label
    written_stamp = match.group("stamp") if match else UNKNOWN_STAMP

    reconstructed = None
    if evidence.has_differentiation:
        key = (entry.gate, entry.sub, entry.label, entry.text)
        reconstructed = evidence.by_content.get(key)

    if reconstructed is not None and entry.precision != _MAX_PRECISION:
        rendered_stamp = reconstructed
    else:
        rendered_stamp = written_stamp

    is_unknown = rendered_stamp == UNKNOWN_STAMP
    rendered = f"[{rendered_stamp}] {raw_label}: {entry.text}" if entry.text else f"[{rendered_stamp}] {raw_label}:"
    return rendered, is_unknown


def _try_ld_repair(content: str, evidence: _Evidence) -> tuple[str, int | None, bool] | None:
    """Best-effort repair for the OI-3 `LD-n (...):` shape (G6-003-b).

    A parenthetical naming exactly one gate (`"(G0)"`) becomes that gate's
    canonical label; anything else (`"(immutable, from invocation)"`, or no
    parenthetical at all) is preserved verbatim as part of a non-gate
    label, so no wording is invented or dropped -- only the missing
    `[stamp]` slot is added and the `LD-n` bullet/bold-wrap scaffolding is
    normalised away.

    Args:
        content: the original line, trailing newline already removed.
        evidence: this record's git evidence.

    G6-004 note: an `LD-n` line carries no `[stamp]` slot at all -- there is
    no existing timestamp for this repair to protect, so writing `UNKNOWN`
    here when no evidence exists is not a downgrade (there was nothing to
    downgrade from); the monotonicity property holds trivially for this
    shape, unlike an already-stamped `GATE_ENTRY` line.

    Returns:
        `(rendered_line, gate, is_unknown)`, or `None` when `content` does
        not match the known `LD-n` shape at all -- callers must then leave
        the line completely untouched rather than guess at a repair
        (FR-8 forbids guessing at a repair as much as at a timestamp).
    """
    match = _LD_LINE.match(content)
    if match is None:
        return None

    paren = (match.group("paren") or "").strip()
    text = match.group("text").strip()
    gate_match = _PAREN_GATE.match(paren)
    if gate_match is not None:
        gate: int | None = int(gate_match.group(1))
        label = f"G{gate}"
    else:
        gate = None
        label = f"LD-{match.group('num')}" + (f" ({paren})" if paren else "")

    stamp = evidence.by_raw_text.get(content.strip()) if evidence.has_differentiation else None
    is_unknown = stamp is None
    rendered_stamp = stamp if stamp is not None else UNKNOWN_STAMP
    rendered = f"[{rendered_stamp}] {label}: {text}" if text else f"[{rendered_stamp}] {label}:"
    return rendered, gate, is_unknown


@dataclass(frozen=True, slots=True)
class ProposedLine:
    """One line of the sweep's proposed `## Decisions locked` block.

    Attributes:
        original: the original raw line (with its line ending), or `None`
            when `source == "added"` -- a gate the git log evidences but
            the block omitted entirely.
        rendered: the line as the sweep proposes to write it, with its
            line ending.
        gate: the gate ordinal this line contributes, or `None` for a
            non-gate entry.
        source: `"kept"` (already normalised -- no visible change),
            `"normalised"` (bullet and/or stamp rewritten), `"repaired"`
            (recovered via the OI-3 `LD-n` heuristic), `"added"` (a gate
            evidenced by history but missing from the block), or
            `"unrepaired"` (malformed, and no known repair applies -- left
            completely untouched).
    """

    original: str | None
    rendered: str
    gate: int | None
    source: str


def _classify_and_render(
    body_lines: Sequence[str], evidence: _Evidence
) -> tuple[list[ProposedLine], list[str], int]:
    """Walk every physical line of the block body and decide what to propose.

    Every physical line is preserved in place: a blank line, an HTML
    comment line, and a continuation line (G6-002 -- prose folded into the
    preceding entry, never reflowed) all pass through byte-identical. Only
    a line that itself opens an entry -- well-formed already, or repairable
    via a known heuristic -- has its bullet stripped and its stamp
    normalised.

    Args:
        body_lines: the block's content lines (the heading itself and
            everything at or after the next section boundary are excluded),
            each retaining its original line ending.
        evidence: this record's git evidence.

    Returns:
        `(proposed_lines, rendered_body, unknown_count)`: `proposed_lines`
        describes every entry-shaped line encountered (for reporting);
        `rendered_body` is `body_lines` with only those lines rewritten,
        suitable for re-joining into the full proposed text; `unknown_count`
        is how many rendered gate stamps are the `[UNKNOWN]` sentinel.
    """
    rendered: list[str] = []
    proposed: list[ProposedLine] = []
    in_comment = False
    have_open_entry = False
    unknown_count = 0

    for raw_line in body_lines:
        ending = _line_ending(raw_line)
        content = raw_line[: len(raw_line) - len(ending)] if ending else raw_line
        stripped = content.strip()

        if not stripped:
            rendered.append(raw_line)
            continue
        if in_comment:
            rendered.append(raw_line)
            if "-->" in content:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            rendered.append(raw_line)
            if "-->" not in stripped:
                in_comment = True
            continue

        entry = gate_record_parser.parse_entry(content)
        if entry is not None:
            have_open_entry = True
            new_content, is_unknown = _normalise_entry_line(content, entry, evidence)
            new_line = new_content + ending
            rendered.append(new_line)
            source = "kept" if new_content == stripped else "normalised"
            proposed.append(ProposedLine(original=raw_line, rendered=new_line, gate=entry.gate, source=source))
            if is_unknown:
                unknown_count += 1
            continue

        # A known repair (the OI-3 `LD-n` shape) is tried before falling
        # back to continuation-line folding: an `LD-n` line carries no
        # `[stamp]` at all, so it never structurally attempts the entry
        # shape either, and would otherwise be mistaken for a continuation
        # of whatever entry -- repaired or not -- most recently opened.
        repaired = _try_ld_repair(content, evidence)
        if repaired is not None:
            new_content, gate, is_unknown = repaired
            have_open_entry = True
            new_line = new_content + ending
            rendered.append(new_line)
            proposed.append(ProposedLine(original=raw_line, rendered=new_line, gate=gate, source="repaired"))
            if is_unknown:
                unknown_count += 1
            continue

        if have_open_entry and gate_record_parser.GATE_ENTRY.match(content) is None:
            # G6-002 continuation line: never reflowed, copied verbatim.
            rendered.append(raw_line)
            continue

        rendered.append(raw_line)
        proposed.append(ProposedLine(original=raw_line, rendered=raw_line, gate=None, source="unrepaired"))

    return proposed, rendered, unknown_count


def _propose_additions(
    proposed_lines: list[ProposedLine], evidence: _Evidence
) -> tuple[list[ProposedLine], list[str]]:
    """Propose re-adding a bare gate the git log evidences but the block omits.

    Deliberately narrow: only a *bare* gate entry (no sub-gate) whose
    content the history evidences and whose gate is not already present
    among the block's kept/normalised/repaired lines is a candidate --
    reusing verbatim text and label the history itself supplies, never
    inventing wording (DESIGN's Sweep data flow: "it may add a line for a
    gate the log evidences but the block omits ... it never rewrites a
    decision's wording").

    Args:
        proposed_lines: the result of `_classify_and_render` for the
            block's existing content.
        evidence: this record's git evidence.

    Returns:
        `(additional_proposed_lines, additional_rendered_lines)`, both
        empty when there is no differentiated evidence or nothing to add.
    """
    if not evidence.has_differentiation:
        return [], []

    existing_gates = {line.gate for line in proposed_lines if line.gate is not None}
    best_per_gate: dict[int, tuple[tuple[int | None, str | None, str, str], str]] = {}
    for key, stamp in evidence.by_content.items():
        gate, sub, _label, _text = key
        if gate is None or sub is not None or gate in existing_gates:
            continue
        current = best_per_gate.get(gate)
        if current is None or stamp < current[1]:
            best_per_gate[gate] = (key, stamp)

    additional_proposed: list[ProposedLine] = []
    additional_rendered: list[str] = []
    for gate in sorted(best_per_gate):
        (_gate, _sub, label, text), stamp = best_per_gate[gate]
        rendered = f"[{stamp}] {label}: {text}\n" if text else f"[{stamp}] {label}:\n"
        additional_rendered.append(rendered)
        additional_proposed.append(ProposedLine(original=None, rendered=rendered, gate=gate, source="added"))
    return additional_proposed, additional_rendered


# ---------------------------------------------------------------------------
# The proposal
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Proposal:
    """The sweep's proposed rewrite of one record's `## Decisions locked` block.

    Attributes:
        path: the `SUPERHUMAN.md` path this proposal targets.
        applicable: whether this record has a `## Decisions locked` block
            at all. Chunk 3 only proposes repairs for records that do
            (DESIGN's Sweep data flow: "for each record with a
            `## Decisions locked` block").
        skipped_reason: why `applicable` is `False`, or `None`.
        original_text: the full file text exactly as read from disk (UTF-8
            decoded, no newline translation).
        proposed_text: `original_text` with only the block rewritten, or
            identical to `original_text` when not applicable, or when
            `aborted`.
        aborted: `True` when FR-9's invariant could not be guaranteed and
            the record was left completely untouched -- fatal, not a
            warning (DESIGN's error table).
        abort_reason: why `aborted` is `True`, or `None`.
        diff: a unified diff between `original_text` and `proposed_text`
            (empty when nothing changed).
        unknown_count: how many proposed gate stamps are the `[UNKNOWN]`
            sentinel.
        lines: every entry-shaped or repaired/added line the sweep
            considered, in the order they appear in the proposed block.
    """

    path: Path
    applicable: bool
    skipped_reason: str | None
    original_text: str
    proposed_text: str
    aborted: bool
    abort_reason: str | None
    diff: str
    unknown_count: int
    lines: tuple[ProposedLine, ...]

    @property
    def changed(self) -> bool:
        """Whether the proposal differs from the original text at all."""
        return self.proposed_text != self.original_text


def _not_applicable(path: Path, original_text: str, reason: str) -> Proposal:
    """Build the `Proposal` for a record with no `## Decisions locked` block."""
    return Proposal(
        path=path,
        applicable=False,
        skipped_reason=reason,
        original_text=original_text,
        proposed_text=original_text,
        aborted=False,
        abort_reason=None,
        diff="",
        unknown_count=0,
        lines=(),
    )


def _aborted(path: Path, original_text: str, reason: str) -> Proposal:
    """Build the `Proposal` for a record FR-9 forbids changing (fatal path)."""
    return Proposal(
        path=path,
        applicable=True,
        skipped_reason=None,
        original_text=original_text,
        proposed_text=original_text,
        aborted=True,
        abort_reason=reason,
        diff="",
        unknown_count=0,
        lines=(),
    )


def propose(path: Path) -> Proposal:
    """Propose a repair for one record's `## Decisions locked` block.

    Reconstructs each gate line's real time from `git log -p --follow`
    over `path`'s own history (FR-7), and upgrades the written stamp only
    when that reconstruction is strictly more precise (G6-004). A line with
    no differentiating git evidence -- including every line in a record
    whose entire history is one commit, the squashed/rewritten-history trap
    (TEST.md TC-39) -- keeps whatever stamp is already written, byte-
    unchanged; only a stamp that was already `UNKNOWN`, with no evidence to
    fill it in, is rendered `UNKNOWN`. Nothing is ever guessed, interpolated,
    or inferred from a neighbouring line (FR-8), and a real, human-recorded
    timestamp is never downgraded merely because git itself has no evidence
    for it.

    Only the bullet and the stamp are normalised. Blank lines, HTML
    comments, and continuation lines (G6-002) pass through byte-identical.
    Every byte outside the matched section -- including the heading line
    itself -- is guaranteed identical between `original_text` and
    `proposed_text`; that guarantee is verified, not merely assumed, before
    any change is proposed (FR-9 is fatal, not a warning).

    Args:
        path: the `SUPERHUMAN.md` path to propose a repair for.

    Returns:
        A `Proposal`. Never writes to disk.
    """
    raw_bytes = path.read_bytes()
    original_text = raw_bytes.decode("utf-8", errors="replace")
    return _propose_from_text(path, original_text)


def _propose_from_text(path: Path, original_text: str) -> Proposal:
    """The pure half of :func:`propose`: build a `Proposal` from already-read text.

    Split out so :func:`apply` can read the file's raw bytes exactly once
    (for the backup) and reuse that same read for proposal construction,
    rather than reading the file twice.

    Args:
        path: the `SUPERHUMAN.md` path this proposal targets (used only for
            labelling the result; not re-read here).
        original_text: the file's full text, already decoded.

    Returns:
        A `Proposal`. Never touches disk.
    """
    lines = original_text.splitlines(keepends=True)

    bounds = _find_section_bounds(lines, gate_record_parser.LOCKED_HEADING)
    if bounds is None:
        return _not_applicable(path, original_text, "no `## Decisions locked` section found")

    start, end = bounds
    prefix = "".join(lines[: start + 1])  # through the heading line, untouched
    body_lines = lines[start + 1 : end]
    suffix = "".join(lines[end:])

    evidence = _collect_evidence(path)
    proposed_lines, rendered_body, unknown_count = _classify_and_render(body_lines, evidence)
    added_lines, added_rendered = _propose_additions(proposed_lines, evidence)
    proposed_lines = proposed_lines + added_lines
    rendered_body = rendered_body + added_rendered
    unknown_count += sum(1 for line in added_lines if UNKNOWN_STAMP in line.rendered)

    proposed_text = prefix + "".join(rendered_body) + suffix

    # FR-9 is fatal, not a warning: `prefix`/`suffix` are literal slices of
    # `original_text` that no rewrite logic above ever touches, so this can
    # only fail if `_find_section_bounds` itself mis-drew the boundary --
    # verified explicitly rather than trusted, because that is exactly the
    # bug class TC-43's decoy-heading fixture exists to catch.
    if not original_text.startswith(prefix) or not original_text.endswith(suffix):
        return _aborted(  # pragma: no cover - defense in depth; unreachable
            path,           # via the public API, since prefix/suffix are
            original_text,  # slices of original_text by construction.
            "the matched `## Decisions locked` boundary does not partition "
            "the original file; refusing to propose any change (FR-9)",
        )

    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            proposed_text.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (proposed)",
        )
    )

    return Proposal(
        path=path,
        applicable=True,
        skipped_reason=None,
        original_text=original_text,
        proposed_text=proposed_text,
        aborted=False,
        abort_reason=None,
        diff=diff,
        unknown_count=unknown_count,
        lines=tuple(proposed_lines),
    )


# ---------------------------------------------------------------------------
# The write path (`apply()`, chunk 4): backup, verify, THEN overwrite.
#
# `DECISIONS.md` G6-004-d: for a `.gitignore`d record there is no git
# history to revert to, so the local backup this section writes is not
# belt-and-braces -- it is the only recovery mechanism that exists. The
# mandatory order is: (1) compute the proposal, (2) if there is nothing to
# write, stop -- no backup, no write; (3) otherwise write a full-file
# pre-write backup; (4) verify the backup is readable and byte-identical to
# the source; (5) only then overwrite the real file. Any failure at (3) or
# (4) fails closed -- the real file is never touched.
# ---------------------------------------------------------------------------


class BackupVerificationError(Exception):
    """Raised when a written backup does not read back byte-identical to the source."""


#: `~/.superhuman/gate-record-backups` -- deliberately outside every git
#: repo (this repo included), so a backup of a private repo's record can
#: never land inside a tree this public repo could ever `git add` (TEST.md
#: §8). A function, not a module-level constant, so tests can monkeypatch it
#: without touching the real home directory.
def _default_backup_root() -> Path:
    """Return the default backup root, `~/.superhuman/gate-record-backups`.

    Returns:
        The default backup root path. Does not create it.
    """
    return Path.home() / ".superhuman" / "gate-record-backups"


#: Characters unsafe (or merely inconvenient) in a filesystem path segment,
#: collapsed to a single underscore.
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def _repo_slug_safe(repo: Path) -> str:
    """Filesystem-safe, deterministic encoding of a repo root for the backup layer.

    Mirrors NFR-7's `(repo_root, slug)` identity rule inside the backup
    layer too, so the same collision risk (two repos sharing a bare name)
    does not recur one layer up (TEST.md §8).

    Args:
        repo: repository root.

    Returns:
        `repo`'s resolved absolute path with every path separator, drive
        colon, and other filesystem-unsafe character collapsed to `_`.
    """
    resolved = str(Path(repo).resolve())
    return _UNSAFE_PATH_CHARS.sub("_", resolved).strip("_")


def _long_path(path: Path) -> Path:
    """Return `path`, prefixed for the Windows extended-length namespace when needed.

    Windows refuses to create or open a path longer than `MAX_PATH` (260
    characters) unless it carries the `\\\\?\\` device-namespace prefix.
    The backup layer's repo-root-flattened directory names (`TEST.md` §8)
    combined with an already-deep `backup_root` can push a backup path over
    that limit even when the record's own path is short and opens fine --
    this keeps the write path robust without changing the on-disk layout
    the design specifies.

    Args:
        path: the path about to be created, written, or read.

    Returns:
        `path` unchanged on any non-Windows platform, or unchanged if
        already prefixed; otherwise the same absolute path with `\\\\?\\`
        prepended.
    """
    if sys.platform != "win32":
        return path
    text = str(path)
    if text.startswith("\\\\?\\"):
        return path
    if not os.path.isabs(text):
        text = str(Path(text).absolute())
    return Path("\\\\?\\" + text.replace("/", "\\"))


def _backup_file_path(run_dir: Path, repo: Path, slug: str) -> Path:
    """Return `<run_dir>/files/<repo-slug-safe>__<slug>/SUPERHUMAN.md` (TEST.md §8 layout).

    Args:
        run_dir: `<backup_root>/<run_id>`.
        repo: repository root.
        slug: project slug.

    Returns:
        The backup file's path. Does not create it.
    """
    return run_dir / "files" / f"{_repo_slug_safe(repo)}__{slug}" / "SUPERHUMAN.md"


def _write_and_verify_backup(backup_path: Path, raw_bytes: bytes) -> None:
    """Write `raw_bytes` to `backup_path`, then re-read and verify byte-identity.

    This is the fail-closed core of the write path: a caller must not
    proceed to overwrite the real file unless this function returns without
    raising.

    Args:
        backup_path: where to write the backup.
        raw_bytes: the exact bytes read from the source record.

    Raises:
        OSError: the backup directory or file could not be created/written
            (disk full, permission denied, a colliding path).
        BackupVerificationError: the backup was written but reading it back
            produced different bytes than `raw_bytes`.
    """
    long_backup_path = _long_path(backup_path)
    long_backup_path.parent.mkdir(parents=True, exist_ok=True)
    long_backup_path.write_bytes(raw_bytes)
    read_back = long_backup_path.read_bytes()
    if read_back != raw_bytes:
        raise BackupVerificationError(
            f"backup at {backup_path} is not byte-identical to the source after being written"
        )


def _head_sha(repo: Path) -> str | None:
    """Return `repo`'s current `HEAD` commit SHA, or `None` when unavailable.

    Best-effort diagnostic metadata for `manifest.json` only -- `apply`
    never depends on this succeeding (a `.gitignore`d record's own repo
    still has a `HEAD`, even though the record itself was never committed).

    Args:
        repo: repository root.

    Returns:
        The full `HEAD` SHA, or `None` if `repo` is not a git repository,
        has no commits yet, or `git` is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _new_run_id() -> str:
    """Generate a fresh backup run identifier: a UTC timestamp at microsecond precision.

    Returns:
        A filesystem-safe run id, e.g. `"20260906T235959123456Z"`.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _append_manifest_entry(run_dir: Path, repo: Path, slug: str, path: Path) -> None:
    """Append this record's backup metadata to `<run_dir>/manifest.json`.

    Args:
        run_dir: `<backup_root>/<run_id>`.
        repo: repository root.
        slug: project slug.
        path: the record's own path (not the backup copy).
    """
    long_run_dir = _long_path(run_dir)
    long_run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = long_run_dir / "manifest.json"
    entries: list[dict[str, str | None]] = []
    if manifest_path.is_file():
        try:
            entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            entries = []
    entries.append(
        {
            "repo_root": str(Path(repo).resolve()),
            "slug": slug,
            "path": str(path),
            "backed_up_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pre_backup_head_sha": _head_sha(repo),
        }
    )
    manifest_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """The outcome of one `apply()` invocation against a single record.

    Attributes:
        path: the `SUPERHUMAN.md` path targeted.
        run_id: the backup run identifier used for this invocation.
        wrote: whether the target file was actually overwritten.
        backup_path: where the pre-write backup was written, or `None` when
            no write occurred (there was nothing to back up).
        outcome: one of `"read_failed"` (the record could not be read),
            `"not_applicable"` (no `## Decisions locked` block),
            `"unchanged"` (already normalised -- a no-op), `"aborted"`
            (FR-9's guarantee could not be verified -- refused before any
            write), `"backup_failed"` (fail-closed: the backup could not be
            written or verified -- refused before any write), or
            `"applied"` (the backup was written, verified, and the record
            overwritten).
        detail: a human-readable explanation, or `None` when `outcome` is
            self-explanatory (`"unchanged"`, `"applied"`).
    """

    path: Path
    run_id: str
    wrote: bool
    backup_path: Path | None
    outcome: str
    detail: str | None


def apply(
    repo: Path,
    slug: str,
    *,
    run_id: str | None = None,
    backup_root: Path | None = None,
) -> ApplyResult:
    """Apply the sweep's proposed repair to one record: backup, verify, THEN overwrite.

    Mandatory order (`DECISIONS.md` G6-004-d): read the record once; if
    there is nothing to change (not applicable, already normalised, or the
    proposal was aborted under FR-9), stop -- no backup, no write, the real
    file is never touched. Otherwise write a full-file backup of the
    pre-write bytes, verify it reads back byte-identical
    (`_write_and_verify_backup`), and only then overwrite the record. Any
    backup failure -- write error or verification mismatch -- fails closed:
    the record is left exactly as it was.

    Args:
        repo: repository root containing the target project.
        slug: project slug (`docs/superhuman/<slug>/SUPERHUMAN.md`).
        run_id: backup run identifier; a fresh one is generated when
            omitted.
        backup_root: root of the backup tree; defaults to
            `_default_backup_root()` (overridable for tests, so tests never
            touch the real `~/.superhuman/gate-record-backups`).

    Returns:
        An `ApplyResult` describing what happened. Never raises for an
        ordinary failure mode (unreadable record, backup failure) -- those
        are reported in the result, not as an exception.
    """
    manifest = _project_manifest(repo, slug)
    run_id = run_id if run_id is not None else _new_run_id()
    backup_root = backup_root if backup_root is not None else _default_backup_root()

    try:
        raw_bytes = manifest.read_bytes()
    except OSError as exc:
        return ApplyResult(manifest, run_id, False, None, "read_failed", str(exc))

    proposal = _propose_from_text(manifest, raw_bytes.decode("utf-8", errors="replace"))

    if not proposal.applicable:
        return ApplyResult(manifest, run_id, False, None, "not_applicable", proposal.skipped_reason)
    if proposal.aborted:
        return ApplyResult(manifest, run_id, False, None, "aborted", proposal.abort_reason)
    if not proposal.changed:
        return ApplyResult(manifest, run_id, False, None, "unchanged", None)

    run_dir = backup_root / run_id
    backup_path = _backup_file_path(run_dir, repo, slug)
    try:
        _write_and_verify_backup(backup_path, raw_bytes)
    except (OSError, BackupVerificationError) as exc:
        return ApplyResult(manifest, run_id, False, None, "backup_failed", str(exc))

    _append_manifest_entry(run_dir, repo, slug, manifest)

    manifest.write_bytes(proposal.proposed_text.encode("utf-8"))

    return ApplyResult(manifest, run_id, True, backup_path, "applied", None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _project_manifest(repo: Path, slug: str) -> Path:
    """Return `<repo>/docs/superhuman/<slug>/SUPERHUMAN.md`.

    Resolution never searches (D-4): defined locally, rather than imported
    from `superhuman_profile.project_dir`, to keep this module's
    dependencies exactly what DESIGN's component table declares
    (`gate_record_parser`, `gate_record_census`, `git`) and no more.

    Args:
        repo: repository root.
        slug: project slug.

    Returns:
        The manifest path.
    """
    return repo / "docs" / "superhuman" / slug / "SUPERHUMAN.md"


def build_parser() -> argparse.ArgumentParser:
    """Build the sweeper's CLI parser.

    Returns:
        An `argparse.ArgumentParser` with `--repo`, `--slug`, `--dry-run`,
        and `--apply`.
    """
    parser = argparse.ArgumentParser(
        prog="gate-record-sweeper",
        description=(
            "Propose FR-7/FR-8/FR-9 repairs to one project's "
            "`## Decisions locked` block, reconstructed from git evidence."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version="gate-record-sweeper 0.1.0 (dry-run only)",
    )
    parser.add_argument(
        "--repo",
        required=True,
        type=Path,
        help="Repository root containing the target project.",
    )
    parser.add_argument(
        "--slug",
        required=True,
        help="Project slug (docs/superhuman/<slug>/SUPERHUMAN.md).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the proposed unified diff; write nothing (the default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write the proposed repair: back up the record, verify the "
            "backup, then overwrite it. Fails closed -- refuses to write "
            "if the backup cannot be written and verified first."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the sweeper CLI.

    `--dry-run` (the default) only reads the target file and prints the
    proposed diff; it never writes. `--apply` writes: it backs up the
    record, verifies the backup, then overwrites the record -- and fails
    closed (refuses to write) if the backup cannot be written and verified
    first (`DECISIONS.md` G6-004-d).

    Args:
        argv: command-line arguments, or `None` to use `sys.argv[1:]`.

    Returns:
        `0` on success -- a dry run (including "nothing to propose" and
        "already normalised") or a completed/no-op apply; `1` when the
        target file could not be read, a record was aborted under FR-9, or
        (`--apply` only) the backup could not be written and verified.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover - stream without reconfigure
        pass

    args = build_parser().parse_args(argv)

    if args.apply:
        result = apply(Path(args.repo), args.slug)
        if result.outcome == "read_failed":
            print(f"gate-record-sweeper: cannot read {result.path}: {result.detail}", file=sys.stderr)
            return 1
        if result.outcome == "not_applicable":
            print(f"gate-record-sweeper: {result.path}: {result.detail} -- nothing to apply")
            return 0
        if result.outcome == "aborted":
            print(f"gate-record-sweeper: {result.path}: ABORTED -- {result.detail}", file=sys.stderr)
            return 1
        if result.outcome == "backup_failed":
            print(
                f"gate-record-sweeper: {result.path}: BACKUP FAILED -- {result.detail} -- "
                "refusing to write (no backup, no write)",
                file=sys.stderr,
            )
            return 1
        if result.outcome == "unchanged":
            print(f"gate-record-sweeper: {result.path}: already normalised -- no changes applied")
            return 0
        print(
            f"gate-record-sweeper: {result.path}: applied -- backup at "
            f"{result.backup_path} (run {result.run_id})"
        )
        return 0

    manifest = _project_manifest(Path(args.repo), args.slug)
    try:
        proposal = propose(manifest)
    except OSError as exc:
        print(f"gate-record-sweeper: cannot read {manifest}: {exc}", file=sys.stderr)
        return 1

    if not proposal.applicable:
        print(f"gate-record-sweeper: {manifest}: {proposal.skipped_reason} -- nothing to propose")
        return 0
    if proposal.aborted:
        print(f"gate-record-sweeper: {manifest}: ABORTED -- {proposal.abort_reason}", file=sys.stderr)
        return 1
    if not proposal.changed:
        print(f"gate-record-sweeper: {manifest}: already normalised -- no changes proposed")
        return 0

    print(proposal.diff, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
