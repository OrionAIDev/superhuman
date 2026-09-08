"""Unit suite for the FR-7/FR-8/FR-9 gate-record sweeper (chunk 3, dry-run only).

Covers TEST.md's "6. Test cases -- sweeper" section (TC-38..TC-40, TC-42,
TC-43) at the `propose()`/CLI level. TC-41 is a review brief for Phase 3.3,
not a pytest case. TC-44..TC-47 exercise `--apply`, which this chunk defines
but refuses unconditionally (writing is chunk 4's responsibility) -- covered
here only to the extent of confirming that refusal, never a real apply.

Per NFR-4 (public repo), every fixture used for git-evidence reconstruction
is a disposable `tmp_path` git repository built fresh per test, never a real
private repo. The shape-faithful corpus fixtures under
`tests/fixtures/gate_records/` (added in chunk 2) are reused for the FR-9
byte-equality property test and the decoy-heading test, exactly as TEST.md
directs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gate_record_census as census_mod  # noqa: E402
import gate_record_parser as grp  # noqa: E402
import gate_record_sweeper as sweeper  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "gate_records"

#: Precision rank for `Entry.precision` values, lowest to highest -- mirrors
#: the vocabulary `gate_record_parser.Entry` documents (`"unknown"` <
#: `"date"` < `"minute"` < `"full"`). A line with no recognisable
#: `[stamp] label: text` shape at all (e.g. the pre-repair `LD-n` shape,
#: which carries no bracket whatsoever) ranks the same as `"unknown"`:
#: neither carries any usable timestamp information.
_PRECISION_RANK = {None: 0, "unknown": 0, "date": 1, "minute": 2, "full": 3}


def _stamp_precision_rank(line: str | None) -> int:
    """Precision rank (0..3) of a rendered/original gate-record line's stamp.

    Reuses `gate_record_parser.parse_entry` -- never re-derives stamp
    classification (FR-1's single owner).

    Args:
        line: a full physical line (original or rendered), or `None` when
            there is no original line to compare against (an "added" line).

    Returns:
        0 for `None`, an unparseable line, or an `UNKNOWN` stamp; otherwise
        the rank of `Entry.precision`.
    """
    if line is None:
        return 0
    entry = grp.parse_entry(line)
    if entry is None:
        return 0
    return _PRECISION_RANK[entry.precision]


# ---------------------------------------------------------------------------
# Disposable git-repo helpers (TC-38, TC-39) -- real `git`, never mocked.
# ---------------------------------------------------------------------------


def _run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    """Run a git command in `repo`, raising on failure."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


def _init_repo(tmp_path: Path) -> Path:
    """Initialise a disposable git repo under `tmp_path` and return its root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "sweeper-test@example.invalid")
    _run_git(repo, "config", "user.name", "Sweeper Test")
    return repo


def _commit_manifest(repo: Path, slug: str, content: str, when_iso: str) -> Path:
    """Write `content` to `docs/superhuman/<slug>/SUPERHUMAN.md` and commit it.

    Args:
        repo: the repo root (from `_init_repo`).
        slug: the project slug.
        content: full file content for this commit.
        when_iso: an ISO-8601 timestamp with an explicit UTC offset, used
            for both author and committer date -- FR-7 specifies the
            committer date, so tests that need to distinguish the two
            would set them independently; none here do.

    Returns:
        The manifest path.
    """
    manifest_dir = repo / "docs" / "superhuman" / slug
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / "SUPERHUMAN.md"
    # `newline=""` disables Python's universal-newline translation on write
    # (which would otherwise turn every `\n` into `os.linesep`, i.e. CRLF on
    # Windows) -- these tests assert exact LF content, matching git's own
    # `core.autocrlf=false`-equivalent byte-for-byte diff behaviour.
    manifest.write_text(content, encoding="utf-8", newline="")
    _run_git(repo, "add", "docs/superhuman/%s/SUPERHUMAN.md" % slug)
    _run_git(
        repo,
        "commit",
        "-q",
        "-m",
        f"record {slug} at {when_iso}",
        env={"GIT_AUTHOR_DATE": when_iso, "GIT_COMMITTER_DATE": when_iso},
    )
    return manifest


# ---------------------------------------------------------------------------
# Not applicable: no `## Decisions locked` block at all
# ---------------------------------------------------------------------------


def test_record_with_no_locked_block_is_not_applicable(tmp_path: Path) -> None:
    """A record with only `## Decisions log` proposes nothing (DESIGN's Sweep scope)."""
    repo = _init_repo(tmp_path)
    manifest = _commit_manifest(
        repo,
        "no-locked-block",
        "## Decisions log\n[2026-08-01T00:00:00Z] G0: baseline\n",
        "2026-08-01T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    assert proposal.applicable is False
    assert proposal.proposed_text == proposal.original_text
    assert proposal.diff == ""


# ---------------------------------------------------------------------------
# TC-38: timestamp reconstruction from git history, happy path
# ---------------------------------------------------------------------------


def test_reconstructed_timestamp_equals_the_introducing_commits_committer_date(
    tmp_path: Path,
) -> None:
    """Each gate line's reconstructed stamp is the committer date of the commit that added it."""
    repo = _init_repo(tmp_path)
    slug = "incremental"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T10:00:00+00:00",
    )
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T11:30:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    assert proposal.applicable is True
    assert proposal.aborted is False
    gate_lines = {line.gate: line.rendered for line in proposal.lines if line.gate is not None}
    assert gate_lines[0] == "[2026-08-01T10:00:00Z] G0: baseline decided\n"
    assert gate_lines[1] == "[2026-08-02T11:30:00Z] G1: kickoff decided\n"
    assert proposal.unknown_count == 0


# ---------------------------------------------------------------------------
# TC-39: no per-line history (one commit, whole file added at once) -> UNKNOWN
# ---------------------------------------------------------------------------


def test_single_commit_history_yields_unknown_for_every_gate_line(tmp_path: Path) -> None:
    """A file committed once, in full, gives no per-line evidence -- every stamp is UNKNOWN.

    This is the direct test of FR-8's hardest case: a repo whose history
    was squashed or rewritten so the whole record appears in one commit
    (REQUIREMENTS Assumption 1 -- superhuman's own history was rewritten in
    2026-08). Naively attributing that one commit's date to every gate
    would assert a distinction ("G0 happened here, separately from G4")
    the evidence cannot support -- exactly the trap D-1 rejects.
    """
    repo = _init_repo(tmp_path)
    manifest = _commit_manifest(
        repo,
        "squashed",
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "- [UNKNOWN] G2: requirements decided\n"
        "## Decisions log\n",
        "2026-08-30T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    assert proposal.applicable is True
    gate_lines = [line for line in proposal.lines if line.gate is not None]
    assert len(gate_lines) == 3
    assert proposal.unknown_count == 3
    for line in gate_lines:
        assert line.rendered.startswith("[UNKNOWN] ")
    # No line is assigned the single commit's date as if it were that
    # gate's real time.
    assert "2026-08-30" not in proposal.proposed_text


def test_no_git_history_at_all_yields_unknown(tmp_path: Path) -> None:
    """A path outside any git repository has no evidence at all -- UNKNOWN, not a crash."""
    manifest_dir = tmp_path / "no-repo" / "docs" / "superhuman" / "orphan"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "SUPERHUMAN.md"
    manifest.write_text(
        "## Decisions locked\n- [UNKNOWN] G0: baseline\n## Decisions log\n",
        encoding="utf-8",
    )

    proposal = sweeper.propose(manifest)

    assert proposal.applicable is True
    assert proposal.unknown_count == 1


# ---------------------------------------------------------------------------
# TC-40 (adapted): every reconstructed timestamp traces to a real commit
# ---------------------------------------------------------------------------


def test_every_reconstructed_timestamp_traces_to_a_real_commit(tmp_path: Path) -> None:
    """Every non-UNKNOWN stamp the sweep proposes is a real commit's committer date.

    Generalises TC-38 into the invariant TEST.md TC-40 asks for: for every
    stamped line in the proposal, an independent `git log -p --follow`
    query must show some commit that actually added a matching line.
    """
    repo = _init_repo(tmp_path)
    slug = "multi-gate"
    stamps = [
        "2026-07-01T09:00:00+00:00",
        "2026-07-05T09:00:00+00:00",
        "2026-07-10T09:00:00+00:00",
        "2026-07-20T09:00:00+00:00",
    ]
    lines: list[str] = []
    manifest = None
    for gate, when in enumerate(stamps):
        lines.append(f"- [UNKNOWN] G{gate}: decision {gate}")
        content = "## Decisions locked\n" + "\n".join(lines) + "\n## Decisions log\n"
        manifest = _commit_manifest(repo, slug, content, when)

    proposal = sweeper.propose(manifest)

    log = subprocess.run(
        ["git", "log", "--follow", "-p", "--pretty=format:%H", "--", str(manifest)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    non_unknown = [line for line in proposal.lines if line.gate is not None and "UNKNOWN" not in line.rendered]
    assert len(non_unknown) == 4
    for line in non_unknown:
        text_only = line.rendered.split("]", 1)[1].strip()
        assert text_only.split(":", 1)[1].strip() in log
        # The reconstructed stamp must itself be one of the commits' dates.
        stamp = line.rendered.split("]", 1)[0].lstrip("[")
        assert stamp.endswith("Z")


# ---------------------------------------------------------------------------
# TC-42: FR-9 byte-equality property test, 100% of corpus fixtures
# ---------------------------------------------------------------------------


def _all_fixture_paths() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.md"))


@pytest.mark.parametrize("fixture_path", _all_fixture_paths(), ids=lambda p: p.name)
def test_sweep_touches_nothing_outside_the_locked_block(fixture_path: Path) -> None:
    """For every corpus fixture, everything outside `## Decisions locked` is untouched.

    Byte-for-byte, not line-for-line or whitespace-normalised, over 100% of
    `tests/fixtures/gate_records/` -- not a sampled subset (TEST.md TC-42).
    """
    proposal = sweeper.propose(fixture_path)

    if not proposal.applicable:
        assert proposal.proposed_text == proposal.original_text
        return

    original = proposal.original_text
    proposed = proposal.proposed_text
    original_lines = original.splitlines(keepends=True)
    bounds = sweeper._find_section_bounds(original_lines, grp.LOCKED_HEADING)
    assert bounds is not None
    start, end = bounds
    prefix = "".join(original_lines[: start + 1])
    suffix = "".join(original_lines[end:])

    assert original.startswith(prefix)
    assert original.endswith(suffix)
    assert proposed.startswith(prefix), f"{fixture_path.name}: prefix changed"
    assert proposed.endswith(suffix), f"{fixture_path.name}: suffix changed"


# ---------------------------------------------------------------------------
# TC-13: the sweep does not homogenise line endings outside the edited block
# ---------------------------------------------------------------------------


def test_sweep_preserves_crlf_outside_the_block_even_when_the_block_itself_changes() -> None:
    """A CRLF-throughout fixture keeps CRLF outside the block; only the block content changes.

    A distinct regression risk from TC-42's generic byte-equality property
    (TEST.md TC-13): a tool that reads and rewrites a whole file through a
    text-mode handle without care silently flips every CRLF to LF, which
    the *comparison* logic in TC-12 alone would not catch.
    """
    fixture = FIXTURES_DIR / "crlf_variant.md"
    raw_bytes = fixture.read_bytes()
    assert b"\r\n" in raw_bytes and b"\n\n" not in raw_bytes.replace(b"\r\n", b"")

    proposal = sweeper.propose(fixture)

    original_lines = proposal.original_text.splitlines(keepends=True)
    bounds = sweeper._find_section_bounds(original_lines, grp.LOCKED_HEADING)
    assert bounds is not None
    start, _end = bounds
    header_region = "".join(original_lines[:start])
    assert header_region and "\r\n" in header_region
    assert proposal.proposed_text.startswith(header_region)


# ---------------------------------------------------------------------------
# TC-43: adversarial-surrounding-content -- a decoy heading inside a fence
# ---------------------------------------------------------------------------


def test_decoy_heading_inside_a_fence_is_not_mistaken_for_the_real_boundary() -> None:
    """The sweep finds the real `## Decisions locked` heading, not one quoted in a fence."""
    fixture = FIXTURES_DIR / "decoy_heading_in_prose.md"
    assert fixture.is_file()

    proposal = sweeper.propose(fixture)

    assert proposal.applicable is True
    # The one real gate entry (dated 2026-09-01, not the decoy's 2026-01-01)
    # is the only gate line the sweep considered.
    assert len(proposal.lines) == 1
    assert proposal.lines[0].gate == 0
    assert "2026-01-01" not in proposal.proposed_text.split(grp.LOCKED_HEADING, 2)[-1].split("## Decisions log")[0]
    # The decoy's own text must never be treated as the real entry's text.
    real_line = proposal.lines[0]
    assert "fenced code block" not in real_line.rendered
    assert "REAL locked entry" in real_line.rendered


# ---------------------------------------------------------------------------
# G2: every proposed record parses cleanly under `gate_record_parser.read_record`
# ---------------------------------------------------------------------------


def _locked_block_fixtures() -> list[Path]:
    result = []
    for path in _all_fixture_paths():
        text = path.read_text(encoding="utf-8", errors="replace")
        if grp.LOCKED_HEADING in text:
            result.append(path)
    return result


@pytest.mark.parametrize("fixture_path", _locked_block_fixtures(), ids=lambda p: p.name)
def test_swept_locked_block_parses_cleanly(fixture_path: Path, tmp_path: Path) -> None:
    """A record the sweep proposed a change for must itself pass its own gate check.

    Direct instance of the "the sweep may not emit records that fail the
    check it exists to enable" property (DECISIONS.md G2).
    """
    proposal = sweeper.propose(fixture_path)
    assert proposal.applicable is True
    assert proposal.aborted is False, proposal.abort_reason

    swept = tmp_path / "swept.md"
    swept.write_text(proposal.proposed_text, encoding="utf-8")
    reading = grp.read_record(swept)

    assert reading.locked.well_formed, (
        f"{fixture_path.name}: swept locked block still malformed: "
        f"{reading.locked.malformed}"
    )


# ---------------------------------------------------------------------------
# G6-003-b: the OI-3 `LD-n` shape is repaired by the sweep
# ---------------------------------------------------------------------------


def test_ld_style_block_is_repaired_purely_additively() -> None:
    """Every `LD-n` line gains a `[stamp]` slot and nothing else (G6-006).

    The repair is purely additive: no gate is ever extracted from the
    parenthetical, every `LD-n` identifier survives, and no sibling line is
    treated differently from another. `LD-2 (G0)` stays `LD-2 (G0)` -- it
    does not become a bare `G0:` line -- so the locked block contributes no
    gates at all, which is the accepted, honest consequence recorded in
    `DECISIONS.md` G6-006.
    """
    fixture = FIXTURES_DIR / "ld_style_locked_block.md"
    assert fixture.is_file()

    proposal = sweeper.propose(fixture)

    assert proposal.applicable is True
    assert proposal.aborted is False
    repaired = [line for line in proposal.lines if line.source == "repaired"]
    assert len(repaired) == 3  # LD-1, LD-2, LD-3 in the fixture
    # Purely additive: no repaired line ever contributes a gate.
    assert all(line.gate is None for line in repaired)
    # Every identifier is preserved, including the ones that name a gate.
    assert any("LD-1" in line.rendered for line in repaired)
    assert any("LD-2 (G0)" in line.rendered for line in repaired)
    assert any("LD-3 (G1)" in line.rendered for line in repaired)
    # Wording is preserved verbatim -- only the bullet/bold/stamp scaffolding changes.
    by_label = {}
    for line in repaired:
        if "LD-2" in line.rendered:
            by_label["LD-2"] = line.rendered
        elif "LD-3" in line.rendered:
            by_label["LD-3"] = line.rendered
    assert "Elicitation depth = primary + fallback per tier." in by_label["LD-2"]
    assert "HITL-H, on-divergence cadence, foundation-first" in by_label["LD-3"]


def test_a_line_with_no_known_repair_is_left_completely_untouched(tmp_path: Path) -> None:
    """A malformed line matching no known repair heuristic is never guessed at (FR-8)."""
    repo = _init_repo(tmp_path)
    # This line structurally attempts the `[stamp] label: text` shape (it
    # has a bracket and a colon) so it is never folded as a G6-002
    # continuation line, but its stamp is not one the grammar recognises --
    # and it does not match the `LD-n` repair shape either, so no known
    # heuristic applies.
    original = (
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [not-a-recognised-stamp] G1: looks like a gate line but isn't one\n"
        "## Decisions log\n"
    )
    manifest = _commit_manifest(repo, "unrepairable", original, "2026-08-01T00:00:00+00:00")

    proposal = sweeper.propose(manifest)

    unrepaired = [line for line in proposal.lines if line.source == "unrepaired"]
    assert len(unrepaired) == 1
    assert unrepaired[0].original == unrepaired[0].rendered
    assert "not-a-recognised-stamp" in unrepaired[0].rendered


# ---------------------------------------------------------------------------
# Adding a gate the git log evidences but the current block omits
# ---------------------------------------------------------------------------


def test_a_gate_dropped_from_the_current_block_but_evidenced_by_history_is_proposed_as_added(
    tmp_path: Path,
) -> None:
    """The sweep may add a line for a gate history evidences but the block now omits."""
    repo = _init_repo(tmp_path)
    slug = "dropped-gate"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    # A later commit drops the G1 line entirely (e.g. an accidental edit).
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-05T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    added = [line for line in proposal.lines if line.source == "added"]
    assert len(added) == 1
    assert added[0].gate == 1
    assert added[0].original is None
    assert "kickoff decided" in added[0].rendered


# ---------------------------------------------------------------------------
# G6-004: stamp precision may only increase -- the sweep is monotone
# ---------------------------------------------------------------------------


def test_date_precision_stamp_upgrades_when_history_differentiates(tmp_path: Path) -> None:
    """A date-precision stamp is upgraded to full precision when git evidence differentiates.

    This is FR-7's real value, preserved by G6-004: reconstruction still
    happens, exactly when it is a genuine improvement.
    """
    repo = _init_repo(tmp_path)
    slug = "date-to-full"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T21:08:00+00:00",
    )
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [2026-08-16] G0: baseline decided\n"
        "- [2026-08-17] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-17T09:30:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    gate_lines = {line.gate: line.rendered for line in proposal.lines if line.gate is not None}
    assert gate_lines[0] == "[2026-08-16T21:08:00Z] G0: baseline decided\n"
    assert gate_lines[1] == "[2026-08-17T09:30:00Z] G1: kickoff decided\n"
    assert proposal.unknown_count == 0


def test_date_precision_stamp_is_kept_byte_unchanged_when_history_does_not_differentiate(
    tmp_path: Path,
) -> None:
    """The G6-004 defect: a real, human-recorded date stamp must never become `[UNKNOWN]`.

    A single-commit history gives no differentiating evidence (the same
    squashed-history trap TC-39 exercises for the `UNKNOWN` case) -- the
    written date stamp is the only evidence this gate will ever have and
    must survive the sweep exactly as written, per `DECISIONS.md` G6-004.
    """
    repo = _init_repo(tmp_path)
    manifest = _commit_manifest(
        repo,
        "date-stays-put",
        "## Decisions locked\n- [2026-08-16] G1: base branch decided\n## Decisions log\n",
        "2026-09-01T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    gate_lines = {line.gate: line.rendered for line in proposal.lines if line.gate is not None}
    assert gate_lines[1] == "[2026-08-16] G1: base branch decided\n"
    assert proposal.unknown_count == 0
    assert "UNKNOWN" not in proposal.proposed_text


def test_full_precision_stamp_stays_put_even_when_evidence_is_found(tmp_path: Path) -> None:
    """A stamp already at full precision is never rewritten -- evidence can never be "more precise".

    The commit's own committer date (`2026-08-20T00:00:00Z`) deliberately
    differs from the stamp already written (`2026-08-16T21:08:00Z`) -- if
    the sweep incorrectly treated "evidence found" as license to overwrite,
    this test would see the commit date leak into the proposal. G6-004
    requires the byte-original, already-full-precision stamp to survive
    untouched: evidence is only ever a strict *upgrade*, never a
    replacement of equal-or-lesser precision.
    """
    repo = _init_repo(tmp_path)
    slug = "full-stays-put"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [2026-08-16T21:08:00Z] G0: baseline decided\n"
        "## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [2026-08-16T21:08:00Z] G0: baseline decided\n"
        "- [2026-08-17T09:30:00Z] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-20T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    gate_lines = {line.gate: line.rendered for line in proposal.lines if line.gate is not None}
    # The bullet is still stripped (chunk 3's mandate), but the full-precision
    # stamp itself is byte-identical to what was written -- neither gate's
    # commit date (2026-08-01 / 2026-08-20) leaks into the rendered stamp.
    assert gate_lines[0] == "[2026-08-16T21:08:00Z] G0: baseline decided\n"
    assert gate_lines[1] == "[2026-08-17T09:30:00Z] G1: kickoff decided\n"


def test_minute_precision_stamp_upgrades_when_history_differentiates(tmp_path: Path) -> None:
    """A minute-precision stamp is upgraded to full precision, symmetrically with the date case."""
    repo = _init_repo(tmp_path)
    slug = "minute-to-full"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16T21:08Z] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T21:08:37+00:00",
    )
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [2026-08-16T21:08Z] G0: baseline decided\n"
        "- [2026-08-17T09:30Z] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-17T09:30:55+00:00",
    )

    proposal = sweeper.propose(manifest)

    gate_lines = {line.gate: line.rendered for line in proposal.lines if line.gate is not None}
    assert gate_lines[0] == "[2026-08-16T21:08:37Z] G0: baseline decided\n"
    assert gate_lines[1] == "[2026-08-17T09:30:55Z] G1: kickoff decided\n"


def test_genuinely_absent_stamp_still_becomes_unknown_with_no_evidence(tmp_path: Path) -> None:
    """A stamp already `UNKNOWN`, with no differentiating evidence, stays `UNKNOWN` -- not a downgrade.

    D-1's sentinel is the correct output exactly when no parseable stamp
    exists at all and no evidence can fill it in; G6-004 narrows *when*
    `UNKNOWN` is written, it does not remove it for this genuine case.
    """
    repo = _init_repo(tmp_path)
    manifest = _commit_manifest(
        repo,
        "still-unknown",
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    gate_lines = {line.gate: line.rendered for line in proposal.lines if line.gate is not None}
    assert gate_lines[0] == "[UNKNOWN] G0: baseline decided\n"
    assert proposal.unknown_count == 1


def test_reproduces_the_delta_report_example_verbatim(tmp_path: Path) -> None:
    """Regression anchor for the exact example quoted in `delta-report-G6-004.md`.

    Before the fix, this proposed replacing `[2026-08-16]` with `[UNKNOWN]`
    for a squashed-history record -- the defect that triggered G6-004.
    """
    repo = _init_repo(tmp_path)
    manifest = _commit_manifest(
        repo,
        "gate-record-integrity",
        "## Decisions locked\n"
        "- [2026-08-16] G1: base branch -- Phase 1 merged to `main` first "
        "(squash-only repo)\n"
        "## Decisions log\n",
        "2026-09-05T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    assert "[UNKNOWN]" not in proposal.proposed_text
    assert (
        "[2026-08-16] G1: base branch -- Phase 1 merged to `main` first (squash-only repo)"
        in proposal.proposed_text
    )


def _all_proposed_lines_for_property_check(path: Path) -> list[sweeper.ProposedLine]:
    """Return the proposal's lines for `path`, or `[]` when not applicable/aborted."""
    proposal = sweeper.propose(path)
    if not proposal.applicable or proposal.aborted:
        return []
    return list(proposal.lines)


def test_sweep_never_downgrades_a_stamp() -> None:
    """No line the sweep proposes ever carries less timestamp information than the line it replaces.

    This is the named monotonicity property G6-004 requires (`DECISIONS.md`
    G6-004: "the sweep is monotone -- it can only ever add information").
    Run over 100% of `tests/fixtures/gate_records/` (never a sampled
    subset, matching TC-42's own standard) and over the live corpus -- the
    same population whose 32/32, 6/6, and 5/5 downgrades triggered this
    corrective chunk in the first place.

    A line with `original is None` (the "added" source: a gate history
    evidences but the current block omits entirely) has nothing to compare
    against and is skipped -- there is no existing stamp for it to
    downgrade.
    """
    checked = 0
    for fixture_path in _all_fixture_paths():
        for line in _all_proposed_lines_for_property_check(fixture_path):
            if line.original is None:
                continue
            before = _stamp_precision_rank(line.original)
            after = _stamp_precision_rank(line.rendered)
            checked += 1
            assert after >= before, (
                f"{fixture_path.name}: stamp downgraded from rank {before} to {after}\n"
                f"  original: {line.original!r}\n  rendered: {line.rendered!r}"
            )
    assert checked > 0, "no fixture exercised the property -- test would pass vacuously"

    live_roots = [root for root in (Path.home() / ".claude" / "skills", Path.home() / "dev") if root.is_dir()]
    if not live_roots:
        pytest.skip("live corpus roots not present on this machine")
    try:
        records = census_mod.census(live_roots)
    except RuntimeError:
        pytest.skip("live census is empty on this machine")

    live_checked = 0
    for record in records:
        for line in _all_proposed_lines_for_property_check(record.path):
            if line.original is None:
                continue
            before = _stamp_precision_rank(line.original)
            after = _stamp_precision_rank(line.rendered)
            live_checked += 1
            assert after >= before, (
                f"{record.repo_root}/{record.slug}: stamp downgraded from rank {before} to {after}\n"
                f"  original: {line.original!r}\n  rendered: {line.rendered!r}"
            )
    assert live_checked > 0, "live corpus present but no record exercised the property"


# ---------------------------------------------------------------------------
# CLI: --dry-run writes nothing; --apply refuses unconditionally
# ---------------------------------------------------------------------------


def test_dry_run_cli_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A `--dry-run` invocation never modifies the target file (mtime and bytes both)."""
    repo = _init_repo(tmp_path)
    slug = "dry-run-project"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    before_mtime = manifest.stat().st_mtime_ns

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--dry-run"])

    assert exit_code == 0
    assert manifest.read_bytes() == before_bytes
    assert manifest.stat().st_mtime_ns == before_mtime
    captured = capsys.readouterr()
    assert captured.out  # a diff (or an explicit "nothing to propose") was printed


def test_default_invocation_without_dry_run_flag_still_writes_nothing(tmp_path: Path) -> None:
    """Omitting `--dry-run` is still a dry run in this build -- `--apply` is the only writer, and it refuses."""
    repo = _init_repo(tmp_path)
    slug = "default-mode-project"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug])

    assert exit_code == 0
    assert manifest.read_bytes() == before_bytes


def test_apply_flag_writes_the_record_via_the_backup_then_write_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--apply` is live from chunk 4: it backs up, then writes, and reports success (D-4/G6-004-d)."""
    repo = _init_repo(tmp_path)
    slug = "apply-writes-project"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"
    monkeypatch.setattr(sweeper, "_default_backup_root", lambda: backup_root)

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--apply"])

    assert exit_code == 0
    assert manifest.read_bytes() != before_bytes
    captured = capsys.readouterr()
    assert "applied" in captured.out.lower()


def test_dry_run_and_apply_are_mutually_exclusive() -> None:
    """The CLI itself rejects `--dry-run --apply` together, before any file is touched."""
    with pytest.raises(SystemExit):
        sweeper.build_parser().parse_args(
            ["--repo", "somewhere", "--slug", "x", "--dry-run", "--apply"]
        )


def test_missing_record_is_reported_as_not_applicable(tmp_path: Path) -> None:
    """A `--repo`/`--slug` pair with no `SUPERHUMAN.md` at all does not crash."""
    repo = tmp_path / "empty-repo"
    repo.mkdir()

    exit_code = sweeper.main(["--repo", str(repo), "--slug", "nothing-here", "--dry-run"])

    assert exit_code != 0


def test_cli_help_and_version() -> None:
    """The CLI supports `--help` and `--version` (python conventions)."""
    with pytest.raises(SystemExit) as exc_info:
        sweeper.build_parser().parse_args(["--help"])
    assert exc_info.value.code == 0

    with pytest.raises(SystemExit) as exc_info:
        sweeper.build_parser().parse_args(["--version"])
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Boundary-detection unit coverage (fence toggling, missing heading)
# ---------------------------------------------------------------------------


def test_find_section_bounds_returns_none_when_heading_absent() -> None:
    """No occurrence of the heading at all -- not even a decoy -- yields `None`."""
    lines = ["# Title\n", "## Decisions log\n", "[2026-08-01] G0: x\n"]
    assert sweeper._find_section_bounds(lines, grp.LOCKED_HEADING) is None


def test_find_section_bounds_extends_to_end_of_file_when_no_next_heading() -> None:
    """A locked block with nothing after it runs to end-of-file."""
    lines = ["## Decisions locked\n", "- [UNKNOWN] G0: x\n"]
    bounds = sweeper._find_section_bounds(lines, grp.LOCKED_HEADING)
    assert bounds == (0, 2)


def test_line_ending_variants_are_recognised() -> None:
    """`_line_ending` recognises CRLF, lone CR, lone LF, and no trailing newline."""
    assert sweeper._line_ending("x\r\n") == "\r\n"
    assert sweeper._line_ending("x\r") == "\r"
    assert sweeper._line_ending("x\n") == "\n"
    assert sweeper._line_ending("x") == ""


def test_missing_record_file_propose_does_not_crash(tmp_path: Path) -> None:
    """`propose()` on a nonexistent path raises the ordinary `read_bytes` error, not a crash-through."""
    with pytest.raises(OSError):
        sweeper.propose(tmp_path / "does-not-exist" / "SUPERHUMAN.md")


def test_aborted_proposal_helper_leaves_the_text_unchanged() -> None:
    """`_aborted` reports the fatal FR-9 path: applicable, aborted, text untouched."""
    proposal = sweeper._aborted(Path("SUPERHUMAN.md"), "original text\n", "a byte outside the block would change")

    assert proposal.applicable is True
    assert proposal.aborted is True
    assert proposal.abort_reason == "a byte outside the block would change"
    assert proposal.proposed_text == proposal.original_text == "original text\n"
    assert proposal.diff == ""
    assert proposal.lines == ()


def test_cli_surfaces_an_aborted_proposal_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If `propose()` ever reports an FR-9 abort, the CLI surfaces it and exits non-zero.

    `propose()` itself never produces this outcome through the public API
    today (the boundary it guards against is structurally unreachable given
    how the block is sliced) -- this test pins the CLI's own handling of
    that outcome independently, via a monkeypatched `propose`, so the
    reporting path is verified rather than merely assumed correct.
    """
    repo = _init_repo(tmp_path)
    slug = "aborted-project"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()

    def _fake_propose(path: Path) -> sweeper.Proposal:
        return sweeper._aborted(path, before_bytes.decode("utf-8"), "simulated FR-9 violation")

    monkeypatch.setattr(sweeper, "propose", _fake_propose)

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--dry-run"])

    assert exit_code == 1
    assert manifest.read_bytes() == before_bytes
    assert "ABORTED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Additional edge cases (fence closing after the real heading, single-line
# comments, blank added lines, diff-marker-shaped content, untracked files,
# duplicate-addition ordering, and the remaining CLI branches)
# ---------------------------------------------------------------------------


def test_find_section_bounds_handles_a_fence_that_opens_after_the_real_heading() -> None:
    """A fence inside the real section (not just ahead of a decoy) still toggles correctly."""
    lines = [
        "## Decisions locked\n",
        "- [UNKNOWN] G0: see example below\n",
        "```\n",
        "## Decisions locked (this is quoted, not a real heading)\n",
        "```\n",
        "## Decisions log\n",
    ]
    bounds = sweeper._find_section_bounds(lines, grp.LOCKED_HEADING)
    assert bounds == (0, 5)


def test_single_line_html_comment_closes_on_the_same_line(tmp_path: Path) -> None:
    """A `<!-- ... -->` comment fully closed on one line never opens multi-line state."""
    repo = _init_repo(tmp_path)
    manifest = _commit_manifest(
        repo,
        "single-line-comment",
        "## Decisions locked\n"
        "<!-- short note -->\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    assert proposal.applicable is True
    assert "<!-- short note -->\n" in proposal.proposed_text
    assert len(proposal.lines) == 1


def test_a_blank_added_line_in_a_diff_is_ignored_as_evidence(tmp_path: Path) -> None:
    """A blank line a commit's diff added contributes no evidence and is never matched."""
    repo = _init_repo(tmp_path)
    slug = "blank-line-in-diff"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n\n- [UNKNOWN] G0: baseline decided\n\n## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    assert proposal.applicable is True
    assert proposal.aborted is False


def test_added_content_line_shaped_like_a_diff_marker_is_not_mistaken_for_a_diff_header(
    tmp_path: Path,
) -> None:
    """Decision text that happens to start with `+++`/`---` is still read as real content."""
    repo = _init_repo(tmp_path)
    slug = "diff-marker-lookalike"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: --- kept as a literal prefix, not a diff header\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    gate_one = next(line for line in proposal.lines if line.gate == 1)
    assert gate_one.rendered == "[2026-08-02T00:00:00Z] G1: --- kept as a literal prefix, not a diff header\n"


def test_untracked_file_with_no_commits_yields_unknown(tmp_path: Path) -> None:
    """A file that exists inside a repo but was never committed has empty, not failing, git output.

    `git log` on an untracked path returns exit 0 with empty stdout, a
    different case from "not a git repository at all" (exit 128) --
    both must resolve to "no evidence" without a crash (FR-8).
    """
    repo = _init_repo(tmp_path)
    # A repo with no commits at all makes `git log` exit 128 ("does not
    # have any commits yet"); committing something unrelated first isolates
    # the actually-interesting case: HEAD exists, but this file was never
    # part of it.
    _commit_manifest(
        repo,
        "unrelated",
        "## Decisions log\n[2026-01-01T00:00:00Z] G0: unrelated\n",
        "2026-01-01T00:00:00+00:00",
    )
    manifest_dir = repo / "docs" / "superhuman" / "untracked"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "SUPERHUMAN.md"
    manifest.write_text(
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        encoding="utf-8",
        newline="",
    )

    proposal = sweeper.propose(manifest)

    assert proposal.applicable is True
    assert proposal.unknown_count == 1


def test_extract_added_lines_skips_diff_markers_but_keeps_marker_shaped_content() -> None:
    """`+++`/`---` diff-header lines are skipped; ordinary content merely starting with them is not."""
    patch = (
        "diff --git a/x b/x\n"
        "index 0000000..1111111 100644\n"
        "--- a/x\n"
        "+++ b/x\n"
        "@@ -0,0 +1,2 @@\n"
        "+--- not a diff header, just content\n"
        "+real line\n"
    )
    added = sweeper._extract_added_lines(patch)
    assert added == ("--- not a diff header, just content", "real line")


def test_git_log_for_evidence_returns_empty_when_subprocess_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A `git` invocation failure (e.g. the binary is missing) is "no evidence", never a crash."""

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert sweeper._git_log_for_evidence(tmp_path / "SUPERHUMAN.md") == []


def test_continuation_line_inside_the_locked_block_is_preserved_verbatim(tmp_path: Path) -> None:
    """A hard-wrapped continuation line (G6-002) is never reflowed by the sweep."""
    repo = _init_repo(tmp_path)
    manifest = _commit_manifest(
        repo,
        "continuation",
        "## Decisions locked\n"
        "- [UNKNOWN] G3: a long decision that continues\n"
        "  onto a second physical line with no stamp of its own\n"
        "## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    assert proposal.applicable is True
    assert "  onto a second physical line with no stamp of its own\n" in proposal.proposed_text
    # Only the one true entry line was considered; the continuation never
    # became a `ProposedLine` of its own.
    assert len(proposal.lines) == 1


def test_ld_repair_uses_real_evidence_when_history_differentiates(tmp_path: Path) -> None:
    """An `LD-n` line's reconstructed stamp is a real commit date when history allows it.

    The repair stays purely additive (G6-006): the rendered line keeps its
    `LD-n (G<m>)` identifier verbatim and contributes no gate, even though
    real git evidence exists for the stamp slot.
    """
    repo = _init_repo(tmp_path)
    slug = "ld-with-evidence"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked -- do not relitigate\n"
        "- **LD-1 (G0):** first locked decision\n"
        "## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked -- do not relitigate\n"
        "- **LD-1 (G0):** first locked decision\n"
        "- **LD-2 (G1):** second locked decision\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    repaired = [line for line in proposal.lines if line.source == "repaired"]
    assert len(repaired) == 2
    assert all(line.gate is None for line in repaired)
    rendered_by_id = {}
    for line in repaired:
        key = "LD-1" if "LD-1" in line.rendered else "LD-2"
        rendered_by_id[key] = line.rendered
    assert rendered_by_id["LD-1"] == "[2026-08-01T00:00:00Z] LD-1 (G0): first locked decision\n"
    assert rendered_by_id["LD-2"] == "[2026-08-02T00:00:00Z] LD-2 (G1): second locked decision\n"
    assert proposal.unknown_count == 0


def test_addition_candidates_keep_the_earliest_stamp_for_a_duplicated_gate(tmp_path: Path) -> None:
    """When two historical variants evidence the same missing gate, the earliest stamp wins."""
    repo = _init_repo(tmp_path)
    slug = "duplicate-gate-history"
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G1: first wording\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G1: second wording, later edit\n## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    # The current content drops gate 1 entirely, so both historical
    # variants are candidates for re-adding.
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n## Decisions log\n",
        "2026-08-03T00:00:00+00:00",
    )

    proposal = sweeper.propose(manifest)

    added = [line for line in proposal.lines if line.source == "added" and line.gate == 1]
    assert len(added) == 1
    assert added[0].rendered == "[2026-08-01T00:00:00Z] G1: first wording\n"


def test_cli_reports_a_not_applicable_record_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A record with no `## Decisions locked` block is reported, not treated as an error."""
    repo = _init_repo(tmp_path)
    slug = "log-only-project"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions log\n[2026-08-01T00:00:00Z] G0: baseline\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--dry-run"])

    assert exit_code == 0
    assert manifest.read_bytes() == before_bytes
    assert "nothing to propose" in capsys.readouterr().out


def test_cli_reports_no_changes_when_the_block_is_already_normalised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A block that is already bullet-free with an `[UNKNOWN]` stamp proposes no visible change."""
    repo = _init_repo(tmp_path)
    slug = "already-normalised-project"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n[UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--dry-run"])

    assert exit_code == 0
    assert manifest.read_bytes() == before_bytes
    assert "no changes proposed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Chunk 4: the write path (`apply()` / `--apply`) -- backup, verify, overwrite.
#
# DECISIONS.md G6-004-d: for records with no git history to revert to (this
# repo's own, `.gitignore`d), the backup directory is the ONLY recovery
# mechanism that exists -- so it must fail closed on any backup failure,
# never overwrite the real file before the backup is written AND verified
# byte-identical, and never write anything at all for a proposal with
# nothing to change.
# ---------------------------------------------------------------------------


def test_apply_backs_up_before_overwriting(tmp_path: Path) -> None:
    """`apply()` writes a full-file, byte-identical backup, then overwrites the record."""
    repo = _init_repo(tmp_path)
    slug = "apply-writes"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T00:00:00+00:00",
    )
    original_bytes = manifest.read_bytes()
    expected_proposed_text = sweeper.propose(manifest).proposed_text
    backup_root = tmp_path / "backups"

    result = sweeper.apply(repo, slug, run_id="run-1", backup_root=backup_root)

    assert result.outcome == "applied"
    assert result.wrote is True
    assert result.backup_path is not None
    assert result.backup_path.is_file()
    assert result.backup_path.read_bytes() == original_bytes
    assert manifest.read_text(encoding="utf-8") == expected_proposed_text
    assert manifest.read_bytes() != original_bytes


def test_apply_on_unchanged_record_is_a_noop(tmp_path: Path) -> None:
    """An already-normalised record is a no-op: nothing written, nothing backed up."""
    repo = _init_repo(tmp_path)
    slug = "already-normalised"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n[2026-08-16T21:08:00Z] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T21:08:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    before_mtime = manifest.stat().st_mtime_ns
    backup_root = tmp_path / "backups"

    result = sweeper.apply(repo, slug, run_id="run-noop", backup_root=backup_root)

    assert result.outcome == "unchanged"
    assert result.wrote is False
    assert result.backup_path is None
    assert manifest.read_bytes() == before_bytes
    assert manifest.stat().st_mtime_ns == before_mtime
    assert not backup_root.exists()


def test_apply_on_record_with_no_locked_block_is_a_noop(tmp_path: Path) -> None:
    """A record with only `## Decisions log` is not applicable -- no write, no backup."""
    repo = _init_repo(tmp_path)
    slug = "no-locked-block-apply"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions log\n[2026-08-01T00:00:00Z] G0: baseline\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"

    result = sweeper.apply(repo, slug, run_id="run-na", backup_root=backup_root)

    assert result.outcome == "not_applicable"
    assert result.wrote is False
    assert result.backup_path is None
    assert manifest.read_bytes() == before_bytes
    assert not backup_root.exists()


def test_apply_never_writes_a_proposal_that_would_touch_bytes_outside_the_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A proposal FR-9 would abort (bytes outside the block would change) is never written.

    `_propose_from_text` is monkeypatched to simulate the FR-9-violation
    outcome directly -- `propose()` itself cannot produce it through the
    public API (the boundary it guards against is structurally unreachable
    given how the block is sliced), exactly as the existing dry-run
    equivalent (`test_cli_surfaces_an_aborted_proposal_and_writes_nothing`)
    already does for the read-only path.
    """
    repo = _init_repo(tmp_path)
    slug = "aborted-apply"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"

    def _fake_propose_from_text(path: Path, original_text: str) -> sweeper.Proposal:
        return sweeper._aborted(path, original_text, "simulated FR-9 violation")

    monkeypatch.setattr(sweeper, "_propose_from_text", _fake_propose_from_text)

    result = sweeper.apply(repo, slug, run_id="run-abort", backup_root=backup_root)

    assert result.outcome == "aborted"
    assert result.wrote is False
    assert result.backup_path is None
    assert manifest.read_bytes() == before_bytes
    assert not backup_root.exists()


def test_apply_fails_closed_when_the_backup_cannot_be_written(tmp_path: Path) -> None:
    """A genuine backup-write failure (a colliding path) aborts before the record is touched.

    The collision is real, not simulated: a plain file sits where the
    backup's own parent directory needs to be created, so
    `Path.mkdir(parents=True)` raises a real `OSError` -- the fail-closed
    path this test exercises is the one `apply()` actually hits, not a
    monkeypatched stand-in.
    """
    repo = _init_repo(tmp_path)
    slug = "backup-fails"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"
    repo_slug_safe = sweeper._repo_slug_safe(repo)
    collision = backup_root / "run-fail" / "files" / f"{repo_slug_safe}__{slug}"
    collision.parent.mkdir(parents=True)
    collision.write_text("a plain file where the backup directory must go")

    result = sweeper.apply(repo, slug, run_id="run-fail", backup_root=backup_root)

    assert result.outcome == "backup_failed"
    assert result.wrote is False
    assert manifest.read_bytes() == before_bytes


def test_apply_on_missing_record_reports_read_failed(tmp_path: Path) -> None:
    """A `--repo`/`--slug` pair with no `SUPERHUMAN.md` fails closed, not a crash."""
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    backup_root = tmp_path / "backups"

    result = sweeper.apply(repo, "nothing-here", backup_root=backup_root)

    assert result.outcome == "read_failed"
    assert result.wrote is False
    assert result.backup_path is None
    assert not backup_root.exists()


def test_apply_writes_a_manifest_entry_for_the_backed_up_record(tmp_path: Path) -> None:
    """A successful apply appends `{repo_root, slug, path, backed_up_at, pre_backup_head_sha}` to manifest.json."""
    repo = _init_repo(tmp_path)
    slug = "manifest-entry"
    manifest_file = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T00:00:00+00:00",
    )
    backup_root = tmp_path / "backups"

    result = sweeper.apply(repo, slug, run_id="run-manifest", backup_root=backup_root)

    assert result.outcome == "applied"
    manifest_json = backup_root / "run-manifest" / "manifest.json"
    assert manifest_json.is_file()
    entries = json.loads(manifest_json.read_text(encoding="utf-8"))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["slug"] == slug
    assert entry["path"] == str(manifest_file)
    assert entry["repo_root"] == str(repo.resolve())
    assert entry["backed_up_at"].endswith("Z")
    assert entry["pre_backup_head_sha"]  # the disposable repo has a real HEAD commit


def test_write_and_verify_backup_succeeds_for_matching_bytes(tmp_path: Path) -> None:
    """The low-level backup helper writes and reads back byte-identical content."""
    backup_path = tmp_path / "backups" / "run" / "files" / "r__s" / "SUPERHUMAN.md"
    sweeper._write_and_verify_backup(backup_path, b"hello world")
    assert backup_path.read_bytes() == b"hello world"


def test_write_and_verify_backup_raises_on_a_corrupted_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A write that silently corrupts (read-back != what was requested) raises, never returns quietly."""
    backup_path = tmp_path / "backups" / "run" / "files" / "r__s" / "SUPERHUMAN.md"
    real_write_bytes = Path.write_bytes

    def _corrupting_write_bytes(self: Path, data: bytes) -> int:
        return real_write_bytes(self, data + b"\ncorruption")

    monkeypatch.setattr(Path, "write_bytes", _corrupting_write_bytes)

    with pytest.raises(sweeper.BackupVerificationError):
        sweeper._write_and_verify_backup(backup_path, b"original content")


def test_repo_slug_safe_is_filesystem_safe_and_deterministic(tmp_path: Path) -> None:
    """`_repo_slug_safe` strips path separators/colons and is stable for the same repo."""
    safe = sweeper._repo_slug_safe(tmp_path)
    assert "/" not in safe and "\\" not in safe and ":" not in safe
    assert safe == sweeper._repo_slug_safe(tmp_path)


def test_apply_cli_reports_backup_failure_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI surfaces a fail-closed backup error distinctly, and exits non-zero."""
    repo = _init_repo(tmp_path)
    slug = "cli-backup-fails"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"
    repo_slug_safe = sweeper._repo_slug_safe(repo)
    collision = backup_root / "will-collide" / "files" / f"{repo_slug_safe}__{slug}"
    collision.parent.mkdir(parents=True)
    collision.write_text("blocks the backup directory")
    monkeypatch.setattr(sweeper, "_default_backup_root", lambda: backup_root)
    monkeypatch.setattr(sweeper, "_new_run_id", lambda: "will-collide")

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--apply"])

    assert exit_code != 0
    assert manifest.read_bytes() == before_bytes
    assert "backup" in capsys.readouterr().err.lower()


def test_apply_cli_reports_unchanged_record_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--apply` on an already-normalised record reports the no-op and writes nothing."""
    repo = _init_repo(tmp_path)
    slug = "cli-unchanged"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n[2026-08-16T21:08:00Z] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T21:08:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"
    monkeypatch.setattr(sweeper, "_default_backup_root", lambda: backup_root)

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--apply"])

    assert exit_code == 0
    assert manifest.read_bytes() == before_bytes
    assert "no changes applied" in capsys.readouterr().out.lower()


def test_apply_cli_reports_read_failed_for_a_missing_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--apply` against a nonexistent record exits non-zero and names the reason."""
    repo = tmp_path / "empty-repo"
    repo.mkdir()

    exit_code = sweeper.main(["--repo", str(repo), "--slug", "nothing-here", "--apply"])

    assert exit_code == 1
    assert "cannot read" in capsys.readouterr().err.lower()


def test_apply_cli_reports_not_applicable_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--apply` on a record with no `## Decisions locked` block reports the no-op via the CLI."""
    repo = _init_repo(tmp_path)
    slug = "cli-not-applicable"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions log\n[2026-08-01T00:00:00Z] G0: baseline\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--apply"])

    assert exit_code == 0
    assert manifest.read_bytes() == before_bytes
    assert "nothing to apply" in capsys.readouterr().out.lower()


def test_apply_cli_reports_an_aborted_proposal_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--apply` surfaces an FR-9 abort exactly as the dry-run CLI path already does."""
    repo = _init_repo(tmp_path)
    slug = "cli-aborted"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()

    def _fake_propose_from_text(path: Path, original_text: str) -> sweeper.Proposal:
        return sweeper._aborted(path, original_text, "simulated FR-9 violation")

    monkeypatch.setattr(sweeper, "_propose_from_text", _fake_propose_from_text)

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--apply"])

    assert exit_code == 1
    assert manifest.read_bytes() == before_bytes
    assert "ABORTED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Backup-layer helper coverage: `_default_backup_root`, `_long_path`, `_head_sha`,
# and the corrupted-manifest.json fallback in `_append_manifest_entry`.
# ---------------------------------------------------------------------------


def test_default_backup_root_points_under_home_dot_superhuman() -> None:
    """The real default (no override) is `~/.superhuman/gate-record-backups`."""
    assert sweeper._default_backup_root() == Path.home() / ".superhuman" / "gate-record-backups"


def test_long_path_is_a_noop_on_a_non_windows_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`_long_path` never rewrites a path on a platform without the MAX_PATH limitation."""
    monkeypatch.setattr(sys, "platform", "linux")
    target = tmp_path / "SUPERHUMAN.md"
    assert sweeper._long_path(target) == target


def test_long_path_is_idempotent_when_already_prefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that already carries the `\\\\?\\` prefix is returned unchanged."""
    monkeypatch.setattr(sys, "platform", "win32")
    already_prefixed = Path("\\\\?\\C:\\already\\prefixed")
    assert sweeper._long_path(already_prefixed) == already_prefixed


def test_long_path_absolutises_a_relative_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative path is made absolute before the `\\\\?\\` prefix is applied."""
    monkeypatch.setattr(sys, "platform", "win32")
    result = sweeper._long_path(Path("relative") / "x.md")
    text = str(result)
    assert text.startswith("\\\\?\\")
    assert "relative" in text
    assert "/" not in text[4:]


def test_head_sha_returns_none_when_git_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A `git` invocation failure (missing binary, etc.) is `None`, not a crash."""

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert sweeper._head_sha(tmp_path) is None


def test_head_sha_returns_none_for_a_repo_with_no_commits(tmp_path: Path) -> None:
    """A freshly `git init`ed repo with no commits yet has no `HEAD` -- `None`, not a crash."""
    repo = _init_repo(tmp_path)
    assert sweeper._head_sha(repo) is None


def test_head_sha_returns_the_real_sha_for_a_repo_with_a_commit(tmp_path: Path) -> None:
    """`_head_sha` returns the actual `HEAD` SHA when one exists."""
    repo = _init_repo(tmp_path)
    _commit_manifest(
        repo, "some-slug", "## Decisions log\n[2026-08-01T00:00:00Z] G0: x\n", "2026-08-01T00:00:00+00:00"
    )
    sha = sweeper._head_sha(repo)
    assert sha is not None
    log = subprocess.run(
        ["git", "log", "--pretty=format:%H"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert sha in log


def test_append_manifest_entry_recovers_from_a_corrupted_manifest_json(tmp_path: Path) -> None:
    """A pre-existing but corrupted `manifest.json` is treated as empty, not a crash."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text("{ this is not valid json", encoding="utf-8")
    repo = _init_repo(tmp_path)
    manifest_file = _commit_manifest(
        repo, "some-slug", "## Decisions log\n[2026-08-01T00:00:00Z] G0: x\n", "2026-08-01T00:00:00+00:00"
    )

    sweeper._append_manifest_entry(run_dir, repo, "some-slug", manifest_file)

    entries = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["slug"] == "some-slug"


# ---------------------------------------------------------------------------
# Chunk 4e: pre-write divergence check (a newer committed version of the
# record may live on an unmerged branch or worktree; the checked-out copy
# must never be swept over it -- see the chunk brief and the module
# docstring's divergence note).
# ---------------------------------------------------------------------------


def _checkout_new_branch(repo: Path, name: str) -> None:
    """Create and switch to a new branch `name` from the current `HEAD`."""
    _run_git(repo, "checkout", "-b", name)


def _current_head_sha(repo: Path) -> str:
    """Return `repo`'s current `HEAD` commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_check_divergence_detects_a_newer_commit_on_an_unmerged_branch(tmp_path: Path) -> None:
    """A record newest on an unmerged branch is reported unsafe to write, with that branch named."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    slug = "divergent-project"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    _checkout_new_branch(repo, "feature")
    newer_manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    newer_sha = _current_head_sha(repo)
    _run_git(repo, "checkout", "trunk")

    result = sweeper.check_divergence(manifest)

    assert result.safe_to_write is False
    assert result.divergent is True
    assert result.newest_commit == newer_sha
    assert "feature" in result.branches
    assert result.reason is not None and newer_sha in result.reason


def test_check_divergence_is_safe_after_the_unmerged_branch_merges(tmp_path: Path) -> None:
    """Once the feature branch merges in, the checked-out copy is the newest known version."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    slug = "will-merge"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    _checkout_new_branch(repo, "feature")
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    _run_git(repo, "checkout", "trunk")
    _run_git(repo, "merge", "feature", "--ff-only")

    result = sweeper.check_divergence(manifest)

    assert result.safe_to_write is True
    assert result.divergent is False
    assert result.head_commit == result.newest_commit
    assert result.reason is None


def test_check_divergence_treats_a_genuinely_untracked_record_as_safe(tmp_path: Path) -> None:
    """A record with no commit touching it, on any ref, has no newer version by definition."""
    repo = _init_repo(tmp_path)
    _commit_manifest(
        repo,
        "unrelated",
        "## Decisions log\n[2026-01-01T00:00:00Z] G0: unrelated\n",
        "2026-01-01T00:00:00+00:00",
    )
    manifest_dir = repo / "docs" / "superhuman" / "untracked-divergence"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "SUPERHUMAN.md"
    manifest.write_text(
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        encoding="utf-8",
        newline="",
    )

    result = sweeper.check_divergence(manifest)

    assert result.safe_to_write is True
    assert result.divergent is False
    assert result.head_commit is None
    assert result.newest_commit is None
    assert result.reason is None


def test_git_log_one_sha_reports_not_ok_when_git_exits_nonzero_without_raising(
    tmp_path: Path,
) -> None:
    """A path outside any git repository makes `git log` exit nonzero -- `ok=False`, no exception.

    Distinct from `test_check_divergence_fails_closed_on_a_subprocess_error`,
    which simulates the binary being entirely absent (an `OSError`): here
    `git` runs fine and simply reports "not a git repository" via its exit
    code, the other route by which `_git_log_one_sha` must report
    "could not tell" (`ok=False`) rather than mistaking it for "no history"
    (`ok=True, sha=None`).
    """
    outside_any_repo = tmp_path / "no-repo-here" / "docs" / "superhuman" / "orphan" / "SUPERHUMAN.md"
    outside_any_repo.parent.mkdir(parents=True)
    outside_any_repo.write_text("## Decisions locked\n- [UNKNOWN] G0: x\n", encoding="utf-8")

    ok, sha = sweeper._git_log_one_sha(outside_any_repo, all_refs=False)

    assert ok is False
    assert sha is None


def test_branches_containing_returns_empty_when_git_exits_nonzero_without_raising(
    tmp_path: Path,
) -> None:
    """An unresolvable commit SHA makes `git branch --contains` exit nonzero -- `()`, no exception."""
    repo = _init_repo(tmp_path)
    _commit_manifest(
        repo, "some-slug", "## Decisions log\n[2026-08-01T00:00:00Z] G0: x\n", "2026-08-01T00:00:00+00:00"
    )

    branches = sweeper._branches_containing(repo / "docs" / "superhuman" / "some-slug" / "SUPERHUMAN.md", "0" * 40)

    assert branches == ()


def test_branches_containing_skips_blank_lines_and_the_remote_head_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A blank line and a `HEAD -> origin/main` alias line are both filtered, never appended.

    `git branch -a` output can contain a blank trailing line and, when a
    remote is configured, a `remotes/origin/HEAD -> origin/main` alias line
    -- neither is a real branch name. This exercises that filtering
    directly via a stubbed `subprocess.run`, since none of this module's
    disposable test repos configure a remote.
    """

    class _FakeResult:
        returncode = 0
        stdout = "  main\n* feature\n\nremotes/origin/HEAD -> origin/main\nremotes/origin/feature\n"

    def _fake_run(*_args: object, **_kwargs: object) -> _FakeResult:
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    branches = sweeper._branches_containing(tmp_path / "SUPERHUMAN.md", "deadbeef")

    assert branches == ("main", "feature", "remotes/origin/feature")


def test_check_divergence_fails_closed_on_a_subprocess_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `git` invocation failure (missing binary, etc.) is reported unsafe, never safe."""

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    result = sweeper.check_divergence(tmp_path / "some-repo" / "SUPERHUMAN.md")

    assert result.safe_to_write is False
    assert result.divergent is False
    assert result.reason is not None and "fail" in result.reason.lower()


def test_check_divergence_handles_detached_head_in_sync_with_the_branch_tip(tmp_path: Path) -> None:
    """A detached checkout exactly at the only branch's tip is still safe to write (no divergence)."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    manifest = _commit_manifest(
        repo,
        "detached-in-sync",
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    tip_sha = _current_head_sha(repo)
    _run_git(repo, "checkout", tip_sha)  # detach HEAD, exactly at trunk's tip

    result = sweeper.check_divergence(manifest)

    assert result.safe_to_write is True
    assert result.divergent is False


def test_check_divergence_detects_divergence_from_a_detached_head_behind_the_tip(
    tmp_path: Path,
) -> None:
    """A detached checkout at an older commit is behind the branch's real tip -- unsafe to write."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    slug = "detached-behind"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    old_sha = _current_head_sha(repo)
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    newest_sha = _current_head_sha(repo)
    _run_git(repo, "checkout", old_sha)  # detach HEAD, behind trunk's real tip

    result = sweeper.check_divergence(manifest)

    assert result.safe_to_write is False
    assert result.divergent is True
    assert result.newest_commit == newest_sha
    assert result.head_commit == old_sha


def test_branches_containing_lists_the_branch_carrying_the_newer_commit(tmp_path: Path) -> None:
    """`_branches_containing` names the branch(es) that actually carry a given commit."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    manifest = _commit_manifest(
        repo,
        "branch-listing",
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    _checkout_new_branch(repo, "feature-x")
    _commit_manifest(
        repo,
        "branch-listing",
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    sha = _current_head_sha(repo)

    branches = sweeper._branches_containing(manifest, sha)

    assert "feature-x" in branches


def test_branches_containing_returns_empty_on_a_subprocess_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failure listing branches never raises -- it degrades to an empty diagnostic tuple."""

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert sweeper._branches_containing(tmp_path / "SUPERHUMAN.md", "deadbeef") == ()


# ---------------------------------------------------------------------------
# `apply()` gates on the divergence check; `--dry-run` surfaces it prominently
# ---------------------------------------------------------------------------


def test_apply_refuses_to_write_a_divergent_record(tmp_path: Path) -> None:
    """`apply()` refuses to overwrite a record that is not the newest known version."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    slug = "apply-divergent"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    _checkout_new_branch(repo, "feature")
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    _run_git(repo, "checkout", "trunk")
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"

    result = sweeper.apply(repo, slug, run_id="run-divergent", backup_root=backup_root)

    assert result.outcome == "divergent"
    assert result.wrote is False
    assert result.backup_path is None
    assert manifest.read_bytes() == before_bytes
    assert not backup_root.exists()
    assert result.detail is not None and "feature" in result.detail


def test_apply_writes_a_genuinely_untracked_record_normally(tmp_path: Path) -> None:
    """`apply()` still writes a record with no git history anywhere -- untracked is safe (D-4/G6-004-d)."""
    repo = _init_repo(tmp_path)
    _commit_manifest(
        repo,
        "unrelated",
        "## Decisions log\n[2026-01-01T00:00:00Z] G0: unrelated\n",
        "2026-01-01T00:00:00+00:00",
    )
    manifest_dir = repo / "docs" / "superhuman" / "untracked-apply"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "SUPERHUMAN.md"
    manifest.write_text(
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        encoding="utf-8",
        newline="",
    )
    backup_root = tmp_path / "backups"

    result = sweeper.apply(repo, "untracked-apply", run_id="run-untracked", backup_root=backup_root)

    assert result.outcome == "applied"
    assert result.wrote is True
    assert manifest.read_text(encoding="utf-8") == "## Decisions locked\n[UNKNOWN] G0: baseline decided\n## Decisions log\n"


def test_apply_fails_closed_when_the_divergence_check_itself_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the divergence check cannot be run at all, `apply()` refuses to write (fail closed)."""
    repo = _init_repo(tmp_path)
    slug = "divergence-check-errors"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T00:00:00+00:00",
    )
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    result = sweeper.apply(repo, slug, run_id="run-check-fails", backup_root=backup_root)

    assert result.outcome == "divergence_unknown"
    assert result.wrote is False
    assert result.backup_path is None
    assert manifest.read_bytes() == before_bytes
    assert not backup_root.exists()


def test_apply_continues_processing_other_records_after_skipping_a_divergent_one(
    tmp_path: Path,
) -> None:
    """One divergent record among several is skipped without aborting the others (no raise, ever)."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    divergent_slug = "batch-divergent"
    divergent_manifest = _commit_manifest(
        repo,
        divergent_slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    _checkout_new_branch(repo, "feature")
    _commit_manifest(
        repo,
        divergent_slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    _run_git(repo, "checkout", "trunk")
    normal_slug_a = _commit_and_slug(repo, "batch-normal-a")
    normal_slug_b = _commit_and_slug(repo, "batch-normal-b")
    backup_root = tmp_path / "backups"

    results = {
        slug: sweeper.apply(repo, slug, run_id="run-batch", backup_root=backup_root)
        for slug in (divergent_slug, normal_slug_a, normal_slug_b)
    }

    assert results[divergent_slug].outcome == "divergent"
    assert results[divergent_slug].wrote is False
    assert results[normal_slug_a].outcome == "applied"
    assert results[normal_slug_a].wrote is True
    assert results[normal_slug_b].outcome == "applied"
    assert results[normal_slug_b].wrote is True
    # The divergent manifest itself was never touched.
    assert "kickoff decided" not in divergent_manifest.read_text(encoding="utf-8")


def _commit_and_slug(repo: Path, slug: str) -> str:
    """Commit a normalisable record for `slug` on the current branch; return `slug`."""
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    return slug


def test_apply_cli_refuses_to_write_a_divergent_record_and_reports_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--apply` surfaces a divergent record loudly on stderr and exits non-zero, writing nothing."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    slug = "cli-divergent"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    _checkout_new_branch(repo, "feature")
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    _run_git(repo, "checkout", "trunk")
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"
    monkeypatch.setattr(sweeper, "_default_backup_root", lambda: backup_root)

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--apply"])

    assert exit_code == 1
    assert manifest.read_bytes() == before_bytes
    err = capsys.readouterr().err
    assert "DIVERGENT" in err
    assert "feature" in err


def test_dry_run_cli_surfaces_divergence_prominently_but_still_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--dry-run` still renders the diff, but a loud divergence warning precedes it."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    slug = "cli-dry-run-divergent"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    _checkout_new_branch(repo, "feature")
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    _run_git(repo, "checkout", "trunk")
    before_bytes = manifest.read_bytes()

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--dry-run"])

    assert exit_code == 0
    assert manifest.read_bytes() == before_bytes
    captured = capsys.readouterr()
    assert "DIVERGENT" in captured.err
    assert "feature" in captured.err
    assert captured.out  # the diff itself is still rendered


def test_dry_run_cli_is_silent_about_divergence_when_there_is_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-divergent record's dry-run never mentions divergence at all."""
    repo = _init_repo(tmp_path)
    slug = "cli-dry-run-in-sync"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [2026-08-16] G0: baseline decided\n## Decisions log\n",
        "2026-08-16T00:00:00+00:00",
    )

    exit_code = sweeper.main(["--repo", str(repo), "--slug", slug, "--dry-run"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "DIVERGENT" not in captured.err
    assert captured.out


# ---------------------------------------------------------------------------
# G6-005-e constructed-case requirement (TEST.md 9.1): the live-corpus run is
# necessary but not sufficient. This is the SAME detached-HEAD constructed
# case already exercised above at the `check_divergence` unit level
# (`test_check_divergence_handles_detached_head_in_sync_with_the_branch_tip`,
# `test_check_divergence_detects_divergence_from_a_detached_head_behind_the_tip`);
# this test pins it end-to-end through `apply()`, which is the write path
# G6-005-e's rule actually governs.
# ---------------------------------------------------------------------------


def test_apply_end_to_end_with_a_detached_head_repository(tmp_path: Path) -> None:
    """`apply()` refuses a detached-HEAD checkout that is behind the branch's real tip."""
    repo = _init_repo(tmp_path)
    _checkout_new_branch(repo, "trunk")
    slug = "detached-apply"
    manifest = _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n- [UNKNOWN] G0: baseline decided\n## Decisions log\n",
        "2026-08-01T00:00:00+00:00",
    )
    old_sha = _current_head_sha(repo)
    _commit_manifest(
        repo,
        slug,
        "## Decisions locked\n"
        "- [UNKNOWN] G0: baseline decided\n"
        "- [UNKNOWN] G1: kickoff decided\n"
        "## Decisions log\n",
        "2026-08-02T00:00:00+00:00",
    )
    _run_git(repo, "checkout", old_sha)
    before_bytes = manifest.read_bytes()
    backup_root = tmp_path / "backups"

    result = sweeper.apply(repo, slug, run_id="run-detached", backup_root=backup_root)

    assert result.outcome == "divergent"
    assert result.wrote is False
    assert manifest.read_bytes() == before_bytes
    assert not backup_root.exists()
