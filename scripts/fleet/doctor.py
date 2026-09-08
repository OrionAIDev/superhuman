"""Read-only estate-health scanner — the FR-10 acceptance instrument and
FR-13 surface (D6).

`fleet doctor` answers, for one or more scan roots, "for every project
record found underneath, would a hook be able to write to it right now, and
if not, why?" It is a DOWN enumeration (walks every `docs/superhuman/<dir>/`
child of each given root), unlike `locate.py`'s UP-then-ladder resolution
from a single `cwd`. Read-only throughout: this module never writes a
manifest row, never edits a `SUPERHUMAN.md`, never mints an id (D3's
`project_id.py` owns minting) — it only reports.

Every project record found lands in exactly one of four states, checked in
this order:

- **unresolvable** — the directory carries a `SUPERHUMAN.md` but
  `locate.locate_project`, run from *inside* the record's own directory,
  cannot uniquely resolve it: a malformed record (D1 ruling 2 — e.g. a
  missing or mismatched `**Slug:**` line, which the locator excludes from
  candidacy entirely) or a genuine multi-candidate ambiguity.
- **fleet_disabled** — resolves, but no profile sets `fleet.enabled: true`
  for its workspace. Checked before `no_project_id`: a disabled workspace
  never writes regardless of what the record itself contains.
- **no_project_id** — resolves, fleet is enabled, but `**Project-id:**` is
  missing. This is FR-13's own silence made visible: today this condition
  only ever reaches `<fleet_dir>/observe-failures.log`, a file inside a
  manifest directory nobody reads.
- **ok** — resolves, fleet is enabled, and `**Project-id:**` is present.

`fleet doctor` also reports the operator's git version and flags anything
older than 2.31 (PM addition, PLAN.md Chunk 3, from the Chunk 2 review):
`locate.py`'s root discovery depends on `git rev-parse
--path-format=absolute`, which requires git >= 2.31 (released March 2021).
On an older git that call fails, the locator refuses silently, and
observation is dead on that machine with nothing to diagnose it — exactly
#217's failure mode wearing a new mechanism.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import config as fleet_config
from . import project as fleet_project
from .locate import locate_project

#: The minimum git version `locate.py`'s `--path-format=absolute` needs
#: (git >= 2.31, released March 2021). Below this, root discovery fails
#: silently and observation is dead on that machine, undiagnosed.
MIN_GIT_VERSION: tuple[int, int, int] = (2, 31, 0)

#: Matches the version numerals in `git --version` output, e.g.
#: `"git version 2.42.0.windows.1"` -> `("2", "42", "0")`.
_GIT_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

#: Wall-clock bound for the `git --version` subprocess -- generous, since
#: this runs once per `fleet doctor` invocation, not per record.
_GIT_VERSION_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class ProjectHealth:
    """One project record's writability, as `fleet doctor` sees it.

    Attributes:
        root: the scan root this record was found under.
        slug: the record's directory name under
            `<root>/docs/superhuman/`.
        state: one of `"ok"`, `"no_project_id"`, `"fleet_disabled"`,
            `"unresolvable"`.
        detail: a short, human-readable explanation of `state`.
    """

    root: Path
    slug: str
    state: str
    detail: str


@dataclass(frozen=True, slots=True)
class GitVersionCheck:
    """The operator's git version, as `fleet doctor` measured it.

    Attributes:
        raw: the unparsed `git --version` output, or `None` if `git` could
            not be run at all.
        version: the parsed `(major, minor, patch)` tuple, or `None` if
            `raw` could not be parsed.
        ok: whether `version >= MIN_GIT_VERSION`. `False` when `version` is
            `None` too — an unmeasurable git version is never assumed safe.
    """

    raw: str | None
    version: tuple[int, int, int] | None
    ok: bool


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """The full result of one `scan()` call.

    Attributes:
        roots: every root that was scanned, exactly as given — even a root
            that yielded zero records, so the report is auditable evidence
            of what was actually looked at (DESIGN.md open issue 5: prior
            estate scans disagreed on scope because roots weren't
            recorded).
        records: every project record found under any root.
        git_version: the operator's measured git version.
    """

    roots: tuple[Path, ...]
    records: tuple[ProjectHealth, ...]
    git_version: GitVersionCheck


def check_git_version() -> GitVersionCheck:
    """Measure the operator's git version and flag it if too old.

    Never raises: a missing `git` executable or unparseable `--version`
    output resolves to a `GitVersionCheck` with `ok=False`, never an
    exception.

    Returns:
        GitVersionCheck: see its docstring.
    """
    try:
        proc = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=_GIT_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return GitVersionCheck(raw=None, version=None, ok=False)
    if proc.returncode != 0:
        return GitVersionCheck(raw=None, version=None, ok=False)

    raw = proc.stdout.strip()
    match = _GIT_VERSION_RE.search(raw)
    if match is None:
        return GitVersionCheck(raw=raw, version=None, ok=False)

    major, minor, patch = match.group(1), match.group(2), match.group(3)
    version = (int(major), int(minor), int(patch) if patch is not None else 0)
    return GitVersionCheck(raw=raw, version=version, ok=version >= MIN_GIT_VERSION)


def _enumerate_all_records(root: Path) -> list[Path]:
    """Enumerate every `<root>/docs/superhuman/<dir>/` carrying a `SUPERHUMAN.md`.

    Deliberately broader than `locate._enumerate_candidates`: this walk
    includes malformed records too (a mismatched `**Slug:**` line, say),
    since `fleet doctor`'s job is to *report* on them as `"unresolvable"`,
    not silently exclude them the way candidacy does.

    Args:
        root: a scan root to enumerate under.

    Returns:
        list[Path]: every project directory found, sorted by name.
    """
    base = root / "docs" / "superhuman"
    try:
        if not base.is_dir():
            return []
        entries = sorted(base.iterdir())
    except OSError:
        return []

    found: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        if (entry / "SUPERHUMAN.md").is_file():
            found.append(entry)
    return found


def _classify(root: Path, entry: Path) -> ProjectHealth:
    """Classify one `<root>/docs/superhuman/<slug>/` entry.

    Args:
        root: the scan root `entry` was found under.
        entry: the candidate directory (contains a `SUPERHUMAN.md`).

    Returns:
        ProjectHealth: the entry's classification, in the state precedence
        documented on the module (unresolvable > fleet_disabled >
        no_project_id > ok).
    """
    slug = entry.name
    location = locate_project(entry)
    if location is None or location.slug != slug:
        return ProjectHealth(
            root=root,
            slug=slug,
            state="unresolvable",
            detail=(
                "the locator could not uniquely resolve this record from its own "
                "directory (malformed SUPERHUMAN.md, or a genuine multi-candidate "
                "ambiguity)"
            ),
        )

    cfg = fleet_config.resolve_fleet_config(location.workspace)
    if not cfg.enabled:
        return ProjectHealth(root=root, slug=slug, state="fleet_disabled", detail=cfg.reason)

    identity = fleet_project.read_project_identity(location.workspace, slug)
    if identity is None:
        return ProjectHealth(
            root=root,
            slug=slug,
            state="no_project_id",
            detail="SUPERHUMAN.md has no **Project-id:** line",
        )

    project_id, _file_slug = identity
    return ProjectHealth(root=root, slug=slug, state="ok", detail=f"project_id={project_id}")


def scan(roots: Sequence[Path | str]) -> DoctorReport:
    """Walk down from every root in `roots` and classify every record found.

    Never raises: an unreadable/missing root, a malformed record, or a git
    failure inside `locate_project` all resolve to a `ProjectHealth` entry
    (typically `"unresolvable"`) rather than propagating an exception —
    this is a diagnostic tool, and a diagnostic tool that can itself crash
    defeats its own purpose (mirrors `observe.py`'s fail-soft posture, even
    though this module has no write path to protect).

    Args:
        roots: one or more directories to scan under (each is expected to
            be a git repository root; shell glob expansion — e.g.
            `~/dev/*` — is expected to have already turned a wildcard into
            one argument per repository before this function ever sees it).

    Returns:
        DoctorReport: every scanned root (even one yielding zero records),
        every record's classification, and the operator's git version.
    """
    resolved_roots = tuple(Path(root) for root in roots)
    records: list[ProjectHealth] = []
    for root in resolved_roots:
        for entry in _enumerate_all_records(root):
            records.append(_classify(root, entry))
    return DoctorReport(
        roots=resolved_roots, records=tuple(records), git_version=check_git_version()
    )
