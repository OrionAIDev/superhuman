"""FR-6 canonical-record enumerator.

Walks a set of scan roots, applies the exclusion rule -- never a path
containing ``/tests/``, and never a record whose repository is a linked git
worktree -- and yields one ``RecordRef`` per canonical
``docs/superhuman/<slug>/SUPERHUMAN.md``.

A worktree is detected by asking the filesystem, never by matching a path
(G6-003): a git worktree can be created anywhere, so a path-convention rule
(e.g. ``/.claude/worktrees/``) misses worktrees created elsewhere -- a
sibling of the repo they belong to, for instance -- and the same project
record gets enumerated twice under two different ``repo_root``s. The fix
relies on a documented git invariant instead: a linked worktree's ``.git``
is a *file* containing ``gitdir: <path>``, while a main checkout's ``.git``
is a directory. A ``tests/`` directory holds fixtures, not real projects --
this is what keeps this project's own `tests/fixtures/gate_records/` from
inflating the count it measures (TC-31).

``repo_root`` is resolved from the enclosing ``.git`` (G6-005-d), never from
a fixed parent count: a fixed count is the record's containing directory,
not the repository root, the moment a record sits somewhere other than
exactly ``<repo>/docs/superhuman/<slug>/SUPERHUMAN.md`` -- inside a monorepo
package, for instance. Git already knows where its own root is; asking it
directly, the same way worktree detection already does, costs nothing and
removes a depth assumption from the enumerator entirely.

Identity is always ``(repo_root, slug)``, never ``slug`` alone (NFR-7):
`memory-sync-evaluation` exists as two byte-identical files in two different
repos, and any reporting keyed on slug alone would silently collapse that
pair. ``RecordRef`` is a plain, hashable value; nothing in this module ever
routes results through a slug-keyed mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

#: Path substrings (checked against the POSIX form) that exclude a matched
#: ``SUPERHUMAN.md`` from the canonical record set (FR-6), independent of
#: the git-worktree check below.
_EXCLUDED_SUBSTRINGS = ("/tests/",)

#: The glob that locates every canonical record under a scan root.
_RECORD_GLOB = "**/docs/superhuman/*/SUPERHUMAN.md"


@dataclass(frozen=True, slots=True)
class RecordRef:
    """One canonical ``SUPERHUMAN.md``, identified by repo root AND slug.

    Attributes:
        repo_root: absolute path to the repository root that owns this
            record (the directory containing ``docs/superhuman/``).
        slug: the project's slug -- the record's parent directory name.
        path: absolute path to the ``SUPERHUMAN.md`` file itself.
    """

    repo_root: Path
    slug: str
    path: Path


def _is_excluded(path: Path) -> bool:
    """Whether `path` falls under the FR-6 exclusion rule.

    Args:
        path: an absolute path to a matched ``SUPERHUMAN.md``.

    Returns:
        True when the path runs through a worktree or a ``tests/`` tree.
    """
    posix = f"/{path.as_posix()}/"
    return any(substring in posix for substring in _EXCLUDED_SUBSTRINGS)


def _enclosing_git_entry(start: Path) -> Path | None:
    """Find the nearest ``.git`` entry (file or directory) walking up from `start`.

    Args:
        start: an absolute directory to begin the upward walk from.

    Returns:
        The path to the nearest ancestor's ``.git`` entry, or `None` when no
        ancestor -- up to and including the filesystem root -- has one.
    """
    current = start
    while True:
        candidate = current / ".git"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _resolve_repo_root(record_path: Path) -> Path:
    """Resolve the repository root that owns `record_path` via its `.git` (G6-005-d).

    Replaces a fixed parent-count walk (`path.parent.parent.parent.parent`),
    which is the record's containing directory rather than the repository
    root whenever a record sits deeper than exactly
    ``<repo>/docs/superhuman/<slug>/SUPERHUMAN.md`` -- inside a monorepo
    package, for instance. `_enclosing_git_entry` already answers this
    question for worktree detection; reusing it here asks the same
    authority (git) for the same fact, rather than counting directories and
    hoping the corpus never grows a shape that count doesn't fit.

    Args:
        record_path: the `SUPERHUMAN.md` path.

    Returns:
        The directory containing the nearest enclosing `.git` entry (file
        or directory), searched upward from the record's parent directory.
        Falls back to the old fixed-parent-count answer only when no
        enclosing `.git` exists at all -- there is no git root to defer to
        in that case, so the previous, purely structural answer is the
        least surprising fallback.
    """
    git_entry = _enclosing_git_entry(record_path.parent)
    if git_entry is not None:
        return git_entry.parent
    return record_path.parent.parent.parent.parent


def _is_linked_worktree(repo_root: Path) -> bool:
    """Whether `repo_root`'s enclosing git checkout is a linked worktree.

    A linked worktree's ``.git`` is a *file* containing ``gitdir: <path>``;
    a main checkout's ``.git`` is a directory (G6-003). The walk starts at
    `repo_root` rather than checking it in isolation, since the nearest
    ``.git`` is the authority on what kind of checkout this is.

    A `repo_root` with no enclosing ``.git`` at all -- not inside any git
    checkout -- is treated as NOT a worktree: the exclusion rule targets a
    specific, provable condition (a ``.git`` file), and the absence of
    ``.git`` entirely is not evidence of that condition. Guessing otherwise
    is exactly the path-convention brittleness G6-003 rejected.

    Args:
        repo_root: the candidate record's computed repository root.

    Returns:
        True only when an enclosing ``.git`` entry exists and is a file.
    """
    git_entry = _enclosing_git_entry(repo_root)
    return git_entry is not None and git_entry.is_file()


def iter_canonical_records(roots: Iterable[Path]) -> Iterator[RecordRef]:
    """Yield one `RecordRef` per canonical gate record under `roots`.

    A root that does not exist on disk (or is not a directory) contributes
    no records and is not an error -- callers may pass roots that are only
    sometimes reachable (REQUIREMENTS Assumption 2).

    Args:
        roots: repository-scanning roots, e.g. ``~/.claude/skills``,
            ``~/dev``.

    Yields:
        A `RecordRef` for every ``docs/superhuman/<slug>/SUPERHUMAN.md``
        found, excluding ``tests/`` trees and linked git worktrees (G6-003).
        Never de-duplicates across `roots` beyond exact path identity, and
        never collapses two repos sharing a slug (NFR-7).
    """
    seen: set[Path] = set()
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for path in sorted(root_path.glob(_RECORD_GLOB)):
            if path in seen or _is_excluded(path):
                continue
            slug = path.parent.name
            repo_root = _resolve_repo_root(path)
            if _is_linked_worktree(repo_root):
                continue
            seen.add(path)
            yield RecordRef(repo_root=repo_root, slug=slug, path=path)


def census(roots: Iterable[Path]) -> list[RecordRef]:
    """Return the full canonical record set under `roots`.

    Args:
        roots: repository-scanning roots.

    Returns:
        Every `RecordRef` found, keyed by nothing (a plain list) -- the
        caller must never fold this into a slug-keyed structure (NFR-7).

    Raises:
        RuntimeError: when the result is empty. Mirrors the
            ``regen_core_manifest.py`` "refusing to write an EMPTY
            manifest" precedent: a census of zero canonical records is a
            bug (a wrong root, a broken exclusion rule) and must never be
            reported as a legitimate finding of "no projects exist".
    """
    records = list(iter_canonical_records(roots))
    if not records:
        searched = ", ".join(Path(root).as_posix() for root in roots)
        raise RuntimeError(
            "gate_record_census: refusing to report an EMPTY census -- 0 "
            f"canonical records found under: {searched}"
        )
    return records
