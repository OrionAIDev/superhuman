"""Exit-code matrix for the `check-gate-record` CLI subcommand.

Covers TEST.md's "5. Test cases -- enforcement" section (TC-34..TC-37) and
the CLI half of TC-23 (an out-of-range `--gate` is a usage error, not a
record failure).

Per FR-12/D-6 and PLAN chunk 2's acceptance criteria, `check-gate-record`
exists here with a full test suite but is called by nothing else in this
repo (TC-37) -- enforcement does not turn on until chunk 8.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gate_record_parser as grp  # noqa: E402
import superhuman_profile as sp  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOLVER = REPO_ROOT / "scripts" / "superhuman_profile.py"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke `check-gate-record` as a real subprocess.

    Args:
        root: project root to pass positionally.
        *args: extra CLI arguments appended after the root.

    Returns:
        The completed process, stdout/stderr captured as text.
    """
    return subprocess.run(
        [sys.executable, str(RESOLVER), "check-gate-record", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _well_formed_record(root: Path, slug: str, *, highest_gate: int = 4) -> Path:
    """Write a well-formed record with G0..`highest_gate` under `root`/`slug`."""
    record_dir = root / "docs" / "superhuman" / slug
    record_dir.mkdir(parents=True, exist_ok=True)
    labels = [f"[2026-08-0{n + 1}T00:00:00Z] G{n}: gate {n}\n" for n in range(highest_gate + 1)]
    path = record_dir / "SUPERHUMAN.md"
    path.write_text("## Decisions log\n" + "".join(labels), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# TC-34: exit-code matrix
# ---------------------------------------------------------------------------


def test_record_parses_and_predecessor_present_exits_ok(tmp_path: Path) -> None:
    """A well-formed record with its predecessor gate present exits 0."""
    _well_formed_record(tmp_path, "proj")
    result = _run(tmp_path, "--slug", "proj", "--gate", "5")
    assert result.returncode == sp.EXIT_OK, result.stderr


def test_record_file_absent_exits_record_failure(tmp_path: Path) -> None:
    """A named project with no `SUPERHUMAN.md` at all exits 5."""
    (tmp_path / "docs" / "superhuman" / "proj").mkdir(parents=True)
    result = _run(tmp_path, "--slug", "proj", "--gate", "1")
    assert result.returncode == sp.EXIT_RECORD, result.stderr


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX file permissions are not reliably enforceable on Windows for the owning user",
)
def test_record_file_unreadable_exits_record_failure(tmp_path: Path) -> None:
    """A `SUPERHUMAN.md` that exists but cannot be read exits 5, same as absent."""
    path = _well_formed_record(tmp_path, "proj")
    path.chmod(0o000)
    try:
        result = _run(tmp_path, "--slug", "proj", "--gate", "1")
        assert result.returncode == sp.EXIT_RECORD, result.stderr
    finally:
        path.chmod(0o644)


def test_missing_slug_exits_unresolved(tmp_path: Path) -> None:
    """Omitting `--slug` exits 4, reusing the roadmap#143 precedent wording."""
    result = _run(tmp_path, "--gate", "1")
    assert result.returncode == sp.EXIT_UNRESOLVED, result.stderr
    assert "--slug" in result.stderr


def test_no_decisions_log_section_exits_record_failure(tmp_path: Path) -> None:
    """A record with no `## Decisions log` section at all exits 5."""
    record_dir = tmp_path / "docs" / "superhuman" / "proj"
    record_dir.mkdir(parents=True)
    (record_dir / "SUPERHUMAN.md").write_text("# Superhuman: proj\nno sections here\n")
    result = _run(tmp_path, "--slug", "proj", "--gate", "1")
    assert result.returncode == sp.EXIT_RECORD, result.stderr


def test_locked_block_absent_at_1_1_0_exits_record_failure(tmp_path: Path) -> None:
    """No `## Decisions locked`, version >= 1.1.0, exits 5 (R6)."""
    record_dir = tmp_path / "docs" / "superhuman" / "proj"
    record_dir.mkdir(parents=True)
    (record_dir / "SUPERHUMAN.md").write_text(
        "**Superhuman-version:** 1.1.0\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "--slug", "proj", "--gate", "1")
    assert result.returncode == sp.EXIT_RECORD, result.stderr


def test_locked_block_absent_below_1_1_0_exits_ok(tmp_path: Path) -> None:
    """No `## Decisions locked`, version < 1.1.0, exits 0 (the legacy-majority shape)."""
    record_dir = tmp_path / "docs" / "superhuman" / "proj"
    record_dir.mkdir(parents=True)
    (record_dir / "SUPERHUMAN.md").write_text(
        "**Superhuman-version:** 1.0.3\n"
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "--slug", "proj", "--gate", "1")
    assert result.returncode == sp.EXIT_OK, result.stderr


def test_malformed_line_exits_record_failure_naming_file_line_and_text(tmp_path: Path) -> None:
    """A line that fails the entry grammar exits 5, and the message names file, line, and text."""
    record_dir = tmp_path / "docs" / "superhuman" / "proj"
    record_dir.mkdir(parents=True)
    manifest = record_dir / "SUPERHUMAN.md"
    manifest.write_text(
        "## Decisions log\n"
        "[2026-08-01T00:00:00Z] G0: baseline\n"
        "[not-a-stamp] G1: broken\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "--slug", "proj", "--gate", "1")
    assert result.returncode == sp.EXIT_RECORD, result.stderr
    assert manifest.as_posix() in result.stderr
    assert ":3:" in result.stderr
    assert "broken" in result.stderr


def test_predecessor_gate_absent_exits_record_failure_naming_the_missing_gate(
    tmp_path: Path,
) -> None:
    """A missing predecessor gate exits 5, and the message names the missing gate."""
    _well_formed_record(tmp_path, "proj", highest_gate=1)
    result = _run(tmp_path, "--slug", "proj", "--gate", "3")
    assert result.returncode == sp.EXIT_RECORD, result.stderr
    assert "gate 2" in result.stderr


def test_gate_10_passes_unconditionally(tmp_path: Path) -> None:
    """Requesting gate 10 exits 0 even against a maximally broken record."""
    record_dir = tmp_path / "docs" / "superhuman" / "proj"
    record_dir.mkdir(parents=True)
    (record_dir / "SUPERHUMAN.md").write_text(
        "## Decisions log\n[not-a-stamp] not valid at all\n", encoding="utf-8"
    )
    result = _run(tmp_path, "--slug", "proj", "--gate", "10")
    assert result.returncode == sp.EXIT_OK, result.stderr


def test_kickoff_defers_the_check_and_never_invokes_it(tmp_path: Path) -> None:
    """`--kickoff` exits 0 without the record ever being read (not merely a passing verdict)."""
    args = sp.build_parser().parse_args(
        ["check-gate-record", str(tmp_path), "--slug", "proj", "--gate", "1", "--kickoff"]
    )
    with mock.patch.object(grp, "read_record") as spy:
        code = sp.cmd_check_gate_record(args)
    assert code == sp.EXIT_OK
    spy.assert_not_called()


def test_gate_out_of_documented_range_exits_usage(tmp_path: Path) -> None:
    """`--gate 47` is a usage error (2), not a record failure (5) and not a crash (R7)."""
    _well_formed_record(tmp_path, "proj")
    result = _run(tmp_path, "--slug", "proj", "--gate", "47")
    assert result.returncode == sp.EXIT_USAGE, result.stderr


@pytest.mark.parametrize("gate", [-1, 999])
def test_other_out_of_range_gates_also_exit_usage(tmp_path: Path, gate: int) -> None:
    """Negative and very large `--gate` values are also usage errors, never a crash."""
    _well_formed_record(tmp_path, "proj")
    result = _run(tmp_path, "--slug", "proj", "--gate", str(gate))
    assert result.returncode == sp.EXIT_USAGE, result.stderr


# ---------------------------------------------------------------------------
# TC-35: D-4 -- record resolution never searches
# ---------------------------------------------------------------------------


def test_resolution_never_finds_a_sibling_project(tmp_path: Path) -> None:
    """The check answers about its own named project, never a sibling under the same root."""
    _well_formed_record(tmp_path, "has-the-gate", highest_gate=5)
    # Sibling with NO gates at all -- if resolution ever searched, this
    # project's absence of G0 could be masked by the sibling's presence.
    record_dir = tmp_path / "docs" / "superhuman" / "missing-the-gate"
    record_dir.mkdir(parents=True)
    (record_dir / "SUPERHUMAN.md").write_text(
        "## Decisions log\n[2026-08-01T00:00:00Z] Constraint: no gates logged\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "--slug", "missing-the-gate", "--gate", "1")
    assert result.returncode == sp.EXIT_RECORD, (
        "resolution must answer about 'missing-the-gate', never fall through "
        f"to its sibling: {result.stdout}{result.stderr}"
    )


# ---------------------------------------------------------------------------
# TC-36: exit code 5 doesn't collide with an existing caller's exit-code handling
# ---------------------------------------------------------------------------


def test_no_existing_caller_enumerates_exit_codes_in_a_way_that_would_swallow_5() -> None:
    """No documented caller branches on specific exit codes with a fallthrough to success.

    Every existing caller (the shim, the phase docs) already documents "any
    non-zero exit aborts" rather than an enumerated allowlist -- which is
    exactly why exit 4 was safe to add in v0.7.0, and why 5 is safe now.
    """
    shim = (REPO_ROOT / "scripts" / "autonomous-precondition.sh").read_text(encoding="utf-8")
    # The shim documents/propagates exit codes; it must not special-case a
    # specific non-zero code as if it were success.
    assert "exit_code == 0" not in shim.replace(" ", "")
    loop_doc = (REPO_ROOT / "phases" / "3-autonomous-loop.md").read_text(encoding="utf-8")
    assert "On exit 0:" in loop_doc
    # No prose anywhere claims a *non-zero* exit continues the loop.
    for non_zero in ("exit 2", "exit 3", "exit 4", "exit 5"):
        assert f"On {non_zero}: continue" not in loop_doc


# ---------------------------------------------------------------------------
# TC-37: FR-12 -- enforcement is not wired until chunk 8 lands
# ---------------------------------------------------------------------------


def test_check_gate_record_has_zero_callers_in_phases_or_skill_md() -> None:
    """`check-gate-record` exists and is tested, but nothing else in this repo invokes it yet."""
    hits: list[str] = []
    candidates = list((REPO_ROOT / "phases").glob("*.md")) + [REPO_ROOT / "SKILL.md"]
    for candidate in candidates:
        if "check-gate-record" in candidate.read_text(encoding="utf-8"):
            hits.append(candidate.as_posix())
    assert hits == [], f"check-gate-record must have zero callers until chunk 8: {hits}"


def test_check_gate_record_subcommand_is_registered() -> None:
    """The subcommand is registered in `build_parser()` even though nothing calls it yet."""
    args = sp.build_parser().parse_args(
        ["check-gate-record", ".", "--slug", "x", "--gate", "0"]
    )
    assert args.func is sp.cmd_check_gate_record
