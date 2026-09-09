"""Finding gitignored artifacts that git worktrees never receive.

This module exists because of a defect CLASS, not a single bug
(``roadmap#242``).

Several controls in this suite read an artifact that is deliberately
**gitignored** — an operator's token list, a machine-local golden fixture.
Gitignoring them is correct: they carry environment vocabulary that must not
enter a published tree. But gitignored also means ``git worktree add`` never
populates them, and essentially all work on this repository happens in
worktrees. A control that reads such an artifact therefore skips in every
worktree, the ``pre-commit`` hook skips with it because the hook runs this same
suite, and the developer sees green.

That is measured, not theorised. Arming the operator-token guard by hand in one
worktree turned a green branch red on three files already committed to it.

The standing workaround was "remember to copy the file into each new worktree".
That is what produced the state this was found in — a per-session instruction to
remember something is a mechanism for creating holes, not for closing them. So
this provisions instead: a worktree that lacks an artifact borrows the main
checkout's copy.

Every function here holds two properties, and both are load-bearing:

* **Never invent a pass.** When no location has the artifact, the returned path
  is the in-tree one and does not exist, so each caller's own absent-artifact
  policy runs exactly as before. Widening where an artifact is FOUND must never
  narrow when a guard FAILS.
* **Never reveal contents.** These functions return paths. They read nothing and
  print nothing, so a resolved location cannot reach failure output through
  them.

A fresh clone and a CI runner have no copy anywhere, and that is the fork case:
covering it needs a secret, not a lookup.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def main_checkout_root(repo_root: Path) -> Path | None:
    """Return the root of the MAIN checkout backing ``repo_root``, or ``None``.

    ``git rev-parse --git-common-dir`` names the git directory SHARED by a
    repository and all of its linked worktrees. From the main checkout it is
    that checkout's own ``.git``; from a linked worktree it is still the main
    checkout's ``.git``, never the worktree's private one. Its parent is
    therefore the main checkout's working tree in both cases, which is what this
    returns.

    The output is relative (``.git``) from the main checkout and absolute from a
    worktree. ``repo_root / out`` absorbs both — ``pathlib`` discards the left
    operand when the right is absolute — so no ``--path-format`` flag is needed
    and this works on every git version that has ``--git-common-dir``.

    Args:
        repo_root: Any working tree of the repository.

    Returns:
        The main checkout's working-tree root, or ``None`` when git is
        unavailable, errors, or says nothing useful.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        # No git, not a repository, or a hung invocation. Callers fall back to
        # the in-tree path, so this degrades to the previous behaviour rather
        # than to a wrong answer.
        return None
    return (repo_root / out).parent if out else None


def locate_ignored_artifact(repo_root: Path, relpath: str) -> Path:
    """Find a gitignored artifact, falling back to the main checkout.

    Args:
        repo_root: The working tree being checked. May be the main checkout or
            any linked worktree.
        relpath: The artifact's path relative to a repository root, e.g.
            ``".publication-tokens"``.

    Returns:
        The first existing copy — this working tree's own, else the main
        checkout's — or this working tree's own path when neither exists. The
        returned path is NOT guaranteed to exist; callers must apply their own
        policy for the absent case.
    """
    direct = repo_root / relpath
    if direct.is_file():
        return direct

    main_root = main_checkout_root(repo_root)
    if main_root is not None:
        shared = main_root / relpath
        if shared.is_file():
            return shared

    return direct
