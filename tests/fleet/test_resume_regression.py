"""Chunk 7 resume regression — the automatable half of `W-NFR-4` rule 4
("a pre-existing project resumes and fires the same gates in the same
order").

Per TEST.md's "W-NFR-4 automatability ruling": the *live-execution* half of
this claim — an orchestrating model actually resuming a real project and
visiting the same gates — has no code path in this repo to execute. PM's
phase progression is itself prose-mediated; no module here parses
`SUPERHUMAN.md` and decides what a resuming session does next; there is
nothing to unit-test that would not be theater (spinning up a live
orchestrating-model session is explicitly out of scope for this suite — see
`docs/superhuman/fleet-wiring/TEST.md`'s ruling and `README.md`'s existing
"full subagent-dispatch smoke is manual-only" precedent for W-FR-1).

What *is* mechanically checkable is the specification a resuming project
reads: `phases/*.md`, `roles/pm.md`, and `SKILL.md`. If none of their
pre-existing lines changed (`TestAdditiveDiffInvarianceFullScope`, TC-24 at
its full enumerated scope) and no phase's `gates:` front-matter list gained,
lost, or reordered an entry (`TestGatesFrontMatterInvariance`, TC-25), then
the sequence of gates a resuming project encounters is provably identical
for *every* run — a stronger claim than a single live resume would prove,
because a live resume only proves the claim held for one run on one day.

Together these two test classes are the resume regression's automatable
half named in PLAN.md Chunk 7 Step 1. The live-execution residual is MV-2
in TEST.md (`docs/fleet-observation.md`'s manual-smoke log records it) —
not automated here, and not invented as a fake unit test.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: TC-24's full enumerated scope (rescoped by PM 2026-08-16, user-approved —
#: see TEST.md). This is every prose file fleet-wiring is permitted to
#: alter under the additive-edit rule; a file absent from this list is a
#: file this project must not touch. `test_seams.py::TestAdditiveDiffInvariant`
#: separately checks the two files TC-10 specifically owns (`roles/pm.md`,
#: `phases/3-implementation.md`) as part of that seam's own content proof;
#: this class re-asserts the same invariant over the complete list so the
#: full W-NFR-4 rule-1 scope has one canonical, exhaustive check.
_TC24_FULL_SCOPE = (
    "roles/pm.md",
    "phases/3-implementation.md",
    "phases/4-acceptance.md",
    "SKILL.md",
)


def _merge_base_with_main() -> str | None:
    """Return the merge-base commit of `HEAD` and `origin/main`, or `None`.

    Symbolic on purpose, matching `test_seams.py`'s precedent: this project
    has already lived through one history rewrite that silently orphaned a
    pinned SHA. Returns `None` (never raises) if git or the ref is
    unavailable, so callers can skip cleanly instead of failing.

    **Mid-merge correction:** a pre-commit hook runs before the merge commit
    exists, so `HEAD` is still the pre-merge tip and `git merge-base HEAD
    origin/main` resolves to the OLD (pre-merge) merge-base — which then
    picks up `main`'s own independent commits (anything `main` itself
    changed since branching) as if THIS branch had changed them, a false
    positive discovered live merging `origin/main` into `fleet-wiring`. Once
    the merge commit lands, `origin/main` becomes a direct parent and this
    function's normal computation would return `origin/main`'s own tip — so
    while `MERGE_HEAD` exists, this returns it directly rather than the
    stale pre-merge value, matching what the post-commit answer will be.
    """
    merge_head = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "MERGE_HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if merge_head.returncode == 0 and merge_head.stdout.strip():
        return merge_head.stdout.strip()

    try:
        result = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


#: W-NFR-4 rule 1 forbids changing a line that already existed in these
#: orchestration files, so that a resumed pre-existing project fires identical
#: gates in identical order. One narrow class of change cannot satisfy that and
#: still be correct: a line naming the fleet CLI as a bare `fleet <verb>`
#: command. No such command has ever been installable — the repo ships no
#: packaging, so nothing puts a `fleet` executable on `PATH`, and
#: `scripts/fleet/cli.py`'s package-relative imports rule out running it as a
#: loose script. Re-spelling those lines to the module form
#: (`python -m scripts.fleet.cli <verb>`) necessarily removes them.
#:
#: The exemption is deliberately as small as the defect: it matches ONLY a
#: line naming a bare-`fleet` subcommand, so any other removal in these files
#: still fails. It also expires on its own — once no line spells a command
#: that way, it matches nothing. The behavioural guarantee it protects is
#: unaffected: these are non-gating observational call-outs, and the
#: gate-sequence check (`TestGateFrontmatterUnchanged`) is independent.
_LEGACY_BARE_FLEET_INVOCATION_RE = re.compile(
    r"`fleet (?:observe|status|handoff|register|query|gen-view|done)\b"
)


def _disallowed_removed_lines(diff_stdout: str) -> list[str]:
    """Return removed diff lines that W-NFR-4 rule 1 does not permit."""
    return [
        line
        for line in diff_stdout.splitlines()
        if line.startswith("-")
        and not line.startswith("---")
        and not _LEGACY_BARE_FLEET_INVOCATION_RE.search(line)
    ]


class TestAdditiveDiffInvarianceFullScope:
    """TC-24: zero removed lines against the merge-base, across the complete
    enumerated file list every one of this project's chunks was permitted
    to touch — not just the two files TC-10's narrower check owns. The one
    exemption is the bare-`fleet` re-spelling described above.
    """

    @pytest.mark.parametrize("relative_path", _TC24_FULL_SCOPE)
    def test_diff_against_merge_base_has_zero_removed_lines(self, relative_path: str) -> None:
        merge_base = _merge_base_with_main()
        if merge_base is None:
            pytest.skip("git or origin/main merge-base unavailable in this environment")

        result = subprocess.run(
            ["git", "diff", merge_base, "--", relative_path],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"git diff against {merge_base} failed in this environment")

        removed_lines = _disallowed_removed_lines(result.stdout)
        assert removed_lines == [], (
            f"{relative_path}: diff against merge-base {merge_base} removed lines "
            "(W-NFR-4 rule 1 violation — an existing line changed or was removed):\n"
            + "\n".join(removed_lines)
        )


def _frontmatter_gates(text: str) -> list[str]:
    """Extract the `gates:` list from a phase recipe's YAML frontmatter.

    Args:
        text: full file contents.

    Returns:
        The `gates` list exactly as declared (order preserved, `?` suffixes
        intact) — empty list if no frontmatter or no `gates` key.
    """
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---", 4)
    if end == -1:
        return []
    fm = yaml.safe_load(text[4:end]) or {}
    return list(fm.get("gates") or [])


class TestGatesFrontMatterInvariance:
    """TC-25: every phase recipe's `gates:` front-matter list is byte-identical
    (same entries, same order) between the merge-base with `main` and HEAD.

    Globs `phases/*.md` rather than hardcoding the file list, so a phase
    recipe added or removed outside this project's scope does not silently
    fall out of coverage.
    """

    def test_gates_lists_unchanged_across_every_phase_recipe(self) -> None:
        merge_base = _merge_base_with_main()
        if merge_base is None:
            pytest.skip("git or origin/main merge-base unavailable in this environment")

        phase_files = sorted((_REPO_ROOT / "phases").glob("*.md"))
        assert phase_files, "no phase recipes found under phases/"

        mismatches: list[str] = []
        for phase_file in phase_files:
            rel = f"phases/{phase_file.name}"
            head_gates = _frontmatter_gates(phase_file.read_text(encoding="utf-8"))

            show = subprocess.run(
                ["git", "show", f"{merge_base}:{rel}"],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if show.returncode != 0:
                # Phase recipe did not exist at the merge-base (added since) —
                # nothing to compare; not a W-NFR-4 rule-3 violation.
                continue
            base_gates = _frontmatter_gates(show.stdout)

            if head_gates != base_gates:
                mismatches.append(
                    f"{rel}: gates changed — merge-base={base_gates!r} head={head_gates!r}"
                )

        assert not mismatches, (
            "W-NFR-4 rule 3 violation — a phase's gates: front-matter list "
            "gained, lost, or reordered an entry:\n  " + "\n  ".join(mismatches)
        )
