"""Tests for the onboarding surface: discovery, proposal, `init`, `doctor`.

These cover the v0.9.0 additions. The load-bearing assertions are the two
authoring rules the proposal must never violate — deny rungs before permissive
ones, and markers only on protected rungs — because getting either wrong turns a
safe ladder into a permissive one without any visible symptom.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import superhuman_profile as sp  # noqa: E402

RESOLVER = Path(__file__).resolve().parents[1] / "scripts" / "superhuman_profile.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PRESETS = Path(__file__).resolve().parents[1] / "profiles" / "presets"


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


# --------------------------------------------------------------------------- #
# models: generator (C-PROF, #139) — TC-10, TC-11, TC-12
# --------------------------------------------------------------------------- #


def test_models_generator_round_trips_primary_fallback(tmp_path: Path) -> None:
    """The writer populates all 3 tiers, and the loader reads them back as mappings.

    TC-10 (FR-9): a deterministic writer, not LLM free-text, produces the
    ``models:`` block; the loader must resolve every tier to a ``{primary,
    fallback}`` mapping with no further edits.
    """
    dest = tmp_path / "profile.yaml"
    answers = {
        "most_capable": {"primary": "vendor-a/big", "fallback": "vendor-b/big"},
        "standard": {"primary": "vendor-a/mid", "fallback": "vendor-b/mid"},
        "cheap": {"primary": "vendor-a/small", "fallback": "vendor-b/small"},
    }
    sp.write_models_block(dest, answers)

    profile = sp.load_profile(dest)
    for tier, entry in answers.items():
        resolved = profile.models[tier]
        assert set(resolved) == {"primary", "fallback"}
        assert resolved["primary"] == entry["primary"]
        assert resolved["fallback"] == entry["fallback"]


def test_models_generator_creates_file_and_section_if_absent(tmp_path: Path) -> None:
    """FR-9: the writer creates the profile file (and `models:` section) when absent."""
    dest = tmp_path / "nested" / "profile.yaml"
    assert not dest.exists()

    sp.write_models_block(dest, {"most_capable": {"primary": "vendor-a/big", "fallback": None}})

    assert dest.is_file()
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"]["primary"] == "vendor-a/big"
    assert profile.models["most_capable"]["fallback"] is None
    # Untouched tiers still fail safe rather than silently vanishing.
    assert profile.models["standard"]["primary"] == sp.MODEL_PLACEHOLDER


def test_models_generator_merges_into_existing_profile(tmp_path: Path) -> None:
    """Re-running the writer for one tier must not clobber a prior tier's answer."""
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, {"most_capable": {"primary": "vendor-a/big", "fallback": None}})
    sp.write_models_block(dest, {"standard": {"primary": "vendor-a/mid", "fallback": None}})

    profile = sp.load_profile(dest)
    assert profile.models["most_capable"]["primary"] == "vendor-a/big"
    assert profile.models["standard"]["primary"] == "vendor-a/mid"


def test_models_generator_rejects_unknown_tier(tmp_path: Path) -> None:
    """An unrecognised tier name fails loud rather than being written silently."""
    dest = tmp_path / "profile.yaml"
    with pytest.raises(sp.ProfileError):
        sp.write_models_block(dest, {"fastest": {"primary": "vendor-a/big", "fallback": None}})
    assert not dest.exists()


def test_legacy_bare_string_models_normalizes_to_mapping(tmp_path: Path) -> None:
    """TC-11: a legacy bare-string `models:` tier loads and normalizes to a mapping.

    ADR-6: back-compat is via parse-time normalization, so every downstream
    reader of ``Profile.models`` sees the mapping form regardless of which
    shape the file was written in.
    """
    dest = tmp_path / "profile.yaml"
    dest.write_text(
        "version: 1\nmodels:\n  most_capable: opus\n",  # test data only, per LD-1
        encoding="utf-8",
    )
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"] == {"primary": "opus", "fallback": None}


def test_mapping_form_models_pass_through_unchanged(tmp_path: Path) -> None:
    """An already-mapping `models:` tier is taken as-is (ADR-6)."""
    dest = tmp_path / "profile.yaml"
    dest.write_text(
        "version: 1\nmodels:\n  standard:\n    primary: vendor-a/mid\n"
        "    fallback: vendor-b/mid\n",
        encoding="utf-8",
    )
    profile = sp.load_profile(dest)
    assert profile.models["standard"] == {"primary": "vendor-a/mid", "fallback": "vendor-b/mid"}


def test_malformed_models_entry_fails_loud(tmp_path: Path) -> None:
    """An unrecognised `models:` tier shape raises rather than silently coercing."""
    dest = tmp_path / "profile.yaml"
    dest.write_text("version: 1\nmodels:\n  most_capable: 123\n", encoding="utf-8")
    with pytest.raises(sp.ProfileError):
        sp.load_profile(dest)


@pytest.mark.parametrize(
    "models_yaml",
    [
        "models:\n  most_capable: ''\n",  # empty bare-string alias
        "models:\n  most_capable:\n    primary: opus\n    extra: nope\n",  # unknown key
        "models:\n  most_capable:\n    fallback: sonnet\n",  # missing primary
        "models:\n  most_capable:\n    primary: opus\n    fallback: 7\n",  # non-string fallback
        "models: nope\n",  # models: itself not a mapping
    ],
)
def test_normalize_models_rejects_every_bad_shape(tmp_path: Path, models_yaml: str) -> None:
    """Each malformed `models:` shape fails loud with a `ProfileError`, not silently."""
    dest = tmp_path / "profile.yaml"
    dest.write_text("version: 1\n" + models_yaml, encoding="utf-8")
    with pytest.raises(sp.ProfileError):
        sp.load_profile(dest)


def test_models_absent_normalizes_to_empty_mapping(tmp_path: Path) -> None:
    """No `models:` key at all is not an error — it normalizes to `{}` (FR-9)."""
    dest = tmp_path / "profile.yaml"
    dest.write_text("version: 1\n", encoding="utf-8")
    assert sp.load_profile(dest).models == {}


def test_decline_path_writes_neutral_placeholder(tmp_path: Path) -> None:
    """TC-12 (FR-10): declining every tier writes a neutral, vendor-free placeholder.

    The written file must still load (fail safe, not fail loud), and the
    placeholder token must never be a concrete vendor/model name.
    """
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, decline=True)

    profile = sp.load_profile(dest)
    for tier in sp.MODEL_TIERS:
        entry = profile.models[tier]
        assert entry["primary"] == sp.MODEL_PLACEHOLDER
        assert entry["fallback"] == sp.MODEL_PLACEHOLDER
        # Vendor-free: the placeholder is a self-documenting token, not a model alias.
        for vendor_hint in ("opus", "sonnet", "haiku", "gpt", "gemini", "claude"):
            assert vendor_hint not in entry["primary"].lower()


# --------------------------------------------------------------------------- #
# models: generator — targeted-patch comment preservation (#139 G6)
# --------------------------------------------------------------------------- #


def test_write_models_block_preserves_comments_and_ladder(tmp_path: Path) -> None:
    """Writing one tier must not disturb any other byte of a hand-edited file.

    G6 follow-up: the writer used to `yaml.safe_load`/`yaml.safe_dump` the
    whole document, silently stripping every comment. It must now splice only
    the `models:` span, leaving header comments, `ladder:`, and an
    already-answered tier verbatim.
    """
    original = (FIXTURES / "profile_with_comments_and_models.yaml").read_text(encoding="utf-8")
    dest = tmp_path / "profile.yaml"
    dest.write_text(original, encoding="utf-8")

    sp.write_models_block(dest, {"most_capable": {"primary": "vendor-a/big", "fallback": None}})

    new_text = dest.read_text(encoding="utf-8")
    match = re.search(r"(?m)^models:", original)
    assert match, "fixture must contain a top-level models: key"
    header = original[: match.start()]

    assert new_text.startswith(header), "everything before the models: key must be byte-identical"
    for line in header.splitlines():
        if line.strip().startswith("#"):
            assert line in new_text, f"comment line lost: {line!r}"

    profile = sp.load_profile(dest)
    assert [r.name for r in profile.ladder] == ["production", "dev", "workstation"]
    assert profile.models["most_capable"] == {"primary": "vendor-a/big", "fallback": None}
    assert profile.models["standard"] == {"primary": "vendor-a/mid", "fallback": "vendor-b/mid"}
    assert profile.models["cheap"]["primary"] == sp.MODEL_PLACEHOLDER


def test_write_models_block_inserts_without_disturbing_commented_profile(tmp_path: Path) -> None:
    """A commented profile with a ladder but no models: block gets one appended.

    Uses the shipped `classic-3tier` preset, which has ~40 lines of
    load-bearing comments and no `models:` key at all.
    """
    original = (PRESETS / "classic-3tier.yaml").read_text(encoding="utf-8")
    dest = tmp_path / "profile.yaml"
    dest.write_text(original, encoding="utf-8")

    sp.write_models_block(dest, decline=True)

    new_text = dest.read_text(encoding="utf-8")
    assert new_text.startswith(original), "existing content must survive untouched, with only an append"
    assert "models:" in new_text

    profile = sp.load_profile(dest)
    assert [r.name for r in profile.ladder] == [
        "production", "staging", "dev", "workstation-trunk", "workstation",
    ]
    for tier in sp.MODEL_TIERS:
        assert profile.models[tier]["primary"] == sp.MODEL_PLACEHOLDER


def test_write_models_block_second_write_patches_only_its_own_span(tmp_path: Path) -> None:
    """Two successive writer calls must not compound damage to the models: span."""
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(dest, {"most_capable": {"primary": "vendor-a/big", "fallback": None}})
    text_after_first = dest.read_text(encoding="utf-8")
    assert text_after_first.count("models:") == 1, "must not duplicate the models: key"

    sp.write_models_block(dest, {"standard": {"primary": "vendor-a/mid", "fallback": None}})
    text_after_second = dest.read_text(encoding="utf-8")
    assert text_after_second.count("models:") == 1

    profile = sp.load_profile(dest)
    assert profile.models["most_capable"]["primary"] == "vendor-a/big"
    assert profile.models["standard"]["primary"] == "vendor-a/mid"


def test_write_models_block_span_ends_at_next_top_level_key(tmp_path: Path) -> None:
    """A `models:` block followed by another top-level key stops there, not at EOF."""
    dest = tmp_path / "profile.yaml"
    dest.write_text(
        "version: 1\n"
        "models:\n"
        "  most_capable: opus\n"  # legacy bare-string form, test data only per LD-1
        "conventions:\n"
        "  - python\n",
        encoding="utf-8",
    )

    sp.write_models_block(dest, {"standard": {"primary": "vendor-a/mid", "fallback": None}})

    new_text = dest.read_text(encoding="utf-8")
    assert "conventions:\n  - python" in new_text, "content after the models: span must survive"
    profile = sp.load_profile(dest)
    assert profile.conventions == ("python",)
    assert profile.models["standard"]["primary"] == "vendor-a/mid"


def test_write_models_block_insert_adds_newline_when_file_lacks_one(tmp_path: Path) -> None:
    """A file with no trailing newline still gets a clean, valid append."""
    dest = tmp_path / "profile.yaml"
    dest.write_text("version: 1\nladder:\n  - name: a\n    detect: {default: true}", encoding="utf-8")

    sp.write_models_block(dest, decline=True)

    new_text = dest.read_text(encoding="utf-8")
    assert "detect: {default: true}\n\nmodels:" in new_text
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"]["primary"] == sp.MODEL_PLACEHOLDER


def test_write_models_block_rejects_invalid_existing_yaml(tmp_path: Path) -> None:
    """A pre-existing file that is not valid YAML fails loud, not silently."""
    dest = tmp_path / "profile.yaml"
    dest.write_text("models: [unterminated\n", encoding="utf-8")
    with pytest.raises(sp.ProfileError):
        sp.write_models_block(dest, decline=True)


def test_write_models_block_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    """A pre-existing file whose top level is not a mapping fails loud."""
    dest = tmp_path / "profile.yaml"
    dest.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(sp.ProfileError):
        sp.write_models_block(dest, decline=True)


# --------------------------------------------------------------------------- #
# models: generator — preflight-review follow-up fixes
# --------------------------------------------------------------------------- #


def test_write_models_block_leaves_original_untouched_on_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER 1 (preflight): a bad splice must never overwrite a valid file.

    The prior implementation wrote the patched text to `profile_path` and
    validated afterwards — so an invalid splice result had already clobbered
    a previously-valid profile.yaml by the time the error was raised, with no
    way back. The writer must validate a temp file FIRST and only atomically
    swap it in on success, leaving the original byte-untouched on failure.
    """
    dest = tmp_path / "profile.yaml"
    original = "version: 1\nladder:\n  - name: a\n    detect: {default: true}\n"
    dest.write_text(original, encoding="utf-8")

    # Force the splice to produce YAML that cannot possibly parse.
    monkeypatch.setattr(sp, "_splice_models_block", lambda text, rendered: "models: [unterminated\n")

    with pytest.raises(sp.ProfileError):
        sp.write_models_block(dest, decline=True)

    assert dest.read_text(encoding="utf-8") == original, "original file must be byte-untouched on failure"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "profile.yaml"]
    assert not leftovers, f"no temp file should be left behind: {leftovers}"


def test_write_models_block_preserves_column0_comment_after_models_block(tmp_path: Path) -> None:
    """BLOCKER 2 (preflight): a column-0 comment after `models:` belongs to what follows it.

    `_find_models_span` used to treat a column-0 comment line as still part of
    the `models:` mapping, so it got silently absorbed into the replaced span
    and deleted — exactly the comment loss the targeted-patch rewrite was
    supposed to prevent. The comment must survive, because it documents the
    NEXT key (`ladder:`), not `models:`.
    """
    original = (
        "version: 1\n"
        "models:\n"
        "  standard:\n"
        "    primary: vendor-a/mid\n"
        "    fallback: null\n"
        "\n"  # a blank line still belongs to the models: span, not the comment after it
        "# Ladder rules: narrower rungs first, deny before allow.\n"
        "ladder:\n"
        "  - name: a\n"
        "    detect: {default: true}\n"
    )
    dest = tmp_path / "profile.yaml"
    dest.write_text(original, encoding="utf-8")

    sp.write_models_block(dest, {"most_capable": {"primary": "vendor-a/big", "fallback": None}})

    new_text = dest.read_text(encoding="utf-8")
    assert "# Ladder rules: narrower rungs first, deny before allow.\n" in new_text
    assert new_text.endswith(
        "# Ladder rules: narrower rungs first, deny before allow.\n"
        "ladder:\n"
        "  - name: a\n"
        "    detect: {default: true}\n"
    ), "everything from the comment onward must survive verbatim, in order"

    profile = sp.load_profile(dest)
    assert profile.models["most_capable"]["primary"] == "vendor-a/big"
    assert profile.models["standard"]["primary"] == "vendor-a/mid"
    assert [r.name for r in profile.ladder] == ["a"]


def test_write_models_block_rejects_non_mapping_models_value(tmp_path: Path) -> None:
    """SHOULD-FIX 3 (preflight): a scalar `models:` value fails loud with ProfileError.

    `dict(existing.get("models") or {})` raises an uncaught `ValueError` on a
    non-mapping `models:` value (e.g. a bare scalar) instead of the
    `ProfileError` the docstring promises every other malformed-input path.
    """
    dest = tmp_path / "profile.yaml"
    dest.write_text("version: 1\nmodels: opus\n", encoding="utf-8")
    with pytest.raises(sp.ProfileError):
        sp.write_models_block(dest, decline=True)


@pytest.mark.parametrize("eol", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_write_models_block_preserves_original_line_endings(tmp_path: Path, eol: bytes) -> None:
    """SHOULD-FIX 4 (preflight): untouched lines keep their exact original EOL bytes.

    `Path.read_text`/`write_text` do universal-newline translation on read and
    OS-default translation on write, so an untouched region's line endings
    could silently flip (e.g. a CRLF-authored file collapsing to LF, or — on
    this Windows dev box — an LF-authored file being rewritten to CRLF) even
    though no content changed. That breaks the "byte-identical untouched
    region" contract the targeted-patch rewrite exists to guarantee.
    """
    lines = [
        b"version: 1", b'citation: "Release policy"', b"",
        b"ladder:", b"  - name: a", b"    detect: {default: true}",
    ]
    original_bytes = eol.join(lines) + eol
    dest = tmp_path / "profile.yaml"
    dest.write_bytes(original_bytes)

    sp.write_models_block(dest, decline=True)

    new_bytes = dest.read_bytes()
    assert new_bytes.startswith(original_bytes), (
        f"untouched region must keep its original {eol!r} line endings byte-for-byte"
    )
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"]["primary"] == sp.MODEL_PLACEHOLDER


def test_decline_specific_tiers_only(tmp_path: Path) -> None:
    """A per-tier decline list leaves answered tiers alone."""
    dest = tmp_path / "profile.yaml"
    sp.write_models_block(
        dest,
        answers={"most_capable": {"primary": "vendor-a/big", "fallback": "vendor-b/big"}},
        decline=["standard", "cheap"],
    )
    profile = sp.load_profile(dest)
    assert profile.models["most_capable"]["primary"] == "vendor-a/big"
    assert profile.models["standard"]["primary"] == sp.MODEL_PLACEHOLDER
    assert profile.models["cheap"]["fallback"] == sp.MODEL_PLACEHOLDER
