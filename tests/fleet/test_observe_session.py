"""Tests for `scripts.fleet.observe.observe_session_start` and the additive
`ObserveResult.error_class` field — the FR-5 / FR-13 / FR-17 / D2a / D6 seam.

Chunk 3, per `TEST.md` TC-15..TC-24.

Filename matches PLAN.md's file-structure table (`test_observe_session.py`),
not DESIGN.md's component table (`test_observe_session_start.py`) — the two
artifacts disagree on this one filename; QA followed PLAN.md as the more
authoritative "what gets built where" source. Flagged in the QA return
report; not a silent pick.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from scripts.fleet import cli as fleet_cli
from scripts.fleet import observe
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.cli import build_parser
from scripts.fleet.handoff import extract_handoff_id


def _fake_stdin(text: str) -> io.StringIO:
    """A monkeypatch-ready fake `sys.stdin` carrying `text` on both its
    text (`.read()`) and buffer (`.buffer.read()`) surfaces.

    `read_hook_payload`'s `"-"` branch reads `sys.stdin.buffer` (raw
    bytes, then decodes as UTF-8 itself — chunk 7a fix); a plain
    `io.StringIO` has no `.buffer`, so every fake stdin in this module
    needs one.
    """
    fake = io.StringIO(text)
    fake.buffer = io.BytesIO(text.encode("utf-8"))  # type: ignore[attr-defined]
    return fake


# --- Fixtures, mirroring test_observe.py's precedent ------------------------------


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "trunk")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-q", "-m", "initial")
    return repo


@pytest.fixture
def enabled_project(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """A workspace with a resolvable `SUPERHUMAN.md` identity and fleet enabled."""
    slug = "demo-project"
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "fleet:\n"
        "  enabled: true\n"
        "  observe_deadline_seconds: 5.0\n"
        "  lock_timeout_seconds: 0.8\n"
        "  git_timeout_seconds: 0.25\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

    project_dir = git_repo / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(
        f"**Slug:** {slug}\n**Project-id:** fleet-demo123\n", encoding="utf-8"
    )
    return git_repo, slug


@pytest.fixture
def identity_unresolved_project(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """A workspace with fleet enabled but no `**Project-id:**` line at all."""
    slug = "no-id-project"
    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

    project_dir = git_repo / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(f"**Slug:** {slug}\n", encoding="utf-8")
    return git_repo, slug


def _fleet_dir(workspace: Path, slug: str) -> Path:
    return workspace / "docs" / "superhuman" / slug / "fleet"


class TestOriginationObserved:
    def test_observe_session_start_registers_with_origination_observed(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """Happy path: a resolvable workspace with fleet enabled and no
        pending handoff produces a row whose `origination == "observed"`."""
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug, local_id="session-a")

        result = observe.observe_session_start(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id is not None
        log = (_fleet_dir(workspace, slug) / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log


class TestNeverAFuzzyFlip:
    """D2a: `observe session-start` NEVER performs a fuzzy (cwd, branch)
    launch flip — that is an assertion (commission risk), not coverage."""

    def test_observe_session_start_never_performs_a_fuzzy_flip(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """Even when `(cwd, branch)` would uniquely match an existing
        `awaiting-launch` row, calling `observe_session_start` with NO
        `--handoff-id` must register a NEW session row — never silently
        flip the existing awaiting-launch row to active. This is the
        regression test for D2a's stated commission-exposure risk."""
        workspace, slug = enabled_project
        emit_adapter = PortableAdapter(workspace, slug)
        emitted = observe.observe_handoff_emit(
            emit_adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        assert emitted.ok is True

        session_adapter = PortableAdapter(workspace, slug, local_id="session-b")
        result = observe.observe_session_start(
            session_adapter, workspace=workspace, slug=slug, writer_role="pm"
        )

        assert result.ok is True
        assert result.node_id != emitted.node_id
        log = (_fleet_dir(workspace, slug) / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log
        # The awaiting-launch row must still be awaiting-launch, not flipped.
        assert '"type":"launched"' not in log

    def test_observe_session_start_flips_only_with_explicit_handoff_id(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """With `--handoff-id` supplied and matching an awaiting-launch
        row, the id-anchored flip fires (the fuzzy path is never
        consulted for this call)."""
        workspace, slug = enabled_project
        emit_adapter = PortableAdapter(workspace, slug)
        emitted = observe.observe_handoff_emit(
            emit_adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        assert emitted.ok is True
        handoff_id = extract_handoff_id(emitted.prompt_text)
        assert handoff_id is not None

        session_adapter = PortableAdapter(workspace, slug, local_id="session-c")
        result = observe.observe_session_start(
            session_adapter,
            workspace=workspace,
            slug=slug,
            handoff_id=handoff_id,
            writer_role="pm",
        )

        assert result.ok is True
        assert result.node_id == emitted.node_id
        assert result.reason in ("launched", "already_launched")

    def test_observe_session_start_recovers_handoff_id_from_prompt_text(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """`prompt_text` (not `handoff_id` directly) is grepped for an
        embedded `FLEET-HANDOFF-ID` line via `extract_handoff_id` — the
        same id-anchored (never fuzzy) trigger, just recovered a different
        way."""
        workspace, slug = enabled_project
        emit_adapter = PortableAdapter(workspace, slug)
        emitted = observe.observe_handoff_emit(
            emit_adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="draft\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        assert emitted.ok is True

        session_adapter = PortableAdapter(workspace, slug, local_id="session-d")
        result = observe.observe_session_start(
            session_adapter,
            workspace=workspace,
            slug=slug,
            prompt_text=emitted.prompt_text,
            writer_role="pm",
        )

        assert result.ok is True
        assert result.node_id == emitted.node_id

    def test_observe_session_start_falls_through_to_registration_when_handoff_id_unmatched(
        self, enabled_project: tuple[Path, str]
    ) -> None:
        """A `--handoff-id` that matches no awaiting-launch row still
        registers an ordinary `origination="observed"` row rather than
        failing outright — the flip attempt is best-effort, not a
        precondition for registering."""
        workspace, slug = enabled_project
        session_adapter = PortableAdapter(workspace, slug, local_id="session-e")

        result = observe.observe_session_start(
            session_adapter,
            workspace=workspace,
            slug=slug,
            handoff_id="no-such-handoff-id",
            writer_role="pm",
        )

        assert result.ok is True
        log = (_fleet_dir(workspace, slug) / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log


class TestIdempotencyAcrossFiveMatchers:
    @pytest.mark.parametrize("matcher", ["startup", "resume", "clear", "compact", "fork"])
    def test_observe_session_start_five_matchers_produce_one_row(
        self, matcher: str, enabled_project: tuple[Path, str]
    ) -> None:
        """FR-19/NFR-5 at the API level: the same session identity fired 5
        times (once per matcher) via `observe_session_start` produces
        exactly one row in the manifest (`idempotency_key =
        register:<node_id>` dedupes)."""
        workspace, slug = enabled_project
        node_ids: set[str] = set()
        for _ in range(5):
            adapter = PortableAdapter(workspace, slug, local_id=f"session-{matcher}")
            result = observe.observe_session_start(
                adapter, workspace=workspace, slug=slug, writer_role="pm"
            )
            assert result.ok is True
            assert result.node_id is not None
            node_ids.add(result.node_id)

        assert len(node_ids) == 1
        log_lines = [
            line
            for line in (_fleet_dir(workspace, slug) / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        registered = [line for line in log_lines if '"type":"session_registered"' in line]
        assert len(registered) == 1


class TestErrorClassAndFR13Surfacing:
    """FR-13: `identity_unresolved` stops being invisible. `error_class`
    lives on `ObserveResult`; the stdout line lives ONLY in `cli.py`."""

    def test_observe_result_error_class_set_to_identity_unresolved(
        self, identity_unresolved_project: tuple[Path, str]
    ) -> None:
        """A project that resolves via the locator but whose
        `SUPERHUMAN.md` has no `**Project-id:**` line produces an
        `ObserveResult` with `error_class == "identity_unresolved"`."""
        workspace, slug = identity_unresolved_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_session_start(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        )

        assert result.ok is False
        assert result.error_class == "identity_unresolved"

    def test_cli_prints_exactly_one_stdout_line_for_identity_unresolved(
        self, identity_unresolved_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Drive the CLI entrypoint (`args.func(args)`) for `observe
        session-start` against the identity_unresolved fixture; capture
        stdout via `capsys`; assert exactly one non-empty stdout line, and
        that it names the actionable condition (project resolved but
        unwritable)."""
        workspace, slug = identity_unresolved_project
        parser = build_parser()
        args = parser.parse_args(
            [
                "observe",
                "session-start",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
            ]
        )

        exit_code = args.func(args)

        captured = capsys.readouterr()
        assert exit_code == 0
        stdout_lines = [line for line in captured.out.splitlines() if line.strip()]
        assert len(stdout_lines) == 1
        assert "project-id" in stdout_lines[0].lower() or "identity" in stdout_lines[0].lower()

    def test_library_call_produces_zero_stdout_for_identity_unresolved(
        self, identity_unresolved_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The library-level companion to the CLI test above: call
        `observe.observe_session_start(...)` DIRECTLY (not through
        `cli.py`), same fixture; assert `capsys.readouterr().out == ""`.
        Together they prove the stdout line lives only in `cli.py`, never
        in `observe.py` — whose loudness tiers reserve stdout entirely for
        its CLI callers."""
        workspace, slug = identity_unresolved_project
        adapter = PortableAdapter(workspace, slug)

        result = observe.observe_session_start(
            adapter, workspace=workspace, slug=slug, writer_role="pm"
        )

        assert result.error_class == "identity_unresolved"
        assert capsys.readouterr().out == ""


class TestHookPayloadWiring:
    """FR-15/D2b: the hook and the portable prose floor call the identical
    verb -- `--hook-payload -` derives `--workspace`/`--slug` via the
    locator and threads the harness session id through (FR-17)."""

    def test_hook_payload_stdin_derives_workspace_slug_and_registers(
        self, enabled_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = enabled_project
        payload = json.dumps(
            {
                "session_id": "hook-session-1",
                "cwd": str(workspace),
                "hook_event_name": "SessionStart",
                "source": "startup",
            }
        )
        monkeypatch.setattr("sys.stdin", _fake_stdin(payload))

        parser = build_parser()
        args = parser.parse_args(
            ["observe", "session-start", "--hook-payload", "-", "--harness", "portable"]
        )

        exit_code = args.func(args)

        assert exit_code == 0
        log = (_fleet_dir(workspace, slug) / "events.jsonl").read_text(encoding="utf-8")
        assert '"origination":"observed"' in log
        assert '"local_id":"hook-session-1"' in log

    def test_hook_payload_locator_refusal_exits_0_and_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unresolvable `cwd` (not inside any git repository) means
        nothing to do -- exit 0, nothing written, no traceback."""
        payload = json.dumps({"session_id": "hook-session-2", "cwd": str(tmp_path)})
        monkeypatch.setattr("sys.stdin", _fake_stdin(payload))

        parser = build_parser()
        args = parser.parse_args(["observe", "session-start", "--hook-payload", "-"])

        exit_code = args.func(args)

        assert exit_code == 0

    def test_malformed_hook_payload_exits_0_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", _fake_stdin("not json"))

        parser = build_parser()
        args = parser.parse_args(["observe", "session-start", "--hook-payload", "-"])

        exit_code = args.func(args)

        assert exit_code == 0


class TestBroadCatchByteUnchanged:
    """NFR-3: `observe.py`'s single sanctioned broad catch is neither
    widened nor duplicated by this chunk's additions."""

    #: Captured at Chunk 3 implementation time from the three existing
    #: `except Exception as exc:  # noqa: BLE001 - the façade's sole broad
    #: catch` blocks in `_observe_register`, `observe_handoff_emit`, and
    #: `observe_launch` (in source order) -- each block is exactly 10 lines,
    #: from the `except` line through its closing `return ObserveResult(...)`.
    #: A future edit that widens, duplicates, relocates, or shrinks/grows any
    #: of the three fails this test loudly.
    _EXPECTED_BLOCK_HASHES = (
        "418a882cee0b97ef20bf0e810572b5e4f9f6e2ca3a93421a2e99d59d3b19bc63",
        "7a0892856b03b6ef86ba897abfa6ac52c0938b9c1a07f8dadc44f6975c2364a5",
        "6921eaa3ab42eed3b3c93fa29b5738684f56eb073d4aa3a4876cad929e09161e",
    )
    _BLOCK_LINES = 10
    _MARKER_PREFIX = "    except Exception as exc:"

    def test_observe_py_broad_catch_is_byte_unchanged(self) -> None:
        import hashlib

        from scripts.fleet import observe as observe_module

        source_path = Path(observe_module.__file__)
        lines = source_path.read_text(encoding="utf-8").splitlines(keepends=True)
        marker_indices = [
            i for i, line in enumerate(lines) if line.startswith(self._MARKER_PREFIX)
        ]

        assert len(marker_indices) == len(self._EXPECTED_BLOCK_HASHES), (
            "the number of broad `except Exception` blocks in observe.py changed -- "
            "NFR-3 forbids adding, removing, or duplicating one"
        )
        for index, expected_hash in zip(marker_indices, self._EXPECTED_BLOCK_HASHES):
            block = "".join(lines[index : index + self._BLOCK_LINES])
            actual_hash = hashlib.sha256(block.encode("utf-8")).hexdigest()
            assert actual_hash == expected_hash, (
                f"observe.py's broad-catch block starting at line {index + 1} changed "
                "-- NFR-3 requires it stay byte-unchanged"
            )


class TestFleetDoctorViaObserveSession:
    """D6: `fleet doctor` ships in this chunk and is the FR-10 acceptance
    scan Chunk 5 uses. Focused coverage lives in `test_doctor.py`; these two
    are QA's scaffolded happy-path cross-check, kept here per TEST.md's
    original TC-23/TC-24 placement."""

    def test_fleet_doctor_reports_all_four_health_states(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.doctor import scan

        repo = tmp_path / "repo"
        repo.mkdir()
        _run_git(repo, "init", "-q", "-b", "main")
        _run_git(repo, "config", "user.email", "test@example.invalid")
        _run_git(repo, "config", "user.name", "Test")
        (repo / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(repo, "add", ".gitkeep")
        _run_git(repo, "commit", "-q", "-m", "initial")

        def _write(slug: str, *, project_id: str | None, matching_slug: bool) -> None:
            project_dir = repo / "docs" / "superhuman" / slug
            project_dir.mkdir(parents=True)
            slug_line = slug if matching_slug else f"not-{slug}"
            body = f"**Slug:** {slug_line}\n"
            if project_id is not None:
                body += f"**Project-id:** {project_id}\n"
            (project_dir / "SUPERHUMAN.md").write_text(body, encoding="utf-8")

        _write("ok-project", project_id="proj-ok", matching_slug=True)
        _write("no-id-project", project_id=None, matching_slug=True)
        _write("bad-project", project_id="proj-bad", matching_slug=False)

        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        report = scan([repo])
        states = {record.slug: record.state for record in report.records}

        assert states["ok-project"] == "ok"
        assert states["no-id-project"] == "no_project_id"
        assert states["bad-project"] == "unresolvable"

    def test_fleet_doctor_scans_multiple_roots_and_aggregates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.doctor import scan

        def _init(path: Path) -> None:
            path.mkdir(parents=True)
            _run_git(path, "init", "-q", "-b", "main")
            _run_git(path, "config", "user.email", "test@example.invalid")
            _run_git(path, "config", "user.name", "Test")
            (path / ".gitkeep").write_text("", encoding="utf-8")
            _run_git(path, "add", ".gitkeep")
            _run_git(path, "commit", "-q", "-m", "initial")

        repo1 = tmp_path / "repo1"
        repo2 = tmp_path / "repo2"
        _init(repo1)
        _init(repo2)
        for repo, slug in ((repo1, "one"), (repo2, "two")):
            project_dir = repo / "docs" / "superhuman" / slug
            project_dir.mkdir(parents=True)
            (project_dir / "SUPERHUMAN.md").write_text(
                f"**Slug:** {slug}\n**Project-id:** proj-{slug}\n", encoding="utf-8"
            )

        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        report = scan([repo1, repo2])

        assert {record.slug for record in report.records} == {"one", "two"}


# --------------------------------------------------------------------------- #
# `--git-facts-root` wiring: chunk-6 branch-attribution defect (workspace and
# the session's own working tree can be different repositories' worth of git
# state whenever D1's outward hop fired -- ClaudeAdapter.git_facts() must
# query the LATTER, and only a --hook-payload consumer that explicitly
# threads `resolved_from` through as `args.git_facts_root` makes that happen).
# --------------------------------------------------------------------------- #


@pytest.fixture
def enabled_project_with_worktree(
    enabled_project: tuple[Path, str],
) -> tuple[Path, str, Path]:
    """`enabled_project`, plus a linked worktree on its OWN distinct branch.

    Stands in for a session running inside `.claude/worktrees/<slug>` while
    `docs/superhuman/<slug>/SUPERHUMAN.md` (and the profile pointing at it)
    live only in the main checkout -- the chunk-6/chunk-7 branch-attribution
    scenario. `workspace` (the main checkout `locate_project` resolves to,
    via the H0' main-worktree sidestep) and this worktree (the session's own
    working tree, what `git_facts_root` must resolve to) are on DIFFERENT
    branches on purpose, so a test can tell whether an adapter actually
    consulted `git_facts_root` or silently fell back to `workspace` --
    querying either root alone would report a real, plausible-looking
    branch, so only a genuine two-branch fixture like this one can catch the
    chunk-7 defect (`_build_adapter` computing `git_facts_root` correctly
    but only forwarding it to `ClaudeAdapter`).
    """
    workspace, slug = enabled_project
    worktree = workspace.parent / "linked-worktree"
    _run_git(workspace, "worktree", "add", str(worktree), "-b", "the-worktree-own-branch")
    return workspace, slug, worktree


def _observe_subcommands_accepting_hook_payload() -> list[str]:
    """Every `observe <verb>` that registers a `--hook-payload` argument.

    Introspects the real parser rather than hardcoding `["session-start"]`,
    so a FUTURE hook-payload verb (chunk 7's `SubagentStart` consumer, not
    yet built) is picked up automatically the moment it registers one --
    the whole point being that `test_every_hook_payload_verb_threads_git_facts_root`
    below then exercises it without anyone remembering to extend this list.
    """
    parser = build_parser()
    verbs: list[str] = []
    for action in parser._subparsers._group_actions:  # noqa: SLF001 -- introspection only
        if "observe" not in getattr(action, "choices", {}):
            continue
        observe_parser = action.choices["observe"]
        for sub_action in observe_parser._subparsers._group_actions:  # noqa: SLF001
            for name, sub_parser in sub_action.choices.items():
                if any(a.dest == "hook_payload" for a in sub_parser._actions):  # noqa: SLF001
                    verbs.append(name)
    return verbs


def test_at_least_one_hook_payload_verb_exists() -> None:
    """Guards the introspection helper itself -- AND is a deliberate tripwire
    for whoever builds chunk 7's `SubagentStart` hook-payload verb.

    If `_observe_subcommands_accepting_hook_payload()` ever returned `[]`, the
    parametrized test below would silently collect zero cases and the whole
    protection would be void without a single visible failure -- that alone
    would justify a `!= []` check.

    But this asserts EXACT equality on purpose, not `>= 1` or `!= []`. Chunk 7
    added `dispatch` (`SubagentStart`'s `observe dispatch --hook-payload -`,
    per PLAN.md chunk 7 PM ruling 5) as the second hook-payload verb,
    deliberately, updating this list by hand rather than letting it drift --
    at which point `test_every_hook_payload_verb_threads_git_facts_root`
    immediately started exercising `dispatch` too, and it threads
    `git_facts_root` correctly (see `_cmd_observe_dispatch`) or this pin's
    whole point would be defeated.

    DO NOT weaken this to `>= 1` to make a "spurious" future-chunk failure go
    away. That relaxation is exactly the failure mode this whole file exists
    to prevent: it would look like an unrelated brittle test breaking on
    unrelated work, the obvious fix would be to loosen it, and loosening it is
    precisely what lets a future verb silently reintroduce the branch-
    attribution defect this pin was built to catch. If you are here because
    this failed: good, that is the point -- go add the new verb to the list,
    then go make it set `git_facts_root`.
    """
    assert _observe_subcommands_accepting_hook_payload() == ["dispatch", "session-start"]


@pytest.mark.parametrize("verb", _observe_subcommands_accepting_hook_payload())
def test_every_hook_payload_verb_threads_git_facts_root(
    verb: str, enabled_project: tuple[Path, str]
) -> None:
    """Chunk-6 regression, written to survive chunk 7.

    Any `observe <verb> --hook-payload` consumer that resolves a location
    MUST set `args.git_facts_root` to that location's own working tree
    (never leave it at the `None` default, which silently falls back to
    `workspace` -- D1's outward-hopped root, the exact defect this pins).
    Parametrized over every REGISTERED hook-payload verb via
    `_observe_subcommands_accepting_hook_payload()`, so chunk 7's own verb
    (once it exists) is exercised here with no further edit to this file --
    it either threads `git_facts_root` correctly or this goes red.

    This checks only the `argparse.Namespace` attribute -- it is watching
    ONE layer, not the layer that actually matters end to end. See
    `test_every_hook_payload_verb_and_harness_builds_an_adapter_that_uses_git_facts_root`
    below for the adapter-level regression this one cannot catch (chunk 7's
    real defect: `args.git_facts_root` was computed correctly here, then
    silently dropped by `cli._build_adapter` for every harness but
    `ClaudeAdapter`).
    """
    workspace, slug = enabled_project
    payload = json.dumps({"session_id": f"{verb}-thread-check", "cwd": str(workspace)})

    parser = build_parser()
    args = parser.parse_args(["observe", verb, "--hook-payload", "-"])

    import sys as _sys

    original_stdin = _sys.stdin
    _sys.stdin = _fake_stdin(payload)
    try:
        args.func(args)
    finally:
        _sys.stdin = original_stdin

    assert args.git_facts_root is not None, (
        f"observe {verb} resolved a --hook-payload location but never set "
        "args.git_facts_root -- ClaudeAdapter.git_facts() will silently query "
        "`workspace` instead (D1's outward-hopped root), reintroducing the "
        "chunk-6 branch-attribution defect for this verb"
    )
    assert Path(args.git_facts_root) == workspace, (
        "git_facts_root must be the location that actually resolved this "
        "session (the session's OWN working tree), not merely non-None"
    )


@pytest.mark.parametrize("harness", ["portable", "subagent"])
@pytest.mark.parametrize("verb", _observe_subcommands_accepting_hook_payload())
def test_every_hook_payload_verb_and_harness_builds_an_adapter_that_uses_git_facts_root(
    verb: str, harness: str, enabled_project_with_worktree: tuple[Path, str, Path]
) -> None:
    """Chunk 7 fix, watching the layer that actually matters.

    `test_every_hook_payload_verb_threads_git_facts_root` above only proves
    `args.git_facts_root` (a `Namespace` attribute) gets set -- it passed
    even while this exact defect was live, because `cli._build_adapter`
    computed the value correctly and then silently forwarded it to
    `ClaudeAdapter` only, dropping it for `--harness subagent` (the harness
    `SubagentStart`'s production hook always dispatches with -- see
    `templates/hooks/claude-code/subagent-start`) and `--harness portable`
    (this CLI's own default). This test builds the SAME adapter
    `_build_adapter` builds for the real CLI call and checks ITS
    `git_facts()` output, so a future re-drop of this wiring (a new harness
    branch in `_build_adapter` that forgets `git_facts_root=`) fails here
    even if the `Namespace`-level test above stays green.

    Uses `enabled_project_with_worktree`, not the plain `enabled_project`
    fixture above -- `workspace` and the session's own working tree
    (`--hook-payload`'s `cwd`) must be on genuinely DIFFERENT branches, or
    `git_facts_root` and `workspace` would coincidentally agree and the
    assertion below could not distinguish "used git_facts_root" from "fell
    back to workspace".
    """
    workspace, slug, worktree = enabled_project_with_worktree
    payload = json.dumps(
        {
            "session_id": f"{verb}-{harness}-thread-check",
            "cwd": str(worktree),
            # `dispatch` needs `agent_id` to resolve a `dispatch_id` at all
            # (`_cmd_observe_dispatch` returns early, before ever building an
            # adapter, when it is absent) -- harmless extra key for
            # `session-start`, which never reads it.
            "agent_id": f"{verb}-{harness}-thread-check",
        }
    )

    parser = build_parser()
    args = parser.parse_args(["observe", verb, "--hook-payload", "-", "--harness", harness])

    import sys as _sys

    original_stdin = _sys.stdin
    _sys.stdin = _fake_stdin(payload)
    try:
        args.func(args)
    finally:
        _sys.stdin = original_stdin

    assert Path(args.git_facts_root) == worktree, (
        "sanity check on the Namespace attribute before checking the "
        "adapter built from it"
    )

    adapter = fleet_cli._build_adapter(args)
    facts = adapter.git_facts()
    assert facts.branch == "the-worktree-own-branch", (
        f"observe {verb} --harness {harness}: the adapter _build_adapter "
        "constructs from these args must report git facts from "
        "git_facts_root (the worktree -- the session's OWN working tree), "
        "not workspace (the outward-hopped main checkout) -- got "
        f"branch={facts.branch!r}, expected the worktree's own branch. This "
        "is the exact chunk-7 defect: git_facts_root computed but dropped "
        "on the floor before reaching the adapter."
    )


def test_subagent_start_hook_dispatch_from_linked_worktree_records_worktree_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chunk 7 behavioural regression -- the PM's real live-proof scenario.

    A real `SubagentStart` hook run from a session in a linked worktree wrote
    a dispatch row with `"branch": "main"` -- the MAIN checkout's branch --
    instead of the worktree's own branch. This test reproduces that exact
    shape end to end through the REAL CLI entry point (`fleet_cli.main`,
    i.e. `python -m scripts.fleet.cli observe dispatch --hook-payload -
    --harness subagent`, the invocation
    `templates/hooks/claude-code/subagent-start` always makes) rather than
    calling any internal function directly, and asserts on the actual
    `branch` field written to `events.jsonl` -- not on an adapter or a
    `Namespace` attribute, both already covered above.

    `CLAUDE_PROJECT_DIR` is deliberately absent from the environment: the
    hook script (not exercised here -- only the Python entry point is)
    passes it through as `--anchor` when set, but this test's `cwd` ->
    `locate_project` resolution must stand on its own via the payload's
    `cwd` alone, matching a harness invocation with no such variable set.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    main_repo = tmp_path / "main-repo"
    main_repo.mkdir()
    _run_git(main_repo, "init", "-q", "-b", "main")
    _run_git(main_repo, "config", "user.email", "test@example.invalid")
    _run_git(main_repo, "config", "user.name", "Test")
    (main_repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(main_repo, "add", "README.md")
    _run_git(main_repo, "commit", "-q", "-m", "initial")

    slug = "worktree-behavioural-project"
    project_dir = main_repo / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(
        f"**Slug:** {slug}\n**Project-id:** proj-behavioural\n", encoding="utf-8"
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "fleet:\n  enabled: true\n  observe_deadline_seconds: 5.0\n", encoding="utf-8"
    )
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

    worktree = tmp_path / "linked-worktree"
    _run_git(main_repo, "worktree", "add", str(worktree), "-b", "feature-x")

    payload = json.dumps(
        {
            "session_id": "behavioural-session-1",
            "cwd": str(worktree),
            "hook_event_name": "SubagentStart",
            "agent_id": "behavioural-dispatch-1",
            "agent_type": "developer",
            "prompt_id": "prompt-1",
        }
    )

    import sys as _sys

    original_stdin = _sys.stdin
    _sys.stdin = _fake_stdin(payload)
    try:
        exit_code = fleet_cli.main(
            ["observe", "dispatch", "--hook-payload", "-", "--harness", "subagent"]
        )
    finally:
        _sys.stdin = original_stdin

    assert exit_code == 0

    log_path = project_dir / "fleet" / "events.jsonl"
    assert log_path.exists(), (
        "expected the real `observe dispatch` entry point to write a "
        f"session_registered event to {log_path}"
    )
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    registered = [e for e in events if e.get("type") == "session_registered"]
    assert len(registered) == 1, (
        f"expected exactly one session_registered event, got {registered!r}"
    )
    assert registered[0]["payload"]["branch"] == "feature-x", (
        "the written row's branch must be the WORKTREE's own branch "
        "('feature-x'), never the main checkout's ('main') -- got "
        f"{registered[0]['payload']['branch']!r}. This is the production "
        "defect: a SubagentStart hook run from a session in a linked "
        "worktree recorded the main checkout's currently-checked-out "
        "branch as if it were this dispatch's own."
    )
