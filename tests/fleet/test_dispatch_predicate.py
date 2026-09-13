"""Tests for `scripts/fleet/dispatch_predicate.py` (chunk 7, D5).

Unit-level coverage of the portable Decision C predicate and its
zero/unanimous/disagreement aggregation rule — the harness-specific
transcript parsing that FEEDS this predicate is exercised separately, at
the subprocess level, in `tests/fleet/test_hooks.py`'s `TestDecisionC
Granularity` (TC-50..TC-52).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fleet.dispatch_predicate import leads_with_role_block, resolve_verdict


@pytest.fixture
def roles_dir(tmp_path: Path) -> Path:
    """A synthetic `roles/` directory carrying two well-formed role files."""
    directory = tmp_path / "roles"
    directory.mkdir()
    (directory / "developer.md").write_text(
        "---\nname: developer\ntier: standard\n---\n\n# Developer role\n", encoding="utf-8"
    )
    (directory / "pm.md").write_text("---\nname: pm\n---\n\n# PM role\n", encoding="utf-8")
    return directory


class TestLeadsWithRoleBlock:
    def test_matches_a_real_role_frontmatter(self, roles_dir: Path) -> None:
        prompt = (
            "---\nname: developer\ntier: standard\ndeclared-references:\n"
            "  - references/test-driven-development/SKILL.md\n---\n\n"
            "# Developer role\n\nYou are the Developer for this project...\n"
        )
        assert leads_with_role_block(prompt, roles_dir) is True

    def test_rejects_a_prose_brief(self, roles_dir: Path) -> None:
        prompt = "You are the Developer for this superhuman project...\n"
        assert leads_with_role_block(prompt, roles_dir) is False

    def test_rejects_roles_mentioned_mid_body_not_leading(self, roles_dir: Path) -> None:
        """TC-52's precision case, at the unit level: mentioning `roles/`
        does not satisfy the predicate unless it LEADS the prompt."""
        prompt = (
            "You are a general-purpose research agent. Our project's role "
            "system lives under roles/*.md, e.g. roles/developer.md.\n"
        )
        assert leads_with_role_block(prompt, roles_dir) is False

    def test_rejects_a_frontmatter_naming_a_nonexistent_role(self, roles_dir: Path) -> None:
        prompt = "---\nname: not-a-real-role\n---\n"
        assert leads_with_role_block(prompt, roles_dir) is False

    def test_rejects_frontmatter_with_no_name_line(self, roles_dir: Path) -> None:
        prompt = "---\ntier: standard\n---\n"
        assert leads_with_role_block(prompt, roles_dir) is False

    def test_rejects_a_frontmatter_that_never_closes(self, roles_dir: Path) -> None:
        prompt = "---\nname: developer\ntier: standard\n(no closing delimiter)\n"
        assert leads_with_role_block(prompt, roles_dir) is False

    def test_rejects_empty_prompt(self, roles_dir: Path) -> None:
        assert leads_with_role_block("", roles_dir) is False

    def test_tolerates_leading_bom_and_whitespace(self, roles_dir: Path) -> None:
        prompt = "﻿  \n\n---\nname: developer\n---\n"
        assert leads_with_role_block(prompt, roles_dir) is True

    def test_tolerates_crlf_line_endings(self, roles_dir: Path) -> None:
        prompt = "---\r\nname: developer\r\n---\r\n\r\nBody text.\r\n"
        assert leads_with_role_block(prompt, roles_dir) is True

    def test_unreadable_roles_dir_returns_false(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        prompt = "---\nname: developer\n---\n"
        assert leads_with_role_block(prompt, missing) is False

    def test_roles_dir_raising_oserror_on_glob_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`Path.glob` does not raise `OSError` for a merely-missing
        directory on every platform/Python version this runs under (it
        quietly yields nothing on this one) -- but a real permission error
        mid-iteration on some other platform is exactly an `OSError`, and
        `_existing_role_basenames`'s `except OSError` guards against it.
        Exercised directly here (rather than relying on a platform quirk)
        so the guard is proven, not merely present."""

        def _raise(self: Path, pattern: str):  # noqa: ANN001 - matches Path.glob's signature
            raise OSError("simulated permission error enumerating roles_dir")

        monkeypatch.setattr(Path, "glob", _raise)
        prompt = "---\nname: developer\n---\n"
        assert leads_with_role_block(prompt, tmp_path) is False

    def test_role_set_is_read_from_disk_not_hardcoded(self, tmp_path: Path) -> None:
        """The role set is read from `roles_dir` at call time — a role
        this module has never heard of still matches if the directory
        carries it (chunk 7 PM ruling 3: 'read from the directory, never
        hardcoded')."""
        custom_roles = tmp_path / "custom-roles"
        custom_roles.mkdir()
        (custom_roles / "totally-custom-role.md").write_text(
            "---\nname: totally-custom-role\n---\n", encoding="utf-8"
        )
        prompt = "---\nname: totally-custom-role\n---\n"
        assert leads_with_role_block(prompt, custom_roles) is True

    def test_a_role_file_removed_from_disk_stops_matching(self, tmp_path: Path) -> None:
        roles = tmp_path / "roles"
        roles.mkdir()
        role_file = roles / "developer.md"
        role_file.write_text("---\nname: developer\n---\n", encoding="utf-8")
        prompt = "---\nname: developer\n---\n"
        assert leads_with_role_block(prompt, roles) is True
        role_file.unlink()
        assert leads_with_role_block(prompt, roles) is False


class TestResolveVerdict:
    def test_zero_candidates_is_none(self) -> None:
        assert resolve_verdict([]) is None

    def test_unanimous_true(self) -> None:
        assert resolve_verdict([True, True, True]) is True

    def test_unanimous_false(self) -> None:
        assert resolve_verdict([False, False]) is False

    def test_single_candidate_true(self) -> None:
        assert resolve_verdict([True]) is True

    def test_disagreement_is_none(self) -> None:
        assert resolve_verdict([True, False]) is None

    def test_disagreement_is_none_regardless_of_order(self) -> None:
        assert resolve_verdict([False, False, True]) is None
