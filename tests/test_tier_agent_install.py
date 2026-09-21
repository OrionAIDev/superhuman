"""Tests for roadmap#275: `effort`/`raised_effort` in the `models:` schema,
and `models install-agents` (Claude Code tier-agent generation, Hermes
`delegation:` merge, and the `doctor` staleness check).

Companion to `tests/test_profile_onboarding.py`, which already covers the
`models:` block's primary/fallback machinery this file builds on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import superhuman_profile as sp  # noqa: E402


# --------------------------------------------------------------------------- #
# effort / raised_effort: parse, validate, round-trip, backward compat
# --------------------------------------------------------------------------- #


def test_effort_round_trips_through_load_profile(tmp_path: Path) -> None:
    """A tier with `effort` and `raised_effort` loads back unchanged."""
    dest = tmp_path / "profile.yaml"
    dest.write_text(
        "version: 1\n"
        "models:\n"
        "  most_capable:\n"
        "    primary: opus\n"
        "    fallback: sonnet\n"
        "    effort: medium\n"
        "    raised_effort: high\n"
        "  cheap:\n"
        "    primary: haiku\n"
        "    effort: n/a\n",
        encoding="utf-8",
    )
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"] == {
        "primary": "opus", "fallback": "sonnet", "effort": "medium", "raised_effort": "high",
    }
    assert profile.models["cheap"] == {"primary": "haiku", "fallback": None, "effort": "n/a"}


@pytest.mark.parametrize("effort", list(sp.MODEL_EFFORTS) + [sp.MODEL_PLACEHOLDER])
def test_every_valid_effort_value_is_accepted(tmp_path: Path, effort: str) -> None:
    """Every value in MODEL_EFFORTS, plus the PROMPT_ME placeholder, is valid."""
    dest = tmp_path / "profile.yaml"
    dest.write_text(
        f"version: 1\nmodels:\n  standard:\n    primary: sonnet\n    effort: {effort}\n",
        encoding="utf-8",
    )
    profile = sp.load_profile(dest)
    assert profile.models["standard"]["effort"] == effort


def test_bad_effort_value_fails_loud(tmp_path: Path) -> None:
    """An effort value outside MODEL_EFFORTS (and not PROMPT_ME) raises."""
    dest = tmp_path / "profile.yaml"
    dest.write_text(
        "version: 1\nmodels:\n  standard:\n    primary: sonnet\n    effort: ultra-mega\n",
        encoding="utf-8",
    )
    with pytest.raises(sp.ProfileError, match="effort"):
        sp.load_profile(dest)


def test_n_a_is_not_a_valid_raised_effort(tmp_path: Path) -> None:
    """`raised_effort: n/a` is rejected — a raised tier is always a real level."""
    dest = tmp_path / "profile.yaml"
    dest.write_text(
        "version: 1\nmodels:\n  most_capable:\n    primary: opus\n    raised_effort: n/a\n",
        encoding="utf-8",
    )
    with pytest.raises(sp.ProfileError, match="raised_effort"):
        sp.load_profile(dest)


def test_legacy_profile_with_no_effort_loads_fine(tmp_path: Path) -> None:
    """Backward compat: a pre-roadmap#275 profile (no effort/raised_effort at
    all) loads unchanged, and the loaded entry carries no `effort` key —
    reading it is `.get("effort")` returning None, never a KeyError."""
    dest = tmp_path / "profile.yaml"
    dest.write_text(
        "version: 1\nmodels:\n  standard:\n    primary: sonnet\n    fallback: haiku\n",
        encoding="utf-8",
    )
    profile = sp.load_profile(dest)
    assert profile.models["standard"] == {"primary": "sonnet", "fallback": "haiku"}
    assert profile.models["standard"].get("effort") is None


def test_legacy_bare_string_tier_has_no_effort_key(tmp_path: Path) -> None:
    """The pre-ADR-6 bare-string tier form still normalizes with no effort key."""
    dest = tmp_path / "profile.yaml"
    dest.write_text("version: 1\nmodels:\n  most_capable: opus\n", encoding="utf-8")
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"] == {"primary": "opus", "fallback": None}


# --------------------------------------------------------------------------- #
# write_models_block / `models set` with effort
# --------------------------------------------------------------------------- #


def test_write_models_block_with_effort_and_raised_effort(tmp_path: Path) -> None:
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(
        dest,
        {"most_capable": {
            "primary": "opus", "fallback": "sonnet", "effort": "medium", "raised_effort": "high",
        }},
    )
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"] == {
        "primary": "opus", "fallback": "sonnet", "effort": "medium", "raised_effort": "high",
    }


def test_write_models_block_answer_without_effort_omits_the_key(tmp_path: Path) -> None:
    """An answer naming only primary/fallback writes no `effort` key at all —
    preserves the pre-roadmap#275 behavior for callers that don't pass effort."""
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, {"standard": {"primary": "sonnet", "fallback": "haiku"}})
    profile = sp.load_profile(dest)
    assert profile.models["standard"] == {"primary": "sonnet", "fallback": "haiku"}
    assert "effort" not in profile.models["standard"]


def test_write_models_block_declined_tier_gets_effort_placeholder(tmp_path: Path) -> None:
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, decline=True)
    profile = sp.load_profile(dest)
    for tier in sp.MODEL_TIERS:
        assert profile.models[tier]["effort"] == sp.MODEL_PLACEHOLDER


def test_write_models_block_untouched_new_tier_gets_effort_placeholder(tmp_path: Path) -> None:
    """A tier neither answered nor declined, and absent from the file, still
    gets an explicit `effort: PROMPT_ME` — fail-safe, matching `primary`."""
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, {"most_capable": {"primary": "opus", "effort": "medium"}})
    profile = sp.load_profile(dest)
    assert profile.models["standard"]["effort"] == sp.MODEL_PLACEHOLDER
    assert profile.models["cheap"]["effort"] == sp.MODEL_PLACEHOLDER


def test_models_set_cli_accepts_effort_and_raised_effort(tmp_path: Path) -> None:
    dest = tmp_path / "profile.yaml"
    import json
    import os
    import subprocess

    env = dict(os.environ)
    env.pop("SUPERHUMAN_PROFILE", None)
    env.pop("SUPERHUMAN_REQUIRE_PROFILE", None)
    answers = json.dumps({
        "most_capable": {"primary": "opus", "fallback": "sonnet",
                          "effort": "medium", "raised_effort": "high"},
    })
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "superhuman_profile.py"),
         "models", "set", "--profile", str(dest), "--answers-json", answers,
         "--decline", "standard,cheap"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == sp.EXIT_OK, result.stderr
    assert "effort=medium" in result.stdout
    assert "raised_effort=high" in result.stdout
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"]["effort"] == "medium"
    assert profile.models["most_capable"]["raised_effort"] == "high"


# --------------------------------------------------------------------------- #
# models install-agents --harness claude-code
# --------------------------------------------------------------------------- #


@pytest.fixture()
def full_profile(tmp_path: Path) -> Path:
    """A profile with all three tiers configured, most_capable has raised_effort."""
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, {
        "most_capable": {"primary": "opus", "fallback": "sonnet",
                          "effort": "medium", "raised_effort": "high"},
        "standard": {"primary": "sonnet", "fallback": "haiku", "effort": "medium"},
        "cheap": {"primary": "haiku", "fallback": "haiku", "effort": "n/a"},
    })
    return dest


def test_install_claude_code_agents_writes_four_files(tmp_path: Path, full_profile: Path) -> None:
    profile = sp.load_profile(full_profile)
    agents_dir = tmp_path / "agents"
    messages = sp.install_claude_code_agents(profile, agents_dir, dry_run=False)
    written = sorted(p.name for p in agents_dir.glob("*.md"))
    assert written == sorted([
        "superhuman-tier-most-capable-subagent.md",
        "superhuman-tier-most-capable-high-effort-subagent.md",
        "superhuman-tier-standard-subagent.md",
        "superhuman-tier-cheap-subagent.md",
    ])
    assert any("wrote" in m for m in messages)


def test_install_claude_code_agents_n_a_omits_effort_line(tmp_path: Path, full_profile: Path) -> None:
    profile = sp.load_profile(full_profile)
    agents_dir = tmp_path / "agents"
    sp.install_claude_code_agents(profile, agents_dir, dry_run=False)
    content = (agents_dir / "superhuman-tier-cheap-subagent.md").read_text(encoding="utf-8")
    assert "effort:" not in content
    assert "model: haiku" in content
    assert sp.GENERATED_AGENT_MARKER in content


def test_install_claude_code_agents_real_effort_included(tmp_path: Path, full_profile: Path) -> None:
    profile = sp.load_profile(full_profile)
    agents_dir = tmp_path / "agents"
    sp.install_claude_code_agents(profile, agents_dir, dry_run=False)
    content = (agents_dir / "superhuman-tier-standard-subagent.md").read_text(encoding="utf-8")
    assert "effort: medium" in content
    raised_content = (
        agents_dir / "superhuman-tier-most-capable-high-effort-subagent.md"
    ).read_text(encoding="utf-8")
    assert "effort: high" in raised_content
    assert "model: opus" in raised_content


def test_install_claude_code_agents_missing_raised_effort_skips(tmp_path: Path) -> None:
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, {
        "most_capable": {"primary": "opus", "effort": "medium"},  # no raised_effort
        "standard": {"primary": "sonnet", "effort": "medium"},
        "cheap": {"primary": "haiku", "effort": "n/a"},
    })
    profile = sp.load_profile(dest)
    agents_dir = tmp_path / "agents"
    messages = sp.install_claude_code_agents(profile, agents_dir, dry_run=False)
    assert not (agents_dir / "superhuman-tier-most-capable-high-effort-subagent.md").is_file()
    assert any("raised_effort" in m and "skip" in m for m in messages)


def test_install_claude_code_agents_prompt_me_tier_skipped(tmp_path: Path) -> None:
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, decline=True)  # every tier PROMPT_ME
    profile = sp.load_profile(dest)
    agents_dir = tmp_path / "agents"
    messages = sp.install_claude_code_agents(profile, agents_dir, dry_run=False)
    assert list(agents_dir.glob("*.md")) == [] if agents_dir.is_dir() else True
    assert all("skip" in m for m in messages)
    assert len(messages) == 4


def test_install_claude_code_agents_refuses_to_clobber_handwritten_file(
    tmp_path: Path, full_profile: Path
) -> None:
    profile = sp.load_profile(full_profile)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    handwritten = agents_dir / "superhuman-tier-standard-subagent.md"
    handwritten.write_text("---\nname: hand-written\n---\nnot generated\n", encoding="utf-8")
    with pytest.raises(sp.ProfileError, match="hand-written"):
        sp.install_claude_code_agents(profile, agents_dir, dry_run=False)
    # untouched
    assert handwritten.read_text(encoding="utf-8") == "---\nname: hand-written\n---\nnot generated\n"


def test_install_claude_code_agents_is_idempotent(tmp_path: Path, full_profile: Path) -> None:
    profile = sp.load_profile(full_profile)
    agents_dir = tmp_path / "agents"
    sp.install_claude_code_agents(profile, agents_dir, dry_run=False)
    first = {p.name: p.read_text(encoding="utf-8") for p in agents_dir.glob("*.md")}
    sp.install_claude_code_agents(profile, agents_dir, dry_run=False)  # re-run, no error
    second = {p.name: p.read_text(encoding="utf-8") for p in agents_dir.glob("*.md")}
    assert first == second


def test_install_claude_code_agents_dry_run_writes_nothing(tmp_path: Path, full_profile: Path) -> None:
    profile = sp.load_profile(full_profile)
    agents_dir = tmp_path / "agents"
    messages = sp.install_claude_code_agents(profile, agents_dir, dry_run=True)
    assert not agents_dir.exists()
    assert any("would write" in m for m in messages)


# --------------------------------------------------------------------------- #
# models install-agents --harness hermes
# --------------------------------------------------------------------------- #


def test_merge_hermes_delegation_preserves_other_content(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "# top comment\n"
        "some_other_key: value\n"
        "\n"
        "delegation:\n"
        "  max_concurrent_children: 3\n"
        "  subagent_auto_approve: true\n"
        "\n"
        "another_top_level:\n"
        "  nested: true\n",
        encoding="utf-8",
    )
    method, new_text = sp.merge_hermes_delegation(
        config, {"model": "sonnet", "reasoning_effort": "high"}, dry_run=True
    )
    assert "text splice" in method or "ruamel" in method
    assert "# top comment" in new_text
    assert "some_other_key: value" in new_text
    assert "another_top_level" in new_text
    assert "nested: true" in new_text
    data = yaml.safe_load(new_text)
    assert data["delegation"]["max_concurrent_children"] == 3
    assert data["delegation"]["subagent_auto_approve"] is True
    assert data["delegation"]["model"] == "sonnet"
    assert data["delegation"]["reasoning_effort"] == "high"
    assert data["some_other_key"] == "value"
    assert data["another_top_level"] == {"nested": True}


def test_merge_hermes_delegation_creates_file_and_block_if_absent(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    method, new_text = sp.merge_hermes_delegation(config, {"model": "sonnet"}, dry_run=False)
    assert config.is_file()
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["delegation"]["model"] == "sonnet"


def test_merge_hermes_delegation_dry_run_writes_nothing(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    sp.merge_hermes_delegation(config, {"model": "sonnet"}, dry_run=True)
    assert not config.is_file()


def test_cmd_install_agents_hermes_omits_effort_for_n_a(tmp_path: Path) -> None:
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, {"standard": {"primary": "sonnet", "effort": "n/a"}},
                           decline=["most_capable", "cheap"])
    config = tmp_path / "config.yaml"
    import sys as _sys
    argv = [
        "models", "install-agents", "--harness", "hermes",
        "--profile", str(dest), "--hermes-config", str(config),
    ]
    parser = sp.build_parser()
    args = parser.parse_args(argv)
    rc = args.func(args)
    assert rc == sp.EXIT_OK
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["delegation"]["model"] == "sonnet"
    assert "reasoning_effort" not in data["delegation"]


def test_cmd_install_agents_hermes_requires_hermes_config(tmp_path: Path) -> None:
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, {"standard": {"primary": "sonnet", "effort": "medium"}})
    parser = sp.build_parser()
    args = parser.parse_args(
        ["models", "install-agents", "--harness", "hermes", "--profile", str(dest)]
    )
    with pytest.raises(sp.ProfileError, match="hermes-config"):
        args.func(args)


# --------------------------------------------------------------------------- #
# doctor staleness detection
# --------------------------------------------------------------------------- #


def test_doctor_detects_missing_and_stale_and_ok(tmp_path: Path, full_profile: Path) -> None:
    profile = sp.load_profile(full_profile)
    agents_dir = tmp_path / "agents"

    lines, problems = sp._claude_code_tier_agent_status(profile, agents_dir)
    assert problems == 4  # nothing installed yet: all MISSING (raised is real here)
    assert all("MISSING" in line for line in lines)

    sp.install_claude_code_agents(profile, agents_dir, dry_run=False)
    lines, problems = sp._claude_code_tier_agent_status(profile, agents_dir)
    assert problems == 0
    assert all("ok" in line for line in lines)

    # Mutate the profile (new effort) without re-running install-agents.
    sp.write_models_block(full_profile, {
        "standard": {"primary": "sonnet", "fallback": "haiku", "effort": "high"},
    })
    stale_profile = sp.load_profile(full_profile)
    lines, problems = sp._claude_code_tier_agent_status(stale_profile, agents_dir)
    assert problems == 1
    assert any("STALE" in line and "standard" in line for line in lines)


def test_doctor_never_fails_with_no_models_block(tmp_path: Path) -> None:
    """A profile with no `models:` key at all: every class is n/a, never an error."""
    dest = tmp_path / "profile.yaml"
    dest.write_text("version: 1\n", encoding="utf-8")
    profile = sp.load_profile(dest)
    agents_dir = tmp_path / "agents"
    lines, problems = sp._claude_code_tier_agent_status(profile, agents_dir)
    assert problems == 0
    assert all("n/a" in line for line in lines)
