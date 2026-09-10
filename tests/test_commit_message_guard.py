"""Tests for the commit-message guard (``roadmap#242``, forward fix).

The guard's whole purpose is to stop being silent, so the cases that matter most
here are the ones where it could pass without running: an absent token list, a
truncated one, and — the instance this issue exists for — a linked worktree,
where the list is invisible to a fixed-path lookup.

The worktree test builds a real `git worktree`. That is deliberate and it is the
lesson of the issue: every static argument about worktree behaviour made during
this work was wrong at least once, and the one measurement settled it in seconds.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from publication_patterns import LEAK_PATTERNS, REQUIRE_TOKENS_ENV  # noqa: E402

from check_commit_message import (  # noqa: E402
    SCISSORS,
    comment_char,
    find_violations,
    main,
    strip_noncommitted,
    token_state,
)

TOKEN = "examplecorp-internal"


def _write_tokens(root: Path, *tokens: str) -> None:
    """Write a token list at ``root``, one token per line."""
    (root / ".publication-tokens").write_text("\n".join(tokens) + "\n", encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    """Run a git command in ``root``, failing the test on a non-zero exit."""
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _no_require_flag(monkeypatch) -> None:
    """Default every test to "the require-flag is not set", explicitly.

    CI exports ``SUPERHUMAN_REQUIRE_OPERATOR_TOKENS=1`` for the whole run, which
    turns the "this clone never declared that it publishes" row from an allow
    into a hard failure. Tests asserting that row therefore passed locally and
    failed on all four CI versions -- the precise divergence this project had
    already written down about a different tier: *a maintainer's local green
    stops predicting CI*, and it stops silently, because both environments are
    green until one of them is not.

    Stating the environment rather than inheriting it is the fix, and it belongs
    on every test in the file rather than the two that happened to fail: a new
    test asserting the same row would inherit the same trap. The one test that
    exercises the flag sets it itself, which runs after this fixture and wins.
    """
    monkeypatch.delenv(REQUIRE_TOKENS_ENV, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with one commit, so worktrees can be added to it."""
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (root / ".gitignore").write_text(".publication-tokens\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    return root


# ---------------------------------------------------------------------------
# strip_noncommitted — text that never reaches the repository must not be scanned
# ---------------------------------------------------------------------------


def test_comment_lines_are_not_scanned() -> None:
    """Git strips `#` lines, so a token in one never reaches the repository.

    Not hypothetical: the default commit template comments out `On branch
    <name>`, and this repo's own branch names are generated from operator
    vocabulary. Scanning comments would refuse nearly every commit.
    """
    raw = f"subject\n\nbody\n# On branch {TOKEN}/some-work\n"
    assert TOKEN not in strip_noncommitted(raw)
    assert "subject" in strip_noncommitted(raw)


def test_verbose_diff_below_the_scissors_is_not_scanned() -> None:
    """`git commit --verbose` appends the staged diff; git cuts it before commit.

    Scanning it would double-report content the FILE guard already owns, and
    would refuse commits for text that is never published.
    """
    raw = f"subject\n\n# {SCISSORS}\ndiff --git a/x b/x\n+{TOKEN}\n"
    stripped = strip_noncommitted(raw)
    assert TOKEN not in stripped
    assert "subject" in stripped


def test_a_token_in_the_real_body_survives_stripping() -> None:
    """The stripping must not be so eager that it removes the committed text."""
    raw = f"subject\n\nsee {TOKEN} for details\n# a comment\n"
    assert TOKEN in strip_noncommitted(raw)


# ---------------------------------------------------------------------------
# find_violations
# ---------------------------------------------------------------------------


def test_clean_message_with_tokens_configured() -> None:
    """A message that avoids the vocabulary passes even when tokens are loaded."""
    assert find_violations("fix(x): close roadmap#242", [TOKEN]) == []


def test_operator_token_in_body_is_caught() -> None:
    """The measured shape of the real leak: body only, never the subject."""
    message = f"fix(x): a subject\n\nRelates to {TOKEN}/some-repo.\n"
    violations = find_violations(message, [TOKEN])
    assert len(violations) == 1
    assert TOKEN in violations[0]


def test_token_match_is_case_insensitive() -> None:
    """Tokens are lowercased on load, so matching must lower the text too."""
    assert find_violations(f"See {TOKEN.upper()} here", [TOKEN])


@pytest.mark.parametrize(
    ("sample", "label"),
    [(positive, label) for _pattern, label, positive, _negative in LEAK_PATTERNS],
    ids=[label.replace(" ", "-") for _p, label, _pos, _neg in LEAK_PATTERNS],
)
def test_leak_patterns_run_without_any_tokens(sample: str, label: str) -> None:
    """A fork with no token list still gets a real check, not a vacuous pass.

    Leak patterns are TRACKED, so they exist in every clone. Making them
    conditional on the gitignored token list would recreate this issue's defect:
    a control whose enforcement strength depends on an artifact that is absent.

    The samples come from the pattern table itself rather than being copied into
    this file, for two reasons. A copied sample goes stale silently when a
    pattern changes — the same "reimplement the vocabulary instead of querying
    the artifact that owns it" error this issue keeps producing. And a literal
    routable IP written here is itself a leak: the first draft of this test
    hardcoded one and the file guard refused the commit, which is the guard
    catching its own author for the third time in this issue.
    """
    violations = find_violations(sample, [])
    assert any(label in v for v in violations), (
        f"the {label!r} pattern did not fire on its own positive sample"
    )


def test_leak_pattern_negative_samples_do_not_fire() -> None:
    """The table's own negative samples must stay clean, or the guard cries wolf.

    A pattern that fires on legitimate text gets bypassed, and a bypassed guard
    is the silent-hole case in a louder costume.
    """
    for _pattern, label, _positive, negative in LEAK_PATTERNS:
        assert not any(label in v for v in find_violations(negative, [])), (
            f"the {label!r} pattern fired on its own negative sample"
        )


def test_only_matched_tokens_are_named() -> None:
    """Failure output can only name tokens already present in the scanned text.

    That bound is what makes naming them safe: the guard cannot disclose a token
    the author has not already written.
    """
    violations = find_violations(f"about {TOKEN}", [TOKEN, "unrelated-secret-word"])
    assert TOKEN in violations[0]
    assert "unrelated-secret-word" not in violations[0]


# ---------------------------------------------------------------------------
# token_state — the not-run policy, which is the part that fails silently
# ---------------------------------------------------------------------------


def test_absent_list_is_allowed_and_says_so(repo: Path) -> None:
    """Presence is the declaration. A clone with no list has not declared."""
    tokens, state = token_state(repo)
    assert tokens == []
    assert "has not declared" in state


def test_empty_list_is_refused_not_ignored(repo: Path) -> None:
    """A truncated list must not read the same as a clone that never published.

    This is the row that actually fires in practice: CI writes the file from a
    secret, so a broken secret arrives as an EMPTY file, not a missing one.
    """
    _write_tokens(repo)
    with pytest.raises(ValueError, match="lists no tokens"):
        token_state(repo)


def test_absent_list_fails_closed_when_required(repo: Path, monkeypatch) -> None:
    """The require-flag turns the fork row into a hard failure."""
    monkeypatch.setenv(REQUIRE_TOKENS_ENV, "1")
    with pytest.raises(ValueError, match="was required on this run"):
        token_state(repo)


def test_state_names_the_tier_not_the_path(repo: Path) -> None:
    """A guard drawing input from several places must say which one it used.

    The tier is named; the path never is and the contents never are.
    """
    _write_tokens(repo, TOKEN)
    tokens, state = token_state(repo)
    assert tokens == [TOKEN]
    assert "this working tree" in state
    assert str(repo) not in state
    assert TOKEN not in state


# ---------------------------------------------------------------------------
# The instance this issue exists for: a linked worktree
# ---------------------------------------------------------------------------


def test_token_list_resolves_from_a_linked_worktree(repo: Path, tmp_path: Path) -> None:
    """The defect class itself: the list is gitignored, so a worktree lacks it.

    Before the shared locator, a fixed-path lookup here found nothing and the
    guard stood down — which is how a hook built to close roadmap#242 would have
    been a fresh instance of roadmap#242 on its first day.
    """
    _write_tokens(repo, TOKEN)
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "--detach", str(linked))

    assert not (linked / ".publication-tokens").exists(), "precondition"

    tokens, state = token_state(linked)
    assert tokens == [TOKEN]
    assert "the main checkout" in state


def test_worktree_without_any_list_anywhere_still_allows(repo: Path, tmp_path: Path) -> None:
    """Widening WHERE the list is found must not change what absence MEANS."""
    linked = tmp_path / "linked-nolist"
    _git(repo, "worktree", "add", "-q", "--detach", str(linked))

    tokens, state = token_state(linked)
    assert tokens == []
    assert "has not declared" in state


# ---------------------------------------------------------------------------
# main() — the exit codes git actually acts on
# ---------------------------------------------------------------------------


def test_main_allows_a_clean_message(repo: Path, tmp_path: Path, capsys) -> None:
    """A passing run still reports what ran; silence is what this issue is about."""
    _write_tokens(repo, TOKEN)
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("fix(x): close roadmap#242\n", encoding="utf-8")

    assert main([str(msg), str(repo)]) == 0
    assert "message clean" in capsys.readouterr().out


def test_main_refuses_a_leaking_message(repo: Path, tmp_path: Path, capsys) -> None:
    """The whole point: exit non-zero so git aborts the commit."""
    _write_tokens(repo, TOKEN)
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(f"fix(x): subject\n\nbody mentions {TOKEN}\n", encoding="utf-8")

    assert main([str(msg), str(repo)]) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert TOKEN in err


def test_main_refuses_when_the_message_file_is_missing(tmp_path: Path, capsys) -> None:
    """Fail closed. An unreadable message must not pass unchecked."""
    assert main([str(tmp_path / "nope"), str(tmp_path)]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_main_requires_an_argument(capsys) -> None:
    """Git always passes one; a missing one means the hook is misinstalled."""
    assert main([]) == 1
    assert "expected a commit message file" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# comment_char — asked of git, because guessing wrong refuses every commit
# ---------------------------------------------------------------------------


def test_comment_char_defaults_to_hash(repo: Path) -> None:
    """An unset `core.commentChar` is git's default."""
    assert comment_char(repo) == "#"


def test_comment_char_honours_a_custom_setting(repo: Path) -> None:
    """A repo that sets its own must be believed, or its comments get scanned."""
    _git(repo, "config", "core.commentChar", ";")
    assert comment_char(repo) == ";"

    raw = f"subject\n; On branch {TOKEN}/x\n"
    assert TOKEN not in strip_noncommitted(raw, comment_char(repo))


def test_comment_char_falls_back_on_auto(repo: Path) -> None:
    """`auto` cannot be resolved outside git; `#` is the safe assumption."""
    _git(repo, "config", "core.commentChar", "auto")
    assert comment_char(repo) == "#"


def test_comment_char_outside_a_repository_falls_back(tmp_path: Path) -> None:
    """Never raise. A guard that crashes is a guard that blocks the day."""
    assert comment_char(tmp_path / "not-a-repo") == "#"
