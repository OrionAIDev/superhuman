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
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from publication_patterns import (  # noqa: E402
    LEAK_PATTERNS,
    PUBLICATION_EXEMPT,
    SCANNED_SUFFIXES,
)


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
    candidates = [
        rel for rel in tracked
        if not rel.startswith(PUBLICATION_EXEMPT) and rel.endswith(SCANNED_SUFFIXES)
    ]
    assert len(candidates) > 100, (
        f"only {len(candidates)} files would be scanned — the extension filter "
        "or exemption list is probably wrong"
    )
    for required in ("SKILL.md", "README.md", "CHANGELOG.md"):
        assert required in candidates, f"{required} must be scanned"
