"""The workspace -> project locator (D1, OQ-2, FR-1..FR-4, NFR-4).

This module answers one question: given a session's `cwd`, which superhuman
project — as a `(workspace, slug)` **pair**, not a bare slug — is that
session working in? A slug alone is the wrong return type (see DESIGN.md's
"Approach summary"): a resolver that only named a slug could hand the right
slug to the wrong workspace, exactly the `gate-record-integrity` failure this
project exists to close.

**Root discovery (normative, D1 revised at G6).** From `cwd`, up to three
`git rev-parse` steps produce a *candidate root*; enumeration and the
disambiguation ladder then run against it. The first step whose root yields
at least one candidate is the last one attempted:

- **H0 — own root.** `git rev-parse --show-toplevel` from `cwd`. Tried
  always. If `cwd` is not inside a git repository at all, this fails and the
  locator refuses immediately (CHUNK-1-FINDINGS #4) — there is no root to
  hop from.
- **H0' — main-worktree sidestep.** `git rev-parse --git-common-dir`; when
  it names a `.git` outside H0's root, that directory's parent is the
  repository's *main* working tree. This is not an ancestry hop — it names
  the same repository's main working tree, an identity relation git itself
  asserts — so it keeps the **full** L1-L4 ladder. Tried only when H0 yields
  zero candidates.
- **H1 — one bounded outward hop.** `git rev-parse --show-toplevel` executed
  in H0's root's *parent* directory, gated by four guards (`MAX_OUTWARD_HOPS`,
  no self-loop, a `Path.home()` ceiling, and the module constant below).
  Because this genuinely crosses a repository boundary, **only rung L1**
  (cwd-containment) may fire here — enforced by an explicit `allowed_rungs`
  argument to the ladder, never by comment or ordering. Tried only when H0
  and H0' both yield zero candidates.
- Otherwise: refuse. Return `None`, with a reason.

**The disambiguation ladder.** A *candidate* is a directory
`<root>/docs/superhuman/<dir>/` containing a readable `SUPERHUMAN.md` whose
`**Slug:**` line equals `<dir>` exactly (D1 ruling R-a: the shallower
`<root>/<slug>/SUPERHUMAN.md` is never a candidate). A record without a
`**Project-id:**` line is still a candidate (D1 ruling 3) — candidacy never
depends on it, so the Chunk 5 backfill cannot change what this module
resolves. An unparseable record is excluded from candidacy, never fatal to
the rest of the enumeration (D1 ruling 2). Four rungs are tried in order;
each is a **uniqueness test over the whole candidate set** — it fires only
when it matches exactly one candidate, never on a first match:

- **L1 cwd-containment** — `cwd` is at or under a candidate's directory.
- **L2 declared** — the profile resolved from `cwd` (`superhuman_profile.
  find_profile`) sets `fleet.slug` to a candidate's slug.
- **L3 branch** — `cwd`'s current git branch equals a candidate's slug.
- **L4 singleton** — exactly one candidate exists.

If no permitted rung produces a unique answer, the locator refuses.

**NFR-4 confinement — the invariant this module's provenance depends on.**
Every `workspace` this module returns is the direct output of a
`git rev-parse --path-format=absolute ...` call (never joined, formatted, or
combined with any other value) — H0's, H0''s, or H1's. `--path-format=
absolute` (git >= 2.31) is used throughout specifically so `git`, not this
module, is the thing that turns a relative plumbing answer into an absolute
path: the only path-shaped operation this module performs on a value that
ends up in a returned `workspace` is `Path(...).parent` on git's own
already-absolute `--git-common-dir` output (H0'), which *narrows* a
git-derived value, never extends one. A profile's `fleet.slug` (L2), an
environment variable, or a branch name (L3) may only ever *select among*
directory names already enumerated under an accepted root — never
contribute a path segment to a returned `workspace`. Path construction
(building `<root>/docs/superhuman/<dir>/SUPERHUMAN.md` candidate paths)
happens only in `_enumerate_candidates`, which is read-only and builds paths
strictly *under* an already-accepted chain member; it never contributes to a
returned `workspace` value either.

`MAX_OUTWARD_HOPS` is a named module constant precisely so raising it is a
one-line, reviewable change justified by a newly measured layout — never a
guess (see DESIGN.md D1-R1's "under-reaching is safe; over-reaching is not").
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The rung permitted set at hop 0 (H0 and H0') — the full ladder.
_FULL_LADDER: tuple[str, ...] = ("L1", "L2", "L3", "L4")

#: The rung permitted set at hop >= 1 (H1) — cwd-containment only (D1-R1).
#: An outward root wins only when the payload `cwd` is positionally inside
#: one of its candidate directories, a fact on disk rather than an
#: inference — see this module's docstring.
_H1_ALLOWED_RUNGS: tuple[str, ...] = ("L1",)

#: Bounded outward-hop ceiling (D1-R1, NFR-4 bounds). Exactly one hop is
#: what the measured nested-clone layout requires (delta-report-001); a
#: too-small value is merely silent (the sanctioned failure mode, FR-2), a
#: too-large one widens the confinement radius — see the module docstring.
MAX_OUTWARD_HOPS = 1

#: Per-subprocess timeout for this module's own git calls (NFR-2). Matches
#: `config.py`'s `_DEFAULT_GIT_TIMEOUT_SECONDS` precedent — the observation
#: façade's overall wall-clock budget has no room for a hung git process,
#: and root discovery may issue up to four calls (H0, H0', H1, L3's branch
#: call) in the worst case.
_GIT_TIMEOUT_SECONDS = 0.25

#: Matches a `**Slug:** <value>` front-matter line, the same bold-key-colon
#: convention `project.py`'s `_SLUG_RE` uses (not imported — this module
#: stays self-contained so a G5 reviewer can audit it in isolation; see the
#: module docstring's provenance claim).
_SLUG_LINE_RE = re.compile(r"^\*\*Slug:\*\*\s*(\S+)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class LocateResult:
    """A resolved `(workspace, slug)` project location.

    Attributes:
        workspace: the resolved project's working tree root. Always a value
            returned by a `git rev-parse` call (NFR-4 provenance) — see the
            module docstring.
        slug: the resolved candidate's directory name under
            `<workspace>/docs/superhuman/`, exactly as read from its
            `SUPERHUMAN.md` (and, by the candidate definition, identical to
            the directory name).
        rung: which ladder rung produced the answer — `"L1"`, `"L2"`,
            `"L3"`, or `"L4"`.
        hop: which root-discovery step found `workspace` — `"H0"` (own
            root), `"H0'"` (main-worktree sidestep), or `"H1"` (one bounded
            outward hop).
    """

    workspace: Path
    slug: str
    rung: str
    hop: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One well-formed candidate found during enumeration.

    Attributes:
        slug: the candidate's slug (equals `directory.name`).
        directory: `<root>/docs/superhuman/<slug>/`.
    """

    slug: str
    directory: Path


def _run_git(cwd: Path, args: list[str], *, timeout: float = _GIT_TIMEOUT_SECONDS) -> str | None:
    """Run one git plumbing command, returning stripped stdout or `None`.

    Never raises: a missing `git` executable, non-zero exit, or timeout all
    return `None` — root discovery treats "not a repository" (or any other
    git failure) as an ordinary refusal (CHUNK-1-FINDINGS #4), never an
    exception. Mirrors `adapter/portable.py`'s `run_git` (deliberately not
    imported — see the module docstring's isolation rationale).

    Args:
        cwd: directory to run the command in.
        args: git arguments, excluding the `git` executable itself.
        timeout: seconds to allow the subprocess before killing it.

    Returns:
        str | None: stripped stdout on success; `None` on any failure.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _show_toplevel(cwd: Path) -> Path | None:
    """Return `git rev-parse --path-format=absolute --show-toplevel` from `cwd`.

    `--path-format=absolute` (git >= 2.31) makes git itself the sole source
    of the absolute path — this module never joins a relative plumbing
    answer onto a directory to build a workspace value (NFR-4 provenance).

    Args:
        cwd: directory to run the command in.

    Returns:
        Path | None: the absolute toplevel, or `None` if `cwd` is not
        inside a git working tree (or git failed for any other reason).
    """
    raw = _run_git(cwd, ["rev-parse", "--path-format=absolute", "--show-toplevel"])
    return Path(raw) if raw else None


def _git_common_dir_parent(cwd: Path) -> Path | None:
    """Return the parent of `cwd`'s repository's main-worktree `.git`.

    `--git-common-dir` names the *same repository's* main working tree — an
    identity relation git itself asserts, never an ancestry move (D1's "why
    H0' is not a hop"). `--path-format=absolute` means the only operation
    this function performs on the value is `.parent`, which *narrows* an
    already git-derived absolute path rather than extending it.

    Args:
        cwd: directory to run the command in.

    Returns:
        Path | None: the main working tree's root, or `None` if `cwd` is
        not inside a git working tree.
    """
    raw = _run_git(cwd, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if not raw:
        return None
    return Path(raw).parent


def _current_branch(cwd: Path) -> str | None:
    """Return `cwd`'s current git branch, or `None` (detached HEAD / not a repo).

    Args:
        cwd: directory to run the command in.

    Returns:
        str | None: the branch name, or `None` if there isn't one to report.
    """
    return _run_git(cwd, ["branch", "--show-current"]) or None


def _read_slug(record: Path) -> str | None:
    """Read a `SUPERHUMAN.md`'s `**Slug:**` value, or `None` if unreadable/absent.

    Never raises: an unreadable file, a non-UTF-8 file, or a missing/blank
    `**Slug:**` line all resolve to `None` — an unparseable record is
    excluded from candidacy, never fatal to the rest of enumeration (D1
    ruling 2).

    Args:
        record: path to the candidate's `SUPERHUMAN.md`.

    Returns:
        str | None: the slug exactly as written, or `None`.
    """
    try:
        text = record.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # `ValueError` covers `UnicodeDecodeError` on a non-UTF-8 file.
        return None
    match = _SLUG_LINE_RE.search(text)
    if match is None:
        return None
    slug = match.group(1).strip()
    return slug or None


def _enumerate_candidates(root: Path) -> list[_Candidate]:
    """Enumerate well-formed candidates under `<root>/docs/superhuman/*/`.

    A candidate is a directory `<root>/docs/superhuman/<dir>/` containing a
    readable `SUPERHUMAN.md` whose `**Slug:**` line equals `<dir>` exactly
    (D1 ruling R-a — the shallower `<root>/<slug>/SUPERHUMAN.md` is never a
    candidate). Walks the filesystem, not the git index, so a git-ignored
    mount point is still visible (FR-3). Read-only: the only place in this
    module that builds a path from more than one segment, and every path it
    builds is strictly *under* `root`, which is itself already a member of
    the git-derived chain (NFR-4 provenance).

    Args:
        root: a git-derived root to enumerate under.

    Returns:
        list[_Candidate]: every well-formed candidate found, sorted by slug
        for deterministic reason strings. A present-but-malformed record
        (missing/mismatched `**Slug:**`) is silently excluded, never fatal
        to the others (D1 ruling 2).
    """
    base = root / "docs" / "superhuman"
    try:
        if not base.is_dir():
            return []
        entries = sorted(base.iterdir())
    except OSError:
        return []

    candidates: list[_Candidate] = []
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        record = entry / "SUPERHUMAN.md"
        if not record.is_file():
            continue
        slug = _read_slug(record)
        if slug is None or slug != entry.name:
            continue
        candidates.append(_Candidate(slug=slug, directory=entry))
    return sorted(candidates, key=lambda c: c.slug)


def _cwd_is_contained(cwd: Path, candidate_dir: Path) -> bool:
    """Return whether `cwd` is at or under `candidate_dir` (L1's test).

    Args:
        cwd: the session's working directory.
        candidate_dir: a candidate's `docs/superhuman/<slug>/` directory.

    Returns:
        bool: `True` if `cwd` is `candidate_dir` or a descendant of it.
    """
    try:
        return cwd.is_relative_to(candidate_dir)
    except ValueError:  # pragma: no cover - is_relative_to does not raise this
        return False


def _is_safe_slug_selector(value: str) -> bool:
    """Return whether `value` is shaped like a plain slug, not a path.

    Defense-in-depth documenting an invariant that already holds
    structurally: L2/L3 only ever compare `value` for equality against a
    candidate's `slug` (a real, already-enumerated directory name), so a
    path-shaped value could never match a real candidate anyway — it can
    only ever *select among* candidates, never build a path (NFR-4;
    mirrors `observe._validate_slug`'s discipline, not imported here to
    avoid a dependency cycle with the future `observe.py` caller of this
    module).

    Args:
        value: the candidate slug value read from a profile or branch name.

    Returns:
        bool: `False` if `value` is blank or contains a path separator or a
        `..` segment.
    """
    return bool(value) and "/" not in value and "\\" not in value and ".." not in value


def _declared_profile_slug(cwd: Path) -> str | None:
    """Read `fleet.slug` from the profile resolved for `cwd` (L2's source).

    Mirrors `config.py`'s own precedent: the profile is located via
    `superhuman_profile.find_profile` but read with `yaml.safe_load`
    directly rather than through `load_profile`, which validates only the
    deployment-ladder schema and would reject the unrecognized `fleet` key.
    Never raises: every failure to locate, read, or parse the profile (or a
    `fleet.slug` that is not a plain, path-safe string) resolves to `None`.

    Args:
        cwd: the session's working directory — `find_profile`'s own bounded
            upward walk (ceiling: the enclosing git root or `Path.home()`)
            starts here, so this reports whatever profile is actually in
            effect for wherever the session is, not for whichever candidate
            root the ladder happens to be testing.

    Returns:
        str | None: the declared slug, or `None`.
    """
    try:
        from ..superhuman_profile import find_profile
    except ImportError:  # pragma: no cover - environment problem, not logic
        return None

    path = find_profile(cwd)
    if path is None or not path.is_file():
        return None

    try:
        import yaml
    except ImportError:  # pragma: no cover - environment problem, not logic
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        # `ValueError` covers `UnicodeDecodeError` on a non-UTF-8 file.
        return None

    if not isinstance(raw, dict):
        return None
    fleet_cfg = raw.get("fleet")
    if not isinstance(fleet_cfg, dict):
        return None
    slug = fleet_cfg.get("slug")
    if not isinstance(slug, str) or not _is_safe_slug_selector(slug):
        return None
    return slug


def _rung_matches(
    rung: str, candidates: list[_Candidate], *, cwd: Path
) -> list[_Candidate]:
    """Return the candidates one ladder rung matches.

    Each rung is a uniqueness test over the whole candidate set, never a
    first-match — the caller decides "fired" by checking `len(...) == 1`.

    Args:
        rung: which rung to evaluate — `"L1"`, `"L2"`, `"L3"`, or `"L4"`.
        candidates: the full candidate set for the root under test.
        cwd: the session's working directory — the source for L1
            (containment), L2 (profile resolution), and L3 (branch), always
            the *session's own* location regardless of which root (H0, H0',
            or H1) is under test (see `locate_project_explain`'s handling of
            the linked-worktree case, TC-2).

    Returns:
        list[_Candidate]: every candidate the rung matches (0, 1, or many).
    """
    if rung == "L1":
        return [c for c in candidates if _cwd_is_contained(cwd, c.directory)]
    if rung == "L2":
        slug = _declared_profile_slug(cwd)
        if slug is None:
            return []
        return [c for c in candidates if c.slug == slug]
    if rung == "L3":
        branch = _current_branch(cwd)
        if not branch or not _is_safe_slug_selector(branch):
            return []
        return [c for c in candidates if c.slug == branch]
    if rung == "L4":
        return list(candidates)
    raise AssertionError(f"unknown rung {rung!r}")  # pragma: no cover - exhaustive above


def _select_candidate(
    candidates: list[_Candidate], *, cwd: Path, allowed_rungs: tuple[str, ...]
) -> tuple[_Candidate, str] | None:
    """Run the disambiguation ladder over `candidates`.

    Args:
        candidates: the full candidate set for the root under test.
        cwd: the session's working directory (see `_rung_matches`).
        allowed_rungs: which rungs may fire, in ladder order — `_FULL_LADDER`
            at H0/H0', `_H1_ALLOWED_RUNGS` at H1 (NFR-4 rung scope, enforced
            here as an explicit argument, never by comment or ordering).

    Returns:
        tuple[_Candidate, str] | None: the uniquely matched candidate and
        the rung that matched it, or `None` if no permitted rung produced a
        unique answer.
    """
    for rung in _FULL_LADDER:
        if rung not in allowed_rungs:
            continue
        matches = _rung_matches(rung, candidates, cwd=cwd)
        if len(matches) == 1:
            return matches[0], rung
    return None


def _ambiguous_reason(candidates: list[_Candidate], *, root: Path, hop: str) -> str:
    """Build a reason string naming every candidate considered (FR-2).

    Args:
        candidates: the full candidate set that no rung could narrow.
        root: the root the candidates were enumerated under.
        hop: which root-discovery step this root came from.

    Returns:
        str: a reason naming the root, the hop, and every candidate slug.
    """
    names = ", ".join(c.slug for c in candidates)
    return (
        f"{len(candidates)} candidate(s) under {root} (hop={hop}) but no "
        f"permitted rung matched a unique one; candidates considered: {names}"
    )


def _resolve_at_root(
    candidates: list[_Candidate], *, cwd: Path, root: Path, hop: str, allowed_rungs: tuple[str, ...]
) -> tuple[LocateResult | None, str | None]:
    """Run the ladder for one root-discovery step and build its outcome.

    Args:
        candidates: the candidates enumerated under `root`.
        cwd: the session's working directory.
        root: the git-derived root under test.
        hop: which root-discovery step this is (`"H0"`, `"H0'"`, `"H1"`).
        allowed_rungs: the rung set permitted at this hop.

    Returns:
        tuple[LocateResult | None, str | None]: `(result, None)` if a rung
        fired; `(None, reason)` if candidates existed but none was unique;
        `(None, None)` if `candidates` was empty (the caller should keep
        walking the root-discovery chain rather than stop here).
    """
    if not candidates:
        return None, None
    match = _select_candidate(candidates, cwd=cwd, allowed_rungs=allowed_rungs)
    if match is None:
        return None, _ambiguous_reason(candidates, root=root, hop=hop)
    candidate, rung = match
    return LocateResult(workspace=root, slug=candidate.slug, rung=rung, hop=hop), None


def locate_project_explain(cwd: Path | str) -> tuple[LocateResult | None, str]:
    """Resolve `cwd` to a project, and explain how or why not.

    This is the module's real entry point; `locate_project` is a thin
    convenience wrapper over it for callers that only need the result.
    Never raises (this feeds a fail-soft façade) — every failure mode below
    resolves to `(None, <reason>)` rather than an exception.

    Args:
        cwd: the session's working directory to resolve from (a hook
            payload's `cwd` field, or any directory a caller wants to
            resolve).

    Returns:
        tuple[LocateResult | None, str]: `(result, reason)`. `reason`
        explains which rung/hop produced `result` on success, or names
        every candidate considered (or the guard that stopped the walk) on
        refusal (FR-2).
    """
    cwd_path = Path(cwd)

    h0_root = _show_toplevel(cwd_path)
    if h0_root is None:
        return None, f"{cwd_path} is not inside a git repository"

    candidates = _enumerate_candidates(h0_root)
    result, reason = _resolve_at_root(
        candidates, cwd=cwd_path, root=h0_root, hop="H0", allowed_rungs=_FULL_LADDER
    )
    if result is not None:
        return result, f"resolved via {result.rung} at H0 ({h0_root})"
    if reason is not None:
        return None, reason

    # H0' — main-worktree sidestep. Not an ancestry hop (see module
    # docstring), so the full ladder stays available. Attempted only
    # because H0 yielded zero candidates.
    main_root = _git_common_dir_parent(h0_root)
    if main_root is not None and main_root != h0_root:
        candidates = _enumerate_candidates(main_root)
        result, reason = _resolve_at_root(
            candidates, cwd=cwd_path, root=main_root, hop="H0'", allowed_rungs=_FULL_LADDER
        )
        if result is not None:
            return result, f"resolved via {result.rung} at H0' ({main_root})"
        if reason is not None:
            return None, reason

    # H1 — one bounded outward hop, L1-only (D1-R1). Attempted only because
    # both H0 and H0' yielded zero candidates. Guard 1 (hops taken so far <
    # MAX_OUTWARD_HOPS): this is the only outward hop this function ever
    # attempts, so it is written as a single guarded step rather than a
    # loop — raising MAX_OUTWARD_HOPS to permit a genuine second hop is a
    # deliberate, reviewable change to this function, not a constant bump
    # alone (see the module docstring).
    _hops_taken_before_h1 = 0
    if _hops_taken_before_h1 < MAX_OUTWARD_HOPS:
        h1_root = _show_toplevel(h0_root.parent)
        if h1_root is not None:
            home = Path.home()
            # Guard 2: no self-loop.
            if h1_root == h0_root:
                return None, f"outward hop from {h0_root} looped back to itself — refusing"
            # Guard 3: strictly below Path.home(), never at or above it.
            elif h1_root == home or home not in h1_root.parents:
                return None, (
                    f"outward hop from {h0_root} would reach {h1_root}, which is at or "
                    "above the home directory ceiling — refusing"
                )
            else:
                candidates = _enumerate_candidates(h1_root)
                # Guard 4: only L1 is permitted at this hop (_H1_ALLOWED_RUNGS).
                result, reason = _resolve_at_root(
                    candidates,
                    cwd=cwd_path,
                    root=h1_root,
                    hop="H1",
                    allowed_rungs=_H1_ALLOWED_RUNGS,
                )
                if result is not None:
                    return result, f"resolved via {result.rung} at H1 ({h1_root})"
                if reason is not None:
                    return None, reason

    return None, (
        f"no candidates found within the bounded root-discovery chain from {cwd_path} "
        f"(H0={h0_root})"
    )


def locate_project(cwd: Path | str) -> LocateResult | None:
    """Resolve `cwd` to a unique superhuman project `(workspace, slug)`.

    Never raises. See the module docstring for the full root-discovery
    sequence and disambiguation ladder this implements.

    Args:
        cwd: the session's working directory to resolve from.

    Returns:
        LocateResult | None: the unique match, or `None` if resolution
        refused (FR-2). Call `locate_project_explain` for the human-readable
        reason behind the outcome.
    """
    result, _reason = locate_project_explain(cwd)
    return result
