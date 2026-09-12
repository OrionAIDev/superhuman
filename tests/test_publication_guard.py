"""Tests for the publication guard itself.

The guard exists to stop infrastructure details reaching a published tree. It
is therefore exactly the kind of code whose failure mode is silence: a pattern
that matches nothing produces a passing suite and a false sense of safety.

That is not hypothetical. An earlier revision of `LEAK_PATTERNS` was written
with literal backspace bytes where word-boundary escapes were intended. Every
pattern using one matched nothing, the suite stayed green, and the defect was
found only by scanning a built publication candidate by hand. These tests exist
so that cannot recur.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from publication_patterns import (  # noqa: E402
    LEAK_PATTERNS,
    PUBLICATION_EXEMPT,
    REQUIRE_TOKENS_ENV,
    SKIPPED_SUFFIXES,
    TOKENS_FILE,
    is_scanned,
)

#: The workflow the meta-test below reads. One file, named once.
_CI_WORKFLOW = Path(".github") / "workflows" / "ci.yml"

#: The repository secret the materialisation step reads. Named here so the test
#: and the failure messages cannot disagree about which secret is missing.
_TOKENS_SECRET = "PUBLICATION_TOKENS"


@pytest.mark.parametrize(
    ("pattern", "label", "positive", "negative"),
    LEAK_PATTERNS,
    ids=[label for _, label, _, _ in LEAK_PATTERNS],
)
def test_pattern_matches_its_positive_sample(
    pattern: str, label: str, positive: str, negative: str
) -> None:
    """Every pattern must actually match the leak it claims to catch.

    Args:
        pattern: The regex under test.
        label: Human-readable name, used as the test id.
        positive: A string that must match.
        negative: A string that must not match.
    """
    assert re.search(pattern, positive), (
        f"pattern for {label!r} does not match its own positive sample "
        f"{positive!r} — the guard is not actually checking for this"
    )


@pytest.mark.parametrize(
    ("pattern", "label", "positive", "negative"),
    LEAK_PATTERNS,
    ids=[label for _, label, _, _ in LEAK_PATTERNS],
)
def test_pattern_rejects_its_negative_sample(
    pattern: str, label: str, positive: str, negative: str
) -> None:
    """Every pattern must not fire on its documented false-positive case.

    Args:
        pattern: The regex under test.
        label: Human-readable name, used as the test id.
        positive: A string that must match.
        negative: A string that must not match.
    """
    assert not re.search(pattern, negative), (
        f"pattern for {label!r} wrongly matches {negative!r} — an over-broad "
        "guard trains people to ignore it"
    )


#: The IPv4 pattern, looked up by label so these samples follow the table.
_IPV4 = next(p for p, label, _, _ in LEAK_PATTERNS if label.startswith("routable IP address"))


@pytest.mark.parametrize(
    "text",
    [
        "HOST=198.51.100.23",
        "http://192.0.2.10:8080/health",
        "the box at 10.20.30.40.",
        "tailnet peer 100.101.102.103",
        "range 192.0.2.1-192.0.2.9",
        "(172.16.0.1)",
        "see ...192.0.2.44",
        "ssh root@10.1.2.3",
    ],
)
def test_ipv4_pattern_still_catches_addresses(text: str) -> None:
    """Tightening the pattern must not open a hole for a real address.

    Each sample is an address in a position a leak actually takes: a config
    value, a URL, the end of a sentence, one end of a range, after an ellipsis.

    Args:
        text: A string containing an address that must be flagged.
    """
    assert re.search(_IPV4, text, re.IGNORECASE), f"IPv4 pattern missed the address in {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "SNMP oid 1.3.6.1.4.1",
        "build 1.2.3.4.5",
        "Windows 10.0.19045.2965",
        "octets 999.1.1.1 and 10.0.0.256",
        "pandoc v2.17.1.1",
    ],
)
def test_ipv4_pattern_ignores_what_cannot_be_an_address(text: str) -> None:
    """Dotted numbers that cannot be an IPv4 address must not fire.

    An octet above 255 is not an address, and neither is any four-part window
    onto a longer dotted run. The commit-message guard reuses this pattern, so a
    false positive here refuses a legitimate commit — roadmap#246 found one.

    Args:
        text: A string with a dotted number that is not an address.
    """
    hit = re.search(_IPV4, text, re.IGNORECASE)
    assert hit is None, f"IPv4 pattern flagged {hit.group(0) if hit else ''!r} in {text!r}"


def test_ipv4_pattern_keeps_flagging_a_bare_four_part_version() -> None:
    """A bare four-part version is spelled like an address, so it stays flagged.

    Pinned so the trade-off is a decision rather than an accident: exempting
    "pandoc 2.17.1.1" would also exempt "server 2.17.1.1", and a leaked
    address costs more than a reworded commit. The label tells the author the
    unambiguous spelling, which the case above shows passing.
    """
    assert re.search(_IPV4, "pandoc 2.17.1.1")
    label = next(lbl for p, lbl, _, _ in LEAK_PATTERNS if p == _IPV4)
    assert "v1.2.3.4" in label, "the label must say how to write a version unambiguously"


@pytest.mark.parametrize(
    ("pattern", "label"),
    [(p, lbl) for p, lbl, _, _ in LEAK_PATTERNS],
    ids=[label for _, label, _, _ in LEAK_PATTERNS],
)
def test_pattern_compiles_and_has_no_control_characters(pattern: str, label: str) -> None:
    """Guard against the exact defect that made this suite necessary.

    A literal control byte in a pattern is almost always a mangled escape
    sequence: someone wrote a backslash-b and got a backspace.

    Args:
        pattern: The regex under test.
        label: Human-readable name, used as the test id.
    """
    re.compile(pattern)  # raises on a malformed pattern
    control = [c for c in pattern if ord(c) < 32]
    assert not control, (
        f"pattern for {label!r} contains control character(s) "
        f"{[hex(ord(c)) for c in control]} — a mangled escape sequence, not a "
        "word boundary. This is the defect that made these tests necessary."
    )


def test_exemptions_are_minimal_and_real(skill_root: Path) -> None:
    """Every exemption must name a file that exists and be justified in place.

    An exemption for a file that no longer exists is dead weight that makes the
    list look better-justified than it is.

    Args:
        skill_root: Repository root.
    """
    for rel in PUBLICATION_EXEMPT:
        assert (skill_root / rel).is_file(), (
            f"exemption {rel!r} names a file that does not exist — remove it"
        )
    assert len(PUBLICATION_EXEMPT) <= 3, (
        "the exemption list is growing; each entry is a file the guard cannot "
        "vouch for, so justify additions deliberately"
    )


def test_guard_actually_scans_a_representative_sample(skill_root: Path) -> None:
    """The candidate set must be non-trivial and cover the shipped surface.

    A filter bug that silently emptied the candidate list would also produce a
    green suite.

    Args:
        skill_root: Repository root.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=skill_root, capture_output=True, text=True, check=True
    ).stdout.split()
    candidates = [rel for rel in tracked if is_scanned(rel)]
    assert len(candidates) > 100, (
        f"only {len(candidates)} files would be scanned — the suffix denylist "
        "or exemption list is probably wrong"
    )
    for required in ("SKILL.md", "README.md", "CHANGELOG.md"):
        assert required in candidates, f"{required} must be scanned"


def test_guard_covers_every_tracked_file_but_binaries(skill_root: Path) -> None:
    """Every tracked file is scanned unless it is binary or explicitly exempt.

    The regression this pins: `SKIPPED_SUFFIXES` replaced an *allowlist* of
    twelve extensions that left eleven tracked files unscanned, among them
    `hooks/session-start` and `scripts/git-hooks/pre-commit` — shipped
    executables, and precisely where an absolute server path would hide. An
    allowlist decays every time a new file type lands; this asserts the
    inverted default so it cannot silently decay again.

    Args:
        skill_root: Repository root.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=skill_root, capture_output=True, text=True, check=True
    ).stdout.split()

    unscanned = [rel for rel in tracked if not is_scanned(rel)]
    unexpected = [
        rel for rel in unscanned
        if not rel.startswith(PUBLICATION_EXEMPT) and not rel.endswith(SKIPPED_SUFFIXES)
    ]
    assert not unexpected, (
        "tracked files are invisible to the publication guard for no stated "
        f"reason: {unexpected}"
    )

    # The specific files the old allowlist missed must now be covered.
    for previously_missed in (
        "LICENSE",
        "VERSION",
        "hooks/session-start",
        "scripts/git-hooks/pre-commit",
        "examples/promote.sh.example",
    ):
        assert previously_missed in tracked, (
            f"{previously_missed} is no longer tracked — update this test"
        )
        assert is_scanned(previously_missed), (
            f"{previously_missed} is tracked but not scanned — the suffix "
            "blind spot has regressed"
        )


def _ci_test_job_steps(skill_root: Path) -> list[dict]:
    """Return the steps of the workflow job that runs the test suite.

    Parsed as YAML rather than grepped, so the assertions below are about the
    workflow's real structure -- which step, which key -- and not about text
    that happens to appear somewhere in the file, including inside a comment.

    Args:
        skill_root: Repository root.

    Returns:
        The job's ``steps`` list.
    """
    workflow = yaml.safe_load((skill_root / _CI_WORKFLOW).read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    pytest_jobs = [
        name for name, job in jobs.items()
        if any("pytest" in str(step.get("run", "")) for step in job.get("steps", []))
    ]
    assert len(pytest_jobs) == 1, (
        f"expected exactly one job in {_CI_WORKFLOW} to run pytest, found "
        f"{pytest_jobs}. The token list is exported through $GITHUB_ENV, which "
        "is scoped to a single job -- a second pytest job would run the suite "
        "with the guard silently disabled."
    )
    return jobs[pytest_jobs[0]]["steps"]


def test_ci_runs_the_operator_token_guard(skill_root: Path) -> None:
    """CI must materialise the operator token list and print its skips.

    **This is the control on the control, and it is the point of the change it
    guards.** The operator-token guard reads a gitignored file, so for the
    whole life of the workflow before this test it skipped on every CI run:
    the check that keeps operator vocabulary out of a public repository had
    never once executed there, and ``-q`` reported that as an integer in a
    summary line rather than as anything a reader would notice. Commit
    ``cb7271d`` is the worked failure -- private repository names reached three
    tracked files through a green build.

    The failure being fixed is therefore "a guard stopped running and nobody
    noticed", so deleting the workflow step must not be a way to turn a red
    build green. With this test present, deleting that step fails *two* tests
    instead of zero.

    It needs no secret and reads only a tracked file, so unlike the guard it
    protects, it runs everywhere -- including on fork pull requests.

    Args:
        skill_root: Repository root.
    """
    steps = _ci_test_job_steps(skill_root)

    materialise = [
        i for i, step in enumerate(steps)
        if TOKENS_FILE in str(step.get("run", ""))
        and REQUIRE_TOKENS_ENV in str(step.get("run", ""))
    ]
    assert len(materialise) == 1, (
        f"{_CI_WORKFLOW} must contain exactly one step that writes "
        f"{TOKENS_FILE} and exports {REQUIRE_TOKENS_ENV}; found "
        f"{len(materialise)}. Without it the operator-token guard skips on "
        "every CI run and this repository publishes unguarded. Steps read "
        f"from {skill_root / _CI_WORKFLOW} "
        f"(mtime={(skill_root / _CI_WORKFLOW).stat().st_mtime_ns}, "
        f"bytes={(skill_root / _CI_WORKFLOW).stat().st_size}): "
        + repr([s.get("name") or f"<{sorted(s)[0]}>" for s in steps])
    )
    step = steps[materialise[0]]

    # The secret reaches the step through `env:`, not through an inline
    # expansion in `run:` -- the latter interpolates the value into the shell
    # command before the shell sees it.
    env = step.get("env") or {}
    assert env.get(_TOKENS_SECRET) == "${{ secrets.%s }}" % _TOKENS_SECRET, (
        f"the materialisation step must read the {_TOKENS_SECRET} repository "
        f"secret through `env:`, got env={env!r}"
    )

    # The gate is on the run's SHAPE, never on the secret's presence. Gating on
    # presence would let a deleted or renamed secret silently re-disable the
    # guard, which is precisely the defect this step exists to fix.
    condition = str(step.get("if", ""))
    assert "github.event.pull_request.head.repo.full_name" in condition, (
        "the materialisation step must be gated on the run not being a FORK "
        f"pull request, got if={condition!r}"
    )
    assert "github.repository" in condition, (
        "the fork gate must compare the PR head repo against github.repository, "
        f"got if={condition!r}"
    )
    assert "secrets." not in condition, (
        "the materialisation step must NOT be gated on the secret's presence "
        f"-- a deleted or renamed secret would then silently re-disable the "
        f"guard instead of failing the build. Got if={condition!r}"
    )

    # `-rs` turns each skip from an unread integer into a named line with its
    # reason, which is what makes a guard that stops running visible at all.
    pytest_steps = [
        i for i, s in enumerate(steps) if "pytest" in str(s.get("run", ""))
    ]
    assert pytest_steps, f"no step in {_CI_WORKFLOW} runs pytest"
    for i in pytest_steps:
        flags = shlex.split(str(steps[i]["run"]))
        reported = {
            char
            for flag in flags
            if flag.startswith("-r") and not flag.startswith("--")
            for char in flag[2:]
        }
        assert reported & {"s", "a", "A"}, (
            f"the pytest step in {_CI_WORKFLOW} must pass `-rs` so every skip "
            f"prints its reason; got {flags!r}. Without it a guard that has "
            "stopped running shows up only as a count nobody diffs."
        )
        assert i > materialise[0], (
            "the pytest step runs BEFORE the token list is materialised, so "
            f"{TOKENS_FILE} does not exist yet and the guard skips anyway. "
            f"pytest at step {i}, materialisation at step {materialise[0]}."
        )
