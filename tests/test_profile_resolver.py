"""Unit tests for the deployment-profile resolver (superhuman v0.7.0).

Covers the pieces the golden-verdict suite cannot reach because it only compares
exit codes against the legacy gate: schema validation, approval-spec parsing,
two-axis conjunctive matching, precedence and tie-breaking, the unresolved
verdict, the built-in zero-config ladder, and the terminal-rung warning.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import superhuman_profile as sp  # noqa: E402

RESOLVER = Path(__file__).resolve().parents[1] / "scripts" / "superhuman_profile.py"


def _write(tmp_path: Path, body: str) -> Path:
    """Write a profile file.

    Args:
        tmp_path: Test temp directory.
        body: YAML content.

    Returns:
        Path to the written profile.
    """
    path = tmp_path / "profile.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Approval specs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "mode"),
    [
        (None, "unresolved"),
        ("none", "none"),
        ("never", "never"),
        (["human"], "any_of"),
        ({"all_of": ["human", "agent:sec"]}, "all_of"),
    ],
)
def test_approval_parse_modes(raw: object, mode: str) -> None:
    """Every documented spec shape parses to the right mode."""
    assert sp.Approval.parse(raw, "x").mode == mode


@pytest.mark.parametrize(
    "raw",
    ["maybe", [], ["nobody"], {"any_of": ["human"], "all_of": ["human"]}, 42],
)
def test_approval_parse_rejects_invalid(raw: object) -> None:
    """Malformed specs raise rather than silently degrading."""
    with pytest.raises(sp.ProfileError):
        sp.Approval.parse(raw, "x")


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("none", "ALLOW"),
        ("never", "DENY"),
        (None, "UNRESOLVED"),
        (["self"], "ALLOW"),
        (["human"], "DENY"),
        (["human", "self"], "ALLOW"),  # any_of: a non-human path exists
        ({"all_of": ["human", "self"]}, "DENY"),  # all_of: a human is unavoidable
        ({"all_of": ["self", "agent:x"]}, "ALLOW"),
    ],
)
def test_unattended_verdict(spec: object, expected: str) -> None:
    """any_of takes the cheapest approver; all_of needs every one of them."""
    assert sp.Approval.parse(spec, "x").unattended_verdict() == expected


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    """Unknown keys fail loudly rather than being ignored (spec §10)."""
    path = _write(tmp_path, "version: 1\nladdr: []\n")
    with pytest.raises(sp.ProfileError, match="unknown top-level key"):
        sp.load_profile(path)


def test_fleet_top_level_key_is_recognized_not_rejected(tmp_path: Path) -> None:
    """`fleet:` (superhuman fleet-wiring, Phase 1.1) must NOT trip the
    unknown-key rejection above, or every profile combining a real
    `ladder:`/`models:` block with `fleet:` observation opt-in would fail
    closed for every OTHER consumer of `load_profile` (done-level ceiling
    resolution, autonomous-precondition checks) the moment `fleet:` is
    added — discovered live when a real machine profile combined both.
    `load_profile` deliberately does not validate `fleet:`'s own contents;
    that is `scripts/fleet/config.py::resolve_fleet_config`'s job.
    """
    path = _write(tmp_path, "version: 1\nfleet:\n  enabled: true\n")
    profile = sp.load_profile(path)  # must not raise
    assert profile is not None


def test_unknown_detector_is_rejected(tmp_path: Path) -> None:
    """A typo'd detector family is an error, not a rung that never matches."""
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: a\n    detect: {brunch: [main]}\n",
    )
    with pytest.raises(sp.ProfileError, match="unknown detector"):
        sp.load_profile(path)


def test_unknown_action_class_is_rejected(tmp_path: Path) -> None:
    """Only the closed set of action classes is accepted."""
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: a\n    detect: {default: true}\n"
        "    approvals: {deploy_to: none}\n",
    )
    with pytest.raises(sp.ProfileError, match="unknown action class"):
        sp.load_profile(path)


def test_duplicate_rung_name_is_rejected(tmp_path: Path) -> None:
    """Rung names must be unique."""
    path = _write(
        tmp_path,
        "version: 1\nladder:\n"
        "  - name: a\n    detect: {default: true}\n"
        "  - name: a\n    detect: {branch: [main]}\n",
    )
    with pytest.raises(sp.ProfileError, match="duplicate rung"):
        sp.load_profile(path)


def test_wrong_version_is_rejected(tmp_path: Path) -> None:
    """A future schema version is refused rather than half-understood."""
    path = _write(tmp_path, "version: 2\nladder:\n  - name: a\n    detect: {default: true}\n")
    with pytest.raises(sp.ProfileError, match="version must be 1"):
        sp.load_profile(path)


def test_scalar_labels_is_rejected(tmp_path: Path) -> None:
    """#R6-2 (PM-reproduced, BLOCKING): `labels` is never validated as a
    mapping — contrast the `detect` check immediately above this, which
    raises `ProfileError` for a non-dict. A profile with a scalar
    `labels: D0-code` builds a `Rung` whose `labels` is the *string*
    `"D0-code"`, and `cli._resolve_d_ceiling`'s
    `_D_CEILING_LABEL_KEY not in resolution.stage.labels` check becomes a
    SUBSTRING test against that string rather than a mapping-key test — it
    is True (the label key text is not literally a substring of the value,
    but `in` still runs and the point is: nothing rejects the malformed
    shape at all), so the ceiling check is silently bypassed and the
    unrestricted D4-prod default is granted. Fixed at the LOAD boundary
    (`load_profile`), next to the `detect` check, so every downstream
    consumer of `labels` is protected, not just `_resolve_d_ceiling`."""
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: a\n    detect: {default: true}\n"
        "    labels: D0-code\n",
    )
    with pytest.raises(sp.ProfileError, match="labels"):
        sp.load_profile(path)


@pytest.mark.parametrize("labels_yaml", ["[]", '""', "false", "0"])
def test_present_but_falsy_non_mapping_labels_is_rejected(
    tmp_path: Path, labels_yaml: str
) -> None:
    """#R7-1 (7th GPT-5 pass, PM-reproduced, BLOCKING): the round-6/#R6-2 fix
    used `entry.get("labels") or {}`, which short-circuits a *present but
    falsy* non-mapping (`labels: []`, `labels: ""`, `labels: false`,
    `labels: 0`) to `{}` BEFORE the `isinstance` check ran — so those
    malformed values slipped straight through to the SAME unrestricted
    D4-prod fail-open the scalar-string case had. The fix distinguishes
    genuinely-absent (`is None` -> default `{}`) from present-falsy (a
    non-mapping -> rejected), so EVERY present non-mapping is failed closed
    at the load boundary. See `test_scalar_labels_is_rejected` for the
    truthy-string case and `test_empty_mapping_labels_is_accepted` for the
    legitimately-empty mapping that must still load."""
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: a\n    detect: {default: true}\n"
        f"    labels: {labels_yaml}\n",
    )
    with pytest.raises(sp.ProfileError, match="labels"):
        sp.load_profile(path)


def test_empty_mapping_labels_is_accepted(tmp_path: Path) -> None:
    """A present, legitimately-empty mapping (`labels: {}`) is a dict and must
    load — its absent-`d_ceiling` behavior is correct, not a fail-open. This
    is the boundary the #R7-1 `is None` vs present-falsy distinction must NOT
    over-reject: `{}` is falsy too, but it is a valid (empty) mapping."""
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: a\n    detect: {default: true}\n"
        "    labels: {}\n",
    )
    profile = sp.load_profile(path)
    assert profile.ladder[0].labels == {}


def test_defaults_are_merged_into_rungs(tmp_path: Path) -> None:
    """`defaults.approvals` fills keys a rung omits."""
    path = _write(
        tmp_path,
        "version: 1\ndefaults:\n  approvals: {promote_into: none}\n"
        "ladder:\n  - name: a\n    detect: {default: true}\n"
        "    approvals: {act_unattended: [self]}\n",
    )
    profile = sp.load_profile(path)
    assert profile.ladder[0].approval("promote_into").mode == "none"


# --------------------------------------------------------------------------- #
# Matching, precedence, tie-breaking
# --------------------------------------------------------------------------- #


def test_detection_is_conjunctive(tmp_path: Path) -> None:
    """Every declared detector key must match, not merely one of them."""
    path = _write(
        tmp_path,
        "version: 1\nladder:\n"
        "  - name: narrow\n    detect: {path_segments: [svc], branch: [nope]}\n"
        "    approvals: {act_unattended: never}\n"
        "  - name: wide\n    detect: {default: true}\n"
        "    approvals: {act_unattended: [self]}\n",
    )
    profile = sp.load_profile(path)
    res = sp.resolve(tmp_path / "svc", profile)
    assert res.stage is not None and res.stage.name == "wide"


def test_explicit_marker_outranks_path(tmp_path: Path) -> None:
    """env_marker (tier 3) beats path_segments (tier 4) — spec §3.4."""
    root = tmp_path / "dev"
    slug = root / "docs" / "superhuman" / "s"
    slug.mkdir(parents=True)
    (slug / "SUPERHUMAN.md").write_text("## Environment: uat\n", encoding="utf-8")
    path = _write(
        tmp_path,
        "version: 1\nladder:\n"
        "  - name: by-path\n    detect: {path_segments: [dev]}\n"
        "    approvals: {act_unattended: [self]}\n"
        "  - name: by-marker\n    detect: {env_marker: [uat]}\n"
        "    approvals: {act_unattended: never}\n",
    )
    res = sp.resolve(root, sp.load_profile(path))
    assert res.stage is not None and res.stage.name == "by-marker"


def test_more_specific_wins_within_a_tier(tmp_path: Path) -> None:
    """Two matched keys beat one at equal authority."""
    root = tmp_path / "svc"
    root.mkdir()
    path = _write(
        tmp_path,
        "version: 1\nladder:\n"
        "  - name: one-key\n    detect: {path_segments: [svc]}\n"
        "    approvals: {act_unattended: [self]}\n"
        "  - name: two-keys\n    detect: {path_segments: [svc], default: true}\n"
        "    approvals: {act_unattended: never}\n",
    )
    res = sp.resolve(root, sp.load_profile(path))
    assert res.stage is not None and res.stage.name == "two-keys"


def test_tie_is_broken_by_declaration_order_and_reported(tmp_path: Path) -> None:
    """Equal authority and specificity falls back to order, and says so."""
    root = tmp_path / "svc"
    root.mkdir()
    path = _write(
        tmp_path,
        "version: 1\nladder:\n"
        "  - name: first\n    detect: {path_segments: [svc]}\n"
        "    approvals: {act_unattended: never}\n"
        "  - name: second\n    detect: {path_segments: ['sv*']}\n"
        "    approvals: {act_unattended: [self]}\n",
    )
    res = sp.resolve(root, sp.load_profile(path))
    assert res.stage is not None and res.stage.name == "first"
    assert "second" in res.ambiguous


def test_path_glob_does_not_span_segments(tmp_path: Path) -> None:
    """A segment glob matches within one directory name only."""
    root = tmp_path / "pro" / "duction"
    root.mkdir(parents=True)
    path = _write(
        tmp_path,
        "version: 1\nladder:\n"
        "  - name: prod\n    detect: {path_segments: ['*production*']}\n"
        "    approvals: {act_unattended: never}\n"
        "  - name: other\n    detect: {default: true}\n"
        "    approvals: {act_unattended: [self]}\n",
    )
    res = sp.resolve(root, sp.load_profile(path))
    assert res.stage is not None and res.stage.name == "other"


# --------------------------------------------------------------------------- #
# Fail-closed behaviour
# --------------------------------------------------------------------------- #


def test_unmatched_denies_when_ladder_declares_a_block(tmp_path: Path) -> None:
    """Declaring a hard block opts into strictness for unmatched locations."""
    root = tmp_path / "elsewhere"
    root.mkdir()
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: prod\n    detect: {path_segments: [prod]}\n"
        "    approvals: {act_unattended: never}\n",
    )
    rc = _cli(["check", str(root), "--level", "M"], profile=path)
    assert rc == sp.EXIT_DENIED


def test_unmatched_allows_when_no_block_declared(tmp_path: Path) -> None:
    """A ladder with no hard block stays permissive for unmatched locations."""
    root = tmp_path / "elsewhere"
    root.mkdir()
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: dev\n    detect: {path_segments: [dev]}\n"
        "    approvals: {act_unattended: [self]}\n",
    )
    assert _cli(["check", str(root), "--level", "M"], profile=path) == sp.EXIT_OK


def test_unresolved_policy_exits_four(tmp_path: Path) -> None:
    """An undeclared cell halts (exit 4) rather than defaulting to allow."""
    root = tmp_path / "svc"
    root.mkdir()
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: svc\n    detect: {path_segments: [svc]}\n",
    )
    assert _cli(["check", str(root), "--level", "M"], profile=path) == sp.EXIT_UNRESOLVED


def test_hitl_h_never_consults_the_ladder(tmp_path: Path) -> None:
    """At HITL-H the approvals map is inert (spec §7.3, decision D-20)."""
    root = tmp_path / "prod"
    root.mkdir()
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: prod\n    detect: {path_segments: [prod]}\n"
        "    approvals: {act_unattended: never}\n",
    )
    assert _cli(["check", str(root), "--level", "H"], profile=path) == sp.EXIT_OK


def test_require_profile_env_var_fails_closed(tmp_path: Path) -> None:
    """A missing profile is fatal when the machine demands one (spec §6.3)."""
    root = tmp_path / "svc"
    root.mkdir()
    rc = _cli(
        ["check", str(root), "--level", "M"],
        profile=None,
        extra={"SUPERHUMAN_REQUIRE_PROFILE": "1", "HOME": str(tmp_path),
               "USERPROFILE": str(tmp_path)},
    )
    assert rc == sp.EXIT_USAGE


# --------------------------------------------------------------------------- #
# Built-in ladder
# --------------------------------------------------------------------------- #


def test_builtin_ladder_loads_and_has_a_catch_all() -> None:
    """Zero-config must resolve somewhere even outside a git repo."""
    profile = sp.load_profile(None)
    assert profile.is_builtin
    assert any(r.detect.get("default") for r in profile.ladder)


def test_builtin_trunk_precedes_work() -> None:
    """On `main` the trunk rung must win the equal-specificity tie."""
    names = [r.name for r in sp.load_profile(None).ladder]
    assert names.index("trunk") < names.index("work")


def test_builtin_allows_a_plain_directory(tmp_path: Path) -> None:
    """A non-git directory resolves to an allowing rung under zero-config.

    The *ladder* allows it; the gate still refuses at M because an unattended
    loop needs git+remote (roadmap #143). `--action promote_into` asks the
    ladder question on its own, without the project-state preconditions.
    """
    root = tmp_path / "plain"
    root.mkdir()
    common = dict(
        profile=None,
        extra={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
    )
    assert _cli(
        ["check", str(root), "--level", "M", "--action", "promote_into"], **common
    ) == sp.EXIT_OK
    assert _cli(["check", str(root), "--level", "M"], **common) == sp.EXIT_DENIED


def test_builtin_blocks_unattended_at_a_stable_tag(tmp_path: Path) -> None:
    """A checkout parked on a release tag forbids unattended operation."""
    root = tmp_path / "repo"
    root.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    for cmd in (
        ["git", "init", "-q"],
        ["git", "commit", "-q", "--allow-empty", "-m", "x"],
        ["git", "tag", "v1.0.0"],
        ["git", "checkout", "-q", "v1.0.0"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True,
                       env={**dict(__import__("os").environ), **env})
    rc = _cli(
        ["check", str(root), "--level", "M"],
        profile=None,
        extra={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
    )
    assert rc == sp.EXIT_DENIED


# --------------------------------------------------------------------------- #
# Warnings and output shape
# --------------------------------------------------------------------------- #


def test_terminal_rung_without_human_warns(tmp_path: Path) -> None:
    """An agent-only approver on a protected rung warns but never blocks."""
    path = _write(
        tmp_path,
        "version: 1\nladder:\n  - name: prod\n    detect: {path_segments: [prod]}\n"
        "    approvals: {act_unattended: never, promote_into: [agent:bot]}\n",
    )
    warnings = sp._terminal_warnings(sp.load_profile(path))
    assert warnings and "no 'human' approver" in warnings[0]


def test_resolve_json_shape(tmp_path: Path) -> None:
    """The documented JSON keys are all present."""
    root = tmp_path / "dev"
    root.mkdir()
    path = _write(
        tmp_path,
        "version: 1\nciteme: x\n".replace("citeme", "citation")
        + "ladder:\n  - name: dev\n    detect: {path_segments: [dev]}\n"
        "    labels: {tier: integration}\n"
        "    approvals: {act_unattended: [self], promote_into: [human]}\n"
        "    tests: [unit]\n",
    )
    out = _cli_out(["resolve", str(root)], profile=path)
    data = json.loads(out)
    assert data["stage"] == "dev"
    assert data["labels"] == {"tier": "integration"}
    assert data["matched_by"] == ["path_segments"]
    assert data["approvals"]["promote_into"] == {"any_of": ["human"]}
    assert data["profile_hash"].startswith("sha256:")
    assert data["tests"] == ["unit"]


def test_digest_ignores_unresolved_cells(tmp_path: Path) -> None:
    """Filling a null cell is a fill, not drift (spec §5, decision D-12)."""
    base = "version: 1\nladder:\n  - name: a\n    detect: {default: true}\n"
    without = sp.load_profile(_mk(tmp_path, "a", base))
    with_null = sp.load_profile(
        _mk(tmp_path, "b", base + "    approvals: {act_unattended: null}\n")
    )
    declared = sp.load_profile(
        _mk(tmp_path, "c", base + "    approvals: {act_unattended: never}\n")
    )
    assert without.digest == with_null.digest, "a null cell must not change the hash"
    assert without.digest != declared.digest, "a declared cell must change the hash"


def _mk(tmp_path: Path, name: str, body: str) -> Path:
    """Write a profile into a named subdirectory.

    Args:
        tmp_path: Test temp directory.
        name: Subdirectory name.
        body: YAML content.

    Returns:
        Path to the written profile.
    """
    directory = tmp_path / name
    directory.mkdir(exist_ok=True)
    path = directory / "profile.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _env(profile: Path | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a subprocess environment for a CLI invocation.

    Args:
        profile: Profile to pin, or ``None`` to exercise discovery.
        extra: Additional variables.

    Returns:
        The environment mapping.
    """
    import os

    env = dict(os.environ)
    env.pop("SUPERHUMAN_PROFILE", None)
    env.pop("SUPERHUMAN_REQUIRE_PROFILE", None)
    if profile is not None:
        env["SUPERHUMAN_PROFILE"] = str(profile)
    if extra:
        env.update(extra)
    return env


def _cli(args: list[str], profile: Path | None, extra: dict[str, str] | None = None) -> int:
    """Run the resolver CLI and return its exit code.

    Args:
        args: CLI arguments.
        profile: Profile to pin, or ``None``.
        extra: Additional environment variables.

    Returns:
        The process exit code.
    """
    return subprocess.run(
        [sys.executable, str(RESOLVER), *args],
        capture_output=True, text=True, env=_env(profile, extra), check=False,
    ).returncode


def _cli_out(args: list[str], profile: Path | None) -> str:
    """Run the resolver CLI and return its stdout.

    Args:
        args: CLI arguments.
        profile: Profile to pin, or ``None``.

    Returns:
        Captured stdout.
    """
    proc = subprocess.run(
        [sys.executable, str(RESOLVER), *args],
        capture_output=True, text=True, env=_env(profile), check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


GOLDEN_CURRENT = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "golden" / "ladder-current.yaml"
)
LIVE_PROFILE = Path.home() / ".superhuman" / "profile.yaml"


def test_golden_ladder_mirrors_the_live_profile() -> None:
    """The golden POLICY fixture must not drift from the live machine profile.

    ``~/.superhuman/profile.yaml`` claims to be "mirrored by
    tests/fixtures/golden/ladder-current.yaml ... edit both together". Nothing
    enforced that claim, so the mirror silently went missing altogether. This
    fails the build when the two disagree on anything that changes a VERDICT —
    rung order, any rung's detectors or approvals, or the fleet opt-in.

    Comments and formatting are deliberately NOT compared: the fixture carries
    its own local-only preamble, and the drift that matters is semantic. Skips
    when either file is absent, which is the normal case for anyone who is not
    this operator — the fixture is local-only by design (see the
    ``/tests/fixtures/golden/`` rule in .gitignore).
    """
    if not GOLDEN_CURRENT.is_file() or not LIVE_PROFILE.is_file():
        pytest.skip("golden policy fixture or live profile absent — nothing to pin")

    golden = yaml.safe_load(GOLDEN_CURRENT.read_text(encoding="utf-8"))
    live = yaml.safe_load(LIVE_PROFILE.read_text(encoding="utf-8"))

    assert golden["version"] == live["version"], "schema version drifted"
    assert golden["citation"] == live["citation"], "citation drifted"
    assert (golden.get("fleet") or {}).get("enabled") == (live.get("fleet") or {}).get(
        "enabled"
    ), "fleet opt-in drifted"

    # Rung ORDER is load-bearing (deny rungs first breaks path-segment ties to
    # the safer verdict), so compare as an ordered list, not a set.
    def _policy(entry: dict) -> tuple:
        return (
            entry["name"],
            entry.get("kind"),
            entry.get("detect"),
            entry.get("approvals"),
        )

    assert [_policy(r) for r in golden["ladder"]] == [_policy(r) for r in live["ladder"]], (
        "ladder policy drifted between the golden fixture and the live profile"
    )
