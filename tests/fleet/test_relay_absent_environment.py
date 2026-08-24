"""TC-23 (fleet-wiring Chunk 6, W-NFR-3): chunks 2-5 remain green with
`session-relay` absent from the interpreter.

Nothing in `scripts/fleet` (outside `adapter/claude.py`'s optional,
subprocess-invoked `session_relay_script` enrichment path, which is
Claude-only and orthogonal to the non-relay flows below) ever does
``import session_relay``. This module proves that directly: it blocks
`session_relay` from being importable at all (the same idiom
`test_adapter_portable.py`'s `TestPortableAdapterDegradesWithoutSessionRelay`
already uses for NFR-3's adapter half), then re-exercises one representative
happy-path test drawn from each of `test_observe.py`, `test_config.py`, and
`test_seams.py` — the Chunk 1-5 test files TEST.md names for TC-23 — under
that blocked environment. None of them reference `session_relay` at all, so
this is a proof of independence, not a new behavior.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.fleet import observe
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.config import FleetConfig, resolve_fleet_config

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def session_relay_unimportable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block `session_relay` from being importable for every test in this module."""
    monkeypatch.setitem(sys.modules, "session_relay", None)
    for name in list(sys.modules):
        if "session_relay" in name and name != "session_relay":
            monkeypatch.delitem(sys.modules, name, raising=False)


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "trunk")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-q", "-m", "initial")
    return repo


class TestChunk1ObserveStillGreen:
    """Representative sample from `test_observe.py` (Chunk 1)."""

    def test_dispatch_writes_a_validated_entry(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug = "demo-project"
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
        project_dir = git_repo / "docs" / "superhuman" / slug
        project_dir.mkdir(parents=True)
        (project_dir / "SUPERHUMAN.md").write_text(
            f"**Slug:** {slug}\n**Project-id:** fleet-demo123\n", encoding="utf-8"
        )
        adapter = PortableAdapter(git_repo, slug, local_id="child-session")

        result = observe.observe_dispatch(
            adapter, workspace=git_repo, slug=slug, dispatch_id="child-session", writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id is not None


class TestChunk1ConfigStillGreen:
    """Representative sample from `test_config.py` (Chunk 1)."""

    def test_profile_with_fleet_enabled_true_resolves_to_enabled(self, tmp_path: Path) -> None:
        profile = tmp_path / "profile.yaml"
        profile.write_text(
            "fleet:\n  enabled: true\n  observe_deadline_seconds: 7.5\n", encoding="utf-8"
        )

        cfg = resolve_fleet_config(tmp_path, profile_path=profile)

        assert cfg.enabled is True
        assert isinstance(cfg, FleetConfig)

    def test_no_profile_at_all_resolves_to_disabled(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cfg = resolve_fleet_config(workspace, profile_path=tmp_path / "does-not-exist.yaml")

        assert cfg.enabled is False


class TestChunk2SeamsStillGreen:
    """Representative sample from `test_seams.py` (Chunk 2, the non-relay seam)."""

    def test_handoff_emission_subsection_names_the_literal_command_shape(self) -> None:
        import re

        pm_md = _REPO_ROOT / "roles" / "pm.md"
        text = pm_md.read_text(encoding="utf-8")
        pattern = re.compile(
            r"fleet observe handoff-emit --prompt-file \.\.\. --output-file \.\.\."
        )
        assert pattern.search(text), "roles/pm.md no longer names the handoff-emit command shape"
