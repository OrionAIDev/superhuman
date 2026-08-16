"""Tests for `scripts.fleet.config` — TC-1: enabled / disabled / absent / malformed profile.

`resolve_fleet_config` must never raise (W-FR-7, W-FR-8) and must return a
distinct, human-readable `reason` for every disabled case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fleet.config import FleetConfig, resolve_fleet_config


def test_no_profile_at_all_resolves_to_disabled_with_a_reason(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing_profile = tmp_path / "does-not-exist.yaml"

    cfg = resolve_fleet_config(workspace, profile_path=missing_profile)

    assert cfg.enabled is False
    assert cfg.reason
    assert isinstance(cfg, FleetConfig)


def test_profile_with_no_fleet_block_resolves_to_disabled_with_a_distinct_reason(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("version: 1\nladder: []\n", encoding="utf-8")

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is False
    assert "fleet" in cfg.reason


def test_profile_with_fleet_enabled_true_resolves_to_enabled_with_settings(
    tmp_path: Path,
) -> None:
    manifest_dir = tmp_path / "custom-fleet-dir"
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "fleet:\n"
        "  enabled: true\n"
        f"  manifest_dir: {manifest_dir.as_posix()}\n"
        "  observe_deadline_seconds: 7.5\n"
        "  git_timeout_seconds: 0.5\n"
        "  lock_timeout_seconds: 1.2\n",
        encoding="utf-8",
    )

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is True
    assert cfg.manifest_dir == manifest_dir
    assert cfg.observe_deadline_seconds == 7.5
    assert cfg.git_timeout_seconds == 0.5
    assert cfg.lock_timeout_seconds == 1.2


def test_profile_with_fleet_enabled_false_resolves_to_disabled(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet:\n  enabled: false\n", encoding="utf-8")

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is False


def test_truncated_invalid_yaml_resolves_to_disabled_never_raises(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet: [enabled: true\n  broken: [[[", encoding="utf-8")

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is False
    assert cfg.reason


def test_non_utf8_profile_resolves_to_disabled_never_raises(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_bytes(b"fleet:\n  enabled: true\n  name: \xff\xfe\x00\x01")

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is False
    assert cfg.reason


def test_profile_that_parses_to_a_scalar_resolves_to_disabled(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("just a plain string\n", encoding="utf-8")

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is False


def test_fleet_block_not_a_mapping_resolves_to_disabled(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet: [1, 2, 3]\n", encoding="utf-8")

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is False


def test_all_disabled_reasons_are_mutually_distinct(tmp_path: Path) -> None:
    """W-FR-8: reason strings must be diagnostic, not interchangeable."""
    no_profile = resolve_fleet_config(tmp_path, profile_path=tmp_path / "missing.yaml")

    no_fleet_block = tmp_path / "a.yaml"
    no_fleet_block.write_text("version: 1\n", encoding="utf-8")
    r_no_block = resolve_fleet_config(tmp_path, profile_path=no_fleet_block)

    not_enabled = tmp_path / "b.yaml"
    not_enabled.write_text("fleet:\n  enabled: false\n", encoding="utf-8")
    r_not_enabled = resolve_fleet_config(tmp_path, profile_path=not_enabled)

    reasons = {no_profile.reason, r_no_block.reason, r_not_enabled.reason}
    assert len(reasons) == 3


def test_defaults_apply_when_only_enabled_is_set(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is True
    assert cfg.manifest_dir is None
    assert cfg.observe_deadline_seconds == 5.0
    assert cfg.git_timeout_seconds == 0.25
    assert cfg.lock_timeout_seconds == 0.8


def test_no_profile_found_via_the_normal_search_resolves_to_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises the `profile_path=None` -> `find_profile()` search path directly."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("scripts.superhuman_profile.find_profile", lambda cwd: None)

    cfg = resolve_fleet_config(workspace)

    assert cfg.enabled is False
    assert "no profile found" in cfg.reason


def test_superhuman_profile_module_unavailable_resolves_to_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "scripts.superhuman_profile", None)

    cfg = resolve_fleet_config(tmp_path)

    assert cfg.enabled is False
    assert "unavailable" in cfg.reason


def test_pyyaml_unavailable_resolves_to_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is False
    assert "PyYAML" in cfg.reason


def test_non_positive_overrides_fall_back_to_defaults(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "fleet:\n  enabled: true\n  observe_deadline_seconds: -1\n  git_timeout_seconds: true\n",
        encoding="utf-8",
    )

    cfg = resolve_fleet_config(tmp_path, profile_path=profile)

    assert cfg.enabled is True
    assert cfg.observe_deadline_seconds == 5.0
    assert cfg.git_timeout_seconds == 0.25
