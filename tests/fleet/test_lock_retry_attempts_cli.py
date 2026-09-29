"""`--lock-retry-attempts` must be >= 1 on every fleet verb that takes it.

Below 1, `_call_with_bounded_lock_retry` never calls core and used to crash
on `assert last_exc is not None` (or `raise None` -> TypeError under
`python -O`). `fleet owner` got `_positive_int` first (increment O); these
tests pin the remaining verbs, and a parser sweep keeps any future verb from
reintroducing a plain `type=int`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scripts.fleet import cli as fleet_cli


def _argv(verb: str, workspace: Path, value: str) -> list[str]:
    """Build a minimal argv for `verb` whose only defect is `value`.

    Args:
        verb: one of the verbs parametrized below.
        workspace: a throwaway directory; argparse rejects the flag before
            anything touches it.
        value: the `--lock-retry-attempts` value under test.

    Returns:
        list[str]: argv for `fleet_cli.main`.
    """
    common = ["--workspace", str(workspace), "--slug", "demo", "--writer-role", "pm"]
    prompt_path = workspace / "prompt.md"
    prompt_path.write_text("demo prompt\n", encoding="utf-8")
    prompt = str(prompt_path)
    by_verb = {
        "register": ["register", "--project-id", "p", "--origination", "manual", *common],
        "handoff emit": ["handoff", "emit", "--project-id", "p", "--prompt-file", prompt, *common],
        "handoff cancel": ["handoff", "cancel", "--node-id", "n", "--project-id", "p", *common],
        "handoff self-register": ["handoff", "self-register", *common],
    }
    return [*by_verb[verb], "--lock-retry-attempts", value]


@pytest.mark.parametrize(
    "verb", ["register", "handoff emit", "handoff cancel", "handoff self-register"]
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_lock_retry_attempts_is_a_usage_error(
    tmp_path: Path, verb: str, value: str
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        fleet_cli.main(_argv(verb, tmp_path, value))
    assert exc_info.value.code == 2


def _lock_retry_actions(parser: argparse.ArgumentParser, path: str = ""):
    """Yield (verb path, action) for every `--lock-retry-attempts` in the tree."""
    for action in parser._actions:
        if "--lock-retry-attempts" in action.option_strings:
            yield path, action
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _lock_retry_actions(sub, f"{path} {name}".strip())


def test_every_lock_retry_attempts_flag_uses_positive_int() -> None:
    found = list(_lock_retry_actions(fleet_cli.build_parser()))
    assert found, "no --lock-retry-attempts flags found; the sweep is broken"
    offenders = [path for path, action in found if action.type is not fleet_cli._positive_int]
    assert offenders == []
