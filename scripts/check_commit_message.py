"""Keep operator vocabulary and infrastructure leaks out of commit MESSAGES.

A commit message is a published surface. The existing guard scans tracked
*files*, so a message could carry the very vocabulary the file guard exists to
block, and nothing looked. Measured when this was written: **14 of 27 commits**
on the published branch carried an operator token in the message body, 4 distinct
tokens, spanning five weeks -- and every one of them in the BODY, none in a
subject, which is why reading subjects never found them.

Two design constraints, both paid for:

1. **This runs independently of the file guard, never as a step after it.** The
   incident that motivated the hook was a commit whose files were clean and
   whose message was not; a message check reached only from inside the file
   guard's findings misses exactly that case. ``commit-msg`` is its own git
   hook with its own trigger, and the file guard's verdict is not an input.

2. **It locates the token list the worktree-aware way**, via
   :func:`publication_patterns.locate_tokens_file`. A hook reading one fixed
   path is dead in every linked worktree -- which is the defect this whole
   issue is about (``roadmap#242``), so a hook built to close it that way would
   be a fresh instance of it on its first day.

The vocabulary itself is deliberately NOT reimplemented here. It is imported
from the module that owns it, because hand-copying the token strings instead of
querying the artifact that holds them is the error that produced the undercount
above -- the first pass found 6 commits and 2 tokens by picking strings from
memory; the repo's own 11-entry list found 14 and 4.

Two populations, two policies, and the split matters:

* **Leak patterns** (routable IPs, hostnames, ...) are TRACKED, so they exist in
  every clone. They always run. A fork with no token list still gets a real
  check rather than a vacuous pass.
* **Operator tokens** are gitignored and per-clone. Their PRESENCE is this
  clone's declaration that it publishes, so an absent list means "not a
  publishing clone" and is allowed -- the same presence-as-declaration semantic
  the rest of the guard uses. Widening where the list is FOUND must not change
  what its absence MEANS.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# The vocabulary and the locator both live with the guard that owns them. This
# script ships in scripts/ but deliberately imports rather than duplicates: see
# the module docstring. `tests/` is present in every clone of this repo, and the
# pre-commit hook already requires the test suite to be runnable.
_TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from publication_patterns import (  # noqa: E402
    LEAK_PATTERNS,
    REQUIRE_TOKENS_ENV,
    TOKENS_FILE,
    find_tokens,
    load_tokens,
    locate_tokens_file,
)

#: Git's default comment character. Lines starting with it are stripped from the
#: message before the commit is created, so scanning them would report leaks that
#: never reach the repository. That is not hypothetical here: the default commit
#: template comments out `On branch <name>`, and this repo's branch names are
#: themselves generated from operator vocabulary.
DEFAULT_COMMENT_CHAR = "#"

#: Everything from this marker onward is cut by `git commit --verbose`, so it is
#: never part of the message -- and it contains the full staged diff, which would
#: otherwise be scanned here and reported twice (the file guard already owns it).
SCISSORS = "------------------------ >8 ------------------------"


def strip_noncommitted(raw: str, comment_char: str = DEFAULT_COMMENT_CHAR) -> str:
    """Return only the text that will actually become the commit message.

    Git discards comment lines and everything after the scissors line before
    creating the commit. Scanning them would produce failures for text that is
    never published -- the ``--verbose`` diff most of all, which is the staged
    content the FILE guard already owns.

    Args:
        raw: Full contents of the ``COMMIT_EDITMSG`` file.
        comment_char: The repo's ``core.commentChar`` (git's default is ``#``).

    Returns:
        The committed text, with comment lines and any scissors section removed.
    """
    kept: list[str] = []
    for line in raw.splitlines():
        if line.rstrip().endswith(SCISSORS) and line.lstrip().startswith(comment_char):
            break
        if line.startswith(comment_char):
            continue
        kept.append(line)
    return "\n".join(kept)


def comment_char(repo_root: Path) -> str:
    """Return this repo's ``core.commentChar``, defaulting to ``#``.

    Asked of git rather than assumed, for the same reason the token list is:
    the repo owns this setting, and guessing it wrong fails in the LOUD
    direction -- comment lines would be scanned, and the default commit template
    comments out a branch name, so nearly every commit would be refused. Git's
    ``auto`` means "pick one that does not collide", which cannot be resolved
    here; ``#`` is the overwhelmingly common result and the safe assumption.

    Args:
        repo_root: The working tree being committed in.

    Returns:
        A single comment character. Falls back to ``#`` whenever git is
        unavailable, the value is unset, or it is ``auto``.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", "core.commentChar"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_COMMENT_CHAR
    if not out or out == "auto" or len(out) != 1:
        return DEFAULT_COMMENT_CHAR
    return out


def token_state(repo_root: Path) -> tuple[list[str], str]:
    """Resolve the operator token list and name the state it resolved from.

    Naming the state is load-bearing rather than cosmetic, and is the same
    lesson as ``-rs`` on the pre-commit hook: a guard that can draw its input
    from more than one place, and reports only "it ran", is indistinguishable
    from a guard that quietly drew a blank. The TIER is named; the path never is
    and the contents never are.

    Args:
        repo_root: The working tree the commit is being made in.

    Returns:
        ``(tokens, state)`` where ``state`` is a short human-readable tier name.

    Raises:
        ValueError: When the list exists but is empty, or is absent while
            ``REQUIRE_TOKENS_ENV`` is set. Both are fail-closed rows: a
            truncated list must not read the same as a clone that never
            published.
    """
    required = bool(os.environ.get(REQUIRE_TOKENS_ENV))
    path = locate_tokens_file(repo_root)

    if path.is_file():
        tokens = load_tokens(path)
        if not tokens:
            raise ValueError(
                f"{TOKENS_FILE} exists but lists no tokens. A truncated list "
                f"disarms this guard silently; refusing the commit instead. "
                f"Restore the list, or delete the file if this clone genuinely "
                f"does not publish."
            )
        tier = (
            "this working tree"
            if path.parent == repo_root
            else "the main checkout (this worktree has no list of its own)"
        )
        return tokens, f"{len(tokens)} operator tokens from {tier}"

    if required:
        raise ValueError(
            f"{TOKENS_FILE} was required on this run ({REQUIRE_TOKENS_ENV} is "
            f"set) and was not found in this working tree or the main checkout."
        )

    return [], (
        f"no operator tokens -- this clone has no {TOKENS_FILE} and so has not "
        f"declared that it publishes"
    )


def find_violations(message: str, tokens: list[str]) -> list[str]:
    """Return human-readable violations for a commit message.

    Args:
        message: The committed text, already stripped of comments.
        tokens: Lowercased operator tokens, possibly empty.

    Returns:
        One string per distinct problem, most specific first. Empty when clean.
    """
    out: list[str] = []

    for pattern, label, _positive, _negative in LEAK_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            out.append(f"commit message contains a {label}")

    # `find_tokens` returns the INTERSECTION -- only tokens already present in
    # the text being scanned -- so naming them here cannot disclose a token the
    # author has not already written. That bound is what makes it safe to be
    # specific, and being specific is what lets the author fix it in one edit.
    hits = find_tokens(message, tokens)
    if hits:
        out.append(
            f"commit message contains operator vocabulary: {', '.join(sorted(hits))}"
        )

    return out


def main(argv: list[str] | None = None) -> int:
    """Check the message file named on the command line.

    Args:
        argv: Arguments after the program name; ``argv[0]`` is the path to the
            commit message file, as git passes it to a ``commit-msg`` hook.

    Returns:
        Process exit status: 0 to allow the commit, 1 to refuse it.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("check_commit_message: expected a commit message file", file=sys.stderr)
        return 1

    message_path = Path(args[0])
    repo_root = Path(args[1]).resolve() if len(args) > 1 else Path.cwd().resolve()

    try:
        raw = message_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"check_commit_message: cannot read {message_path}: {exc}", file=sys.stderr)
        return 1

    message = strip_noncommitted(raw, comment_char(repo_root))

    try:
        tokens, state = token_state(repo_root)
    except ValueError as exc:
        print(f"commit-msg: {exc}", file=sys.stderr)
        return 1

    violations = find_violations(message, tokens)
    if not violations:
        # Say what ran, on the PASSING path too. A guard heard from only when it
        # fails cannot be distinguished from one that has stopped running.
        print(f"commit-msg: message clean ({state}).")
        return 0

    print("commit-msg: REFUSED -- this message would publish private data.", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    print(f"  checked against: {state}", file=sys.stderr)
    print(
        "\nEdit the message and commit again. The convention in this repo is the "
        "bare `roadmap#NNN` form rather than a full private path.\n"
        "Emergency bypass (prefer fixing the message): git commit --no-verify",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the hook
    raise SystemExit(main())
