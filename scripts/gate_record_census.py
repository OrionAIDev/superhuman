"""FR-6 canonical-record enumerator.

Walks a set of scan roots, applies the exclusion rule (never a path
containing ``/.claude/worktrees/`` or ``/tests/``), and yields one
``RecordRef`` per canonical ``docs/superhuman/<slug>/SUPERHUMAN.md``.

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
#: ``SUPERHUMAN.md`` from the canonical record set (FR-6). A worktree is a
#: working copy of a project already counted at its home location; a
#: ``tests/`` directory holds fixtures, not real projects -- this is what
#: keeps this project's own `tests/fixtures/gate_records/` from inflating
#: the count it measures (TC-31).
_EXCLUDED_SUBSTRINGS = ("/.claude/worktrees/", "/tests/")

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
        found, excluding worktrees and ``tests/`` trees. Never
        de-duplicates across `roots` beyond exact path identity, and never
        collapses two repos sharing a slug (NFR-7).
    """
    seen: set[Path] = set()
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for path in sorted(root_path.glob(_RECORD_GLOB)):
            if path in seen or _is_excluded(path):
                continue
            seen.add(path)
            slug = path.parent.name
            repo_root = path.parent.parent.parent.parent
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
