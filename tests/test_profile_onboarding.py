"""Tests for the onboarding surface: discovery, proposal, `init`, `doctor`.

These cover the v0.9.0 additions. The load-bearing assertions are the two
authoring rules the proposal must never violate — deny rungs before permissive
ones, and markers only on protected rungs — because getting either wrong turns a
safe ladder into a permissive one without any visible symptom.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import superhuman_profile as sp  # noqa: E402

RESOLVER = Path(__file__).resolve().parents[1] / "scripts" / "superhuman_profile.py"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Build a synthetic multi-environment project.

    Args:
        tmp_path: Test temp directory.

    Returns:
        The project root.
    """
    root = tmp_path / "proj"
    (root / ".github" / "workflows").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=root, check=True, capture_output=True,
    )
    for name in (".env.dev", ".env.staging", ".env.production", "docker-compose-lab.yml"):
        (root / name).touch()
    (root / ".github" / "workflows" / "cd.yml").write_text(
        "jobs:\n  deploy:\n    environment: production\n    steps: []\n", encoding="utf-8"
    )
    return root


def _cli(args: list[str], extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the resolver CLI with a clean environment.

    Args:
        args: CLI arguments.
        extra: Additional environment variables.

    Returns:
        The completed process.
    """
    env = dict(os.environ)
    env.pop("SUPERHUMAN_PROFILE", None)
    env.pop("SUPERHUMAN_REQUIRE_PROFILE", None)
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(RESOLVER), *args],
        capture_output=True, text=True, env=env, check=False,
    )


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_discovery_finds_every_local_signal(project: Path) -> None:
    """dotenv, compose, and CI-workflow environments are all picked up."""
    disc = sp.discover(project, offline=True)
    assert disc.is_repo
    assert disc.default_branch == "main"
    assert set(disc.dotenv_environments) == {"dev", "staging", "production"}
    assert disc.compose_environments == ("lab",)
    assert disc.workflow_environments == ("production",)


def test_discovery_deduplicates_across_signals(project: Path) -> None:
    """An environment seen twice (dotenv + workflow) appears once."""
    names = sp.discover(project, offline=True).deployment_names
    assert names.count("production") == 1
    assert set(names) == {"dev", "staging", "production", "lab"}


def test_discovery_is_offline_and_total_when_not_a_repo(tmp_path: Path) -> None:
    """A plain directory yields an empty discovery rather than an error."""
    disc = sp.discover(tmp_path, offline=True)
    assert not disc.is_repo and not disc.probed_network
    assert disc.deployment_names == ()


# --------------------------------------------------------------------------- #
# Proposal — the two authoring rules
# --------------------------------------------------------------------------- #


def test_proposal_orders_deny_rungs_before_permissive(project: Path) -> None:
    """Deny rungs must precede permissive ones so ties resolve to the safer verdict."""
    ladder = sp.propose_ladder(sp.discover(project, offline=True))
    names = [r["name"] for r in ladder]
    denies = [
        i for i, r in enumerate(ladder)
        if r["approvals"].get("act_unattended") == "never" and "path_segments" in r["detect"]
    ]
    allows = [
        i for i, r in enumerate(ladder)
        if r["approvals"].get("act_unattended") == ["self"] and "path_segments" in r["detect"]
    ]
    assert denies and allows, f"expected both kinds in {names}"
    assert max(denies) < min(allows), f"deny rungs must come first, got {names}"


def test_proposal_never_marks_a_permissive_rung(project: Path) -> None:
    """`env_marker` outranks paths, so it must not appear on a permissive rung.

    A marker on a permissive rung would let `## Environment: dev` inside a
    production path override the production block — the exact inversion the
    ordering rules exist to prevent.
    """
    for rung in sp.propose_ladder(sp.discover(project, offline=True)):
        permissive = rung["approvals"].get("act_unattended") == ["self"]
        if permissive:
            assert "env_marker" not in rung["detect"], (
                f"permissive rung {rung['name']!r} must not carry an env_marker"
            )


def test_proposal_classifies_protected_names(project: Path) -> None:
    """production/staging are denied unattended work; dev is permitted."""
    by_name = {r["name"]: r for r in sp.propose_ladder(sp.discover(project, offline=True))}
    assert by_name["production"]["approvals"]["act_unattended"] == "never"
    assert by_name["production"]["kind"] == "production"
    assert by_name["staging"]["approvals"]["act_unattended"] == "never"
    assert by_name["staging"]["kind"] == "acceptance"
    assert by_name["dev"]["approvals"]["act_unattended"] == ["self"]


def test_proposal_always_ends_with_a_catch_all(tmp_path: Path) -> None:
    """Even with zero signals the ladder resolves somewhere."""
    ladder = sp.propose_ladder(sp.discover(tmp_path, offline=True))
    assert ladder, "proposal must never be empty"
    assert ladder[-1]["detect"].get("default") is True


def test_proposal_leaves_trunk_promotion_undeclared_without_evidence(project: Path) -> None:
    """Absent a protection rule, trunk's promote_into is left null, not guessed."""
    by_name = {r["name"]: r for r in sp.propose_ladder(sp.discover(project, offline=True))}
    assert by_name["trunk"]["approvals"]["promote_into"] is None


# --------------------------------------------------------------------------- #
# Rendering round-trip
# --------------------------------------------------------------------------- #


def test_rendered_profile_loads_back(project: Path, tmp_path: Path) -> None:
    """Whatever init renders must parse under the same schema it will be read by."""
    disc = sp.discover(project, offline=True)
    text = sp.render_profile(sp.propose_ladder(disc), disc)
    out = tmp_path / "rendered.yaml"
    out.write_text(text, encoding="utf-8")
    profile = sp.load_profile(out)
    assert [r.name for r in profile.ladder] == [r["name"] for r in sp.propose_ladder(disc)]


def test_rendered_profile_documents_the_authoring_rules(project: Path) -> None:
    """The generated file must carry the rules an operator will later edit against."""
    disc = sp.discover(project, offline=True)
    text = sp.render_profile(sp.propose_ladder(disc), disc)
    assert "Narrower rungs first" in text
    assert "env_marker" in text and "outrank" in text
    assert "proposal is not a policy" in text


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #


def test_init_dry_run_writes_nothing(project: Path, tmp_path: Path) -> None:
    """--dry-run prints the proposal and leaves the filesystem alone."""
    dest = tmp_path / "out.yaml"
    proc = _cli(["init", str(project), "--dry-run", "--offline", "--output", str(dest)])
    assert proc.returncode == 0, proc.stderr
    assert "version: 1" in proc.stdout
    assert not dest.exists(), "--dry-run must not write"


def test_init_writes_and_reports_undeclared_cells(project: Path, tmp_path: Path) -> None:
    """A real run writes the file and names every cell left undeclared."""
    dest = tmp_path / "out.yaml"
    proc = _cli(["init", str(project), "--offline", "--output", str(dest)])
    assert proc.returncode == 0, proc.stderr
    assert dest.is_file()
    assert "trunk.promote_into" in proc.stdout
    sp.load_profile(dest)


def test_init_refuses_to_clobber(project: Path, tmp_path: Path) -> None:
    """An existing profile is never overwritten without --force."""
    dest = tmp_path / "out.yaml"
    dest.write_text("version: 1\nladder:\n  - name: a\n    detect: {default: true}\n",
                    encoding="utf-8")
    proc = _cli(["init", str(project), "--offline", "--output", str(dest)])
    assert proc.returncode == sp.EXIT_USAGE
    assert "refusing to overwrite" in proc.stderr
    assert "name: a" in dest.read_text(encoding="utf-8"), "original must survive"

    forced = _cli(["init", str(project), "--offline", "--output", str(dest), "--force"])
    assert forced.returncode == 0
    assert "name: a" not in dest.read_text(encoding="utf-8")


@pytest.mark.parametrize("preset", ["solo-git", "classic-3tier"])
def test_init_preset_round_trips(preset: str, tmp_path: Path) -> None:
    """Every shipped preset installs and loads."""
    dest = tmp_path / f"{preset}.yaml"
    proc = _cli(["init", ".", "--preset", preset, "--output", str(dest)])
    assert proc.returncode == 0, proc.stderr
    profile = sp.load_profile(dest)
    assert profile.ladder
    assert any(r.detect.get("default") for r in profile.ladder), (
        f"preset {preset} must have a catch-all rung"
    )


def test_init_unknown_preset_lists_the_real_ones(tmp_path: Path) -> None:
    """A typo'd preset name fails loudly and says what exists."""
    proc = _cli(["init", ".", "--preset", "nope", "--output", str(tmp_path / "x.yaml")])
    assert proc.returncode == sp.EXIT_USAGE
    assert "solo-git" in proc.stderr


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def test_doctor_reports_resolution(project: Path, tmp_path: Path) -> None:
    """doctor names the resolved rung and both policies."""
    dest = tmp_path / "p.yaml"
    _cli(["init", str(project), "--offline", "--output", str(dest)])
    proc = _cli(["doctor", str(project)], extra={"SUPERHUMAN_PROFILE": str(dest)})
    assert proc.returncode == 0, proc.stderr
    assert "resolved rung" in proc.stdout
    assert "act_unattended" in proc.stdout and "promote_into" in proc.stdout


def test_doctor_fails_closed_when_profile_required_and_missing(tmp_path: Path) -> None:
    """With REQUIRE_PROFILE set and nothing found, doctor fails rather than reassures."""
    root = tmp_path / "empty"
    root.mkdir()
    proc = _cli(["doctor", str(root)], extra={
        "SUPERHUMAN_REQUIRE_PROFILE": "1",
        "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
    })
    assert proc.returncode == sp.EXIT_USAGE
    assert "no profile found" in proc.stderr


def test_doctor_flags_the_ref_only_gap(tmp_path: Path) -> None:
    """A ladder with no location rungs must say production is undetectable.

    This is the honest-gap message from spec §7.4: git refs cannot see a
    deployment target, and doctor must not let a ref-only ladder imply coverage
    it does not have.
    """
    dest = tmp_path / "refonly.yaml"
    _cli(["init", ".", "--preset", "solo-git", "--output", str(dest)])
    proc = _cli(["doctor", str(tmp_path)], extra={"SUPERHUMAN_PROFILE": str(dest)})
    assert proc.returncode == 0, proc.stderr
    assert "no deployment rungs declared" in proc.stdout
    assert "NOT detectable" in proc.stdout


def test_doctor_surfaces_the_agent_only_approver_warning(tmp_path: Path) -> None:
    """A protected rung approvable without a human warns, but does not block."""
    dest = tmp_path / "risky.yaml"
    dest.write_text(
        "version: 1\nladder:\n"
        "  - name: prod\n    detect: {path_segments: [prod]}\n"
        "    approvals: {act_unattended: never, promote_into: [agent:bot]}\n",
        encoding="utf-8",
    )
    proc = _cli(["doctor", str(tmp_path / "prod")], extra={"SUPERHUMAN_PROFILE": str(dest)})
    assert proc.returncode == 0, "a warning must not block"
    assert "WARNING" in proc.stdout and "no 'human' approver" in proc.stdout
