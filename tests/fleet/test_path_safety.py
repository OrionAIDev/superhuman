"""Tests for `scripts.fleet.path_safety` -- the shared slug-safety guard
extracted at Phase 3.3 preflight B5 (`observe.py`'s `_validate_slug` had
this check; `role_block.py`'s `record_role_gate_decision` never inherited
it, the same class of defect standing unfixed in a sibling module).
"""

from __future__ import annotations

import pytest

from scripts.fleet.path_safety import slug_is_safe


class TestSlugIsSafe:
    @pytest.mark.parametrize(
        "slug",
        [
            "demo-project",
            "fleet-deterministic-seams",
            "a",
            "chunk9_docs",
        ],
    )
    def test_ordinary_slugs_are_safe(self, slug: str) -> None:
        assert slug_is_safe(slug) is True

    @pytest.mark.parametrize(
        "slug",
        [
            "../../evil",
            "..",
            "a/../../b",
            "foo/bar",
            "foo\\bar",
            "/etc/passwd",
            "C:\\Windows",
        ],
    )
    def test_traversal_and_separator_slugs_are_unsafe(self, slug: str) -> None:
        assert slug_is_safe(slug) is False

    def test_empty_slug_is_safe(self) -> None:
        """An empty slug carries no separator or `..` segment -- callers
        that build a path from it get a harmless no-op path component
        (`Path` drops an empty segment), not an escape. Rejecting it is not
        this function's job; a caller that cares about emptiness checks it
        separately."""
        assert slug_is_safe("") is True
