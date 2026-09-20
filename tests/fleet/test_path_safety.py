"""Tests for `scripts.fleet.path_safety` -- the shared slug-safety guard
extracted at Phase 3.3 preflight B5 (`observe.py`'s `_validate_slug` had
this check; `role_block.py`'s `record_role_gate_decision` never inherited
it, the same class of defect standing unfixed in a sibling module).
"""

from __future__ import annotations

import sys

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

    def test_empty_slug_is_unsafe(self) -> None:
        """TC-129 (Phase 3.3 preflight RE-RUN item E): an earlier version of
        this function treated `""` as safe (a harmless no-op `Path`
        segment). Reversed: a slug is meant to name one specific project,
        and treating `""` as safe let every misconfigured caller silently
        collapse onto the same `docs/superhuman/fleet` directory instead
        of failing loudly."""
        assert slug_is_safe("") is False

    def test_dot_slug_is_unsafe(self) -> None:
        """TC-129: `"."` is the identical no-real-identity shape as `""`,
        one path segment later."""
        assert slug_is_safe(".") is False

    @pytest.mark.parametrize(
        "slug",
        [
            "C:evil",
            "D:evil",
            "C:",
            "c:evil",
        ],
    )
    def test_drive_qualified_slug_is_unsafe(self, slug: str) -> None:
        """TC-129: a Windows drive-relative/drive-qualified slug contains
        none of `/`, `\\`, or `..`, so the original three-character check
        missed it entirely -- but joining it onto ANY base path with
        `pathlib` REPLACES that base path outright rather than extending
        it: `Path("D:/ws") / "C:evil" == Path("C:evil")` (verified). A
        caller building `<workspace>/docs/superhuman/<slug>/fleet` from
        this slug lands completely outside the workspace, silently."""
        assert slug_is_safe(slug) is False

    def test_drive_qualified_slug_actually_escapes_a_joined_path(self) -> None:
        """Confirms the mechanism `test_drive_qualified_slug_is_unsafe`
        exists to reject, so the regression this guards against is
        traceable in the test suite itself, not just in a comment.

        The join-based escape below is a WINDOWS-only `pathlib` behavior:
        `Path("D:/ws") / "C:evil"` replaces the base path outright only
        under `WindowsPath`/`PureWindowsPath` semantics, where a leading
        `"C:"` segment is a drive. On POSIX, `Path` is `PosixPath`, which
        has no drive concept at all -- `"C:evil"` is an ordinary path
        component there, so joining it EXTENDS the base path instead of
        replacing it, and the escape this sanity check exists to confirm
        simply does not reproduce on that platform. The product guard
        itself is unconditional regardless (`path_safety.slug_is_safe`
        evaluates `PureWindowsPath(slug).drive` on every OS -- see its
        docstring), so the assertion below checks that directly first,
        independent of platform; only the join-mechanics sanity check
        after it is Windows-only.
        """
        assert slug_is_safe("C:evil") is False

        if sys.platform != "win32":
            pytest.skip(
                "the Path.__truediv__ drive-replacement escape is Windows-only "
                "pathlib behavior (WindowsPath/PureWindowsPath); on POSIX "
                "'C:evil' is an ordinary path segment with no drive semantics, "
                "so joining it does not escape the base path -- the product "
                "guard itself is still verified above, unconditionally"
            )

        from pathlib import Path

        joined = Path("D:/fake-workspace") / "docs" / "superhuman" / "C:evil" / "fleet"
        assert not joined.is_relative_to(Path("D:/fake-workspace")), (
            "sanity check failed: a drive-qualified slug must escape a joined path "
            "for this to be the defect item E describes"
        )
