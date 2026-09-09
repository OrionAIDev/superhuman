"""Tests for gitignored-artifact resolution across worktrees.

The module under test exists because of roadmap#242's defect class: a control
that reads a gitignored artifact skips in every worktree, and a skip reported by
`-q` is an integer nobody diffs. These tests build REAL repositories and REAL
worktrees rather than monkeypatching `subprocess`. A mock of git would have
reported the behaviour this depends on regardless of whether git has it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from publication_patterns import TOKENS_FILE, locate_tokens_file  # noqa: E402
from repo_artifacts import locate_ignored_artifact  # noqa: E402

# ---------------------------------------------------------------------------
# Where a gitignored artifact is found (roadmap#242, instance 2)
#
# The guard has always known what to do with a token list. What it could not do
# was find one from a linked worktree, because `.publication-tokens` is
# gitignored and `git worktree add` therefore never populates it. The guard
# skipped, the pre-commit hook ran the same skipping suite and passed, and a
# developer committed unguarded against a green signal.
#
# These tests build real repositories and real worktrees rather than
# monkeypatching `subprocess`. A mock of git would have happily reported the
# behaviour this fix depends on regardless of whether git actually has it.
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> None:
    """Run a git command in ``cwd``, raising on failure.

    Args:
        *args: Arguments after ``git``.
        cwd: Working directory for the invocation.
    """
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


@pytest.fixture
def main_checkout(tmp_path: Path) -> Path:
    """A real git repository with one commit, ready for `git worktree add`.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        The repository's working-tree root.
    """
    root = tmp_path / "main"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    # Identity is set locally so the commit works on a machine with no global
    # git config, e.g. a CI container.
    _git("config", "user.email", "guard-test@example.com", cwd=root)
    _git("config", "user.name", "guard test", cwd=root)
    (root / "README.md").write_text("placeholder\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "initial", cwd=root)
    return root


def test_locate_prefers_the_working_trees_own_token_list(main_checkout: Path) -> None:
    """A tree with its own list uses it, never a borrowed one.

    Precedence matters in the direction of specificity: a fork or a worktree
    that deliberately carries a different vocabulary must not be silently
    overridden by whatever the main checkout happens to hold.

    Args:
        main_checkout: A real repository from the fixture.
    """
    own = main_checkout / TOKENS_FILE
    own.write_text("acme\n", encoding="utf-8")

    assert locate_tokens_file(main_checkout) == own


def test_locate_borrows_the_main_checkouts_list_from_a_worktree(
    main_checkout: Path,
) -> None:
    """THE REGRESSION. A worktree with no list of its own finds the real one.

    This is the whole defect: `.publication-tokens` is gitignored, so the
    worktree below is created without it, exactly as every worktree on a real
    machine is. Before this fix the guard skipped here — and so did the
    pre-commit hook that runs the same suite.

    Args:
        main_checkout: A real repository from the fixture.
    """
    shared = main_checkout / TOKENS_FILE
    shared.write_text("acme\n", encoding="utf-8")

    worktree = main_checkout.parent / "linked"
    _git("worktree", "add", "-b", "side", str(worktree), cwd=main_checkout)

    # Precondition, asserted rather than assumed: the fix must be doing the
    # work, not the filesystem. If `git worktree add` ever starts copying
    # ignored files, this test would otherwise pass for the wrong reason.
    assert not (worktree / TOKENS_FILE).exists()

    assert locate_tokens_file(worktree) == shared


def test_locate_does_not_invent_a_list_when_neither_tree_has_one(
    main_checkout: Path,
) -> None:
    """No list anywhere means no list — the fork case must still skip.

    Widening where a list is found must never narrow when the guard fails. The
    returned path is the working tree's own and does not exist, so every
    caller's absent-list policy runs unchanged.

    Args:
        main_checkout: A real repository from the fixture.
    """
    worktree = main_checkout.parent / "linked"
    _git("worktree", "add", "-b", "side", str(worktree), cwd=main_checkout)

    found = locate_tokens_file(worktree)

    assert found == worktree / TOKENS_FILE
    assert not found.is_file()


def test_locate_is_inert_outside_a_git_repository(tmp_path: Path) -> None:
    """A directory git knows nothing about resolves without raising.

    The guard runs from source trees that are not repositories — an unpacked
    release, a vendored copy. `git rev-parse` fails there, and the failure must
    degrade to today's behaviour rather than erroring the suite.

    Args:
        tmp_path: pytest's per-test temporary directory.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    found = locate_tokens_file(plain)

    assert found == plain / TOKENS_FILE
    assert not found.is_file()


def test_locate_resolves_the_main_checkout_to_itself(main_checkout: Path) -> None:
    """From the main checkout the lookup is a no-op.

    `git rev-parse --git-common-dir` prints a RELATIVE `.git` here and an
    absolute path from a worktree. This pins that both are handled, which is
    what lets the implementation skip `--path-format=absolute` and stay
    compatible with older git.

    Args:
        main_checkout: A real repository from the fixture.
    """
    own = main_checkout / TOKENS_FILE
    own.write_text("acme\n", encoding="utf-8")
    # Deleting and re-reading through the fallback path would be indirect; the
    # honest check is that a main checkout whose file was removed still points
    # back at itself rather than somewhere else.
    own.unlink()

    assert locate_tokens_file(main_checkout) == main_checkout / TOKENS_FILE


def test_locate_generalises_beyond_the_token_list(main_checkout: Path) -> None:
    """A nested gitignored artifact resolves the same way.

    Written after nearly shipping the opposite defect. Fixing the token list
    alone and then making `tests/fixtures/golden/` FAIL on absence would have
    turned every worktree red, because that fixture is gitignored too and three
    of five worktrees on the machine this was written on lack it. One instance
    of the class repaired into a fresh instance of the class.

    So the locator takes a relative path rather than being hardcoded to one
    artifact, and this pins that it works for a nested one.

    Args:
        main_checkout: A real repository from the fixture.
    """
    relpath = "tests/fixtures/golden/ladder-current.yaml"
    shared = main_checkout / relpath
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text("version: 1\n", encoding="utf-8")

    worktree = main_checkout.parent / "linked"
    _git("worktree", "add", "-b", "side", str(worktree), cwd=main_checkout)
    assert not (worktree / relpath).exists()

    assert locate_ignored_artifact(worktree, relpath) == shared
