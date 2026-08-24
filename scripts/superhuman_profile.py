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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, NamedTuple, Sequence

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

#: Capability tiers the ``models:`` block declares (spec/DESIGN §Component C-PROF).
#: Order is display order only; lookup is by name.
MODEL_TIERS: tuple[str, ...] = ("most_capable", "standard", "cheap")

#: Neutral, vendor-free placeholder written for a declined/deferred tier (FR-10).
#: Self-documenting on purpose — never a concrete vendor/model name — so the
#: dispatch layer can warn (C-DISP) instead of silently assuming a provider.
MODEL_PLACEHOLDER = "PROMPT_ME"

_MODEL_ENTRY_KEYS = {"primary", "fallback"}

#: Matches a top-level (column-0) `models:` key line, with or without trailing
#: inline content (flow mapping) or a trailing comment.
_MODELS_KEY_RE = re.compile(r"^models:(?:\s|$)")

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
        models: Tier -> ``{"primary": <alias>, "fallback": <alias> | None}``.
            Normalized at load time (ADR-6): a legacy bare-string tier value
            (``most_capable: opus``) is read as ``{"primary": "opus", "fallback":
            None}``; an already-mapping value passes through unchanged. Every
            downstream reader sees the mapping form regardless of which shape
            the file was written in.
        path: Source file, or ``None`` for the built-in ladder.
        digest: Hash over declared cells only (spec §5, decision D-12).
    """

    version: int
    citation: str | None
    require_profile: bool
    ladder: tuple[Rung, ...]
    conventions: tuple[str, ...]
    models: dict[str, dict[str, str | None]]
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
    # `fleet` is fleet-wiring's own opt-in observation block (Phase 1.1,
    # G8-accepted 2026-08-24). This module never reads or validates its
    # contents — that's `scripts/fleet/config.py::resolve_fleet_config`'s
    # job — but a profile combining `fleet:` with a real `ladder:`/`models:`
    # block must still pass `load_profile`'s own unknown-key check, or every
    # OTHER consumer of this profile (done-level ceiling resolution,
    # autonomous-precondition checks) fails closed the moment `fleet:` is
    # present. Recognizing the key here, without validating it, is the
    # minimal fix; deeper schema validation stays config.py's job.
    "fleet",
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


def _normalize_model_entry(value: Any, where: str) -> dict[str, str | None]:
    """Normalize one ``models:`` tier value to the canonical mapping form.

    Per ADR-6, a legacy bare string is the primary with no fallback; an
    already-mapping value is validated and taken as-is. Any other shape fails
    loud rather than being silently coerced (spec §Error handling).

    Args:
        value: The raw YAML value for one tier (``str`` or a mapping).
        where: Dotted path used in error messages.

    Returns:
        A mapping with exactly ``primary`` and ``fallback`` keys.

    Raises:
        ProfileError: If ``value`` is neither a non-empty string nor a mapping
            with a valid ``primary``/``fallback`` shape.
    """
    if isinstance(value, str):
        if not value:
            raise ProfileError(f"{where}: model alias must not be empty")
        return {"primary": value, "fallback": None}
    if isinstance(value, dict):
        extra = set(value) - _MODEL_ENTRY_KEYS
        if extra:
            raise ProfileError(f"{where}: unknown key(s) {sorted(extra)}")
        primary = value.get("primary")
        if not isinstance(primary, str) or not primary:
            raise ProfileError(f"{where}: 'primary' is required and must be a non-empty string")
        fallback = value.get("fallback")
        if fallback is not None and not isinstance(fallback, str):
            raise ProfileError(f"{where}: 'fallback' must be a string or null")
        return {"primary": primary, "fallback": fallback}
    raise ProfileError(f"{where}: must be a string or a {{primary, fallback}} mapping, got {value!r}")


def _normalize_models(raw_models: Any, where: str) -> dict[str, dict[str, str | None]]:
    """Normalize the whole ``models:`` block to the canonical per-tier mapping.

    Args:
        raw_models: The raw YAML value of the ``models:`` key, or ``None``.
        where: Dotted path used in error messages.

    Returns:
        Tier name -> normalized ``{primary, fallback}`` mapping. Empty when
        ``raw_models`` is ``None`` (FR-9: an absent block is not an error).

    Raises:
        ProfileError: If ``raw_models`` is present but not a mapping, or any
            tier value fails :func:`_normalize_model_entry`.
    """
    if raw_models is None:
        return {}
    if not isinstance(raw_models, dict):
        raise ProfileError(f"{where}: must be a mapping")
    return {
        tier: _normalize_model_entry(value, f"{where}.{tier}")
        for tier, value in raw_models.items()
    }


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

        labels = entry.get("labels")
        if labels is None:
            # Genuinely absent — a rung need not declare any labels. This is
            # the ONLY case that defaults to an empty mapping.
            labels = {}
        elif not isinstance(labels, dict):
            # #R6-2 / #R7-1 (PM-reproduced, BLOCKING): unlike `detect` above,
            # `labels` was never validated as a mapping. A scalar
            # `labels: D0-code` loaded cleanly and produced a `Rung` whose
            # `labels` was the *string* `"D0-code"`; downstream,
            # `cli._resolve_d_ceiling`'s
            # `_D_CEILING_LABEL_KEY not in resolution.stage.labels` then ran
            # as a substring test against that string instead of a mapping
            # membership test, silently granting the unrestricted D4-prod
            # default rather than failing closed.
            #
            # #R7-1 is why this MUST test `is None` above rather than the
            # `entry.get("labels") or {}` idiom the earlier fix used: `or {}`
            # short-circuits a *present but falsy* non-mapping (`labels: []`,
            # `labels: ""`, `labels: false`, `labels: 0`) to `{}` BEFORE this
            # check ever runs, so those malformed values slipped straight
            # through to the same D4-prod fail-open. Distinguishing absent
            # (`is None`) from present-falsy closes that whole class: EVERY
            # present non-mapping — falsy or truthy — is rejected here, at the
            # LOAD boundary, protecting every downstream consumer of `labels`,
            # not just `_resolve_d_ceiling` (mirroring the `detect` check
            # immediately above). A present, legitimately-empty mapping
            # (`labels: {}`) is a dict and passes — its absent-`d_ceiling`
            # behavior is correct, not a fail-open.
            raise ProfileError(f"rung {name!r}: 'labels' must be a mapping")

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
                labels=labels,
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
        models=_normalize_models(raw.get("models"), "models"),
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


class Gap(NamedTuple):
    """A project-state precondition that did not hold.

    Attributes:
        code: Exit code to surface. ``EXIT_DENIED`` (3) for a gap that was
            measured; ``EXIT_UNRESOLVED`` (4) for one that could not be
            evaluated at all, which a human must settle rather than a guess.
        message: Explanation, printed verbatim by the gate.
    """

    code: int
    message: str


def project_dir(root: Path, slug: str) -> Path:
    """Return the artifact directory for one project.

    Args:
        root: Repository root.
        slug: Project slug.

    Returns:
        ``<root>/docs/superhuman/<slug>``.
    """
    return root / "docs" / "superhuman" / slug


def git_remote_gap(root: Path) -> Gap | None:
    """Check the git-with-remote precondition.

    An unattended loop keeps work only by committing it, and its rollback story
    is a revert against a remote that outlives the working tree. A local-only
    repo, or none at all, cannot offer that. Enforced before v0.7.0, lost when
    the ladder moved into this resolver, restored here.

    Args:
        root: Project root.

    Returns:
        A :class:`Gap`, or ``None`` when satisfied.
    """
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return Gap(
            EXIT_DENIED,
            f"HITL-M/L require git; {root.as_posix()} is not inside a git "
            "work tree (run `git init` and add a remote, or use HITL-H)",
        )
    if not _git(root, "remote", "get-url", "origin"):
        return Gap(
            EXIT_DENIED,
            f"HITL-M/L require a remote; {root.as_posix()} has no `origin` "
            "(run `git remote add origin <url>`, or use HITL-H)",
        )
    return None


def goal_gap(root: Path, slug: str | None) -> Gap | None:
    """Check the ``GOAL.md`` fitness-function precondition.

    The autonomous loop measures every iteration against ``GOAL.md``; without
    one there is no fitness function, so "keep or roll back" has nothing to
    read. File-first, per ``phases/0-kickoff.md``: a root-level ``GOAL.md``
    satisfies this without a slug.

    Args:
        root: Project root.
        slug: Project slug, or ``None`` when the caller did not name one.

    Returns:
        A :class:`Gap`, or ``None`` when satisfied.
    """
    if (root / "GOAL.md").is_file():
        return None
    if slug is None:
        return Gap(
            EXIT_UNRESOLVED,
            "HITL-M/L require a GOAL.md fitness function, and no --slug was "
            f"given to locate one under {root.as_posix()}/docs/superhuman/ "
            "(pass --slug <project>, or place GOAL.md at the project root)",
        )
    if (project_dir(root, slug) / "GOAL.md").is_file():
        return None
    return Gap(
        EXIT_DENIED,
        f"HITL-M/L require {project_dir(root, slug).as_posix()}/GOAL.md or "
        f"{root.as_posix()}/GOAL.md (templates/artifacts/GOAL.md.tpl) — "
        "neither found",
    )


def rollback_plan_gap(root: Path, slug: str | None) -> Gap | None:
    """Check the low-HITL rollback-plan precondition.

    This is a *project-state* precondition, not ladder policy: a run that edits
    pre-existing code at the lowest human-involvement level must have a written
    revert procedure.

    Scoped to the named project. Before v1.1.0 this took only a root and
    ``rglob``-ed every ``SUPERHUMAN.md`` beneath it, so in a repo with
    concurrent projects it answered about whichever sibling sorted first — and
    when no sibling tripped it, it returned ``None`` having inspected nothing
    about the running project (roadmap #143).

    A missing ``Modifies-existing-code:`` field is a gap, not a pass: the
    absence of a declared fact is not evidence that the fact is false.

    Args:
        root: Repository root.
        slug: Project slug, or ``None`` when the caller did not name one.

    Returns:
        A :class:`Gap`, or ``None`` when satisfied.
    """
    if slug is None:
        return Gap(
            EXIT_UNRESOLVED,
            "HITL-L checks the rollback plan of one project and no --slug was "
            "given; refusing to guess across sibling projects under "
            f"{root.as_posix()}/docs/superhuman/ (pass --slug <project>)",
        )

    slug_dir = project_dir(root, slug)
    manifest = slug_dir / "SUPERHUMAN.md"
    if not manifest.is_file():
        return Gap(
            EXIT_UNRESOLVED,
            f"HITL-L cannot evaluate the rollback precondition: no "
            f"{manifest.as_posix()} for project {slug!r}",
        )

    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Gap(
            EXIT_UNRESOLVED,
            f"HITL-L cannot read {manifest.as_posix()}: {exc}",
        )

    declared: str | None = None
    for line in text.splitlines():
        match = re.match(
            r"^\*\*Modifies-existing-code:\*\*\s*(.*)$", line, re.IGNORECASE
        )
        if match:
            declared = re.sub(r"\s+", "", match.group(1)).lower()
            break

    if not declared:
        return Gap(
            EXIT_DENIED,
            f"HITL-L requires {manifest.as_posix()} to declare "
            "`**Modifies-existing-code:** yes|no`; the field is absent or "
            "empty, and an undeclared field is not a declaration of `no`",
        )
    if declared not in {"yes", "no"}:
        return Gap(
            EXIT_DENIED,
            f"HITL-L cannot read `Modifies-existing-code: {declared}` in "
            f"{manifest.as_posix()} — expected `yes` or `no`",
        )
    if declared == "yes" and not (slug_dir / "ROLLBACK.md").is_file():
        return Gap(
            EXIT_DENIED,
            f"HITL-L requires {slug_dir.as_posix()}/ROLLBACK.md when "
            "Modifies-existing-code: yes "
            "(templates/artifacts/ROLLBACK.md.tpl) — none found",
        )
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

    # Project-state preconditions, checked only for the unattended action class:
    # `promote_into` is a ladder question and has no project state to inspect.
    if args.action == "act_unattended":
        slug = getattr(args, "slug", None)
        checks: list[Gap | None] = [git_remote_gap(res.context.root)]
        if not getattr(args, "kickoff", False):
            checks.append(goal_gap(res.context.root, slug))
            if level == "L":
                checks.append(rollback_plan_gap(res.context.root, slug))

        for gap in checks:
            if gap is None:
                continue
            label = "UNRESOLVED" if gap.code == EXIT_UNRESOLVED else "BLOCKED"
            print(f"superhuman-profile: {label} — {gap.message}", file=sys.stderr)
            return gap.code

    scope = f", project {args.slug}" if getattr(args, "slug", None) else ""
    deferred = " [kickoff: project-state checks deferred]" if getattr(
        args, "kickoff", False
    ) else ""
    print(
        f"superhuman-profile: OK (rung {res.stage.name}, HITL-{level}, "
        f"{args.action}={policy.describe()}{scope}) — "
        f"{res.context.root.as_posix()}{deferred}"
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


def _find_models_span(lines: list[str]) -> tuple[int, int] | None:
    """Find the line-index span of a top-level ``models:`` block in raw YAML text.

    YAML top-level keys sit at column 0 (the authoring convention every
    profile in this repo follows). The span starts at the ``models:`` line
    and extends through every following line that is blank or indented (a
    child of the mapping) — ending at the next column-0, non-blank line, or
    end of file. This lets the caller replace only that span and leave every
    other byte of the file — comments included — untouched.

    A column-0 comment terminates the span exactly like a column-0 key does
    (preflight BLOCKER 2): a comment sitting between ``models:`` and the next
    top-level key documents *that* key, not ``models:``, so treating it as
    part of the mapping being replaced would silently delete it on every
    write. Only an *indented* comment — one that is visually a child of the
    ``models:`` mapping — is still absorbed into the span.

    A column-0 comment does **not** always mean "the next key starts here",
    though (post-review FIX 4): a comment can sit *between two indented tier
    entries*, still inside the block (e.g. a note above ``standard:``). To
    tell the two cases apart, a column-0 comment triggers a lookahead past
    itself (and any further blank lines / column-0 comments) to the next
    substantive line: if that line is indented, the comment (and everything
    skipped to reach it) is a child of the ``models:`` mapping and the scan
    continues; if it is a column-0 key, or the file ends, the comment
    documents whatever comes next and the span stops before it — unchanged
    from the BLOCKER-2 behavior above. Without this lookahead, a comment
    between two indented tiers would truncate the span early, leaving the
    later tier un-replaced and orphaned as a stray duplicate key after the
    splice (roadmap #165 post-review FIX 4).

    Args:
        lines: File content split with ``str.splitlines(keepends=True)``.

    Returns:
        ``(start, end)`` line indices with ``end`` exclusive, or ``None`` if
        no top-level ``models:`` line is present.
    """
    start = None
    for i, line in enumerate(lines):
        if _MODELS_KEY_RE.match(line):
            start = i
            break
    if start is None:
        return None

    end = len(lines)
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue  # blank line — still inside/around the block
        if line[:1] in (" ", "\t"):
            i += 1
            continue  # indented — a child of the models: mapping
        # Column-0, non-blank: either a comment or the next top-level key.
        if line.lstrip().startswith("#"):
            j = i + 1
            while j < len(lines) and (
                lines[j].strip() == ""
                or (lines[j][:1] not in (" ", "\t") and lines[j].lstrip().startswith("#"))
            ):
                j += 1  # skip blank lines and further column-0 comments
            if j < len(lines) and lines[j][:1] in (" ", "\t"):
                i += 1  # more block content follows — the comment is a child
                continue
        end = i  # column-0 key, or a comment with nothing but a key/EOF after it
        break
    return start, end


def _splice_models_block(text: str, rendered_block: str) -> str:
    """Replace or append the top-level ``models:`` block in raw profile text.

    This is the targeted-patch primitive behind :func:`write_models_block`
    (#139 G6 follow-up): every other line — comments, blank lines, ``ladder:``,
    ``version:``, anything else — passes through byte-identical. Only the
    ``models:`` key's own span (per :func:`_find_models_span`) is replaced.
    When no ``models:`` key exists yet, ``rendered_block`` is appended after a
    blank-line separator so it never fuses onto a preceding comment or the
    last line of an existing mapping.

    Args:
        text: The original file content (``""`` for a brand-new file).
        rendered_block: The freshly rendered ``models:\\n  ...`` block text,
            trailing newline included.

    Returns:
        The full document text with only the ``models:`` span touched.
    """
    lines = text.splitlines(keepends=True)
    span = _find_models_span(lines)
    if span is None:
        prefix = text
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        return prefix + rendered_block

    start, end = span
    return "".join(lines[:start]) + rendered_block + "".join(lines[end:])


def _read_profile_text(path: Path) -> str:
    """Read a profile file's text, preserving its original line-ending bytes.

    Per the ``open()`` builtin's newline-translation contract (Python
    stdlib): https://docs.python.org/3/library/functions.html#open —
    ``newline=""`` disables universal-newline translation on read, so
    ``\\r\\n``/``\\r``/``\\n`` line endings come through exactly as written
    rather than all being collapsed to ``\\n``. Pairs with
    :func:`_write_profile_text` so an untouched region of the file round-trips
    byte-for-byte (preflight SHOULD-FIX 4).

    Args:
        path: File to read.

    Returns:
        The file's text, with original line-ending bytes intact.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_profile_text(path: Path, text: str) -> None:
    """Write text to a profile file without line-ending translation.

    See :func:`_read_profile_text`: ``newline=""`` also disables write-side
    translation, so a ``\\n`` already embedded in ``text`` — e.g. inside an
    untouched CRLF region carried through unmodified from the original file
    — is written as-is rather than being expanded to ``os.linesep`` (which
    would silently flip an LF-authored file to CRLF on Windows, or vice
    versa on a CRLF-authored file elsewhere).

    Args:
        path: Destination file.
        text: Text to write, verbatim.
    """
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _semantic_top_level_models_key_count(text: str, profile_path: Path) -> int:
    """Count top-level ``models`` keys the way ``yaml.safe_load`` actually sees them.

    The column-0 ``^models:`` regex (:data:`_MODELS_KEY_RE`) only recognises the
    one canonical spelling this writer's targeted splice can edit. YAML itself
    is far more permissive: a quoted ``"models":`` or ``'models':`` key is a
    perfectly ordinary mapping key that resolves to the same scalar value
    ``"models"`` and therefore collides with a canonical ``models:`` key under
    ``yaml.safe_load``'s last-key-wins duplicate handling — a collision the
    regex-only count cannot see (post-review FIX D, round 3.5).

    This walks the document's parse tree with ``yaml.compose`` (which builds
    nodes without resolving Python objects, so it is cheap and side-effect
    free) rather than re-parsing with regex, and counts key nodes at the root
    mapping whose scalar value is exactly ``"models"`` — catching every
    YAML-equivalent spelling, not just the ones a regex happens to anticipate.

    Args:
        text: The raw profile YAML text.
        profile_path: Source file, used only to build error messages.

    Returns:
        The number of top-level mapping keys that resolve to ``"models"``.
        ``0`` for an empty document or a non-mapping root (both are guarded
        elsewhere; this helper simply has nothing to count in those cases).

    Raises:
        ProfileError: If ``text`` is not parseable YAML at all.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"{profile_path}: invalid YAML — {exc}") from exc
    if root is None or not isinstance(root, yaml.MappingNode):
        return 0
    return sum(
        1
        for key_node, _value_node in root.value
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == "models"
    )


def write_models_block(
    profile_path: Path,
    answers: dict[str, dict[str, str | None]] | None = None,
    *,
    decline: bool | Iterable[str] = False,
) -> Path:
    """Deterministically write/merge the ``models:`` block of a profile.yaml.

    This is C-PROF's writer (FR-9): the elicitation sub-flow (Chunk 6, C-KICK)
    collects per-tier primary/fallback answers and hands them here — config
    generation stays code, never LLM free-text, per dev-principle #5. Creates
    ``profile_path`` (and its parent directories) if absent, and creates the
    ``models:`` section if the file exists but lacks one. Existing top-level
    keys (``ladder``, ``conventions``, …) and tiers not touched by this call
    are preserved — a second call for one tier must not clobber another
    tier's prior answer.

    Every one of :data:`MODEL_TIERS` ends up populated after this call: a
    tier that is neither answered nor explicitly declined, and was not
    already present in the file, still gets :data:`MODEL_PLACEHOLDER` rather
    than being left absent — fail safe, not fail silent (FR-10).

    This is a **targeted patch**, not a full-document re-dump (#139 G6): only
    the ``models:`` key's own text span is replaced (or appended if absent).
    Comments, formatting, line endings, and every other top-level key —
    ``ladder:``, ``citation:``, an operator's own annotations — pass through
    byte-identical. A prior implementation round-tripped the whole file
    through ``yaml.safe_load``/``yaml.safe_dump``, which silently discarded
    every comment in the file, including the ~40 lines of load-bearing
    authoring-rule commentary the shipped presets carry.

    The write itself is validate-then-swap, never write-then-validate
    (preflight BLOCKER 1): the patched text is written to a uniquely-named
    temp sibling file, that temp file is loaded through :func:`load_profile`
    to confirm it is valid, and only then is it atomically renamed over
    ``profile_path``. If anything fails — the write, the validation, or the
    rename — the temp file is discarded and the original, if one existed, is
    left completely untouched; a previously-valid ``profile.yaml`` is never
    at risk of being overwritten by a broken write, and no stray ``.tmp``
    sibling is left behind on any failure path.

    Args:
        profile_path: Destination ``profile.yaml``.
        answers: Tier -> ``{"primary": <alias>, "fallback": <alias> | None}``
            for tiers the operator answered. ``None`` (the default) answers
            none.
        decline: ``True`` to decline every tier in :data:`MODEL_TIERS`, or an
            iterable of tier names to decline individually. A declined tier
            is written with :data:`MODEL_PLACEHOLDER` for both ``primary`` and
            ``fallback`` — never a concrete vendor/model name (FR-10, the
            provider-agnostic immutable constraint).

    Returns:
        ``profile_path``, for chaining.

    Raises:
        ProfileError: If ``answers``/``decline`` name a tier outside
            :data:`MODEL_TIERS`; if ``profile_path`` exists but is not valid
            YAML, its top level is not a mapping, its existing ``models:``
            value is not itself a mapping, or the file declares more than one
            top-level ``models:`` key; or if the patched result fails to load
            back. In every failure case the original file (if any) is left
            byte-untouched — see "The write itself" above.
    """
    answers = answers or {}
    declined = set(MODEL_TIERS) if decline is True else (set() if decline is False else set(decline))

    unknown = (set(answers) | declined) - set(MODEL_TIERS)
    if unknown:
        raise ProfileError(f"models: unknown tier(s) {sorted(unknown)}")

    if profile_path.is_file():
        text = _read_profile_text(profile_path)
        try:
            existing: Any = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise ProfileError(f"{profile_path}: invalid YAML — {exc}") from exc
        if not isinstance(existing, dict):
            raise ProfileError(f"{profile_path}: top level must be a mapping")
    else:
        text = ""
        existing = {}

    # Post-review FIX 3 / FIX C (round 3), unified into one semantic check
    # (round 3.5): a regex-only count of the canonical `^models:` spelling
    # cannot see a YAML-equivalent duplicate — e.g. one canonical `models:`
    # plus one quoted `"models":` — because `yaml.safe_load` resolves both to
    # the SAME key (`"models"`) and keeps only the last, while the regex count
    # stays at 1 and the old guard passed. The writer would then patch the
    # canonical (first, now-shadowed) block and leave the quoted duplicate
    # untouched, silently producing a profile whose `models:` the loader and
    # the file on disk disagree about.
    #
    # `model_key_count` (the canonical, EDITABLE span count) and
    # `semantic_count` (every top-level key that YAML resolves to `"models"`,
    # any spelling) must both be exactly 1, or both be 0 (no `models:` key at
    # all — nothing to guard). Any other combination — two canonical keys, a
    # canonical key shadowed by a quoted one, or a quoted-only key with no
    # canonical span to splice into — means the targeted patch below cannot
    # safely edit this file, so fail loud rather than guess.
    #
    # `raw_models` is computed FIRST and the guard is gated on it (round 3.6
    # FIX Y) because `yaml.safe_load` can populate a top-level `models` key
    # WITHOUT any `models` scalar node existing at the document root at
    # all — a top-level YAML merge key (`<<: *anchor`) merges a `models:`
    # mapping from the anchored document into the root. `yaml.compose` (used
    # by `_semantic_top_level_models_key_count`) does not resolve merge keys,
    # so it sees no `models` key node and reports `semantic_count == 0`; the
    # regex-only `model_key_count` also stays 0. The old guard treated
    # `(0, 0)` as always safe and let the splice below APPEND a second
    # `models:` block underneath the merge-derived one, which `yaml.safe_load`
    # would then silently prefer over the merge (last-key-wins) — a second,
    # invisible-to-the-regex `models:` block, not a fail-loud error. Gating on
    # `raw_models` closes that: whenever the loader actually sees a `models`
    # value (merge key or otherwise), the canonical/semantic span count must
    # be exactly `(1, 1)` or this is not a file the targeted patch can safely
    # edit.
    raw_models = existing.get("models")
    if raw_models is not None and not isinstance(raw_models, dict):
        raise ProfileError(f"{profile_path}: 'models:' must be a mapping")

    model_key_count = sum(1 for line in text.splitlines(keepends=True) if _MODELS_KEY_RE.match(line))
    semantic_count = _semantic_top_level_models_key_count(text, profile_path)
    required = (1, 1) if raw_models is not None else (0, 0)
    if (semantic_count, model_key_count) != required:
        raise ProfileError(
            f"{profile_path}: expected exactly one top-level 'models:' key, found "
            f"{semantic_count} that YAML resolves to 'models' (any spelling) and "
            f"{model_key_count} in the canonical, unquoted 'models:' spelling this writer's "
            "targeted patch can edit — remove the duplicate, or rewrite the key as a plain, "
            "unquoted 'models:', before writing"
        )

    models_block: dict[str, Any] = dict(raw_models or {})
    for tier in MODEL_TIERS:
        if tier in declined:
            models_block[tier] = {"primary": MODEL_PLACEHOLDER, "fallback": MODEL_PLACEHOLDER}
        elif tier in answers:
            entry = answers[tier]
            models_block[tier] = {
                "primary": entry.get("primary") or MODEL_PLACEHOLDER,
                "fallback": entry.get("fallback"),
            }
        elif tier not in models_block:
            models_block[tier] = {"primary": MODEL_PLACEHOLDER, "fallback": MODEL_PLACEHOLDER}

    # yaml.safe_dump(data, default_flow_style=False, sort_keys=False):
    # https://pyyaml.org/wiki/PyYAMLDocumentation (PyYAML 6.0.3, installed version)
    # — block style, insertion order preserved, no Python-specific tags. Only
    # the small {"models": ...} sub-document is dumped; it is spliced into the
    # original text rather than replacing it, so nothing else gets re-dumped.
    rendered = yaml.safe_dump(
        {"models": models_block}, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    new_text = _splice_models_block(text, rendered)
    if "version" not in existing:
        new_text = f"version: {SCHEMA_VERSION}\n\n" + new_text

    # Post-review FIX 2: if `profile_path` is itself a symlink (a common setup
    # for sharing one real profile.yaml across worktrees/checkouts),
    # `os.replace(tmp, profile_path)` would replace the symlink's directory
    # entry with a plain file — destroying the link and leaving the real,
    # shared target stale and un-updated. Resolve through the symlink first
    # and write/replace against the RESOLVED target instead, so the symlink
    # itself is left completely untouched and the shared file it points at is
    # the one that actually changes. A non-symlink path resolves to itself,
    # so this is a no-op for the common case.
    target_path = profile_path.resolve() if profile_path.is_symlink() else profile_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Validate-then-swap (preflight BLOCKER 1): write to a temp sibling in the
    # same directory, validate THAT file, and only on success replace the
    # real target — atomically, since os.replace() is a same-filesystem
    # rename. A failure anywhere in the write/validate/replace never touches
    # profile_path, so a previously-valid file can never be left broken. The
    # temp sibling lives beside `target_path` (not `profile_path`) so the
    # rename in the symlink case stays a same-filesystem, atomic swap of the
    # resolved target rather than a cross-filesystem copy.
    #
    # tempfile.mkstemp gives a process/thread-unique sibling name so two
    # concurrent invocations cannot collide on a shared ".tmp" file, and the
    # cleanup is in a `finally` keyed on whether the replace succeeded: the
    # temp is removed on EVERY failure path, not only ProfileError. A
    # non-ProfileError raised by the write (e.g. OSError) or by validation
    # would otherwise leak a stray ".tmp" beside the profile (preflight
    # correctness residual). On success the temp no longer exists — it has
    # been renamed over target_path — so `finally` leaves it alone.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target_path.parent), prefix=f".{target_path.name}.", suffix=".tmp"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    replaced = False
    try:
        _write_profile_text(tmp, new_text)
        load_profile(tmp)
        os.replace(tmp, target_path)
        replaced = True
    finally:
        if not replaced:
            tmp.unlink(missing_ok=True)
    return profile_path


def cmd_models_set(args: argparse.Namespace) -> int:
    """Write/merge the ``models:`` block of a profile from CLI-supplied JSON.

    This is the CLI seam for :func:`write_models_block` (post-review FIX 1):
    before this subcommand existed, the writer was a bare Python function with
    no way to invoke it from a dispatched shell command, so
    ``phases/0-kickoff.md`` Step 3 could describe calling it but nothing could
    actually do so. Config generation stays code (dev-principle #5); this is
    that code's process boundary.

    Args:
        args: Parsed CLI arguments. ``--answers-json`` is a JSON object
            mapping tier name -> ``{"primary": <alias>, "fallback": <alias>}``
            (``fallback`` may be omitted or ``null``), given inline on the
            command line. ``--answers-json-file`` is the same JSON object,
            but read from a file (or from stdin, when the path is ``-``)
            instead of being interpolated into a shell word — the
            injection-proof path ``phases/0-kickoff.md`` Step 3 uses, since
            an operator alias or elicited answer may itself contain a quote,
            ``$``, or backtick (dev-principle #5: no LLM-marshalled data into
            a shell word). Exactly one of ``--answers-json`` /
            ``--answers-json-file`` may be given unless ``--decline`` alone
            supplies everything there is to do. ``--decline`` is an optional
            comma-separated list of tier names to write as
            :data:`MODEL_PLACEHOLDER`; ``--profile`` defaults to
            ``~/.superhuman/profile.yaml``, the same default destination
            :func:`cmd_init` uses for the operator's profile.

    Returns:
        Process exit code (0 on success).

    Raises:
        ProfileError: If both ``--answers-json`` and ``--answers-json-file``
            are given; if neither is given and ``--decline`` is also empty
            (nothing for the command to do); if ``--answers-json-file``
            names a file that cannot be read; if the resulting JSON is not
            valid, is not a JSON object, or its value for a tier is not an
            object; or if :func:`write_models_block` rejects the tier names
            or the existing profile. Caught by :func:`main`, which prints
            the message and exits 2 — never a raw traceback.
    """
    dest = Path(args.profile) if args.profile else (Path.home() / ".superhuman" / "profile.yaml")
    decline = {tok.strip() for tok in (args.decline or "").split(",") if tok.strip()}

    if args.answers_json is not None and args.answers_json_file is not None:
        raise ProfileError(
            "--answers-json and --answers-json-file are mutually exclusive — pass only one"
        )
    if args.answers_json_file is not None:
        if args.answers_json_file == "-":
            raw_json = sys.stdin.read()
        else:
            try:
                raw_json = Path(args.answers_json_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise ProfileError(f"--answers-json-file: {exc}") from exc
    elif args.answers_json is not None:
        raw_json = args.answers_json
    elif decline:
        raw_json = "{}"
    else:
        raise ProfileError(
            "models set: nothing to do — pass --answers-json, --answers-json-file, or --decline"
        )

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"--answers-json: invalid JSON — {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProfileError("--answers-json: must be a JSON object of tier -> {primary, fallback}")

    answers: dict[str, dict[str, str | None]] = {}
    for tier, entry in parsed.items():
        if not isinstance(entry, dict):
            raise ProfileError(f"--answers-json.{tier}: must be an object with 'primary'/'fallback'")
        answers[tier] = {"primary": entry.get("primary"), "fallback": entry.get("fallback")}

    # write_models_block validates tier names against MODEL_TIERS and raises
    # ProfileError for anything unknown — no need to duplicate that check here.
    write_models_block(dest, answers, decline=decline)

    profile = load_profile(dest)
    print(f"superhuman-profile: wrote {dest.as_posix()}")
    for tier in MODEL_TIERS:
        entry = profile.models.get(tier, {})
        primary = entry.get("primary")
        if primary == MODEL_PLACEHOLDER or primary is None:
            print(f"  {tier:<14} {MODEL_PLACEHOLDER}")
        else:
            fallback = entry.get("fallback")
            suffix = f" fallback={fallback}" if fallback else ""
            print(f"  {tier:<14} primary={primary}{suffix}")
    return EXIT_OK


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
    check.add_argument(
        "--slug",
        "--project",
        dest="slug",
        default=None,
        metavar="SLUG",
        help=(
            "project slug under docs/superhuman/ whose state the project "
            "preconditions apply to. Without it they cannot be scoped and the "
            "gate exits 4 rather than guessing across sibling projects."
        ),
    )
    check.add_argument(
        "--kickoff",
        action="store_true",
        help=(
            "the project's own state is still being written (phases/0-kickoff.md "
            "Step 3): check the ladder and git+remote, and defer GOAL.md and the "
            "rollback plan to the re-run at the end of kickoff. Never pass this "
            "to authorize a loop."
        ),
    )
    check.set_defaults(func=cmd_check)

    models = subs.add_parser("models", help="manage the models: block of a profile")
    models_subs = models.add_subparsers(dest="models_cmd", required=True)
    models_set = models_subs.add_parser(
        "set", help="write/merge the models: block from elicited per-tier answers"
    )
    models_set.add_argument(
        "--profile",
        default=None,
        help="destination profile.yaml (default: ~/.superhuman/profile.yaml, same as `init`)",
    )
    models_set.add_argument(
        "--answers-json",
        default=None,
        help=(
            'JSON object: {"<tier>": {"primary": "...", "fallback": "..."}, ...} — given '
            "inline. Prefer --answers-json-file when any value is not a fixed literal "
            "(operator aliases, elicited answers): inline JSON is interpolated into a shell "
            "word and an alias containing a quote, $, or backtick can break the quoting or "
            "inject shell."
        ),
    )
    models_set.add_argument(
        "--answers-json-file",
        default=None,
        help=(
            "same JSON object as --answers-json, read from PATH instead (PATH may be '-' to "
            "read from stdin). Mutually exclusive with --answers-json; this is the "
            "injection-proof form phases/0-kickoff.md Step 3 uses."
        ),
    )
    models_set.add_argument(
        "--decline",
        default=None,
        help="comma-separated tier names to decline (written as the neutral placeholder)",
    )
    models_set.set_defaults(func=cmd_models_set)

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
