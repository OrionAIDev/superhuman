#!/usr/bin/env python3
"""Deterministic entry-tier router for superhuman (design spec `entry-tiering`).

``route.py`` resolves one tier -- T0 (direct), T1 (express), or T2 (full) -- for
a ``(repo, request)`` pair, so that the tier decision is a lattice join computed
in code rather than a judgement the orchestrating model makes for itself
(``DESIGN.md`` "Approach summary"). The resolved tier is the maximum of
independent per-axis floors (request, repo, rung, and from a later chunk,
risk); nothing here computes a weighted score, because a sum could let more
evidence produce a *cheaper* tier, which is exactly the bug the lattice
structurally cannot express.

**This chunk (Chunk 1 of the ``entry-tiering`` plan) builds the skeleton
only.** The five subcommands exist and the resume short-circuit (FR-4) is
fully implemented, but the request/repo/rung signal axes are not: scoring a
request that reaches them raises ``NotImplementedError``, which the top-level
handler maps to exit 3. That is correct behaviour for this chunk, not a bug --
Chunk 2 implements the request/repo axes and Chunk 3 the rung axis.

Exit-code contract for ``resolve`` (FR-6):

| Exit | Meaning                                                          |
|------|-------------------------------------------------------------------|
| 0    | RESUME -- an open or stale ``SUPERHUMAN.md`` short-circuited      |
|      | routing; no tier is emitted                                       |
| 10   | T0 -- direct                                                       |
| 11   | T1 -- express                                                      |
| 12   | T2 -- full                                                          |
| 2    | usage error (missing/empty ``--request``, unreadable ``--repo``)  |
| 3    | internal error                                                     |
| any other | undefined -- **the caller must treat this as T2**             |

10/11/12 were chosen well clear of argparse's own usage-error code (2), an
uncaught Python exception's default (1), and the shell's reserved 126/127, so
that a crash exiting 1 can never be misread as a real T1 verdict. Any exit
code outside this table -- including a future code nobody has invented yet --
resolves upward: **the caller must treat it as T2**. This is the safety net
that makes an uncaught exception resolve upward rather than into the cheap
lane.

``risk-scan`` (added in full at Chunk 4; a documented stub here) has its own,
*differently polarized* contract, and callers must never assume one
subcommand's exit-0 meaning for the other:

| Exit | Meaning                                                       |
|------|----------------------------------------------------------------|
| 0    | clean -- no risk-class surface found                          |
| 20   | risk found -- caller must treat this as an escalation trigger |
| 2 / 3 / any other | usage, internal, or undefined -- **treat as 20** |

**The truthiness footgun.** ``resolve``'s exit 0 means *resume*; ``risk-scan``'s
exit 0 means *clean*. A caller that does ``if route.py resolve ...; then`` in
a shell reads *resume* as true and *every tier* (10/11/12) as false -- the
opposite of what a careless reader expects. Callers must switch on the numeric
exit code (or parse ``--json``), never on shell truthiness, and never assume
exit 0 means the same thing for both subcommands.

**The resume-authority note (load-bearing for FR-27).** In ``SKILL.md``'s
HARD-GATE, **step 1 is the sole authority on whether a project should be
resumed**, and the router is never consulted about that decision -- step 2
(which invokes this script) only runs after step 1 has already concluded "does
not exist". The resume check implemented here exists so the tool is safe to
run standalone as a preview, and so FR-4 is assertable by a test rather than
by prose alone. Any implementation that lets *this* script's resume answer
override step 1's is a defect, full stop -- this script's opinion about
resuming is advisory outside of that standalone/preview use.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Sequence

#: Schema/CLI version for ``--version``. Independent of the skill bundle's own
#: ``VERSION`` file (out of scope for this chunk) -- this is route.py's own
#: contract version, bumped when its CLI surface or exit contract changes.
ROUTER_SCHEMA_VERSION = 1

#: Exit codes for `resolve`. See the module docstring's exit-code table.
EXIT_RESUME = 0
EXIT_T0 = 10
EXIT_T1 = 11
EXIT_T2 = 12
EXIT_USAGE = 2
EXIT_INTERNAL = 3

#: Exit codes for `risk-scan`, deliberately re-using 0 and 2/3 with the
#: OPPOSITE polarity documented in the module docstring's truthiness-footgun
#: paragraph: 0 means *clean* here, not *resume*.
EXIT_RISK_CLEAN = 0
EXIT_RISK_FOUND = 20

#: Exit codes for `lint-ledger` (validated for real starting Chunk 5; this
#: chunk's stub only ever returns EXIT_LINT_OK).
EXIT_LINT_OK = 0
EXIT_LINT_INVALID = 1


class Tier(IntEnum):
    """The three routing tiers, ordered so ``max()`` selects the highest floor."""

    T0 = 0
    T1 = 1
    T2 = 2


#: Maps a resolved Tier to its `resolve` exit code (FR-6).
_TIER_EXIT_CODES: dict[Tier, int] = {
    Tier.T0: EXIT_T0,
    Tier.T1: EXIT_T1,
    Tier.T2: EXIT_T2,
}


@dataclass(frozen=True, slots=True)
class Signal:
    """One fired signal within an axis.

    Attributes:
        name: Signal identifier as it appears in the basis string, e.g.
            ``"verbs=greenfield"``.
        evidence: The ``k=v`` evidence pairs backing this signal, in the order
            they should render in the basis string.
        floor: The tier this signal contributes.
    """

    name: str
    evidence: tuple[tuple[str, str], ...]
    floor: Tier


@dataclass(frozen=True, slots=True)
class AxisResult:
    """One axis's contribution to the routing lattice.

    Attributes:
        axis: Axis name -- ``"request"``, ``"repo"``, ``"rung"``, or (from
            Chunk 4) ``"risk"``.
        signals: Every signal that fired on this axis.
        floor: ``max(s.floor for s in signals)`` -- this axis's contribution
            to the max-of-floors join.
    """

    axis: str
    signals: tuple[Signal, ...]
    floor: Tier


@dataclass(frozen=True, slots=True)
class Routing:
    """The resolved outcome of ``route.py resolve`` once scoring has run.

    Attributes:
        tier: ``max(a.floor for a in axes)`` -- the resolved tier (FR-1, FR-5).
        axes: Every axis's contribution.
        decided_by: Names of every signal tied at the winning floor.
        ambiguity: ``"none"``, or a message naming the axis/span whose
            evidence disagreed (FR-5).
    """

    tier: Tier
    axes: tuple[AxisResult, ...]
    decided_by: tuple[str, ...]
    ambiguity: str


@dataclass(frozen=True, slots=True)
class ResumeOutcome:
    """One outcome of the FR-4 resume short-circuit.

    Attributes:
        kind: One of ``"resume"``, ``"ambiguous"``, ``"stale"``, ``"unmatched"``.
        detail: The value substituted into this outcome's rendering -- a
            slug, a comma-joined list of slugs, or a path, depending on
            ``kind``.
    """

    kind: str
    detail: str

    def render(self) -> str:
        """Render this outcome exactly as documented in DESIGN.md's resume table.

        Returns:
            The literal line printed to stdout for this outcome.

        Raises:
            ValueError: If ``kind`` is not one of the four documented values.
        """
        if self.kind == "resume":
            return f"resume({self.detail})"
        if self.kind == "ambiguous":
            return f"resume: ambiguous(slugs={self.detail})"
        if self.kind == "stale":
            return f"resume: stale({self.detail})"
        if self.kind == "unmatched":
            return f"resume: unmatched-open-projects({self.detail})"
        raise ValueError(f"unknown resume outcome kind: {self.kind!r}")


# --------------------------------------------------------------------------- #
# Resume short-circuit (FR-4) -- runs before any signal evaluation
# --------------------------------------------------------------------------- #

#: Matches the '## Decisions log' heading line.
_DECISIONS_LOG_HEADING = re.compile(r"^##[ \t]+Decisions log[ \t]*$", re.MULTILINE)

#: Matches the next '## ' heading, used to bound the Decisions log section.
_NEXT_HEADING = re.compile(r"^##[ \t]+", re.MULTILINE)

#: Matches one Decisions-log entry per the SUPERHUMAN.md.tpl format:
#: ``[<ISO timestamp>] G<n>: <summary>; user decision: <decision>``.
_DECISION_ENTRY = re.compile(
    r"^\[[^\]\n]*\][ \t]*G(\d+):[^\n]*user decision:", re.MULTILINE | re.IGNORECASE
)

#: Collapses hyphen/underscore/whitespace runs for exact-token slug matching.
_NORMALIZE_RE = re.compile(r"[-_\s]+")

#: The gate number that marks a project closed (accepted).
_CLOSING_GATE = 8


def _decisions_log_body(text: str) -> str | None:
    """Extract the body of a SUPERHUMAN.md's '## Decisions log' section.

    Args:
        text: Full file contents.

    Returns:
        The section body -- from just after the heading line to the next
        ``## `` heading or end of file -- or ``None`` if the heading is
        absent.
    """
    match = _DECISIONS_LOG_HEADING.search(text)
    if match is None:
        return None
    rest = text[match.end() :]
    next_heading = _NEXT_HEADING.search(rest)
    return rest[: next_heading.start()] if next_heading else rest


def _classify_superhuman_file(path: Path) -> str:
    """Classify one ``SUPERHUMAN.md`` as ``"open"``, ``"closed"``, or ``"stale"``.

    Per DESIGN.md's resume table, VALID means the file has a '## Decisions
    log' section containing at least one entry of the form ``[<ts>] G<n>:
    ...; user decision: <decision>``. A VALID file with no G8 entry is
    ``"open"`` (unclosed, i.e. still in flight); a VALID file with a G8 entry
    is ``"closed"``. Anything else -- an unreadable file, or a readable file
    with no matching Decisions-log entry -- is ``"stale"`` (INVALID).

    Args:
        path: Path to the ``SUPERHUMAN.md`` file.

    Returns:
        One of ``"open"``, ``"closed"``, ``"stale"``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "stale"
    body = _decisions_log_body(text)
    if body is None:
        return "stale"
    gates = {int(n) for n in _DECISION_ENTRY.findall(body)}
    if not gates:
        return "stale"
    return "closed" if _CLOSING_GATE in gates else "open"


def _normalize(text: str) -> str:
    """Case-fold `text` and collapse hyphen/underscore/whitespace runs to one space.

    Args:
        text: Raw text.

    Returns:
        The normalised text, stripped of leading/trailing whitespace.
    """
    return _NORMALIZE_RE.sub(" ", text).strip().casefold()


def _slug_matches(slug: str, request: str) -> bool:
    """Exact-token slug match (FR-4 step 3) -- never fuzzy.

    A slug matches when it appears verbatim in the request text once both are
    case-folded and their hyphen/underscore/whitespace runs are normalised to
    a single space, and the match is anchored to whole tokens (both sides
    padded with a space before comparing) so a slug can never match as a mere
    substring of a longer word -- e.g. slug ``"key"`` must not match
    ``"keymap"``.

    Args:
        slug: The candidate project slug (a ``docs/superhuman/<slug>/``
            directory name).
        request: The raw user request text.

    Returns:
        Whether the slug appears in the request as a whole token sequence.
    """
    padded_request = f" {_normalize(request)} "
    padded_slug = f" {_normalize(slug)} "
    return padded_slug in padded_request


def check_resume(repo: Path, request: str, slug_hint: str | None = None) -> ResumeOutcome | None:
    """Run the FR-4 resume short-circuit ahead of any signal evaluation.

    Globs ``<repo>/docs/superhuman/*/SUPERHUMAN.md``, classifies each hit
    (see ``_classify_superhuman_file``), and resolves to one of four outcomes
    -- all of which mean "do not score, return to the resume path" -- or to
    ``None``, meaning scoring should proceed (either no file was found, or
    every file found was closed).

    A stale (INVALID) file takes precedence over any open project found
    alongside it in the same repo: a malformed record needs attention before
    anything else does, and DESIGN.md does not specify a different ordering.
    This precedence choice is deterministic (FR-2 requires nothing less) but
    is this implementation's own resolution of an underspecified corner, not
    a documented requirement -- noted here rather than left silent.

    Args:
        repo: Project root to search for existing superhuman projects.
        request: The raw user request text, used for exact-token slug
            matching against open projects.
        slug_hint: An explicit slug from ``--slug``, used to disambiguate
            directly (bypassing text matching) when the caller already knows
            which open project it means -- e.g. answering the "Which
            project?" prompt ``SKILL.md`` step 1 already asks on an ambiguous
            resume. DESIGN.md declares ``--slug`` on the CLI surface but does
            not specify its semantics; this is this implementation's own
            reading, flagged for confirmation rather than asserted as settled.

    Returns:
        A ``ResumeOutcome`` if routing should short-circuit, or ``None`` if
        scoring should proceed.
    """
    paths = sorted(repo.glob("docs/superhuman/*/SUPERHUMAN.md"))
    if not paths:
        return None

    stale_paths: list[Path] = []
    open_slugs: list[str] = []
    for path in paths:
        slug = path.parent.name
        status = _classify_superhuman_file(path)
        if status == "stale":
            stale_paths.append(path)
        elif status == "open":
            open_slugs.append(slug)
        # "closed" contributes nothing further -- it does not short-circuit.

    if stale_paths:
        return ResumeOutcome("stale", str(stale_paths[0]))

    if not open_slugs:
        return None

    if slug_hint is not None:
        hinted = [slug for slug in open_slugs if slug == slug_hint]
        if len(hinted) == 1:
            return ResumeOutcome("resume", hinted[0])
        # An unrecognised or ambiguous hint falls back to text matching below
        # rather than erroring -- every failure resolves upward, never into a
        # crash, per the module docstring's error-handling principle.

    matches = [slug for slug in open_slugs if _slug_matches(slug, request)]
    if len(matches) == 1:
        return ResumeOutcome("resume", matches[0])
    if len(matches) > 1:
        return ResumeOutcome("ambiguous", ",".join(sorted(matches)))
    return ResumeOutcome("unmatched", ",".join(sorted(open_slugs)))


# --------------------------------------------------------------------------- #
# Signal evaluation -- stubbed until Chunk 2 (request/repo axes) and Chunk 3
# (rung axis)
# --------------------------------------------------------------------------- #


def evaluate_signals(repo: Path, request: str) -> Routing:
    """Compute the resolved tier for a ``(repo, request)`` pair (FR-1, FR-9).

    Not implemented in this chunk. Chunk 2 implements the request and repo
    axes; Chunk 3 adds the rung axis via the profile bridge. Calling this
    before then always raises, and the top-level handler in `main` maps that
    to exit 3 (EXIT_INTERNAL) -- the documented, correct behaviour for a
    request that reaches scoring in this chunk, not a bug to work around.

    Args:
        repo: Project root being scored.
        request: The raw user request text.

    Raises:
        NotImplementedError: Always, in this chunk.
    """
    raise NotImplementedError(
        "signal evaluation (request/repo/rung axes) is not implemented until "
        "entry-tiering Chunk 2 (request/repo) and Chunk 3 (rung)"
    )


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #


def _resolve_repo(repo_arg: str | None) -> tuple[Path | None, str | None]:
    """Validate the ``--repo`` argument shared by every subcommand.

    Args:
        repo_arg: The raw ``--repo`` value, or ``None`` if omitted.

    Returns:
        A ``(path, None)`` pair on success, or ``(None, message)`` on
        failure, where ``message`` is ready to print to stderr and names the
        offending path per the error-handling table.
    """
    if repo_arg is None or not repo_arg.strip():
        return None, "route.py: --repo is required"
    path = Path(repo_arg)
    if not path.is_dir():
        return None, f"route.py: --repo path is missing or unreadable: {repo_arg}"
    return path, None


def cmd_resolve(args: argparse.Namespace) -> int:
    """Handle the ``resolve`` subcommand.

    Args:
        args: Parsed CLI arguments (``repo``, ``request``, ``slug``, ``json``).

    Returns:
        The process exit code per the ``resolve`` exit-code table.
    """
    repo, error = _resolve_repo(args.repo)
    if repo is None:
        print(error, file=sys.stderr)
        return EXIT_USAGE
    if args.request is None or not args.request.strip():
        print("route.py resolve: --request is required and must not be empty", file=sys.stderr)
        return EXIT_USAGE

    outcome = check_resume(repo, args.request, slug_hint=args.slug)
    if outcome is not None:
        if args.json:
            print(json.dumps({"resume": outcome.render()}))
        else:
            print(outcome.render())
        return EXIT_RESUME

    routing = evaluate_signals(repo, args.request)
    if args.json:
        print(
            json.dumps(
                {
                    "tier": routing.tier.name,
                    "decided_by": list(routing.decided_by),
                    "ambiguity": routing.ambiguity,
                }
            )
        )
    else:
        print(f"tier: {routing.tier.name}")
    return _TIER_EXIT_CODES[routing.tier]


def cmd_explain(args: argparse.Namespace) -> int:
    """Handle the ``explain`` subcommand -- a human trace, always exit 0 (D-5).

    In this chunk this reports only the resume state; the signal trace itself
    is not yet available, and this says so plainly rather than printing an
    empty or misleading trace (per this chunk's dispatch contract).

    Args:
        args: Parsed CLI arguments (``repo``, ``request``).

    Returns:
        Always ``EXIT_OK`` (0) -- ``explain`` never carries a verdict.
    """
    repo, error = _resolve_repo(args.repo)
    if repo is None:
        print(error)
        return EXIT_RESUME
    request = args.request or ""
    outcome = check_resume(repo, request)
    print(outcome.render() if outcome is not None else "resume: none")
    print("signal trace: not yet available -- scoring lands in a later chunk (see route.py --help)")
    return EXIT_RESUME


def cmd_doctor(args: argparse.Namespace) -> int:
    """Handle the ``doctor`` subcommand -- a one-screen health report, always exit 0.

    Args:
        args: Parsed CLI arguments (``repo``).

    Returns:
        Always ``EXIT_OK`` (0) -- ``doctor`` never carries a verdict.
    """
    repo, error = _resolve_repo(args.repo)
    if repo is None:
        print(error)
        return EXIT_RESUME
    outcome = check_resume(repo, "")
    print(f"repo: {repo}")
    print(outcome.render() if outcome is not None else "resume: none")
    print("signal evaluation: not yet available -- scoring lands in a later chunk (see route.py --help)")
    return EXIT_RESUME


def cmd_lint_ledger(args: argparse.Namespace) -> int:
    """Handle the ``lint-ledger`` subcommand -- a documented stub until Chunk 5.

    Args:
        args: Parsed CLI arguments (``repo``).

    Returns:
        ``EXIT_USAGE`` (2) if ``--repo`` is missing or unreadable; otherwise
        always ``EXIT_LINT_OK`` (0) in this chunk.
    """
    _, error = _resolve_repo(args.repo)
    if error is not None:
        print(error, file=sys.stderr)
        return EXIT_USAGE
    print("lint-ledger: stub -- ledger validation is not implemented until entry-tiering Chunk 5")
    return EXIT_LINT_OK


def cmd_risk_scan(args: argparse.Namespace) -> int:
    """Handle the ``risk-scan`` subcommand -- a documented stub until Chunk 4.

    Deliberately exits 20 (risk-found), never 0 (clean), once ``--repo`` is
    valid: a partially-built scanner must never report "clean" for a question
    it cannot yet answer. See the module docstring's truthiness-footgun
    paragraph. A usage error still exits 2 -- that row is unchanged by the
    stub, since exit 2 is documented as "treat as 20" by the caller anyway.

    Args:
        args: Parsed CLI arguments (``repo``).

    Returns:
        ``EXIT_USAGE`` (2) if ``--repo`` is missing or unreadable; otherwise
        always ``EXIT_RISK_FOUND`` (20) in this chunk.
    """
    _, error = _resolve_repo(args.repo)
    if error is not None:
        print(error, file=sys.stderr)
        return EXIT_USAGE
    print(
        "risk-scan: stub -- risk-class scanning is not implemented until entry-tiering "
        "Chunk 4; treat this run as risk-found, not clean"
    )
    return EXIT_RISK_FOUND


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="route.py",
        description="Resolve one entry tier (T0/T1/T2) for a (repo, request) pair.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"route.py schema v{ROUTER_SCHEMA_VERSION}",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # --repo and --request are deliberately NOT declared `required=True`:
    # argparse would then raise SystemExit(2) itself, before our own code
    # runs, for the *absent* case only -- leaving the *whitespace-only* case
    # (which argparse cannot detect) to a differently-worded check. Making
    # both cases go through `_resolve_repo` / the request check below gives
    # one consistent message and exit path for D-4's two sub-cases, and keeps
    # `main()` callable in-process without a stray SystemExit escaping from
    # argument parsing itself.
    resolve = subs.add_parser("resolve", help="resolve one tier for a (repo, request) pair")
    resolve.add_argument("--repo", default=None, help="project root")
    resolve.add_argument("--request", default=None, help="the user's invocation text")
    resolve.add_argument("--slug", default=None, help="disambiguate an open project directly")
    resolve.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    resolve.set_defaults(func=cmd_resolve)

    explain = subs.add_parser("explain", help="human trace of the routing decision; always exits 0")
    explain.add_argument("--repo", default=None, help="project root")
    explain.add_argument("--request", default=None, help="the user's invocation text")
    explain.set_defaults(func=cmd_explain)

    doctor = subs.add_parser("doctor", help="one-screen health report; always exits 0")
    doctor.add_argument("--repo", default=None, help="project root")
    doctor.set_defaults(func=cmd_doctor)

    lint_ledger = subs.add_parser("lint-ledger", help="validate LEDGER.md format (stub until Chunk 5)")
    lint_ledger.add_argument("--repo", default=None, help="project root")
    lint_ledger.set_defaults(func=cmd_lint_ledger)

    risk_scan = subs.add_parser("risk-scan", help="scan a diff for risk-class surfaces (stub until Chunk 4)")
    risk_scan.add_argument("--repo", default=None, help="project root")
    risk_scan.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    risk_scan.set_defaults(func=cmd_risk_scan)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Every unexpected exception -- including ``evaluate_signals``'s documented
    ``NotImplementedError`` in this chunk -- is caught here and mapped to
    ``EXIT_INTERNAL`` (3) with a one-line message on stderr; no traceback
    reaches stdout. ``argparse`` usage errors (e.g. an unknown subcommand)
    raise ``SystemExit`` directly and are not caught here, so they keep
    argparse's own exit code (2) and message.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 -- top-level boundary; see docstring above.
        print(f"route.py: internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
