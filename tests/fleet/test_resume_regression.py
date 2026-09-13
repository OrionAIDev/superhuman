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

What *is* mechanically checkable is the gate declaration a resuming project
reads: every phase recipe's `gates:` front matter, keyed by the recipe that
declares it. `TestGatesFrontMatterInvariance` (TC-25) asserts that map is
identical between the merge-base with `main` and HEAD, so the gates a
resuming project encounters, and the phases that carry them, are unchanged
for *every* run — a stronger claim than a single live resume would prove,
because a live resume only proves the claim held for one run on one day.
`TestGateMapDifferences` proves that comparison fails on each way the gate
sequence can change, so a green TC-25 is evidence rather than a check that
could never have failed.

**TC-24 was retired here.** It froze every pre-existing line of four prose
files (`roles/pm.md`, `phases/3-implementation.md`, `phases/4-acceptance.md`,
`SKILL.md`). That was fleet-wiring's scope fence, not a gate-order check: it
covered two of the nine phase recipes that declare gates, so renaming or
deleting any other one passed both it and the old per-file TC-25, while
every later correction to those four files needed a regex exemption to
merge. The gate-map comparison below closes the rename/delete gap directly.

TC-25 is the resume regression's automatable half named in PLAN.md Chunk 7
Step 1. The live-execution residual is MV-2 in TEST.md
(`docs/fleet-observation.md`'s manual-smoke log records it) — not automated
here, and not invented as a fake unit test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _merge_base_with_main() -> str | None:
    """Return the merge-base commit of `HEAD` and `origin/main`, or `None`.

    Symbolic on purpose: this project has already lived through one history
    rewrite that silently orphaned a pinned SHA. Returns `None` (never raises)
    if git or the ref is unavailable, so callers can skip cleanly instead of
    failing.

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


def _frontmatter_gates(text: str) -> list[str]:
    """Extract the `gates:` list from a phase recipe's YAML frontmatter.

    Line endings are normalised first: a CRLF checkout would otherwise fail
    the `---\\n` test, parse every recipe as gate-less, and let two empty
    maps compare equal.

    Args:
        text: full file contents.

    Returns:
        The `gates` list exactly as declared (order preserved, `?` suffixes
        intact) — empty list if no frontmatter or no `gates` key.
    """
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---", 4)
    if end == -1:
        return []
    fm = yaml.safe_load(text[4:end]) or {}
    return [str(gate) for gate in fm.get("gates") or []]


def _gate_map(recipes: dict[str, str]) -> dict[str, list[str]]:
    """Map each phase recipe that declares gates to its gate list.

    Recipes declaring no gates are left out on purpose. Adding, renaming, or
    removing a gate-less recipe (e.g. `3.3-preflight-review.md`) changes no
    gate a resuming project fires, so it must not register as a difference.

    Args:
        recipes: recipe path (`phases/<name>.md`) → full file contents.

    Returns:
        Recipe path → declared gate list, for every recipe whose list is
        non-empty.
    """
    gate_map: dict[str, list[str]] = {}
    for rel, text in recipes.items():
        gates = _frontmatter_gates(text)
        if gates:
            gate_map[rel] = gates
    return gate_map


def _gate_map_differences(
    base: dict[str, list[str]], head: dict[str, list[str]]
) -> list[str]:
    """Describe every way `head`'s gate map departs from `base`'s.

    Compares the whole map rather than each HEAD file against its own past,
    so a gate-bearing recipe that disappears (deleted, or renamed, which
    renumbers where its gates fire) is reported. The old per-file loop only
    visited files present at HEAD, which is the gap that let a phase rename
    or deletion pass.

    Args:
        base: gate map at the merge-base.
        head: gate map at HEAD.

    Returns:
        One human-readable line per recipe whose gates differ; empty when
        the maps are identical.
    """
    differences: list[str] = []
    for rel in sorted(base.keys() | head.keys()):
        before, after = base.get(rel), head.get(rel)
        if before == after:
            continue
        if after is None:
            differences.append(
                f"{rel}: declared {before!r} at the merge-base but no gates at HEAD "
                "(recipe removed or renamed, or its gates dropped)"
            )
        elif before is None:
            differences.append(
                f"{rel}: declares {after!r} at HEAD but no gates at the merge-base "
                "(recipe added or renamed, or gates added to it)"
            )
        else:
            differences.append(f"{rel}: gates changed — merge-base={before!r} head={after!r}")
    return differences


def _recipes_at(commit: str) -> dict[str, str] | None:
    """Read every `phases/*.md` recipe as it stood at `commit`.

    Args:
        commit: any commit-ish git can resolve.

    Returns:
        Recipe path → full file contents, or `None` if git could not list or
        read the tree, so the caller can skip instead of comparing against a
        partial map.
    """
    listing = subprocess.run(
        ["git", "ls-tree", "--name-only", commit, "phases/"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if listing.returncode != 0:
        return None

    recipes: dict[str, str] = {}
    for rel in listing.stdout.splitlines():
        if not rel.endswith(".md"):
            continue
        show = subprocess.run(
            ["git", "cat-file", "blob", f"{commit}:{rel}"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        if show.returncode != 0:
            return None
        recipes[rel] = show.stdout
    return recipes


class TestGatesFrontMatterInvariance:
    """TC-25: the map of phase recipe → `gates:` list is identical between the
    merge-base with `main` and HEAD.

    Fails when a gate is added, dropped, reordered, or moved between phases,
    and when a gate-bearing recipe is added, deleted, or renamed. Prose edits
    and gate-less recipes are free to change. HEAD's recipes are globbed from
    the working tree rather than hardcoded, so a new recipe cannot fall out of
    coverage.
    """

    def test_gate_map_unchanged_since_merge_base(self) -> None:
        merge_base = _merge_base_with_main()
        if merge_base is None:
            pytest.skip("git or origin/main merge-base unavailable in this environment")

        base_recipes = _recipes_at(merge_base)
        if base_recipes is None:
            pytest.skip(f"could not read phases/ at merge-base {merge_base}")

        head_recipes = {
            f"phases/{path.name}": path.read_text(encoding="utf-8")
            for path in sorted((_REPO_ROOT / "phases").glob("*.md"))
        }
        base_map = _gate_map(base_recipes)
        head_map = _gate_map(head_recipes)

        # Two empty maps compare equal. If parsing ever breaks, this stops the
        # comparison below from passing on nothing.
        assert base_map, f"no phase recipe declares gates at merge-base {merge_base}"
        assert head_map, "no phase recipe declares gates at HEAD"

        differences = _gate_map_differences(base_map, head_map)
        assert not differences, (
            "W-NFR-4 rule 3 violation — the gates a resumed project fires changed:\n  "
            + "\n  ".join(differences)
        )


#: A small gate map standing in for the real one, so each mutation below is
#: legible on its own.
_BASE_MAP: dict[str, list[str]] = {
    "phases/0-kickoff.md": ["G0", "G1"],
    "phases/1-requirements.md": ["G2"],
    "phases/2.1-test-plan.md": ["G4"],
    "phases/4-acceptance.md": ["G8"],
}


def _mutated(changes: dict[str, list[str] | None] | None = None) -> dict[str, list[str]]:
    """Return a copy of `_BASE_MAP` with recipes replaced, added, or removed.

    Args:
        changes: recipe stem (`2.1-test-plan`) → new gate list, or `None`
            to remove the recipe.

    Returns:
        The mutated gate map.
    """
    mutated = {rel: list(gates) for rel, gates in _BASE_MAP.items()}
    for stem, gates in (changes or {}).items():
        rel = f"phases/{stem}.md"
        if gates is None:
            mutated.pop(rel)
        else:
            mutated[rel] = gates
    return mutated


class TestGateMapDifferences:
    """Proves TC-25's comparison fails on every way the gate sequence changes
    and stays quiet on changes that leave it alone."""

    def test_identical_maps_have_no_differences(self) -> None:
        assert _gate_map_differences(_BASE_MAP, _mutated()) == []

    @pytest.mark.parametrize(
        ("head", "offending_recipe"),
        [
            pytest.param(
                _mutated({"0-kickoff": ["G1", "G0"]}),
                "phases/0-kickoff.md",
                id="reorder-within-a-phase",
            ),
            pytest.param(
                _mutated({"0-kickoff": ["G0"], "1-requirements": ["G1", "G2"]}),
                "phases/1-requirements.md",
                id="move-a-gate-between-phases",
            ),
            pytest.param(
                _mutated({"2.1-test-plan": None, "5-test-plan": ["G4"]}),
                "phases/2.1-test-plan.md",
                id="rename-renumbers-a-gate-bearing-phase",
            ),
            pytest.param(
                _mutated({"1-requirements": None}),
                "phases/1-requirements.md",
                id="delete-a-gate-bearing-phase",
            ),
            pytest.param(
                _mutated({"3.5-extra-review": ["G5"]}),
                "phases/3.5-extra-review.md",
                id="add-a-gate-bearing-phase",
            ),
            pytest.param(
                _mutated({"4-acceptance": ["G8", "G11"]}),
                "phases/4-acceptance.md",
                id="add-a-new-gate",
            ),
            pytest.param(
                _mutated({"4-acceptance": ["G8?"]}),
                "phases/4-acceptance.md",
                id="make-a-gate-conditional",
            ),
        ],
    )
    def test_gate_sequence_change_is_reported(
        self, head: dict[str, list[str]], offending_recipe: str
    ) -> None:
        differences = _gate_map_differences(_BASE_MAP, head)
        assert differences, "a gate-sequence change produced no difference"
        assert any(line.startswith(offending_recipe) for line in differences), differences

    def test_gate_less_recipe_and_prose_changes_are_not_differences(self) -> None:
        base = {
            "phases/1-requirements.md": "---\nphase: 1\ngates: [G2]\n---\nDraft it.\n",
            "phases/3.3-preflight-review.md": "---\nphase: 3.3\ngates: []\n---\nReview.\n",
        }
        head = {
            "phases/1-requirements.md": "---\nphase: 1\ngates: [G2]\n---\nReworded.\n",
            "phases/3.4-preflight-review.md": "---\nphase: 3.4\ngates: []\n---\nReview.\n",
            "phases/3.5-new-check.md": "---\nphase: 3.5\ngates: []\n---\nNew.\n",
        }
        assert _gate_map_differences(_gate_map(base), _gate_map(head)) == []

    def test_crlf_front_matter_still_yields_gates(self) -> None:
        text = "---\r\nphase: 0\r\ngates: [G0, G1]\r\n---\r\nBody.\r\n"
        assert _frontmatter_gates(text) == ["G0", "G1"]
