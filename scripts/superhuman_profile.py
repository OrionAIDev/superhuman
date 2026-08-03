#!/usr/bin/env python3
"""Deterministic deployment-profile resolver for superhuman.

This module is the single decision point for the three questions superhuman asks
about a developer's deployment topology (design spec
``docs/superhuman/specs/2026-07-24-portable-profile-and-ladder.md`` §1.2):

1. May an unattended (HITL-M / HITL-L) loop run at this location?
2. May work land here, and who must approve it?
3. Which classes of test belong here?

All policy is data (``profile.yaml``); all decisions are code (this module). No
LLM inference participates in the safety path — skill-design rule 5.

The module is dependency-light on purpose: PyYAML plus the standard library, so
it runs anywhere Python does, including Windows without a POSIX shell.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, not logic
    print(
        "superhuman-profile: PyYAML is required (pip install pyyaml)",
        file=sys.stderr,
    )
    raise SystemExit(2)

SCHEMA_VERSION = 1

#: Exit codes. 0/2/3 preserve the pre-0.7.0 ``autonomous-precondition.sh``
#: contract; 4 is new and safe because every caller aborts on any non-zero exit.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DENIED = 3
EXIT_UNRESOLVED = 4

#: Detector families in precedence order (spec §3.4). Lower tier == higher
#: authority; explicit declarations outrank inferred ones.
DETECTOR_TIERS: dict[str, int] = {
    "marker_file": 2,
    "env_marker": 3,
    "path_segments": 4,
    "branch": 5,
    "tag_channel": 5,
    "worktree": 5,
    "default": 6,
}

ACTION_CLASSES = ("promote_into", "act_unattended")

_APPROVER_RE = re.compile(r"^(human|self|human:[\w.-]+|agent:[\w.-]+)$")
_STABLE_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+$")

Verdict = Literal["ALLOW", "DENY", "UNRESOLVED"]


class ProfileError(Exception):
    """Raised for a malformed profile or an invalid CLI invocation."""


# --------------------------------------------------------------------------- #
# Approval specs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Approval:
    """A parsed approval policy for one action class.

    Attributes:
        mode: One of ``none``, ``never``, ``unresolved``, ``any_of``, ``all_of``.
        approvers: Approver tokens, empty unless mode is ``any_of``/``all_of``.
    """

    mode: Literal["none", "never", "unresolved", "any_of", "all_of"]
    approvers: tuple[str, ...] = ()

    @staticmethod
    def parse(raw: Any, where: str) -> "Approval":
        """Parse an approval spec from profile YAML.

        Args:
            raw: The raw YAML value (``None``, a string, a list, or a mapping).
            where: Dotted path used in error messages.

        Returns:
            The parsed policy.

        Raises:
            ProfileError: If the spec is not valid per the schema grammar.
        """
        if raw is None:
            return Approval("unresolved")
        if isinstance(raw, str):
            if raw in ("none", "never"):
                return Approval(raw)  # type: ignore[arg-type]
            raise ProfileError(
                f"{where}: bare string must be 'none' or 'never', got {raw!r}"
            )
        if isinstance(raw, list):
            return Approval("any_of", Approval._approvers(raw, where))
        if isinstance(raw, dict):
            keys = set(raw)
            if keys == {"any_of"}:
                return Approval("any_of", Approval._approvers(raw["any_of"], where))
            if keys == {"all_of"}:
                return Approval("all_of", Approval._approvers(raw["all_of"], where))
            raise ProfileError(
                f"{where}: mapping must have exactly one of 'any_of'/'all_of', got {sorted(keys)}"
            )
        raise ProfileError(f"{where}: unsupported approval spec {raw!r}")

    @staticmethod
    def _approvers(raw: Any, where: str) -> tuple[str, ...]:
        """Validate a list of approver tokens.

        Args:
            raw: Candidate list of tokens.
            where: Dotted path used in error messages.

        Returns:
            The validated tokens.

        Raises:
            ProfileError: If the list is empty or a token is unrecognised.
        """
        if not isinstance(raw, list) or not raw:
            raise ProfileError(f"{where}: approver list must be a non-empty list")
        out: list[str] = []
        for tok in raw:
            if not isinstance(tok, str) or not _APPROVER_RE.match(tok):
                raise ProfileError(
                    f"{where}: invalid approver {tok!r} "
                    "(expected human | human:<name> | agent:<name> | self)"
                )
            out.append(tok)
        return tuple(out)

    def requires_human(self) -> bool:
        """Report whether satisfying this policy necessarily involves a human.

        ``any_of`` can be satisfied by the cheapest sufficient approver, so it
        needs a human only when *every* listed approver is a human. ``all_of``
        needs one as soon as *any* listed approver is.

        Returns:
            True if no non-human path exists to satisfy the policy.
        """
        if self.mode in ("none", "never", "unresolved"):
            return False
        human = [a.startswith("human") for a in self.approvers]
        return all(human) if self.mode == "any_of" else any(human)

    def unattended_verdict(self) -> Verdict:
        """Evaluate this policy as an ``act_unattended`` question.

        Returns:
            ``ALLOW`` if an unattended loop may operate, ``DENY`` if forbidden,
            ``UNRESOLVED`` if the policy has not been declared yet.
        """
        if self.mode == "never":
            return "DENY"
        if self.mode == "unresolved":
            return "UNRESOLVED"
        if self.mode == "none":
            return "ALLOW"
        return "DENY" if self.requires_human() else "ALLOW"

    def describe(self) -> str:
        """Render the policy for human-readable output.

        Returns:
            A short display string.
        """
        if self.mode in ("none", "never"):
            return self.mode
        if self.mode == "unresolved":
            return "null (unresolved)"
        return f"{self.mode} [{', '.join(self.approvers)}]"

    def to_json(self) -> Any:
        """Render the policy for machine-readable output.

        Returns:
            ``None`` when unresolved, a bare string for ``none``/``never``,
            otherwise a single-key mapping.
        """
        if self.mode == "unresolved":
            return None
        if self.mode in ("none", "never"):
            return self.mode
        return {self.mode: list(self.approvers)}


# --------------------------------------------------------------------------- #
# Rungs and profiles
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Rung:
    """One named position in a developer's deployment ladder.

    Attributes:
        name: Unique rung name.
        kind: Optional, semantics-free label for human readability.
        labels: Free-form metadata carried into resolver output.
        detect: Raw detector mapping (validated at load time).
        approvals: Action class -> approval policy.
        tests: Optional test-class labels; inert by default.
        promote: Optional promotion command / manual marker.
        index: Declaration order, used to break specificity ties.
    """

    name: str
    kind: str | None
    labels: dict[str, Any]
    detect: dict[str, Any]
    approvals: dict[str, Approval]
    tests: tuple[str, ...]
    promote: dict[str, Any]
    index: int

    def approval(self, action: str) -> Approval:
        """Return the policy for an action class, defaulting to unresolved.

        Args:
            action: One of :data:`ACTION_CLASSES`.

        Returns:
            The declared policy, or an unresolved policy if none was declared.
        """
        return self.approvals.get(action, Approval("unresolved"))


@dataclass(frozen=True, slots=True)
class Profile:
    """A loaded, validated deployment profile.

    Attributes:
        version: Schema version.
        citation: Optional policy citation quoted in denial messages.
        require_profile: In-file equivalent of ``SUPERHUMAN_REQUIRE_PROFILE``.
        ladder: Ordered rungs.
        conventions: Convention pack names or overlay paths.
        models: Tier -> model id/alias.
        path: Source file, or ``None`` for the built-in ladder.
        digest: Hash over declared cells only (spec §5, decision D-12).
    """

    version: int
    citation: str | None
    require_profile: bool
    ladder: tuple[Rung, ...]
    conventions: tuple[str, ...]
    models: dict[str, str]
    path: Path | None
    digest: str

    @property
    def is_builtin(self) -> bool:
        """Whether this is the built-in zero-config ladder.

        Returns:
            True when no profile file backed this profile.
        """
        return self.path is None


#: Built-in ladder used when no profile file is found (spec §7.1). Ref-space
#: only — no path detection — so a stranger's ``~/code/my-products/`` is never
#: matched by accident (spec §7.2).
#:
#: Ordering is load-bearing: rungs that match on equally-specific detectors are
#: broken by declaration order, so narrower rungs come first. ``trunk`` must
#: precede ``work`` (both match one ``branch`` key on ``main``), and ``local``
#: is last so any git-backed rung outranks it.
BUILTIN_LADDER: list[dict[str, Any]] = [
    {
        "name": "stable",
        "detect": {"tag_channel": "stable"},
        "approvals": {"act_unattended": "never", "promote_into": ["human"]},
    },
    {
        "name": "trunk",
        "detect": {"branch": ["main", "master", "trunk"]},
        "approvals": {"act_unattended": None, "promote_into": None},
    },
    {
        "name": "work",
        "detect": {"branch": ["*"]},
        "approvals": {"act_unattended": ["self"], "promote_into": "none"},
    },
    # Catch-all for locations git cannot describe (a plain directory, a
    # checkout with no commits). Without it, a non-git project would match no
    # rung at all and — because `stable` declares a hard block — be denied.
    {
        "name": "local",
        "detect": {"default": True},
        "approvals": {"act_unattended": ["self"], "promote_into": "none"},
    },
]

_RUNG_KEYS = {"name", "kind", "labels", "detect", "approvals", "tests", "promote"}
_TOP_KEYS = {
    "version",
    "citation",
    "require_profile",
    "defaults",
    "ladder",
    "conventions",
    "models",
}


def find_profile(cwd: Path) -> Path | None:
    """Locate a profile by search path (spec §6.2).

    There is no registration step: every consumer runs this same lookup at call
    time, so install order between skills is irrelevant.

    The project-local walk is **bounded**. Without a ceiling it would escape the
    project and match the user-level profile as though it were project-local,
    because the home directory is an ancestor of most checkouts — collapsing
    tiers 2 and 3 into one and making ``explain`` misreport which rule fired.
    The walk therefore stops at the enclosing git repository root, or at the
    home directory when the location is not a repository.

    Args:
        cwd: Directory to start the upward walk from.

    Returns:
        The profile path, or ``None`` when running zero-config.
    """
    override = os.environ.get("SUPERHUMAN_PROFILE")
    if override:
        return Path(override)

    home = Path.home()
    toplevel = _git(cwd, "rev-parse", "--show-toplevel") if cwd.is_dir() else None
    ceiling = Path(toplevel) if toplevel else None

    for directory in [cwd, *cwd.parents]:
        if directory == home:
            break  # the home directory is tier 3, checked explicitly below
        candidate = directory / ".superhuman" / "profile.yaml"
        if candidate.is_file():
            return candidate
        if ceiling is not None and directory == ceiling:
            break  # never search outside the project

    candidate = home / ".superhuman" / "profile.yaml"
    return candidate if candidate.is_file() else None


def _digest(declared: Any) -> str:
    """Hash the declared cells of a profile.

    Unresolved (``null``) cells are excluded so that filling one in later is a
    non-drift event (spec §5, decision D-12).

    Args:
        declared: JSON-serialisable view of the declared configuration.

    Returns:
        A ``sha256:``-prefixed hex digest.
    """
    blob = json.dumps(declared, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_profile(path: Path | None) -> Profile:
    """Load and validate a profile, or build the zero-config default.

    Args:
        path: Profile file, or ``None`` to use :data:`BUILTIN_LADDER`.

    Returns:
        The validated profile.

    Raises:
        ProfileError: On unreadable YAML, unknown keys, or an invalid value.
    """
    if path is None:
        raw: dict[str, Any] = {"version": SCHEMA_VERSION, "ladder": BUILTIN_LADDER}
    else:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ProfileError(f"{path}: invalid YAML — {exc}") from exc
        if not isinstance(raw, dict):
            raise ProfileError(f"{path}: top level must be a mapping")

    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise ProfileError(f"unknown top-level key(s): {sorted(unknown)}")

    version = raw.get("version")
    if version != SCHEMA_VERSION:
        raise ProfileError(f"version must be {SCHEMA_VERSION}, got {version!r}")

    defaults = raw.get("defaults") or {}
    default_approvals = defaults.get("approvals") or {}

    entries = raw.get("ladder")
    if entries is None:
        entries = BUILTIN_LADDER
    if not isinstance(entries, list) or not entries:
        raise ProfileError("ladder must be a non-empty list")

    rungs: list[Rung] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ProfileError(f"ladder[{index}] must be a mapping")
        extra = set(entry) - _RUNG_KEYS
        if extra:
            raise ProfileError(f"ladder[{index}]: unknown key(s) {sorted(extra)}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ProfileError(f"ladder[{index}]: 'name' is required")
        if name in seen:
            raise ProfileError(f"duplicate rung name {name!r}")
        seen.add(name)

        detect = entry.get("detect") or {}
        if not isinstance(detect, dict) or not detect:
            raise ProfileError(f"rung {name!r}: 'detect' is required and non-empty")
        bad = set(detect) - set(DETECTOR_TIERS)
        if bad:
            raise ProfileError(f"rung {name!r}: unknown detector(s) {sorted(bad)}")

        merged = {**default_approvals, **(entry.get("approvals") or {})}
        approvals: dict[str, Approval] = {}
        for action in ACTION_CLASSES:
            if action in merged:
                approvals[action] = Approval.parse(
                    merged[action], f"rung {name!r}.approvals.{action}"
                )
        stray = set(merged) - set(ACTION_CLASSES)
        if stray:
            raise ProfileError(f"rung {name!r}: unknown action class(es) {sorted(stray)}")

        rungs.append(
            Rung(
                name=name,
                kind=entry.get("kind"),
                labels=entry.get("labels") or {},
                detect=detect,
                approvals=approvals,
                tests=tuple(entry.get("tests") or ()),
                promote=entry.get("promote") or {},
                index=index,
            )
        )

    declared = {
        "version": version,
        "ladder": [
            {
                "name": r.name,
                "detect": r.detect,
                "approvals": {
                    a: p.to_json()
                    for a, p in sorted(r.approvals.items())
                    if p.mode != "unresolved"
                },
            }
            for r in rungs
        ],
    }

    return Profile(
        version=version,
        citation=raw.get("citation"),
        require_profile=bool(raw.get("require_profile", False)),
        ladder=tuple(rungs),
        conventions=tuple(raw.get("conventions") or ()),
        models=raw.get("models") or {},
        path=path,
        digest=_digest(declared),
    )


# --------------------------------------------------------------------------- #
# Context probing (offline, deterministic)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Context:
    """The resolved coordinates of the current location.

    Attributes:
        root: Absolute project root.
        segments: Path segments of ``root``.
        branch: Current git branch, or ``None``.
        tag_channel: ``stable``/``prerelease`` when at a tag, else ``None``.
        worktree: ``linked``/``main`` when in git, else ``None``.
        env_markers: Values of ``## Environment:`` found in SUPERHUMAN.md files.
        marker_files: Names of files present in the project root.
    """

    root: Path
    segments: tuple[str, ...]
    branch: str | None
    tag_channel: str | None
    worktree: str | None
    env_markers: tuple[str, ...]
    marker_files: frozenset[str]


def _git(root: Path, *args: str) -> str | None:
    """Run a git command, returning stripped stdout or ``None`` on failure.

    Args:
        root: Working directory.
        *args: Git arguments.

    Returns:
        Stdout with surrounding whitespace removed, or ``None``.
    """
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _env_markers(root: Path) -> tuple[str, ...]:
    """Collect ``## Environment:`` values from every SUPERHUMAN.md under the root.

    Args:
        root: Project root.

    Returns:
        Lower-cased, whitespace-stripped marker values.
    """
    found: list[str] = []
    base = root / "docs" / "superhuman"
    if not base.is_dir():
        return ()
    for path in base.rglob("SUPERHUMAN.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = re.match(r"^##\s*Environment:\s*(.*)$", line, re.IGNORECASE)
            if match:
                value = re.sub(r"\s+", "", match.group(1)).lower()
                if value and not value.startswith("{{"):
                    found.append(value)
                break
    return tuple(found)


def probe(root: Path) -> Context:
    """Gather every detector coordinate for a project root.

    All probes are offline and deterministic (spec §9.1): git plumbing and file
    reads only, never a network call.

    Args:
        root: Project root (need not exist as a git repo).

    Returns:
        The probed context.
    """
    absolute = root if root.is_absolute() else (Path.cwd() / root)
    try:
        absolute = absolute.resolve()
    except OSError:
        absolute = absolute.absolute()

    branch = _git(absolute, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        branch = None

    tag = _git(absolute, "describe", "--exact-match", "--tags", "HEAD")
    tag_channel = None
    if tag:
        tag_channel = "stable" if _STABLE_TAG_RE.match(tag) else "prerelease"

    worktree = None
    common = _git(absolute, "rev-parse", "--git-common-dir")
    own = _git(absolute, "rev-parse", "--git-dir")
    if common and own:
        worktree = "main" if Path(common).name == Path(own).name else "linked"

    markers: frozenset[str] = frozenset(
        p.name for p in absolute.iterdir() if p.is_file()
    ) if absolute.is_dir() else frozenset()

    return Context(
        root=absolute,
        segments=tuple(part for part in absolute.as_posix().split("/") if part),
        branch=branch,
        tag_channel=tag_channel,
        worktree=worktree,
        env_markers=_env_markers(absolute),
        marker_files=markers,
    )


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def _globs(value: Any) -> list[str]:
    """Normalise a detector value to a list of glob strings.

    Args:
        value: A string or list of strings.

    Returns:
        The values as a list.
    """
    return [value] if isinstance(value, str) else list(value or ())


def _match_key(key: str, spec: Any, ctx: Context) -> bool:
    """Evaluate one detector key against the probed context.

    Args:
        key: Detector family name.
        spec: The declared value for that key.
        ctx: Probed context.

    Returns:
        Whether the key matches.
    """
    if key == "default":
        return bool(spec)
    if key == "marker_file":
        return spec in ctx.marker_files
    if key == "env_marker":
        return any(m in _globs(spec) for m in ctx.env_markers)
    if key == "path_segments":
        pats = _globs(spec)
        return any(
            fnmatch.fnmatch(seg, pat) for seg in ctx.segments for pat in pats
        )
    if key == "branch":
        if ctx.branch is None:
            return False
        return any(fnmatch.fnmatch(ctx.branch, pat) for pat in _globs(spec))
    if key == "tag_channel":
        if ctx.tag_channel is None:
            return False
        return any(
            ctx.tag_channel == pat or fnmatch.fnmatch(ctx.tag_channel, pat)
            for pat in _globs(spec)
        )
    if key == "worktree":
        return spec == "any" or (ctx.worktree is not None and ctx.worktree == spec)
    return False


@dataclass(frozen=True, slots=True)
class Match:
    """A rung that matched, with the evidence used to rank it.

    Attributes:
        rung: The matching rung.
        matched: Detector keys that matched, strongest tier first.
        authority: Tier of the strongest matched key (lower wins).
        specificity: Number of matched keys (higher wins).
    """

    rung: Rung
    matched: tuple[str, ...]
    authority: int
    specificity: int


def match_rungs(profile: Profile, ctx: Context) -> list[Match]:
    """Find every rung whose detectors all match, ranked by precedence.

    Matching is conjunctive: every declared detector key must match (spec §3.2).
    Ranking is by authority tier, then specificity, then declaration order
    (spec §3.4).

    Args:
        profile: The loaded profile.
        ctx: Probed context.

    Returns:
        Matching rungs, best first.
    """
    matches: list[Match] = []
    for rung in profile.ladder:
        keys = list(rung.detect)
        if not all(_match_key(k, rung.detect[k], ctx) for k in keys):
            continue
        ranked = sorted(keys, key=lambda k: DETECTOR_TIERS[k])
        matches.append(
            Match(
                rung=rung,
                matched=tuple(ranked),
                authority=min(DETECTOR_TIERS[k] for k in keys),
                specificity=len(keys),
            )
        )
    matches.sort(key=lambda m: (m.authority, -m.specificity, m.rung.index))
    return matches


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of resolving a context against a profile.

    Attributes:
        profile: The profile used.
        context: The probed context.
        matches: All matching rungs, best first.
        stage: The winning rung, or ``None`` when nothing matched.
        ambiguous: Rungs tied with the winner on authority and specificity.
        warnings: Advisory messages (never fatal).
    """

    profile: Profile
    context: Context
    matches: tuple[Match, ...]
    stage: Rung | None
    ambiguous: tuple[str, ...]
    warnings: tuple[str, ...] = field(default=())

    @property
    def has_deny_rung(self) -> bool:
        """Whether the ladder declares any hard block.

        Returns:
            True if some rung forbids some action outright.
        """
        return any(
            p.mode == "never"
            for rung in self.profile.ladder
            for p in rung.approvals.values()
        )


def _terminal_warnings(profile: Profile) -> list[str]:
    """Warn when a terminal rung can be promoted into without a human.

    Never blocks (spec §4.4, decision D-10): the schema permits an agent-only
    approver, but it must never be silent.

    Args:
        profile: The loaded profile.

    Returns:
        Warning strings, possibly empty.
    """
    out: list[str] = []
    for rung in profile.ladder:
        policy = rung.approvals.get("promote_into")
        if policy is None or policy.mode in ("never", "unresolved"):
            continue
        unattended = rung.approvals.get("act_unattended")
        terminal = unattended is not None and unattended.mode == "never"
        if terminal and not policy.requires_human():
            out.append(
                f"rung {rung.name!r}: promote_into ({policy.describe()}) contains no "
                "'human' approver, but the rung forbids unattended operation — "
                "work can land in a protected rung without human sign-off"
            )
    return out


def resolve(root: Path, profile: Profile) -> Resolution:
    """Resolve a project root to a single rung.

    Args:
        root: Project root.
        profile: The loaded profile.

    Returns:
        The resolution, including ambiguity and warnings.
    """
    ctx = probe(root)
    matches = match_rungs(profile, ctx)
    stage = matches[0].rung if matches else None
    ambiguous: tuple[str, ...] = ()
    if len(matches) > 1:
        best = matches[0]
        tied = [
            m.rung.name
            for m in matches[1:]
            if m.authority == best.authority and m.specificity == best.specificity
        ]
        ambiguous = tuple(tied)
    return Resolution(
        profile=profile,
        context=ctx,
        matches=tuple(matches),
        stage=stage,
        ambiguous=ambiguous,
        warnings=tuple(_terminal_warnings(profile)),
    )


# --------------------------------------------------------------------------- #
# Project preconditions (orthogonal to the ladder)
# --------------------------------------------------------------------------- #


def rollback_plan_gap(root: Path) -> str | None:
    """Check the low-HITL rollback-plan precondition.

    This is a *project-state* precondition, not ladder policy: a run that edits
    pre-existing code at the lowest human-involvement level must have a written
    revert procedure. Ported unchanged from the pre-0.7.0 gate's Guard 3.

    Args:
        root: Project root.

    Returns:
        A message describing the gap, or ``None`` when satisfied.
    """
    base = root / "docs" / "superhuman"
    if not base.is_dir():
        return None
    for path in sorted(base.rglob("SUPERHUMAN.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = re.match(
                r"^\*\*Modifies-existing-code:\*\*\s*(.*)$", line, re.IGNORECASE
            )
            if not match:
                continue
            if re.sub(r"\s+", "", match.group(1)).lower() == "yes":
                slug_dir = path.parent
                if not (slug_dir / "ROLLBACK.md").is_file():
                    return (
                        f"HITL-L requires {slug_dir.as_posix()}/ROLLBACK.md when "
                        "Modifies-existing-code: yes "
                        "(templates/artifacts/ROLLBACK.md.tpl) — none found"
                    )
            break
    return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _load_for_cli(root: Path) -> Profile:
    """Locate and load the profile for a CLI invocation.

    Args:
        root: Project root, used as the start of the upward search.

    Returns:
        The loaded profile, or the built-in ladder.

    Raises:
        ProfileError: If no profile is found and one is required.
    """
    path = find_profile(root)
    if path is not None and not path.is_file():
        raise ProfileError(f"SUPERHUMAN_PROFILE points at a missing file: {path}")
    if path is None and os.environ.get("SUPERHUMAN_REQUIRE_PROFILE") == "1":
        raise ProfileError(
            "no profile found and SUPERHUMAN_REQUIRE_PROFILE=1 "
            f"(searched {root}/.superhuman/profile.yaml upward, then "
            f"{Path.home() / '.superhuman' / 'profile.yaml'})"
        )
    profile = load_profile(path)
    if profile.is_builtin and profile.require_profile:  # pragma: no cover
        raise ProfileError("require_profile is set but no profile file was found")
    return profile


def _resolution_json(res: Resolution) -> dict[str, Any]:
    """Render a resolution as the documented JSON object.

    Args:
        res: The resolution.

    Returns:
        A JSON-serialisable mapping.
    """
    best = res.matches[0] if res.matches else None
    return {
        "stage": res.stage.name if res.stage else None,
        "kind": res.stage.kind if res.stage else None,
        "labels": res.stage.labels if res.stage else {},
        "matched_by": list(best.matched) if best else [],
        "specificity": best.specificity if best else 0,
        "ambiguous_with": list(res.ambiguous),
        "profile": res.profile.path.as_posix() if res.profile.path else "(built-in)",
        "profile_hash": res.profile.digest,
        "citation": res.profile.citation,
        "approvals": {
            action: res.stage.approval(action).to_json() for action in ACTION_CLASSES
        }
        if res.stage
        else {},
        "tests": list(res.stage.tests) if res.stage else [],
        "warnings": list(res.warnings),
    }


def cmd_resolve(args: argparse.Namespace) -> int:
    """Print the resolved rung as JSON.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code.
    """
    profile = _load_for_cli(Path(args.root))
    res = resolve(Path(args.root), profile)
    print(json.dumps(_resolution_json(res), indent=2, sort_keys=False))
    return EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    """Print a human-readable trace of the precedence chain.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code.
    """
    profile = _load_for_cli(Path(args.root))
    res = resolve(Path(args.root), profile)
    ctx = res.context
    src = profile.path.as_posix() if profile.path else "(built-in ladder)"

    print(f"root     {ctx.root.as_posix()}")
    print(f"branch   {ctx.branch or '(none / detached)'}")
    print(f"tag      {ctx.tag_channel or '(not at a tag)'}")
    print(f"worktree {ctx.worktree or '(not a git repo)'}")
    print(f"markers  {', '.join(ctx.env_markers) or '(none)'}")
    print(f"profile  {src}  ({profile.digest[:18]}…)")
    print()
    for rung in profile.ladder:
        hits = [k for k in rung.detect if _match_key(k, rung.detect[k], ctx)]
        misses = [k for k in rung.detect if k not in hits]
        status = "MATCH" if len(hits) == len(rung.detect) else "no match"
        detail = f"matched {hits}" if hits else ""
        if misses and status != "MATCH":
            detail = f"failed on {misses}"
        print(f"  {rung.name:<24} {status:<9} {detail}")
    print()
    if res.stage is None:
        print("resolved  (nothing matched)")
    else:
        best = res.matches[0]
        print(
            f"resolved  {res.stage.name}   "
            f"(authority tier {best.authority}, specificity {best.specificity}, "
            f"{'tied with ' + ', '.join(res.ambiguous) if res.ambiguous else 'no ties'})"
        )
        for action in ACTION_CLASSES:
            print(f"  {action:<16} {res.stage.approval(action).describe()}")
    for warning in res.warnings:
        print(f"\nWARNING: {warning}", file=sys.stderr)
    return EXIT_OK


def _hitl(value: str) -> str:
    """Normalise a HITL level, accepting the pre-0.8.0 numeric spelling.

    Args:
        value: ``H``/``M``/``L`` or the legacy ``0``/``1``/``2``.

    Returns:
        The canonical ``H``/``M``/``L`` form.

    Raises:
        ProfileError: If the value is not a recognised level.
    """
    mapping = {"0": "H", "1": "M", "2": "L", "H": "H", "M": "M", "L": "L"}
    key = value.strip().upper()
    if key not in mapping:
        raise ProfileError(f"--level must be H, M or L (or legacy 0/1/2), got {value!r}")
    return mapping[key]


def cmd_check(args: argparse.Namespace) -> int:
    """Evaluate one action class and return a verdict exit code.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code (0 allow, 3 deny, 4 unresolved).
    """
    root = Path(args.root)
    level = _hitl(args.level)
    if level == "H":
        print("superhuman-profile: OK (HITL-H needs no ladder verdict)")
        return EXIT_OK

    profile = _load_for_cli(root)
    res = resolve(root, profile)

    if res.stage is None:
        if res.has_deny_rung:
            print(
                "superhuman-profile: BLOCKED — no rung matched and the ladder declares "
                "a hard block; add a rung with `detect: {default: true}` to say what "
                "an unmatched location means.",
                file=sys.stderr,
            )
            return EXIT_DENIED
        print("superhuman-profile: OK (no rung matched; ladder declares no hard block)")
        return EXIT_OK

    policy = res.stage.approval(args.action)
    verdict = policy.unattended_verdict() if args.action == "act_unattended" else (
        "UNRESOLVED" if policy.mode == "unresolved"
        else "DENY" if policy.mode == "never"
        else "ALLOW"
    )

    for warning in res.warnings:
        print(f"superhuman-profile: WARNING — {warning}", file=sys.stderr)

    if verdict == "DENY":
        best = res.matches[0]
        cite = f" ({profile.citation})" if profile.citation else ""
        print(
            f"superhuman-profile: BLOCKED — rung {res.stage.name!r}, "
            f"{args.action}: {policy.describe()}{cite}; matched by "
            f"{', '.join(best.matched)}",
            file=sys.stderr,
        )
        return EXIT_DENIED

    if verdict == "UNRESOLVED":
        print(
            f"superhuman-profile: UNRESOLVED — rung {res.stage.name!r} has no declared "
            f"policy for {args.action}. Declare it in "
            f"{profile.path.as_posix() if profile.path else '~/.superhuman/profile.yaml'} "
            "before running unattended.",
            file=sys.stderr,
        )
        return EXIT_UNRESOLVED

    if level == "L":
        gap = rollback_plan_gap(res.context.root)
        if gap:
            print(f"superhuman-profile: BLOCKED — {gap}", file=sys.stderr)
            return EXIT_DENIED

    print(
        f"superhuman-profile: OK (rung {res.stage.name}, HITL-{level}, "
        f"{args.action}={policy.describe()}) — {res.context.root.as_posix()}"
    )
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate the profile and list unresolved cells.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code.
    """
    profile = _load_for_cli(Path(args.root))
    src = profile.path.as_posix() if profile.path else "(built-in ladder)"
    print(f"profile  {src}")
    print(f"version  {profile.version}")
    print(f"rungs    {len(profile.ladder)}")

    unresolved = [
        f"{rung.name}.{action}"
        for rung in profile.ladder
        for action in ACTION_CLASSES
        if rung.approval(action).mode == "unresolved"
    ]
    if unresolved:
        print(f"\nunresolved cells ({len(unresolved)}) — will halt an unattended run:")
        for cell in unresolved:
            print(f"  - {cell}")
    else:
        print("\nunresolved cells: none")

    for warning in _terminal_warnings(profile):
        print(f"\nWARNING: {warning}", file=sys.stderr)

    print("\nvalidate: OK")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Discovery (onboarding only — may probe; never on the resolver's hot path)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Discovery:
    """What could be learned about a project's deployment surface.

    Per spec §9.1 this runs only during onboarding, so unlike :func:`probe` it
    may be slow and may reach the network. Every field degrades to empty rather
    than failing: a missing ``gh``, an unauthenticated host, or no network must
    never stop ``init`` from producing a usable profile.

    Attributes:
        is_repo: Whether the root is inside a git repository.
        branches: Local branch names.
        default_branch: Best guess at the trunk branch.
        has_remote: Whether an ``origin`` remote is configured.
        protected: Branches carrying a hosted protection rule.
        ci_environments: Hosted deployment environments -> required reviewer count.
        workflow_environments: ``environment:`` values found in CI workflow files.
        compose_environments: Suffixes of ``docker-compose-<env>.yml`` files.
        dotenv_environments: Suffixes of ``.env.<name>`` files.
        probed_network: Whether a network probe was attempted and succeeded.
    """

    is_repo: bool = False
    branches: tuple[str, ...] = ()
    default_branch: str | None = None
    has_remote: bool = False
    protected: tuple[str, ...] = ()
    ci_environments: tuple[tuple[str, int], ...] = ()
    workflow_environments: tuple[str, ...] = ()
    compose_environments: tuple[str, ...] = ()
    dotenv_environments: tuple[str, ...] = ()
    probed_network: bool = False

    @property
    def deployment_names(self) -> tuple[str, ...]:
        """Every distinct environment name found by any local or hosted signal.

        Returns:
            Sorted, de-duplicated environment names.
        """
        names = {n for n, _ in self.ci_environments}
        names |= set(self.workflow_environments)
        names |= set(self.compose_environments)
        names |= set(self.dotenv_environments)
        return tuple(sorted(n for n in names if n and n.lower() not in {"example", "sample"}))


def _gh_json(args: list[str], cwd: Path) -> Any:
    """Call ``gh`` and parse JSON, returning ``None`` on any failure.

    Args:
        args: Arguments after ``gh``.
        cwd: Working directory.

    Returns:
        Parsed JSON, or ``None`` when gh is missing, unauthenticated, or errors.
    """
    try:
        out = subprocess.run(
            ["gh", *args], cwd=cwd, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def discover(root: Path, offline: bool = False) -> Discovery:
    """Inspect a project for deployment-environment signals.

    Args:
        root: Project root.
        offline: Skip every network probe.

    Returns:
        What could be determined; absent signals are simply empty.
    """
    root = root if root.is_absolute() else (Path.cwd() / root)
    is_repo = _git(root, "rev-parse", "--is-inside-work-tree") == "true"

    branches: tuple[str, ...] = ()
    default_branch = None
    has_remote = False
    if is_repo:
        raw = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads") or ""
        branches = tuple(b for b in raw.splitlines() if b)
        for candidate in ("main", "master", "trunk"):
            if candidate in branches:
                default_branch = candidate
                break
        has_remote = bool(_git(root, "remote", "get-url", "origin"))

    # -- local, offline signals -------------------------------------------
    workflow_envs: set[str] = set()
    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        for wf in list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")):
            try:
                for line in wf.read_text(encoding="utf-8", errors="replace").splitlines():
                    match = re.match(r"\s*environment:\s*([A-Za-z0-9._-]+)\s*$", line)
                    if match:
                        workflow_envs.add(match.group(1))
            except OSError:
                continue

    compose_envs = {
        m.group(1)
        for f in root.glob("docker-compose-*.y*ml")
        if (m := re.match(r"docker-compose-(.+?)\.ya?ml$", f.name))
    }
    dotenv_envs = {
        m.group(1)
        for f in root.glob(".env.*")
        if (m := re.match(r"\.env\.(.+)$", f.name))
        and not f.name.endswith((".bak", ".example"))
    }

    # -- hosted signals (opt-out) -----------------------------------------
    protected: list[str] = []
    ci_envs: list[tuple[str, int]] = []
    probed = False
    if not offline and is_repo and has_remote:
        envs = _gh_json(["api", "repos/{owner}/{repo}/environments"], root)
        if isinstance(envs, dict):
            probed = True
            for env in envs.get("environments") or []:
                name = env.get("name")
                if not name:
                    continue
                reviewers = 0
                for rule in env.get("protection_rules") or []:
                    if rule.get("type") == "required_reviewers":
                        reviewers = len(rule.get("reviewers") or []) or 1
                ci_envs.append((name, reviewers))
        for branch in filter(None, [default_branch]):
            rule = _gh_json(
                ["api", "repos/{owner}/{repo}/branches/" + branch + "/protection"], root
            )
            if isinstance(rule, dict):
                probed = True
                protected.append(branch)

    return Discovery(
        is_repo=is_repo,
        branches=branches,
        default_branch=default_branch,
        has_remote=has_remote,
        protected=tuple(protected),
        ci_environments=tuple(ci_envs),
        workflow_environments=tuple(sorted(workflow_envs)),
        compose_environments=tuple(sorted(compose_envs)),
        dotenv_environments=tuple(sorted(dotenv_envs)),
        probed_network=probed,
    )


#: Name fragments that mark an environment as protected. Matching is on the
#: whole lower-cased name. These drive a PROPOSAL the operator reviews — never a
#: silent policy decision.
_PROTECTED_HINTS = ("prod", "live", "uat", "acceptance", "staging", "stage")
_PERMISSIVE_HINTS = ("dev", "lab", "sandbox", "local", "ci", "test", "qa")


def propose_ladder(disc: Discovery) -> list[dict[str, Any]]:
    """Turn discovery output into a proposed ladder.

    Ordering follows the authoring rule the presets document: narrower rungs
    first, deny before allow, so an equal-specificity tie resolves to the safer
    verdict.

    Args:
        disc: Discovery results.

    Returns:
        Rung dicts ready to render. Never empty — always at least a catch-all.
    """
    protected: list[dict[str, Any]] = []
    permissive: list[dict[str, Any]] = []
    reviewers = dict(disc.ci_environments)

    for name in disc.deployment_names:
        lowered = name.lower()
        is_protected = any(h in lowered for h in _PROTECTED_HINTS) or reviewers.get(name, 0) > 0
        if is_protected:
            need = reviewers.get(name, 0)
            protected.append({
                "name": name,
                "kind": "production" if ("prod" in lowered or "live" in lowered) else "acceptance",
                # A marker is safe on a protected rung: markers outrank paths, so
                # here it can only ever tighten. It is deliberately absent from
                # the permissive rungs below, where it could loosen.
                "detect": {"path_segments": [name], "env_marker": [name]},
                "approvals": {
                    "act_unattended": "never",
                    "promote_into": {"all_of": ["human"] * need} if need > 1 else ["human"],
                },
            })
        else:
            permissive.append({
                "name": name,
                "kind": "development" if any(h in lowered for h in _PERMISSIVE_HINTS) else None,
                "detect": {"path_segments": [name]},
                "approvals": {"act_unattended": ["self"], "promote_into": None},
            })

    trunk = disc.default_branch or "main"
    trunk_rung = {
        "name": "trunk",
        "kind": "integration",
        "detect": {"default": True, "branch": [trunk]},
        "approvals": {
            "act_unattended": "never",
            # A hosted protection rule is strong evidence a human gates trunk;
            # without that evidence, leave it undeclared rather than guess.
            "promote_into": ["human"] if disc.protected else None,
        },
    }
    work_rung = {
        "name": "work",
        "kind": "authoring",
        "detect": {"default": True},
        "approvals": {"act_unattended": ["self"], "promote_into": "none"},
    }
    return [*protected, *permissive, trunk_rung, work_rung]


def _render_approval(value: Any) -> str:
    """Render one approval spec as inline YAML.

    Args:
        value: An approval value in proposal form.

    Returns:
        A YAML scalar, flow sequence, or flow mapping.
    """
    if value is None:
        return "null            # UNRESOLVED — declare before any unattended run"
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        key, items = next(iter(value.items()))
        return "{ " + key + ": [" + ", ".join(items) + "] }"
    return "[" + ", ".join(value) + "]"


def render_profile(ladder: list[dict[str, Any]], disc: "Discovery | None" = None) -> str:
    """Render a ladder as commented YAML.

    Hand-rendered rather than ``yaml.dump``-ed so declaration order is preserved
    exactly and the load-bearing authoring rules are written into the file the
    operator will later edit.

    Args:
        ladder: Rung dicts.
        disc: Discovery results, summarised into a provenance comment.

    Returns:
        The profile file contents.
    """
    out: list[str] = [
        "# superhuman deployment profile.",
        "#",
        "# Generated by `superhuman_profile.py init`. Review every line: this ladder was",
        "# PROPOSED from what could be discovered, and a proposal is not a policy.",
        "#",
        "# Two authoring rules are load-bearing:",
        "#   1. Narrower rungs first. Rungs matching equally-specific detectors are broken",
        "#      by declaration order, so deny rungs precede allow rungs and a tie resolves",
        "#      to the safer verdict.",
        "#   2. Permissive rungs carry no `env_marker`. Markers outrank path detection, so",
        "#      marking a permissive rung would let a `dev` marker inside a production path",
        "#      override the production block.",
        "#",
        "# Verify:  superhuman_profile.py explain <project-root>",
        "#          superhuman_profile.py doctor  <project-root>",
    ]
    if disc is not None:
        found: list[str] = []
        if disc.ci_environments:
            found.append("hosted environments: " + ", ".join(n for n, _ in disc.ci_environments))
        if disc.workflow_environments:
            found.append("CI workflows: " + ", ".join(disc.workflow_environments))
        if disc.compose_environments:
            found.append("compose files: " + ", ".join(disc.compose_environments))
        if disc.dotenv_environments:
            found.append("dotenv files: " + ", ".join(disc.dotenv_environments))
        if disc.protected:
            found.append("branch protection on: " + ", ".join(disc.protected))
        out.append("#")
        out.append("# Discovered: " + ("; ".join(found) if found else "no deployment signals"))
        if not disc.probed_network:
            out.append("# (no hosted probe — offline, or `gh` unavailable/unauthenticated)")
    out += ["", "version: " + str(SCHEMA_VERSION), "", "ladder:"]

    for rung in ladder:
        out.append("  - name: " + rung["name"])
        if rung.get("kind"):
            out.append("    kind: " + rung["kind"])
        out.append("    detect:")
        for key, val in rung["detect"].items():
            rendered = "true" if val is True else "[" + ", ".join(val) + "]"
            out.append("      " + key + ": " + rendered)
        out.append("    approvals:")
        for action in ACTION_CLASSES:
            if action in rung["approvals"]:
                out.append("      " + action + ": " + _render_approval(rung["approvals"][action]))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def cmd_init(args: argparse.Namespace) -> int:
    """Propose, and optionally write, a profile.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code.
    """
    root = Path(args.root)
    dest = Path(args.output) if args.output else (Path.home() / ".superhuman" / "profile.yaml")

    if args.preset:
        src = (
            Path(__file__).resolve().parent.parent
            / "profiles" / "presets" / (args.preset + ".yaml")
        )
        if not src.is_file():
            available = sorted(p.stem for p in src.parent.glob("*.yaml"))
            raise ProfileError(
                "unknown preset " + repr(args.preset) + "; available: " + ", ".join(available)
            )
        content = src.read_text(encoding="utf-8")
        disc = None
    else:
        disc = discover(root, offline=args.offline)
        content = render_profile(propose_ladder(disc), disc)

    # Validate what we are about to hand over, before handing it over.
    tmp = dest.parent / (dest.name + ".init-check")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(content, encoding="utf-8")
        profile = load_profile(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    if args.dry_run:
        print(content, end="")
        print("\n# --- dry run: nothing written. Target would be " + dest.as_posix() + " ---")
        return EXIT_OK

    if dest.exists() and not args.force:
        print(
            "superhuman-profile: " + dest.as_posix() + " already exists — refusing to overwrite.\n"
            "  Review the proposal first:   init --dry-run\n"
            "  Then overwrite deliberately: init --force",
            file=sys.stderr,
        )
        return EXIT_USAGE

    dest.write_text(content, encoding="utf-8")
    print("superhuman-profile: wrote " + dest.as_posix())
    print("  " + str(len(profile.ladder)) + " rungs: "
          + ", ".join(r.name for r in profile.ladder))
    unresolved = [
        r.name + "." + a
        for r in profile.ladder
        for a in ACTION_CLASSES
        if r.approval(a).mode == "unresolved"
    ]
    if unresolved:
        print("  " + str(len(unresolved))
              + " cell(s) left undeclared — an unattended run halts on these:")
        for cell in unresolved:
            print("    - " + cell)
    print("\nReview it, then verify with:  superhuman_profile.py doctor .")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    """Print a one-screen health report for this location.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code.
    """
    root = Path(args.root)
    require = os.environ.get("SUPERHUMAN_REQUIRE_PROFILE") == "1"
    found = find_profile(root)

    print("superhuman profile doctor")
    print("=" * 60)
    print("  root            " + (root.resolve().as_posix() if root.exists() else str(root)))
    print("  profile         " + (found.as_posix() if found else "(none — built-in ladder)"))
    print("  require-profile " + (
        "ON  — a missing profile is a hard error" if require
        else "off — a missing profile falls back to the built-in ladder"
    ))

    if found is None and require:
        print("\n  FAIL: no profile found while SUPERHUMAN_REQUIRE_PROFILE=1", file=sys.stderr)
        return EXIT_USAGE

    try:
        profile = load_profile(found)
    except ProfileError as exc:
        print("\n  FAIL: " + str(exc), file=sys.stderr)
        return EXIT_USAGE

    res = resolve(root, profile)
    ctx = res.context
    print("  schema          v" + str(profile.version) + "  (" + profile.digest[:18] + "…)")
    print("  rungs           " + str(len(profile.ladder)))
    print()
    print("  branch          " + (ctx.branch or "(none / detached)"))
    print("  tag channel     " + (ctx.tag_channel or "(not at a tag)"))
    print("  worktree        " + (ctx.worktree or "(not a git repo)"))
    print("  env markers     " + (", ".join(ctx.env_markers) or "(none)"))
    print()

    problems = 0
    if res.stage is None:
        problems += 1
        print("  resolved rung   (nothing matched)")
        print("    -> add a rung with `detect: {default: true}` to declare what an")
        print("       unmatched location means.")
    else:
        best = res.matches[0]
        print("  resolved rung   " + res.stage.name
              + ("  [" + res.stage.kind + "]" if res.stage.kind else ""))
        print("    matched by    " + ", ".join(best.matched)
              + " (authority tier " + str(best.authority) + ")")
        if res.ambiguous:
            print("    AMBIGUOUS     also matched: " + ", ".join(res.ambiguous)
                  + " — resolved by declaration order")
        for action in ACTION_CLASSES:
            print("    " + action.ljust(14) + " " + res.stage.approval(action).describe())

    unresolved = [
        r.name + "." + a
        for r in profile.ladder
        for a in ACTION_CLASSES
        if r.approval(a).mode == "unresolved"
    ]
    if unresolved:
        print("\n  UNDECLARED (" + str(len(unresolved)) + ") — an unattended run halts on these:")
        for cell in unresolved:
            print("    - " + cell)
    for warning in res.warnings:
        problems += 1
        print("\n  WARNING: " + warning)

    # The honest gap (spec §7.4): a ref-only ladder cannot see a deployment target.
    has_location_rung = any(
        ("path_segments" in r.detect or "env_marker" in r.detect or "marker_file" in r.detect)
        for r in profile.ladder
    )
    if not has_location_rung:
        print("\n  NOTE: no deployment rungs declared — only branch/tag rungs are active.")
        print("        A production target is NOT detectable from git refs alone. If you")
        print("        deploy to a protected environment, declare it:  init --dry-run")

    print("\n" + "=" * 60)
    print("  " + ("issues found" if (problems or unresolved) else "no issues"))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="superhuman-profile",
        description="Resolve a deployment profile to a rung and evaluate its policy.",
    )
    parser.add_argument("--version", action="version", version=f"schema v{SCHEMA_VERSION}")
    subs = parser.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subs.add_parser(name, help=help_text)
        sub.add_argument("root", nargs="?", default=".", help="project root")
        return sub

    add("resolve", "print the resolved rung as JSON").set_defaults(func=cmd_resolve)
    add("explain", "trace the precedence chain").set_defaults(func=cmd_explain)
    add("validate", "validate the profile").set_defaults(func=cmd_validate)

    add('doctor', 'one-screen health report for this location').set_defaults(func=cmd_doctor)

    init = add('init', 'propose (and optionally write) a profile')
    init.add_argument('--preset', help='start from a shipped preset instead of discovery')
    init.add_argument('--output', help='destination (default ~/.superhuman/profile.yaml)')
    init.add_argument('--dry-run', action='store_true', help='print the proposal, write nothing')
    init.add_argument('--offline', action='store_true', help='skip every network probe')
    init.add_argument('--force', action='store_true', help='overwrite an existing profile')
    init.set_defaults(func=cmd_init)

    check = add("check", "evaluate one action class")
    check.add_argument("--action", default="act_unattended", choices=ACTION_CLASSES)
    check.add_argument(
        "--level", default="M", help="HITL level: H, M or L (legacy 0/1/2 accepted)"
    )
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ProfileError as exc:
        print(f"superhuman-profile: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
