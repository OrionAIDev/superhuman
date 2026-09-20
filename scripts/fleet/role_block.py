"""Chunk 7a's role gate: the exact verbatim-role-block predicate (DESIGN.md
D7, D7.3) plus the decision-log primitive `fleet doctor` reads (D7.7, FR-21).

**Portable, harness-agnostic (D4).** This module names no harness anywhere.
The Claude-Code-specific work of extracting the dispatch prompt from a
`PreToolUse` payload, and building its harness-shaped deny JSON, lives
in `templates/hooks/claude-code/pre_tool_use_role_gate.py`, which imports
this module rather than duplicating it (D7.6: "no new harness name enters
`scripts/`"). This module also **reuses** chunk 7's
`scripts/fleet/dispatch_predicate.py` (`leads_with_role_block`) for the
"is this prompt's frontmatter shaped like SOME existing role" test, rather
than re-implementing the role-set lookup and its `OSError` handling —
`dispatch_predicate.py` itself is left untouched (it predates this module;
D7.9's own file list does not include it).

**The four verdicts a caller sees (D7.2, D7.3).**

- ``ROLE`` — the prompt, after normalisation, is byte-identical to an
  existing ``roles/<name>.md`` file's content (the file's trailing
  whitespace stripped), followed by end-of-text or a newline.
- ``NON_ROLE`` — the prompt's first line is the exact literal
  `NON_ROLE_LINE`, optionally followed by more text.
- ``MISMATCH`` — the prompt's leading frontmatter names an existing role
  (chunk 7's predicate already says this prompt is role-SHAPED), but the
  body that follows is not verbatim.
- ``UNMARKED`` — everything else: a prose brief, a prompt that only
  *mentions* `roles/` mid-body (TC-52's analogue), an unknown role name,
  or a misspelt/non-leading non-role line.

A fifth, internal-only value, ``FAULT``, is never a verdict a real prompt
earns — it means the check itself could not be trusted (an empty/unreadable
`roles/` directory, or the one specific role file this prompt names being
unreadable). D7.5 is explicit that this must **never** become a basis for
denial: "an unreadable or empty `roles/` directory, or an unreadable role
file, is a fault... It must never become a verdict: otherwise one missing
directory would deny every role dispatch on the machine." Callers (the
`PreToolUse` adapter, and the `role-block check` CLI verb) must treat
`FAULT` as "nothing could be proven" — the adapter emits no deny at all;
the fail-closed CLI verb (unlike the hook, which must fail *soft* on its
own infrastructure per NFR-9) exits non-zero for it, the same as MISMATCH/
UNMARKED, since it cannot certify compliance either.

**Normalisation (D7.3, exact; BOM stripping symmetrised across both sides
at the Phase 3.3 preflight — item 5).** Both the prompt and the role
file's content have a leading BOM and leading whitespace stripped, and
CRLF (and lone CR) normalised to LF. The role file's content additionally
has trailing whitespace stripped (the prompt's does not — trailing
content after the role block, e.g. a task brief, matters to the caller).
The (normalised) prompt must then *start with* the (normalised) role
file's content, immediately followed by end-of-text or a newline.

**Never raises.** `check_role_block` degrades to `Verdict.FAULT` (never an
exception) on any unexpected failure — this is this module's own,
documented, sole broad catch, mirroring `dispatch_predicate.py`'s and
`subagent_dispatch_filter.py`'s identical precedent for the same reason
(NFR-9: a hook built on this module must never let ITS OWN failure become
the reason a dispatch is wrongly denied — or wrongly allowed through a
silently-wrong verdict).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import zip_longest
from pathlib import Path
from typing import Any

from . import config as fleet_config
from .bounded_journal import append_bounded_line
from .core.errors import LockTimeoutError
from .dispatch_predicate import leads_with_role_block
from .path_safety import slug_is_safe

#: The literal first-line marker for a deliberately non-role dispatch
#: (D7.2, chunk 7a G3-narrow ruling). Cited verbatim by the floor docs
#: (`SKILL.md`, `roles/pm.md`, `phases/3-implementation.md`) — TC-88 asserts
#: the prose literal equals this constant, never a re-typed copy.
NON_ROLE_LINE = "superhuman-dispatch: non-role"

#: Mirrors `dispatch_predicate.py`'s identical frontmatter regex (not
#: imported — that module exposes only the boolean `leads_with_role_block`
#: predicate, not the matched role NAME this module additionally needs to
#: locate `roles/<name>.md` for the verbatim comparison; duplicating two
#: small regexes here is cheaper and safer than modifying a chunk-7 module
#: D7.9's own file list excludes). Kept byte-identical to that module's
#: `_FRONTMATTER_RE`/`_NAME_LINE_RE` on purpose.
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)
_NAME_LINE_RE = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)

#: Bounded rolling tail for `role-gate.jsonl` (D7.7) — same bound and
#: rationale as `observe.py`'s `_JOURNAL_MAX_LINES` (a diagnostic side-file,
#: not an ordered archive).
_ROLE_GATE_LOG_MAX_LINES = 500

_ROLE_GATE_LOG_FILENAME = "role-gate.jsonl"


class Verdict(str, Enum):
    """The role gate's verdict space (D7.2/D7.3, plus the internal `FAULT`).

    A `str` subclass so `verdict.value` (used in JSON output/logging) and
    plain equality against a literal string both work without ceremony.
    """

    ROLE = "ROLE"
    NON_ROLE = "NON_ROLE"
    MISMATCH = "MISMATCH"
    UNMARKED = "UNMARKED"
    FAULT = "FAULT"


@dataclass(frozen=True, slots=True)
class RoleCheckResult:
    """One `check_role_block` outcome.

    Attributes:
        verdict: one of `Verdict`'s five values.
        role: the frontmatter-claimed role name — set for `MISMATCH` (and
            for `ROLE`); `None` for `NON_ROLE`/`UNMARKED`/`FAULT`.
        role_file: `roles_dir/<role>.md` — set alongside `role`.
        prompt_mismatch_line: the prompt's own first differing line
            (`MISMATCH` only; `None` otherwise). Verbatim prompt text —
            used only to build the deny reason shown to the harness
            (D7.5), never written to the decision log (NFR-8; B6).
        file_mismatch_line: the role file's first differing line at the
            same position (`MISMATCH` only; `None` otherwise).
        mismatch_line_number: the 1-based line number the two diverge at
            (`MISMATCH` only; `None` otherwise). Carries no content — this
            is the field safe to write to the decision log.
    """

    verdict: Verdict
    role: str | None = None
    role_file: Path | None = None
    prompt_mismatch_line: str | None = None
    file_mismatch_line: str | None = None
    mismatch_line_number: int | None = None


def _normalize_newlines(text: str) -> str:
    """Normalise CRLF and lone CR to LF.

    Args:
        text: raw text (a prompt or a role file's content).

    Returns:
        str: `text` with every `\\r\\n` and remaining `\\r` replaced by `\\n`.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_bom_and_leading_ws(text: str) -> str:
    """Strip a leading BOM, then leading whitespace (D7.3).

    Args:
        text: the raw prompt text.

    Returns:
        str: `text` with a leading `\\ufeff` (if present) and any further
        leading whitespace removed.
    """
    return text.lstrip("﻿").lstrip()


def _first_line(text: str) -> str:
    """Return `text`'s first line (up to the first `\\n`, exclusive).

    Args:
        text: already-newline-normalised text.

    Returns:
        str: everything before the first `\\n`, or all of `text` if it
        carries no newline at all.
    """
    return text.split("\n", 1)[0]


def _is_non_role_line(prompt: str) -> bool:
    """Return whether `prompt`'s first line is the exact `NON_ROLE_LINE` (D7.2).

    Args:
        prompt: the raw dispatch prompt.

    Returns:
        bool: `True` iff, after BOM/leading-whitespace stripping and CRLF
        normalisation, the first line equals `NON_ROLE_LINE` exactly.
        Misspelt, or present but not on the first line, is `False` (TC-79)
        — the caller falls through to the ordinary role-block check, which
        also yields `False`/`UNMARKED` for such text, matching TC-79's
        contract.
    """
    normalized = _normalize_newlines(_strip_bom_and_leading_ws(prompt))
    return _first_line(normalized) == NON_ROLE_LINE


def _roles_dir_has_any_role(roles_dir: Path) -> bool:
    """Return whether `roles_dir` carries at least one readable `*.md` file.

    This is the FAULT precondition (D7.5): an empty or unreadable
    `roles/` directory must never silently degrade into "no prompt can
    ever match a role", which would deny every role dispatch on the
    machine. Checked once per `check_role_block` call, before any
    prompt-specific logic runs.

    Args:
        roles_dir: the directory holding `roles/*.md`.

    Returns:
        bool: `True` if at least one `*.md` entry is enumerable; `False`
        for a missing directory, an empty one, or an enumeration failure
        (`OSError`, e.g. a permissions problem) — never raises.
    """
    try:
        return any(roles_dir.glob("*.md"))
    except OSError:
        return False


def _frontmatter_role_name(prompt: str) -> str | None:
    """Extract the leading frontmatter's `name:` value, if any.

    Does not check the name against the role set on disk — that is
    `leads_with_role_block`'s job (already called by `check_role_block`
    before this is). This only recovers the NAME so the caller can build
    `roles_dir/<name>.md` for the verbatim comparison.

    Args:
        prompt: the raw dispatch prompt.

    Returns:
        str | None: the frontmatter's `name:` value, or `None` if the
        prompt carries no leading `---` frontmatter block or no `name:`
        line within it.
    """
    stripped = _strip_bom_and_leading_ws(prompt)
    match = _FRONTMATTER_RE.match(stripped)
    if match is None:
        return None
    name_match = _NAME_LINE_RE.search(match.group("body"))
    if name_match is None:
        return None
    return name_match.group(1)


def _first_differing_line(prompt_norm: str, file_norm: str) -> tuple[int, str, str]:
    """Return the first line at which `prompt_norm` and `file_norm` diverge.

    Only ever called after a `startswith` comparison between the two has
    already failed (the `MISMATCH` branch), so a divergence is guaranteed
    to exist.

    Args:
        prompt_norm: the normalised prompt (BOM/leading-whitespace
            stripped, CRLF normalised — NOT trailing-whitespace-stripped).
        file_norm: the normalised, BOM/leading-whitespace-stripped,
            trailing-whitespace-stripped role file
            content.

    Returns:
        tuple[int, str, str]: `(line_number, prompt_line, file_line)` at
        the first index where the two differ. `line_number` is 1-based.
        An empty string stands in for "this side ran out of lines first."
    """
    prompt_lines = prompt_norm.split("\n")
    file_lines = file_norm.split("\n")
    for index, (prompt_line, file_line) in enumerate(
        zip_longest(prompt_lines, file_lines, fillvalue=None)
    ):
        if prompt_line != file_line:
            return (index + 1, prompt_line or "", file_line or "")
    return (0, "", "")  # pragma: no cover - unreachable: callers only invoke this once startswith() has already failed, guaranteeing a divergence


def check_role_block(prompt: str, roles_dir: Path) -> RoleCheckResult:
    """Chunk 7a's exact role-block predicate (D7.2/D7.3).

    Order of checks: (1) the `roles/` health precondition — a `FAULT` if
    it fails, before anything prompt-specific runs; (2) the `NON_ROLE_LINE`
    literal-first-line test (D7.2); (3) chunk 7's frontmatter/name test
    (`leads_with_role_block`) to decide whether the prompt is role-SHAPED
    at all; (4) for a role-shaped prompt, the strict verbatim comparison
    against `roles_dir/<name>.md` (D7.3).

    Args:
        prompt: the dispatching `Agent`/`Task` tool_use's `prompt` input,
            exactly as read from the harness payload — never pre-processed
            by the caller beyond extraction. A non-`str` value is treated
            as a `FAULT` (the caller handed this function something it
            cannot check).
        roles_dir: the directory holding `roles/*.md`; the role set is
            read from disk each call, never hardcoded (matching
            `dispatch_predicate.py`'s precedent).

    Returns:
        RoleCheckResult: never raises. See the module docstring for the
        five possible verdicts.
    """
    try:
        if not isinstance(prompt, str):
            return RoleCheckResult(verdict=Verdict.FAULT)

        roles_dir = Path(roles_dir)
        if not _roles_dir_has_any_role(roles_dir):
            return RoleCheckResult(verdict=Verdict.FAULT)

        if _is_non_role_line(prompt):
            return RoleCheckResult(verdict=Verdict.NON_ROLE)

        if not leads_with_role_block(prompt, roles_dir):
            return RoleCheckResult(verdict=Verdict.UNMARKED)

        role_name = _frontmatter_role_name(prompt)
        if role_name is None:  # pragma: no cover - leads_with_role_block already required a name: line to match
            return RoleCheckResult(verdict=Verdict.UNMARKED)

        role_file = roles_dir / f"{role_name}.md"
        try:
            file_content = role_file.read_text(encoding="utf-8")
        except (OSError, ValueError):
            # `ValueError` covers `UnicodeDecodeError` on a non-UTF-8 file.
            # An unreadable role file this prompt specifically names is a
            # FAULT (D7.5), never a MISMATCH/deny with no evidence behind it.
            return RoleCheckResult(verdict=Verdict.FAULT, role=role_name, role_file=role_file)

        prompt_norm = _normalize_newlines(_strip_bom_and_leading_ws(prompt))
        # Phase 3.3 preflight item 5: the role file's content used to skip
        # `_strip_bom_and_leading_ws` entirely, so a BOM present on one
        # side only (most likely the role file, saved by an editor that
        # adds one) produced a false MISMATCH even though the two texts
        # were otherwise identical. Both sides now go through the exact
        # same BOM/leading-whitespace stripping before comparison.
        file_norm = _normalize_newlines(_strip_bom_and_leading_ws(file_content)).rstrip()

        matches = prompt_norm.startswith(file_norm) and (
            len(prompt_norm) == len(file_norm) or prompt_norm[len(file_norm)] == "\n"
        )
        if matches:
            return RoleCheckResult(verdict=Verdict.ROLE, role=role_name, role_file=role_file)

        line_number, prompt_line, file_line = _first_differing_line(prompt_norm, file_norm)
        return RoleCheckResult(
            verdict=Verdict.MISMATCH,
            role=role_name,
            role_file=role_file,
            prompt_mismatch_line=prompt_line,
            file_mismatch_line=file_line,
            mismatch_line_number=line_number,
        )
    except Exception:  # noqa: BLE001 - this module's sole broad catch; see module docstring (NFR-9)
        return RoleCheckResult(verdict=Verdict.FAULT)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def role_gate_log_path(fleet_dir: Path | str) -> Path:
    """Return `<fleet_dir>/role-gate.jsonl` (D7.7).

    Args:
        fleet_dir: the project's fleet manifest directory.

    Returns:
        Path: the decision log's path.
    """
    return Path(fleet_dir) / _ROLE_GATE_LOG_FILENAME


def record_role_gate_decision(
    workspace: Path | str,
    slug: str,
    *,
    session_id: str | None,
    verdict: Verdict,
    role: str | None,
    subagent_type: str | None,
    mismatch_line_number: int | None,
) -> None:
    """Append one D7.7 decision-log line for a `NON_ROLE`/`MISMATCH`/`UNMARKED` verdict.

    Shares `observe.py`'s `_write_journal` bounded-tail-append primitive
    (D7.7: "using the journaling primitive `observe-failures.log` already
    uses") — both now call `bounded_journal.append_bounded_line` (Phase
    3.3 preflight: the two were previously independent, un-locked
    read-modify-write copies of the same shape, which lost rows under
    parallel dispatch) — a JSON line per decision, a rolling bounded tail
    (`_ROLE_GATE_LOG_MAX_LINES`), and a silent no-op on any I/O failure. A
    failed write must never change the verdict already computed and acted
    on by the caller (TC-89) — this function's return value carries no
    signal either way.

    Never called for `Verdict.ROLE` (chunk 7's own dispatch-registration
    row already counts it — D7.7: "a role dispatch in the correct form is
    not logged here") or `Verdict.FAULT` (a fault is never a decision).
    Writes nothing when fleet observation is disabled/unconfigured for
    `workspace` (mirrors `observe.journal_early_cli_failure`'s zero-I/O
    guarantee for a disabled workspace) and never carries prompt or
    description text (NFR-8) — only the six named fields. Preflight B6:
    this field used to be `mismatch_line`, the verbatim first differing
    LINE OF THE PROMPT — a real egress path for whatever an operator's
    prompt happened to diverge into, in an estate whose hard rule is that
    regulated data never reaches GitHub, and whose convention puts
    `docs/superhuman/` inside tracked product repositories. Replaced by
    `mismatch_line_number`, the 1-based line NUMBER the divergence starts
    at — locates the diff for a human re-running the check by hand,
    without ever writing its content anywhere.

    Args:
        workspace: the project's working tree root.
        slug: the superhuman project slug.
        session_id: the harness session id, or `None` if unavailable.
        verdict: `Verdict.NON_ROLE`, `Verdict.MISMATCH`, or
            `Verdict.UNMARKED` — the three verdicts D7.7 logs.
        role: the frontmatter-claimed role name, if any (`MISMATCH` only;
            `None` for `NON_ROLE`/`UNMARKED`).
        subagent_type: the payload's dispatch-tool `subagent_type` field, if any.
        mismatch_line_number: the prompt's first differing line's 1-based
            line number (`MISMATCH` only) — never the line's own content,
            the full prompt, or a description (NFR-8).
    """
    # Preflight B5: `workspace`/`slug` reach this function from a locator
    # cache file the agent itself writes into its own scratchpad
    # (`pre_tool_use_role_gate.py`'s `_cached_locate`) — agent-writable, so
    # no more trustworthy than a raw CLI argument, and this function joined
    # them into a path with no validation at all. Reuses `observe.py`'s
    # guard (moved to `path_safety.slug_is_safe`, B5) plus two more checks
    # that guard specifically named: `workspace` must be an absolute,
    # existing directory, and the path actually about to be written must
    # resolve inside `<workspace>/docs/superhuman/<slug>/`. Any failure
    # here writes nothing and never raises — same contract as every other
    # fault this function already degrades silently on.
    if not slug_is_safe(slug):
        return
    workspace_path = Path(workspace)
    if not workspace_path.is_absolute() or not workspace_path.is_dir():
        return
    try:
        cfg = fleet_config.resolve_fleet_config(workspace)
        if not cfg.enabled:
            return
        if cfg.manifest_dir is not None:
            # An explicit operator override from the profile YAML (a
            # trusted, separate configuration surface `resolve_fleet_config`
            # already confines to `workspace`, Phase 3.3 preflight FIX 2) —
            # not the untrusted workspace/slug pair this function guards
            # against, so it is not additionally required to land inside
            # `<workspace>/docs/superhuman/<slug>/` specifically.
            fleet_dir = cfg.manifest_dir
        else:
            fleet_dir = workspace_path / "docs" / "superhuman" / slug / "fleet"
            expected_root = (workspace_path / "docs" / "superhuman" / slug).resolve()
            if not fleet_dir.resolve().is_relative_to(expected_root):
                return  # pragma: no cover - unreachable once slug_is_safe rejects traversal; kept as defense in depth (B5), mirroring config.py's identical resolve+is_relative_to pattern for manifest_dir
        fleet_dir.mkdir(parents=True, exist_ok=True)
        path = role_gate_log_path(fleet_dir)
        line = json.dumps(
            {
                "ts": _now_iso(),
                "session_id": session_id,
                "verdict": verdict.value,
                "role": role,
                "subagent_type": subagent_type,
                "mismatch_line_number": mismatch_line_number,
            },
            sort_keys=True,
        )
        # Phase 3.3 preflight: delegates to the shared, lock-protected
        # primitive (see `bounded_journal`'s module docstring) rather than
        # this module's own read-modify-write -- without a lock, two hook
        # processes racing to append here under parallel dispatch lose
        # rows: one writer's whole-file rewrite silently discards the
        # other's already-durable line.
        append_bounded_line(path, line, max_lines=_ROLE_GATE_LOG_MAX_LINES)
    except (OSError, ValueError, LockTimeoutError):
        # Loudness tier 2 (mirrors observe.py's identical posture): the
        # fleet directory unwritable is often the very failure that would
        # have blocked the primary write too; there is no primary write
        # here to protect, so this degrades silently rather than adding a
        # second stderr convention this module would then have to keep in
        # sync with observe.py's.
        #
        # Phase 3.3 preflight RE-RUN item D: `ValueError` is widened in
        # alongside `OSError`/`LockTimeoutError` as defense in depth --
        # `bounded_journal.append_bounded_line` no longer raises
        # `UnicodeDecodeError` (a `ValueError` subclass) itself (fixed at
        # the source via `errors="replace"`), but this call site must not
        # regress into the "one bad byte kills the journal forever" defect
        # class if a future edit there reintroduces a `ValueError`-raising
        # path.
        pass


def read_role_gate_log(fleet_dir: Path | str) -> list[dict[str, Any]]:
    """Read every parseable line from `<fleet_dir>/role-gate.jsonl`.

    Args:
        fleet_dir: the project's fleet manifest directory.

    Returns:
        list[dict[str, Any]]: one dict per parseable JSON line, in file
        order. A missing file yields `[]`. A line that fails to parse as a
        JSON object is skipped, never fatal to the rest (mirrors
        `core.events.read_all`'s tolerance of a torn final line).
    """
    path = role_gate_log_path(fleet_dir)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows
