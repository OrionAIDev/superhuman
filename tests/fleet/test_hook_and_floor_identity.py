"""Chunk 9 content tests: FR-14 portability regression and FR-15 identical-verb
assertion — the D4 boundary ruling's enforcement.

TDD scaffold at Phase 2.1; TC-63/TC-64 implemented at chunk 9 per `TEST.md`.
TC-65 (D4 boundary documentation, `docs/fleet-observation.md`) is
implemented here too, as this project's second chunk-9 dispatch, now that
the docs half of the chunk has landed.

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
from scripts.fleet.adapter.base import format_launch_instruction  # noqa: E402
from scripts.fleet.cli import build_parser  # noqa: E402


def _operator_tokens() -> list[str]:
    """Mirrors `test_seams.py`'s own helper (same rationale, same source)."""
    return resolve_tokens(locate_tokens_file(_REPO_ROOT))


#: The literal "python -m scripts.fleet.cli observe <verb>" invocation,
#: capturing just the verb group -- `observe session-start` or
#: `observe dispatch` -- so two occurrences can be compared for BYTE
#: equality rather than mere containment (TC-64/FR-15). Matches the literal
#: word `python`, which is correct for PROSE sources (SKILL.md, roles/pm.md,
#: phases/3-implementation.md, and `format_launch_instruction()`'s returned
#: string) -- none of those spell the interpreter as a shell variable.
_VERB_INVOCATION_RE = re.compile(r"python -m scripts\.fleet\.cli (observe [\w-]+)")

#: Same idea, but for the BASH HOOK WRAPPERS, which invoke `"$PYTHON"` (an
#: interpreter resolved at runtime -- see each wrapper's own comment on the
#: Microsoft Store "app execution alias" stub), never the literal word
#: `python`.
#:
#: B3 preflight regression: the previous `_VERB_INVOCATION_RE` above matched
#: only the literal word `python`, which exists in these wrappers'
#: COMMENTS (e.g. session-start's line-16 prose explaining what the script
#: does) but never on the executable line, which reads `"$PYTHON" -m ...`.
#: That comment-only match let this identity test pass no matter what the
#: executable line actually invoked -- confirmed by mutation (see this
#: chunk's status report).
#:
#: Phase 3.3 preflight RE-RUN item A: tolerates interpreter-level flags
#: (`-E -s`, added to harden every wrapper against an inherited
#: `PYTHONPATH`/user site-packages plant -- see session-start's own
#: comment) between the interpreter token and `-m`. Those flags govern
#: HOW the interpreter starts, never WHICH verb/flags the `observe` call
#: itself carries, so admitting them here does not weaken what this test
#: checks (still exact, still reads the real executable line, still
#: excludes comments) -- it only stops an unrelated, already-landed
#: security hardening from masquerading as a D4/FR-15 divergence.
_HOOK_VERB_RE = re.compile(
    r'(?:"\$PYTHON"|\$PYTHON|python3?)(?:\s+-\S+)*\s+-m\s+scripts\.fleet\.cli\s+(observe\s+[\w-]+)'
)

#: A `--flag-name` token, used to extract the flag SHAPE of an invocation
#: for comparison, not just its verb (B3 requirement (c): compare the verb
#: AND the flag names).
_FLAG_RE = re.compile(r"--[a-zA-Z][\w-]*")

#: A backtick-delimited span (Markdown/docstring code span). `[^`]` is a
#: negated character class, not `.`, so it matches embedded newlines --
#: needed because roles/pm.md's own invocation is hand-wrapped across two
#: source lines inside one backtick span.
_BACKTICK_SPAN_RE = re.compile(r"`([^`]*)`")


def _first_verb(text: str) -> str | None:
    """Return the first `observe <verb>` token `_VERB_INVOCATION_RE` finds."""
    match = _VERB_INVOCATION_RE.search(text)
    return match.group(1) if match else None


def _prose_command_flags(text: str, near: re.Match, window: int = 300) -> frozenset[str]:
    """Return every `--flag` name found in a backtick code span near `near`.

    `near` is the verb-invocation match this flag set belongs to. Only
    backtick spans starting within `window` characters of it count -- e.g.
    SKILL.md's floor step splits one invocation across two adjacent spans
    (`` `python -m ... observe session-start --workspace ... --slug ...` ``
    then, ~100 characters later, `` `--handoff-id ...` ``), so a raw
    "first backtick span only" scope would miss the conditional flag. But
    scoping to backtick spans ANYWHERE in the subsection is too wide: both
    SKILL.md and roles/pm.md separately warn readers not to confuse the
    invocation with `` `--workspace` ``, over a thousand characters away in
    the same subsection -- unrelated prose that happens to also be
    backtick-wrapped. The window keeps the real, nearby continuation while
    excluding that unrelated, far-off mention.
    """
    flags: set[str] = set()
    for span_match in _BACKTICK_SPAN_RE.finditer(text):
        if abs(span_match.start() - near.start()) <= window:
            flags.update(_FLAG_RE.findall(span_match.group(1)))
    return frozenset(flags)


def _strip_bash_comment_lines(text: str) -> str:
    """Drop every line whose stripped form starts with `#` -- a full-line
    bash comment.

    B3 requirement (a): a verb/flag mentioned only in a wrapper's own
    explanatory comment (e.g. session-start's line 16) must never be able
    to satisfy a check meant to read the EXECUTED invocation.
    """
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def _executed_hook_invocation(
    comment_stripped_text: str,
) -> tuple[str, frozenset[str]] | None:
    """Return `(verb, flag_names)` for the first EXECUTED `observe <verb>`
    call in a comment-stripped bash hook wrapper, or None if there is none.

    B3 requirement (b): finds the invocation on an actual executable line
    (matching `"$PYTHON" -m ...`, never the literal word `python`, which
    only ever appears in these wrappers' comments), then gathers flag names
    across every physical line of that SAME backslash-continued logical
    statement, so a flag on a continuation line (e.g. `--harness claude \\`)
    is not missed.
    """
    lines = comment_stripped_text.splitlines()
    for i, line in enumerate(lines):
        match = _HOOK_VERB_RE.search(line)
        if not match:
            continue
        block = [line]
        j = i
        while block[-1].rstrip().endswith("\\"):
            j += 1
            block.append(lines[j])
        flags = frozenset(_FLAG_RE.findall("\n".join(block)))
        return match.group(1), flags
    return None


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
    <-> `roles/pm.md` / `phases/3-implementation.md`) -- plus, since B7, the
    session-start floor's SECOND carrier (`format_launch_instruction()`,
    embedded in every adapter's handoff prompt).

    B3 preflight regression: the OLD version of this test read every
    invocation with `_VERB_INVOCATION_RE`, which matches only the literal
    word `python`. The bash hook wrappers invoke `"$PYTHON"` (a resolved-
    at-runtime interpreter path) on their EXECUTED line, so the only place
    that regex ever matched in a wrapper was a COMMENT describing the
    invocation, never the invocation itself -- the test could not fail no
    matter what the executable line said. Fixed by (a) stripping comment
    lines from the hook wrapper text before searching
    (`_strip_bash_comment_lines`), (b) matching the real interpreter
    reference (`_HOOK_VERB_RE`) on what remains, (c) comparing flag names in
    addition to the verb wherever the two sides are meant to have the same
    shape, and (d) adding the base.py/B7 pair below. Confirmed by mutation
    (see this chunk's status report): changing the verb OR a flag name on
    session-start's executable line (leaving its comments untouched) turns
    this test red; reverting turns it green again.
    """

    def test_hook_and_prose_floor_name_identical_verb(self) -> None:
        session_start_hook_text = _strip_bash_comment_lines(
            (
                _REPO_ROOT / "templates" / "hooks" / "claude-code" / "session-start"
            ).read_text(encoding="utf-8")
        )
        subagent_start_hook_text = _strip_bash_comment_lines(
            (
                _REPO_ROOT / "templates" / "hooks" / "claude-code" / "subagent-start"
            ).read_text(encoding="utf-8")
        )
        skill_md_text = (_REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        pm_md_text = (_REPO_ROOT / "roles" / "pm.md").read_text(encoding="utf-8")
        impl_md_text = (_REPO_ROOT / "phases" / "3-implementation.md").read_text(
            encoding="utf-8"
        )

        # --- Session-start pair: the SessionStart hook vs. SKILL.md's -----
        # --- session-start-floor first-action step -------------------------
        #
        # Read from the EXECUTED line (comments already stripped above).
        # Both verb AND flags are pinned against a hardcoded expectation, so
        # either kind of mutation on the hook's real invocation fails this
        # test -- not just a verb change.
        hook_session_result = _executed_hook_invocation(session_start_hook_text)
        assert hook_session_result is not None, (
            "templates/hooks/claude-code/session-start: no EXECUTED "
            "`observe <verb>` invocation found (comment lines excluded)"
        )
        hook_session_verb, hook_session_flags = hook_session_result
        assert (hook_session_verb, hook_session_flags) == (
            "observe session-start",
            frozenset({"--hook-payload", "--harness"}),
        ), (
            f"templates/hooks/claude-code/session-start's EXECUTED invocation is "
            f"{hook_session_verb!r} with flags {sorted(hook_session_flags)} -- "
            "expected 'observe session-start' with flags ['--harness', '--hook-payload']"
        )

        skill_md_floor_subsection = _subsection(skill_md_text, "fleet observation", "floor")
        floor_session_match = _VERB_INVOCATION_RE.search(skill_md_floor_subsection)
        assert floor_session_match, (
            "SKILL.md's session-start-floor step names no `observe <verb>` invocation"
        )
        floor_session_verb = floor_session_match.group(1)
        floor_session_flags = _prose_command_flags(skill_md_floor_subsection, floor_session_match)
        assert floor_session_verb == hook_session_verb, (
            f"SKILL.md's session-start-floor step names {floor_session_verb!r}, but "
            f"templates/hooks/claude-code/session-start names {hook_session_verb!r} -- "
            "FR-15 requires the identical verb in both places, no parallel or "
            "divergent invocation"
        )
        assert floor_session_flags == frozenset(
            {"--workspace", "--slug", "--handoff-id"}
        ), (
            f"SKILL.md's session-start-floor step names flags "
            f"{sorted(floor_session_flags)} -- expected "
            "['--handoff-id', '--slug', '--workspace']"
        )
        # NOTE: the hook's flags and the floor's flags are asserted
        # separately above, never against EACH OTHER -- they legitimately
        # differ. The hook derives --workspace/--slug/--handoff-id from the
        # harness's own hook JSON via --hook-payload (D2b; ARCHITECTURE.md
        # Addendum A); the floor has no hook payload to parse and so
        # names them directly. Only the verb is required to match across
        # this pair; weakening either side's flag assertion to force a
        # false equality would hide a real regression, not catch one.

        # --- Second floor carrier (B7): format_launch_instruction(), -------
        # --- embedded in every adapter's handoff prompt ---------------------
        #
        # Calls the real function rather than regex-scraping base.py's
        # source, so this reads the actual text a launched session would
        # see, not a comment near it. Unlike the hook-vs-floor pair above,
        # this carrier and SKILL.md's floor step use the SAME explicit-flag
        # form (both name --workspace/--slug/--handoff-id directly), so verb
        # AND flags are required to match exactly here.
        base_instruction = format_launch_instruction()
        base_match = _VERB_INVOCATION_RE.search(base_instruction)
        assert base_match, (
            "scripts/fleet/adapter/base.py's format_launch_instruction() no longer "
            f"names an `observe <verb>` invocation: {base_instruction!r}"
        )
        base_verb = base_match.group(1)
        base_flags = _prose_command_flags(base_instruction, base_match)
        assert base_verb == floor_session_verb, (
            f"scripts/fleet/adapter/base.py's format_launch_instruction() names "
            f"{base_verb!r}, but SKILL.md's floor step names {floor_session_verb!r} -- "
            "FR-15 has two floor carriers (SKILL.md's first-action step and "
            "base.py's _LAUNCH_INSTRUCTION, embedded in every adapter's handoff "
            "prompt) and both must name the identical verb"
        )
        assert base_flags == floor_session_flags, (
            f"scripts/fleet/adapter/base.py's format_launch_instruction() names "
            f"flags {sorted(base_flags)}, but SKILL.md's floor step names "
            f"{sorted(floor_session_flags)} -- both carriers use the same explicit "
            "--workspace/--slug/--handoff-id form and must name the same flags"
        )

        # --- Dispatch pair: the SubagentStart hook vs. both prose ----------
        # --- call-outs -------------------------------------------------------
        #
        # Each prose file also carries an EARLIER, unrelated
        # `observe handoff-emit` mention (Chunk 2's seam), so this stays
        # scoped to the dispatch-observation subsection specifically, never
        # a whole-file first-match.
        hook_dispatch_result = _executed_hook_invocation(subagent_start_hook_text)
        assert hook_dispatch_result is not None, (
            "templates/hooks/claude-code/subagent-start: no EXECUTED "
            "`observe <verb>` invocation found (comment lines excluded)"
        )
        hook_dispatch_verb, hook_dispatch_flags = hook_dispatch_result
        assert (hook_dispatch_verb, hook_dispatch_flags) == (
            "observe dispatch",
            frozenset({"--hook-payload", "--harness"}),
        ), (
            f"templates/hooks/claude-code/subagent-start's EXECUTED invocation is "
            f"{hook_dispatch_verb!r} with flags {sorted(hook_dispatch_flags)} -- "
            "expected 'observe dispatch' with flags ['--harness', '--hook-payload']"
        )

        # The dispatch floor prose names --dispatch-id/--local-id, which the
        # hook has no counterpart for (the hook derives them from the
        # transcript via --hook-payload, D5) -- a legitimate difference,
        # same reasoning as the session-start pair above, so only --harness
        # is shared and only the verb is compared across hook and floor.
        expected_dispatch_prose_flags = frozenset(
            {"--harness", "--dispatch-id", "--local-id"}
        )
        for label, text in (
            ("roles/pm.md", pm_md_text),
            ("phases/3-implementation.md", impl_md_text),
        ):
            dispatch_subsection = _subsection(text, "dispatch", "observation")
            floor_dispatch_match = _VERB_INVOCATION_RE.search(dispatch_subsection)
            assert floor_dispatch_match, f"{label}: no `observe <verb>` invocation found"
            floor_dispatch_verb = floor_dispatch_match.group(1)
            floor_dispatch_flags = _prose_command_flags(dispatch_subsection, floor_dispatch_match)
            assert floor_dispatch_verb == hook_dispatch_verb, (
                f"{label} names {floor_dispatch_verb!r}, but "
                f"templates/hooks/claude-code/subagent-start names {hook_dispatch_verb!r} "
                "-- FR-15 requires the identical verb in both places"
            )
            assert floor_dispatch_flags == expected_dispatch_prose_flags, (
                f"{label} names flags {sorted(floor_dispatch_flags)} -- expected "
                f"{sorted(expected_dispatch_prose_flags)}"
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
    """TC-65 (D4 boundary documentation, `docs/fleet-observation.md`).

    Guards against keyword-stuffing several ways: HTML comments are
    stripped before any substance check runs, so a match hidden inside a
    `<!-- -->` block does not count; the checks that matter most are
    pinned to a NAMED subsection (via this module's own `_subsection`
    helper) rather than a whole-file substring search, so a stray mention
    outside its proper section does not satisfy it; several checks require
    MULTIPLE co-occurring, specific technical terms rather than one word,
    so a single keyword dropped anywhere cannot satisfy them; the stated-
    limitations section is additionally required to hold at least nine
    separately NUMBERED list items (not nine words scattered in prose) and
    a minimum count of hedge words (`not`/`never`/`cannot`/`silently`),
    which a marketing-shaped rewrite would not have; and the material this
    rewrite must remove (deleted template paths, the old placeholder
    procedure, the stale `$(pwd)` cwd source, the misleading
    `git_timeout_seconds: 0.25` "default") is asserted ABSENT, not merely
    that new material was added alongside it.
    """

    def test_docs_fleet_observation_covers_the_boundary_and_limitations(
        self,
    ) -> None:
        doc_path = _REPO_ROOT / "docs" / "fleet-observation.md"
        raw_text = doc_path.read_text(encoding="utf-8")
        # Strip HTML comments so a match hidden inside one doesn't count.
        text = re.sub(r"<!--.*?-->", "", raw_text, flags=re.DOTALL)
        lowered = text.lower()

        # --- stale/superseded material must be GONE, not merely -----------
        # --- superseded by new material added alongside it ----------------
        for gone in (
            "templates/hooks/SessionStart",
            "templates/hooks/PreToolUse",
            "REPLACE_WITH_",
            "$(pwd)",
            "git_timeout_seconds: 0.25",
        ):
            assert gone not in text, (
                f"docs/fleet-observation.md still contains {gone!r}, which "
                "the chunk 6/7/8/9 rewrites made stale or false"
            )

        # --- observe session-start / origination:"observed" ---------------
        assert "observe session-start" in lowered, (
            "docs/fleet-observation.md never names the `observe session-start` "
            "verb the SessionStart floor and ceiling both call"
        )
        assert '"origination": "observed"' in text, (
            'docs/fleet-observation.md never documents the `"origination": '
            '"observed"` tag a hook- or floor-written row carries'
        )

        # --- the project locator's disambiguation ladder -------------------
        locator_section = _subsection(text, "locator").lower()
        for needle in (
            "h0",
            "h1",
            "git-common-dir",
            "path.home()",
            "cwd-containment",
            "declared",
            "branch",
            "singleton",
            "max_outward_hops",
        ):
            assert needle in locator_section, (
                f"the locator section is missing {needle!r} -- the ladder must "
                "be described concretely, not just referenced by name"
            )
        assert "outer" in locator_section and "nested" in locator_section, (
            "the locator section must state that a nested project resolves "
            "to the OUTER repository, not merely assert a ladder exists"
        )

        # --- the granularity rule's deterministic ceiling -------------------
        granularity_section = _subsection(text, "granularity").lower()
        assert "subagentstart" in granularity_section
        assert "subagent-start" in granularity_section
        assert "agent_type" in granularity_section, (
            "the granularity section must explain WHY agent_type cannot "
            "substitute for the predicate, not just name the predicate"
        )

        # --- the role-first enforcement gate (chunk 7a) --------------------
        role_gate_section = _subsection(text, "role-first").lower()
        for verdict in ("role", "non_role", "mismatch", "unmarked"):
            assert verdict in role_gate_section, (
                f"the role-first gate section never names the {verdict!r} verdict"
            )
        assert "role-gate.jsonl" in role_gate_section
        assert "six fields" in role_gate_section or "6 fields" in role_gate_section
        assert "prompt text" in role_gate_section, (
            "the role-first gate section must state the decision log never "
            "carries the prompt text itself (NFR-8), not just that it logs"
        )

        # --- installing the hook ceiling ------------------------------------
        installer_section = _subsection(text, "install").lower()
        for needle in (
            "hooks install",
            "hooks status",
            "hooks uninstall",
            "--harness claude-code",
            "--settings-path",
            "--skill-root",
            "--dry-run",
            "startup",
            "resume",
            "clear",
            "compact",
            "fork",
            "agent|task",
            "timeout",
            "600",
            "duplicate",
            "worktree",
        ):
            assert needle in installer_section, (
                f"the installer section is missing {needle!r}"
            )
        assert re.search(r"main (git )?checkout", installer_section), (
            "the installer section must state registered commands resolve "
            "to the MAIN checkout, not merely mention checkouts in passing"
        )
        assert "reap" in installer_section or "branch" in installer_section, (
            "the installer section must give the REASON a worktree-pinned "
            "hook is dangerous (branch switches, or reap-safety blindness), "
            "not just assert that worktrees are avoided"
        )

        # --- the D4 boundary rule (five clauses) ---------------------------
        d4_section = _subsection(text, "d4").lower()
        for needle in (
            "harness-agnostic",
            "no auto-detection",
            "never invoked automatically",
            "identical verb",
            "byte-identically",
            "observe session-start",
            "observe dispatch",
        ):
            assert needle in d4_section, f"the D4 boundary section is missing {needle!r}"

        # --- fleet doctor + fleet observe status ---------------------------
        doctor_section = _subsection(text, "doctor").lower()
        for needle in (
            "unresolvable",
            "fleet_disabled",
            "no_project_id",
            "2.31",
            "unknown",
            "not configured",
            "zero writes recorded",
            "last write for this project succeeded",
            "last write for this project failed",
        ):
            assert needle in doctor_section, f"the doctor/status section is missing {needle!r}"

        # --- stated limitations: at least nine NUMBERED items, each with ---
        # --- specific, co-occurring technical terms -------------------------
        limitations_section = _subsection(text, "limitation")
        limitations_lower = limitations_section.lower()
        numbered_items = re.findall(r"^\d+\. \*\*", limitations_section, flags=re.MULTILINE)
        assert len(numbered_items) >= 9, (
            f"stated limitations must be at least 9 separately numbered items, "
            f"found {len(numbered_items)}"
        )
        hedge_words = re.findall(r"\b(not|never|cannot|silently)\b", limitations_lower)
        assert len(hedge_words) >= 9, (
            "a limitations section reading like marketing would not repeat "
            "hedge words this often -- found only "
            f"{len(hedge_words)} of not/never/cannot/silently"
        )
        required_pairs = [
            ("nested dispatches", "no row"),
            ("workflow", "sendmessage"),
            ("cannot resolve", "prose floor"),
            ("subagent_type", "own message"),
            ("non-role", "counted"),
            ("session_id", "fixture"),
            ("git_timeout_seconds", "malformed"),
            ("latency", "windows"),
            ("main checkout", "inert"),
        ]
        for first, second in required_pairs:
            assert first in limitations_lower and second in limitations_lower, (
                f"stated limitations never pairs {first!r} with {second!r} -- "
                "a limitation must be substantiated, not just named"
            )
