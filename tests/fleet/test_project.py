"""Tests for `scripts.fleet.project` — TC-2: identity present / absent / unparseable.

`read_project_identity` must never raise and never invent an id (W-FR-6).
"""

from __future__ import annotations

from pathlib import Path

from scripts.fleet.project import read_project_identity


def _write_superhuman_md(workspace: Path, slug: str, body: str) -> None:
    project_dir = workspace / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "SUPERHUMAN.md").write_text(body, encoding="utf-8")


def test_valid_front_matter_returns_project_id_and_slug_exactly_as_written(
    tmp_path: Path,
) -> None:
    _write_superhuman_md(
        tmp_path,
        "fleet-wiring",
        "# Superhuman: Fleet wiring\n\n"
        "**Slug:** fleet-wiring\n"
        "**Project-id:** fleet-abc123\n"
        "**Started:** 2026-08-16T00:00:00Z\n",
    )

    identity = read_project_identity(tmp_path, "fleet-wiring")

    assert identity == ("fleet-abc123", "fleet-wiring")


def test_missing_file_returns_none(tmp_path: Path) -> None:
    identity = read_project_identity(tmp_path, "no-such-project")

    assert identity is None


def test_front_matter_present_but_no_project_id_key_returns_none(tmp_path: Path) -> None:
    _write_superhuman_md(tmp_path, "demo", "**Slug:** demo\n**Started:** 2026-08-16\n")

    identity = read_project_identity(tmp_path, "demo")

    assert identity is None


def test_front_matter_present_but_no_slug_key_returns_none(tmp_path: Path) -> None:
    _write_superhuman_md(tmp_path, "demo", "**Project-id:** fleet-xyz\n")

    identity = read_project_identity(tmp_path, "demo")

    assert identity is None


def test_malformed_non_utf8_file_returns_none_never_raises(tmp_path: Path) -> None:
    project_dir = tmp_path / "docs" / "superhuman" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_bytes(b"\xff\xfe\x00\x01garbage")

    identity = read_project_identity(tmp_path, "demo")

    assert identity is None


def test_blank_project_id_value_returns_none(tmp_path: Path) -> None:
    _write_superhuman_md(tmp_path, "demo", "**Slug:** demo\n**Project-id:**    \n")

    identity = read_project_identity(tmp_path, "demo")

    assert identity is None


def test_slug_rename_returns_the_file_slug_not_the_argument(tmp_path: Path) -> None:
    """W-FR-6: identity is carried from the file, not re-derived from the caller."""
    _write_superhuman_md(
        tmp_path, "old-slug", "**Slug:** new-slug\n**Project-id:** fleet-renamed\n"
    )

    identity = read_project_identity(tmp_path, "old-slug")

    assert identity == ("fleet-renamed", "new-slug")


def test_project_id_present_but_slug_value_blank_returns_none(tmp_path: Path) -> None:
    _write_superhuman_md(tmp_path, "demo", "**Project-id:** fleet-xyz\n**Slug:**   \n")

    identity = read_project_identity(tmp_path, "demo")

    assert identity is None


def test_never_invents_an_id_for_an_unreadable_directory(tmp_path: Path) -> None:
    # No docs/superhuman/<slug> directory at all.
    identity = read_project_identity(tmp_path, "totally-absent-project")

    assert identity is None
