"""Chunk 9 content tests: FR-14 portability regression and FR-15 identical-verb
assertion — the D4 boundary ruling's enforcement.

TDD scaffold at Phase 2.1; TC-63/TC-64 implemented at chunk 9 per `TEST.md`.
TC-65 (D4 boundary documentation, `docs/fleet-observation.md`) stays a
skipped scaffold here — chunk 9 runs as two dispatches, and the docs half
that would make TC-65 true is the SECOND dispatch's job, not this one's.

Not named in PLAN.md's Chunk 9 file list (which lists no new test file for
this chunk — its acceptance criteria read as regressions/content
assertions layered on existing suites). QA scaffolds a dedicated file here
rather than overloading `tests/fleet/test_seams.py`'s existing
Chunk-2-specific docstring scope. Flagged in the QA return report as a QA
naming choice, not an authoritative PLAN.md path.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_REPO_ROOT / "tests"))
from publication_patterns import find_tokens, locate_tokens_file, resolve_tokens  # noqa: E402

sys.path.insert(0, str(_REPO_ROOT))
import scripts.fleet as fleet_package  # noqa: E402
from scripts.fleet.cli import build_parser  # noqa: E402


def _operator_tokens() -> list[str]:
    """Mirrors `test_seams.py`'s own helper (same rationale, same source)."""
    return resolve_tokens(locate_tokens_file(_REPO_ROOT))


#: The literal "python -m scripts.fleet.cli observe <verb>" invocation,
#: capturing just the verb group -- `observe session-start` or
#: `observe dispatch` -- so two occurrences can be compared for BYTE
#: equality rather than mere containment (TC-64/FR-15).
_VERB_INVOCATION_RE = re.compile(r"python -m scripts\.fleet\.cli (observe [\w-]+)")


def _first_verb(text: str) -> str | None:
    """Return the first `observe <verb>` token `_VERB_INVOCATION_RE` finds."""
    match = _VERB_INVOCATION_RE.search(text)
    return match.group(1) if match else None


def _find_heading_containing(lines: list[str], *substrings: str) -> tuple[int, int]:
    """Same shape as `test_seams.py`'s own helper (not imported -- that
    module's version operates on a file path, this one on lines already
    read, since this module reads each file once for BOTH the verb
    comparison and the operator-token check). Returns `(start, end)` for
    the first `## ` heading whose lowercased text contains every one of
    `substrings`, spanning to the next `## ` heading or end of file.
    """
    start = None
    for i, line in enumerate(lines):
        lowered = line.lower()
        if line.startswith("## ") and all(s in lowered for s in substrings):
            start = i
            break
    assert start is not None, f"no '## ...' heading found containing all of {substrings!r}"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return start, end


def _subsection(text: str, *substrings: str) -> str:
    """Extract one `## `-headed subsection's body, by heading substrings."""
    lines = text.splitlines()
    start, end = _find_heading_containing(lines, *substrings)
    return "\n".join(lines[start:end])


class TestPortabilityRegression:
    """FR-14 / TC-63.

    Not a recursive `pytest`-inside-`pytest` invocation of "the complete
    pre-existing fleet suite plus content tests" -- that would roughly
    double this suite's own runtime every time it runs, and a self-
    referential collection risks unbounded recursion. Per PLAN.md/TEST.md's
    own QA note, this is implemented as the meta-check the two documents
    describe: a PRECONDITION half (structural: the portable observation
    code path never references a harness's hook registry at all) plus an
    EXERCISE half (behavioral: the floor's actual write path succeeds with
    literally no `settings.json` anywhere on the filesystem this test
    touches). The full-suite invocation itself is run directly by the
    Developer as the chunk's own acceptance evidence (see the status
    report), per TEST.md's "TC-63 is therefore a meta-check the Developer
    runs (full suite invocation)".
    """

    def test_full_suite_green_with_no_hooks_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # --- PRECONDITION (structural): D4's own boundary rule (DESIGN.md
        # D4, Option A) states harness/hook knowledge lives in EXACTLY two
        # places -- `templates/hooks/claude-code/*` and
        # `hooks_install.py` -- and nowhere else under `scripts/fleet/`.
        # Assert this for the specific modules that IMPLEMENT the floor's
        # plain (non-`--hook-payload`) `observe session-start` write path,
        # so "no hooks installed" is a property this path PROVABLY cannot
        # see, not just something that happens to be true today.
        # `cli.py` is deliberately excluded from this scan even though it
        # dispatches the floor's call: it also HOSTS the `install`/
        # `uninstall`/`status` subcommands, whose own `--settings-path`
        # help strings legitimately name `settings.json` (that IS one of
        # D4's two sanctioned places) -- scanning the whole multi-verb
        # module would conflate "the floor's own verb never touches it"
        # with "no verb in this file ever mentions it", which is a
        # different, false claim. The exercise half below instead proves
        # the floor's OWN call path behaviorally, by running it with no
        # `settings.json` anywhere on disk.
        floor_modules = ("observe.py", "locate.py", "hook_payload.py", "config.py")
        fleet_dir = Path(fleet_package.__file__).parent
        for name in floor_modules:
            text = (fleet_dir / name).read_text(encoding="utf-8")
            assert "settings.json" not in text, (
                f"scripts/fleet/{name}: references settings.json -- the session-start "
                "floor's own call path must never see the harness's hook registry"
            )
            assert "templates/hooks" not in text, (
                f"scripts/fleet/{name}: references templates/hooks -- the session-start "
                "floor's own call path must never see a harness hook template"
            )

        # --- EXERCISE (behavioral): point HOME at an empty directory with
        # NO `.claude/settings.json` anywhere -- the harness's own hook
        # registry -- so any code path that secretly depended on a hook
        # being installed would surface here as a hard failure, not a
        # silent pass. Then invoke the CLI exactly as SKILL.md's prose
        # first-action step does: a plain `observe session-start` call
        # naming `--workspace`/`--slug` directly, no `--hook-payload`, no
        # harness anywhere in the call.
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.delenv("SUPERHUMAN_PROFILE", raising=False)
        assert not (fake_home / ".claude" / "settings.json").exists()

        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)

        slug = "portable-floor-demo"
        project_dir = repo / "docs" / "superhuman" / slug
        project_dir.mkdir(parents=True)
        (project_dir / "SUPERHUMAN.md").write_text(
            f"**Slug:** {slug}\n**Project-id:** fleet-{slug.replace('-', '')[:12]}\n",
            encoding="utf-8",
        )
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "session-start",
                "--workspace",
                str(repo),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "floor-session",
            ]
        )
        exit_code = args.func(args)
        assert exit_code == 0

        log = (project_dir / "fleet" / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log, (
            f"the floor's plain observe session-start call did not register -- log={log!r}"
        )


class TestIdenticalVerb:
    """FR-15 / TC-64. Both pairs the floor names a verb for: the
    session-start pair (hook <-> `SKILL.md`) and the dispatch pair (hook
    <-> `roles/pm.md` / `phases/3-implementation.md`)."""

    def test_hook_and_prose_floor_name_identical_verb(self) -> None:
        session_start_hook_text = (
            _REPO_ROOT / "templates" / "hooks" / "claude-code" / "session-start"
        ).read_text(encoding="utf-8")
        subagent_start_hook_text = (
            _REPO_ROOT / "templates" / "hooks" / "claude-code" / "subagent-start"
        ).read_text(encoding="utf-8")
        skill_md_text = (_REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        pm_md_text = (_REPO_ROOT / "roles" / "pm.md").read_text(encoding="utf-8")
        impl_md_text = (_REPO_ROOT / "phases" / "3-implementation.md").read_text(
            encoding="utf-8"
        )

        # Session-start pair: the SessionStart hook vs. SKILL.md's
        # session-start-floor first-action step (chunk 9's own migration,
        # PM ruling R7 -- previously a MISMATCH: the hook said
        # `observe session-start`, SKILL.md said `observe launch`). Scoped
        # to the specific subsection (not a whole-file first-match) so
        # this stays correct even if SKILL.md's section order changes.
        skill_md_floor_subsection = _subsection(skill_md_text, "fleet observation", "floor")
        hook_session_verb = _first_verb(session_start_hook_text)
        floor_session_verb = _first_verb(skill_md_floor_subsection)
        assert hook_session_verb == "observe session-start", (
            f"templates/hooks/claude-code/session-start names {hook_session_verb!r}, "
            "expected 'observe session-start'"
        )
        assert floor_session_verb == hook_session_verb, (
            f"SKILL.md's session-start-floor step names {floor_session_verb!r}, but "
            f"templates/hooks/claude-code/session-start names {hook_session_verb!r} -- "
            "FR-15 requires the identical verb in both places, no parallel or "
            "divergent invocation"
        )

        # Dispatch pair: the SubagentStart hook vs. both prose call-outs
        # (already wired identically since chunks 3/7 -- pinned here too,
        # in the one place FR-15's own requirement is centrally asserted,
        # rather than left implicit in test_seams.py's separate seam
        # checks). Each prose file also carries an EARLIER, unrelated
        # `observe handoff-emit` mention (Chunk 2's seam), so this must be
        # scoped to the dispatch-observation subsection specifically,
        # never a whole-file first-match.
        hook_dispatch_verb = _first_verb(subagent_start_hook_text)
        assert hook_dispatch_verb == "observe dispatch", (
            f"templates/hooks/claude-code/subagent-start names {hook_dispatch_verb!r}, "
            "expected 'observe dispatch'"
        )
        for label, text in (
            ("roles/pm.md", pm_md_text),
            ("phases/3-implementation.md", impl_md_text),
        ):
            floor_dispatch_verb = _first_verb(_subsection(text, "dispatch", "observation"))
            assert floor_dispatch_verb == hook_dispatch_verb, (
                f"{label} names {floor_dispatch_verb!r}, but "
                f"templates/hooks/claude-code/subagent-start names {hook_dispatch_verb!r} "
                "-- FR-15 requires the identical verb in both places"
            )

    def test_hooks_contain_no_operator_token(self) -> None:
        """Guard the new comparisons above against reading a file that
        happens to also carry an operator token this project's own guard
        would otherwise catch elsewhere -- belt-and-suspenders, matching
        `test_seams.py`'s own convention for every file it reads."""
        tokens = _operator_tokens()
        for relative_path in (
            "templates/hooks/claude-code/session-start",
            "templates/hooks/claude-code/subagent-start",
            "SKILL.md",
        ):
            text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
            hits = find_tokens(text, tokens)
            assert not hits, f"{relative_path}: operator token(s) found: {hits!r}"


class TestBoundaryDocumentation:
    @pytest.mark.skip(
        reason="TC-65 is the docs half of chunk 9, a separate dispatch: "
        "docs/fleet-observation.md is not yet updated to describe the "
        "implemented state (D4/PLAN chunk 9 note)."
    )
    def test_docs_fleet_observation_covers_the_boundary_and_limitations(
        self,
    ) -> None:
        """`docs/fleet-observation.md` documents: the locator's
        disambiguation ladder, `observe session-start`, `origination`
        `"observed"`, hook install/uninstall, `fleet doctor`, the D4
        portable/ceiling boundary rule, and every stated limitation
        confirmed by Chunk 1 (same-turn parallel fan-out collapse, D5's
        fallback if it was triggered instead of Option A)."""
