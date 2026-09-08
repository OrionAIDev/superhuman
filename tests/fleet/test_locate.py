"""Tests for `scripts.fleet.locate` — the (workspace, slug) project locator.

TC-1..TC-14 per `TEST.md`'s Chunk 2 table; TC-66..TC-71 per the G6 D1
revision's additional cases. Covers FR-1..FR-4, FR-2's refusal cases, FR-3's
four real-estate layouts (plain repo, linked worktree, gitignored mount
point, nested inner repository), D1's 4-rung disambiguation ladder, D1-R1's
bounded outward hop, and NFR-4's confinement property (the locator is a
*selector* among git-derived roots, never a path *constructor*).

Real git fixtures throughout (`git init` under `tmp_path`) — no mocking of
git itself, since the whole point of this module is what the real git facts
say. Fixture git identity is pinned (`user.email`/`user.name`) so tests
don't depend on the running machine's global git config, matching
`tests/fleet/test_adapter_portable.py`'s precedent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.fleet.cli import build_parser
from scripts.fleet import locate as locate_module
from scripts.fleet.locate import (
    MAX_OUTWARD_HOPS,
    locate_project,
    locate_project_explain,
)

# --- Fixture builders --------------------------------------------------------------


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path, *, branch: str = "main") -> None:
    """`git init` a repo at `path`, with a pinned identity and one commit."""
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q", "-b", branch)
    _run(path, "config", "user.email", "test@example.invalid")
    _run(path, "config", "user.name", "Test")
    (path / ".gitkeep").write_text("", encoding="utf-8")
    _run(path, "add", ".gitkeep")
    _run(path, "commit", "-q", "-m", "initial")


def _write_record(root: Path, slug: str) -> Path:
    """Write a well-formed `<root>/docs/superhuman/<slug>/SUPERHUMAN.md`."""
    project_dir = root / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    record = project_dir / "SUPERHUMAN.md"
    record.write_text(
        f"# Superhuman: {slug}\n\n**Slug:** {slug}\n**Project-id:** proj-{slug}\n",
        encoding="utf-8",
    )
    return record


def _build_plain_repo(base: Path) -> Path:
    repo = base / "repo"
    _init_repo(repo)
    _write_record(repo, "solo-project")
    return repo


def _build_worktree_stale_copy(base: Path) -> Path:
    """The `gate-record-integrity` shape: a linked worktree whose own
    `docs/superhuman/<slug>/` has no `SUPERHUMAN.md`; the canonical record
    lives in the main working tree."""
    main = base / "main"
    _init_repo(main, branch="main")
    _write_record(main, "gate-record-integrity")
    _write_record(main, "other-project")
    worktree = base / "worktree"
    _run(main, "worktree", "add", "-q", "-b", "gate-record-integrity", str(worktree))
    (worktree / "docs" / "superhuman" / "gate-record-integrity").mkdir(parents=True)
    return worktree


def _build_gitignored_mount(base: Path) -> Path:
    repo = base / "repo"
    _init_repo(repo)
    (repo / ".gitignore").write_text("docs/superhuman/\n", encoding="utf-8")
    _run(repo, "add", ".gitignore")
    _run(repo, "commit", "-q", "-m", "ignore the docs mount")
    _write_record(repo, "mounted-project")
    return repo


def _build_l1_containment(base: Path) -> Path:
    repo = base / "repo"
    _init_repo(repo)
    _write_record(repo, "alpha")
    _write_record(repo, "beta")
    record = _write_record(repo, "gamma")
    cwd = record.parent / "subdir"
    cwd.mkdir()
    return cwd


def _build_l2_declared(base: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = base / "repo"
    _init_repo(repo)
    _write_record(repo, "alpha")
    _write_record(repo, "beta")
    _write_record(repo, "gamma")
    profile = base / "profile.yaml"
    profile.write_text("fleet:\n  slug: beta\n", encoding="utf-8")
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))
    return repo


def _build_l3_branch(base: Path) -> Path:
    repo = base / "repo"
    _init_repo(repo, branch="alpha")
    _write_record(repo, "alpha")
    _write_record(repo, "beta")
    return repo


def _build_l4_singleton(base: Path) -> Path:
    repo = base / "repo"
    _init_repo(repo)
    _write_record(repo, "solo")
    return repo


def _build_nested_inner_repo(base: Path) -> Path:
    """TC-66's fixture: `docs/superhuman/` is itself a separate git repo,
    containing exactly one real candidate at the outer root."""
    outer = base / "outer"
    _init_repo(outer)
    _write_record(outer, "nested-project")
    inner = outer / "docs" / "superhuman"
    _init_repo(inner, branch="inner-branch")
    return inner / "nested-project"


def _build_nested_inner_repo_multi(base: Path) -> Path:
    """Like `_build_nested_inner_repo`, but with two outer candidates and
    `cwd` at the inner clone's own root (no `<slug>` component) — TC-67."""
    outer = base / "outer"
    _init_repo(outer)
    _write_record(outer, "nested-project")
    _write_record(outer, "sibling-project")
    inner = outer / "docs" / "superhuman"
    _init_repo(inner, branch="inner-branch")
    return inner


def _build_two_level_nest(base: Path) -> Path:
    """TC-68's fixture: nested TWO repository levels deep. The real
    candidate lives at `outer`, two hops away from `cwd` — out of reach of
    a single bounded hop."""
    outer = base / "outer"
    _init_repo(outer)
    _write_record(outer, "far-project")
    level1 = outer / "docs" / "superhuman"
    _init_repo(level1, branch="level1-branch")
    level2 = level1 / "extra-nest"
    _init_repo(level2, branch="level2-branch")
    cwd = level2 / "somewhere"
    cwd.mkdir()
    return cwd


def _build_h1_trap(base: Path) -> Path:
    """TC-70's fixture: the outer root, reachable via H1, has a branch name
    AND a singleton candidate that would each fire if a non-L1 rung were
    (incorrectly) permitted at H1 — but `cwd` sits elsewhere in the inner
    repo, not inside the candidate directory."""
    outer = base / "outer"
    _init_repo(outer, branch="trap-project")
    _write_record(outer, "trap-project")
    inner = outer / "docs" / "superhuman"
    _init_repo(inner, branch="inner-branch")
    cwd = inner / "unrelated-subdir"
    cwd.mkdir()
    return cwd


def _build_unrelated_capture(base: Path) -> Path:
    """TC-71's fixture: an inner repo nested (at an arbitrary path, not
    `docs/superhuman`) inside an unrelated outer repo that has its own real
    candidate elsewhere — `cwd` is not inside it."""
    outer = base / "outer"
    _init_repo(outer)
    _write_record(outer, "unrelated-project")
    inner = outer / "some-tool" / "vendor-checkout"
    _init_repo(inner, branch="vendor-branch")
    cwd = inner / "src"
    cwd.mkdir()
    return cwd


def _oracle_roots(cwd: Path) -> set[Path]:
    """Independently compute the legitimate git-derived root set for `cwd`.

    Calls `git rev-parse` directly via `subprocess` from the TEST's own
    process — the same primitives `locate.py` is documented to use, but not
    imported from the module under test (TEST.md's differential-oracle
    requirement for TC-13).
    """

    def _git(cwd_: Path, *args: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(cwd_), *args], capture_output=True, text=True, check=False
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    roots: set[Path] = set()
    toplevel = _git(cwd, "rev-parse", "--path-format=absolute", "--show-toplevel")
    if toplevel is None:
        return roots
    h0_root = Path(toplevel)
    roots.add(h0_root)

    common_dir = _git(h0_root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common_dir:
        roots.add(Path(common_dir).parent)

    h1_toplevel = _git(h0_root.parent, "rev-parse", "--path-format=absolute", "--show-toplevel")
    if h1_toplevel:
        roots.add(Path(h1_toplevel))

    return roots


@pytest.fixture(autouse=True)
def _isolate_profile_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin `SUPERHUMAN_PROFILE` to a nonexistent path by default.

    Every test in this module must be insensitive to whatever profile
    happens to exist on the machine actually running the suite (including a
    real developer's own `~/.superhuman/profile.yaml`) — otherwise L2 could
    spuriously fire (or fail to) depending on who runs the tests. Tests that
    specifically exercise L2 override this within the test itself.
    """
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(tmp_path / "no-such-profile.yaml"))


# --- FR-3: the four real layouts -----------------------------------------------------


class TestFR3Layouts:
    """One test per real-estate layout named in FR-3 / DESIGN.md D1."""

    def test_locate_layout_plain_repo(self, tmp_path: Path) -> None:
        """A plain repo with exactly one `docs/superhuman/<slug>/SUPERHUMAN.md`
        resolves via the L4 singleton rung."""
        repo = _build_plain_repo(tmp_path)

        result = locate_project(repo)

        assert result is not None
        assert result.workspace.samefile(repo)
        assert result.slug == "solo-project"
        assert result.rung == "L4"
        assert result.hop == "H0"

    def test_locate_layout_worktree_stale_copy_resolves_to_main_worktree(
        self, tmp_path: Path
    ) -> None:
        """The `gate-record-integrity` shape: a linked worktree's
        `docs/superhuman/<slug>/` directory exists but has NO
        `SUPERHUMAN.md` in it. The locator must retry once against the main
        working tree (via `git rev-parse --git-common-dir`'s parent) and
        resolve to the canonical record there — never silently resolve to
        the stale, id-less copy in the worktree."""
        worktree = _build_worktree_stale_copy(tmp_path)
        main = tmp_path / "main"

        result = locate_project(worktree)

        assert result is not None
        assert result.workspace.samefile(main)
        assert not result.workspace.samefile(worktree)
        assert result.slug == "gate-record-integrity"
        assert result.rung == "L3"
        assert result.hop == "H0'"

    def test_locate_layout_gitignored_mount_point(self, tmp_path: Path) -> None:
        """`docs/superhuman/` is git-ignored (a mount point for a separate
        clone) but present as a plain directory on disk. The locator walks
        the filesystem, not the git index, so it must see it."""
        repo = _build_gitignored_mount(tmp_path)

        result = locate_project(repo)

        assert result is not None
        assert result.workspace.samefile(repo)
        assert result.slug == "mounted-project"


# --- D1: the 4-rung disambiguation ladder ---------------------------------------------


class TestDisambiguationLadder:
    """One test per rung, per DESIGN.md D1's normative ladder table."""

    def test_locate_rung_l1_cwd_containment(self, tmp_path: Path) -> None:
        """Payload `cwd` is at/under `<root>/docs/superhuman/<slug>/`, with
        two OTHER candidates present elsewhere in the same repo — L1 alone
        must uniquely narrow to the containing candidate."""
        cwd = _build_l1_containment(tmp_path)

        result = locate_project(cwd)

        assert result is not None
        assert result.slug == "gamma"
        assert result.rung == "L1"

    def test_locate_rung_l2_declared_profile_slug(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The resolved profile's `fleet.slug` names a candidate; cwd is the
        repo root (L1 does not fire); two other candidates exist."""
        repo = _build_l2_declared(tmp_path, monkeypatch)

        result = locate_project(repo)

        assert result is not None
        assert result.slug == "beta"
        assert result.rung == "L2"

    def test_locate_rung_l3_branch_name(self, tmp_path: Path) -> None:
        """The workspace's current git branch equals a candidate slug; L1
        and L2 do not fire."""
        repo = _build_l3_branch(tmp_path)

        result = locate_project(repo)

        assert result is not None
        assert result.slug == "alpha"
        assert result.rung == "L3"

    def test_locate_rung_l4_singleton(self, tmp_path: Path) -> None:
        """Exactly one well-formed candidate exists; no other rung is
        needed to reach a unique answer."""
        repo = _build_l4_singleton(tmp_path)

        result = locate_project(repo)

        assert result is not None
        assert result.slug == "solo"
        assert result.rung == "L4"


# --- FR-2: refusal cases ---------------------------------------------------------------


class TestRefusal:
    """FR-2: zero, several-with-no-unique-rung, or unparseable candidates
    each yield 'no result' — never a first-match-wins pick."""

    def test_locate_refuses_on_zero_candidates(self, tmp_path: Path) -> None:
        """No `SUPERHUMAN.md` anywhere under `docs/superhuman/*/` returns
        `None` with a reason string."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        result, reason = locate_project_explain(repo)

        assert result is None
        assert reason

    def test_locate_refuses_when_no_rung_produces_a_unique_answer(
        self, tmp_path: Path
    ) -> None:
        """Several well-formed candidates, none matched by cwd, profile, or
        branch — L1-L3 silent, L4 fails (not singleton). The reason string
        names every candidate considered."""
        repo = tmp_path / "repo"
        _init_repo(repo, branch="unrelated-branch")
        _write_record(repo, "alpha")
        _write_record(repo, "beta")
        _write_record(repo, "gamma")

        result, reason = locate_project_explain(repo)

        assert result is None
        assert "alpha" in reason
        assert "beta" in reason
        assert "gamma" in reason

    def test_locate_excludes_unparseable_record_without_blinding_the_others(
        self, tmp_path: Path
    ) -> None:
        """One candidate's `SUPERHUMAN.md` has no `**Slug:**` line (or it
        disagrees with its directory name) and is excluded from candidacy;
        the OTHER candidates in the same repo still resolve normally via
        their own rung. One malformed record must not blind an 11-record
        repo."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_record(repo, "good-one")
        malformed_dir = repo / "docs" / "superhuman" / "broken"
        malformed_dir.mkdir(parents=True)
        (malformed_dir / "SUPERHUMAN.md").write_text("no slug line here\n", encoding="utf-8")
        # Also cover "disagrees with its directory name".
        mismatched_dir = repo / "docs" / "superhuman" / "mismatched"
        mismatched_dir.mkdir(parents=True)
        (mismatched_dir / "SUPERHUMAN.md").write_text(
            "**Slug:** not-the-directory-name\n", encoding="utf-8"
        )

        result = locate_project(repo)

        assert result is not None
        assert result.slug == "good-one"
        assert result.rung == "L4"

    def test_locate_ignores_the_shallower_pattern_without_docs_superhuman_prefix(
        self, tmp_path: Path
    ) -> None:
        """D1 ruling R-a: `<root>/<slug>/SUPERHUMAN.md` (no `docs/superhuman/`
        prefix) is never a candidate, even when perfectly well-formed —
        only the deeper `<root>/docs/superhuman/<slug>/SUPERHUMAN.md`
        pattern counts. A repo with only the shallow shape must refuse
        exactly like one with zero candidates."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        shallow_dir = repo / "shallow-project"
        shallow_dir.mkdir()
        (shallow_dir / "SUPERHUMAN.md").write_text(
            "**Slug:** shallow-project\n", encoding="utf-8"
        )

        result, reason = locate_project_explain(repo)

        assert result is None
        assert reason

    def test_locate_candidacy_does_not_require_project_id(
        self, tmp_path: Path
    ) -> None:
        """D1 ruling 3: a candidate whose `SUPERHUMAN.md` has no
        `**Project-id:**` line is still selectable. Resolution behavior
        must be IDENTICAL before and after the Chunk 5 backfill — if
        Project-id were required for candidacy, the hook would silently
        change what it resolves to the moment ids are backfilled."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        project_dir = repo / "docs" / "superhuman" / "no-id-project"
        project_dir.mkdir(parents=True)
        (project_dir / "SUPERHUMAN.md").write_text(
            "**Slug:** no-id-project\n", encoding="utf-8"
        )

        result = locate_project(repo)

        assert result is not None
        assert result.slug == "no-id-project"


# --- FR-4: CLI + API surface ------------------------------------------------------------


class TestCliAndApiSurface:
    def test_cli_locate_prints_slug_or_nothing_exit_0(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`fleet locate` prints the resolved slug on a resolvable
        workspace, prints nothing on an ambiguous one, and exits 0 in both
        cases."""
        parser = build_parser()

        repo = _build_l4_singleton(tmp_path)
        args = parser.parse_args(["locate", "--cwd", str(repo)])
        rc = args.func(args)
        out = capsys.readouterr().out

        assert rc == 0
        assert out.strip() == "solo"

        ambiguous = tmp_path / "ambiguous"
        _init_repo(ambiguous, branch="zzz-no-match")
        _write_record(ambiguous, "a")
        _write_record(ambiguous, "b")
        args2 = parser.parse_args(["locate", "--cwd", str(ambiguous)])
        rc2 = args2.func(args2)
        out2 = capsys.readouterr().out

        assert rc2 == 0
        assert out2.strip() == ""


# --- NFR-4: confinement (the locator is a selector, never a constructor) -----------------


class TestNFR4Confinement:
    """The locator may return ONLY a git-derived root (H0's toplevel, H0''s
    main-worktree parent, or H1's bounded-hop toplevel) and must never
    construct a path from a profile value, an env var, or a branch name.
    See TEST.md's 'How to test the NFR-4 negative' for the rationale behind
    the differential-oracle and trap-fixture approach below.
    """

    @pytest.mark.parametrize(
        "layout",
        [
            "plain_repo",
            "worktree_stale_copy",
            "gitignored_mount",
            "l1_containment",
            "l3_branch",
            "l4_singleton",
            "nested_inner_repo",
        ],
    )
    def test_locate_confinement_returns_only_git_derived_paths(
        self, tmp_path: Path, layout: str
    ) -> None:
        """Differential oracle: independently compute the legitimate
        candidate paths via direct `subprocess` calls to `git rev-parse`
        (run from the TEST's own process, not imported from `locate.py`).
        Run `locate_project(cwd)` across every layout fixture; assert
        `result.workspace` is path-EQUAL (`Path.samefile` — never a mere
        `.is_relative_to()` containment check) to one of the
        independently-computed candidates."""
        base = tmp_path / layout
        base.mkdir()
        builders = {
            "plain_repo": lambda: _build_plain_repo(base),
            "worktree_stale_copy": lambda: _build_worktree_stale_copy(base),
            "gitignored_mount": lambda: _build_gitignored_mount(base),
            "l1_containment": lambda: _build_l1_containment(base),
            "l3_branch": lambda: _build_l3_branch(base),
            "l4_singleton": lambda: _build_l4_singleton(base),
            "nested_inner_repo": lambda: _build_nested_inner_repo(base),
        }
        cwd = builders[layout]()

        result = locate_project(cwd)
        oracle = _oracle_roots(cwd)

        assert result is not None
        assert oracle, "the oracle itself must have found at least one root"
        assert any(result.workspace.samefile(root) for root in oracle)

    def test_locate_confinement_returns_only_git_derived_paths_l2_declared(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same property, for the L2 layout specifically (needs its own
        `monkeypatch`, so kept out of the parametrized group above)."""
        cwd = _build_l2_declared(tmp_path, monkeypatch)

        result = locate_project(cwd)
        oracle = _oracle_roots(cwd)

        assert result is not None
        assert any(result.workspace.samefile(root) for root in oracle)

    def test_locate_confinement_rejects_a_profile_supplied_trap_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Trap fixture: the L2 rung's `fleet.slug` profile value is set to
        a path-shaped string that resolves to a directory OUTSIDE the
        legitimate git-derived candidates, where that directory also
        contains a validly-named `SUPERHUMAN.md` (a planted trap). Assert
        the locator never resolves to the trap directory — it either
        refuses, or resolves to one of the legitimate roots. This is the
        test that would fail if D1's 'selector, never a constructor'
        property were violated."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_record(repo, "alpha")
        _write_record(repo, "beta")

        trap_root = tmp_path / "trap"
        _write_record(trap_root, "alpha")  # same slug as a REAL candidate

        profile = tmp_path / "profile.yaml"
        trap_slug_path = (trap_root / "docs" / "superhuman" / "alpha").as_posix()
        profile.write_text(f"fleet:\n  slug: {trap_slug_path}\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        result, _reason = locate_project_explain(repo)

        if result is not None:
            assert not str(result.workspace).startswith(str(trap_root))
            assert result.slug in {"alpha", "beta"}
            assert result.rung != "L2"  # the path-shaped value can never select via L2


# --- G6 D1 revision: nested inner repository (TC-66..TC-71) ---------------------------


class TestNestedInnerRepository:
    def test_locate_layout_nested_inner_repo_resolves_outward_to_outer_root(
        self, tmp_path: Path
    ) -> None:
        """TC-66: `docs/superhuman/` is itself a separate git repo.
        `cwd` = the project dir. The resolved workspace must equal the
        OUTER root (`Path.samefile`), reached via H1/L1."""
        cwd = _build_nested_inner_repo(tmp_path)
        outer = tmp_path / "outer"

        result = locate_project(cwd)

        assert result is not None
        assert result.workspace.samefile(outer)
        assert result.slug == "nested-project"
        assert result.rung == "L1"
        assert result.hop == "H1"

    def test_locate_refuses_at_the_inner_clone_root(self, tmp_path: Path) -> None:
        """TC-67: same fixture, `cwd` = the inner clone's own root (no
        `<slug>` component). Refuses: candidates exist at the outer root
        but there is no positional evidence for any of them."""
        inner_root = _build_nested_inner_repo_multi(tmp_path)

        result, reason = locate_project_explain(inner_root)

        assert result is None
        assert "nested-project" in reason
        assert "sibling-project" in reason


class TestG6Bounds:
    def test_locate_h1_is_bounded_to_one_hop(self, tmp_path: Path) -> None:
        """TC-68: nested TWO repository levels deep. Assert refusal, not a
        two-hop resolution — proves `MAX_OUTWARD_HOPS` is honoured."""
        assert MAX_OUTWARD_HOPS == 1
        cwd = _build_two_level_nest(tmp_path)

        result, reason = locate_project_explain(cwd)

        assert result is None
        assert "far-project" not in reason

    def test_locate_never_returns_home_or_above(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-69: `Path.home()` is monkeypatched so the only reachable
        outer root at H1 IS home. Assert refusal."""
        fake_home = tmp_path / "home"
        _write_record(fake_home, "home-project")
        _init_repo(fake_home, branch="main")
        inner = fake_home / "docs" / "superhuman"
        _init_repo(inner, branch="inner-branch")
        cwd = inner / "home-project"
        resolved_home = fake_home.resolve()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: resolved_home))

        result, _reason = locate_project_explain(cwd)

        assert result is None

    def test_locate_h1_only_admits_l1(self, tmp_path: Path) -> None:
        """TC-70: the outer root reachable via H1 has a branch name AND a
        singleton candidate that would each fire if a non-L1 rung were
        (incorrectly) permitted there — but `cwd` is not inside the
        candidate directory. Assert refusal. This is the test that catches
        a flattened ladder."""
        cwd = _build_h1_trap(tmp_path)

        result, _reason = locate_project_explain(cwd)

        assert result is None

    def test_locate_is_not_captured_by_an_unrelated_enclosing_repo(
        self, tmp_path: Path
    ) -> None:
        """TC-71: an inner repo with zero candidates, nested inside an
        UNRELATED outer repo that has its own real candidate elsewhere,
        with `cwd` not inside it. Assert the locator does not resolve to
        the neighbour's project."""
        cwd = _build_unrelated_capture(tmp_path)

        result, reason = locate_project_explain(cwd)

        assert result is None
        assert "unrelated-project" in reason


# --- Coverage: private-helper failure branches -----------------------------------------


class TestCoverageEdges:
    """Failure branches that are hard or unsafe to reach through real git
    behavior (a hung/missing git process, a corrupted repo, a self-looping
    `.git` layout) are exercised directly against the private helper they
    live in, or via a targeted monkeypatch — mirroring
    `tests/fleet/test_done.py`'s precedent of importing a module's private
    functions directly for exactly this kind of edge."""

    def test_run_git_returns_none_on_subprocess_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*_args: object, **_kwargs: object) -> None:
            raise OSError("git executable not found")

        monkeypatch.setattr(locate_module.subprocess, "run", _raise)

        assert locate_module._run_git(tmp_path, ["rev-parse", "--show-toplevel"]) is None

    def test_git_common_dir_parent_returns_none_outside_a_repo(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "plain-dir"
        not_a_repo.mkdir()

        assert locate_module._git_common_dir_parent(not_a_repo) is None

    def test_read_slug_returns_none_for_non_utf8_file(self, tmp_path: Path) -> None:
        record = tmp_path / "SUPERHUMAN.md"
        record.write_bytes(b"\xff\xfe\x00\x01garbage")

        assert locate_module._read_slug(record) is None

    def test_enumerate_candidates_returns_empty_when_docs_dir_is_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "docs" / "superhuman"
        base.mkdir(parents=True)

        def _raise_iterdir(_self: Path) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "iterdir", _raise_iterdir)

        assert locate_module._enumerate_candidates(tmp_path) == []

    def test_enumerate_candidates_skips_an_entry_whose_is_dir_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "docs" / "superhuman" / "flaky"
        base.mkdir(parents=True)
        real_is_dir = Path.is_dir

        def _flaky_is_dir(self: Path) -> bool:
            if self.name == "flaky":
                raise OSError("stat failed")
            return real_is_dir(self)

        monkeypatch.setattr(Path, "is_dir", _flaky_is_dir)

        assert locate_module._enumerate_candidates(tmp_path) == []

    def test_declared_profile_slug_returns_none_for_invalid_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet: [unterminated\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        assert locate_module._declared_profile_slug(tmp_path) is None

    def test_declared_profile_slug_returns_none_when_profile_is_not_a_mapping(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = tmp_path / "profile.yaml"
        profile.write_text("- just\n- a\n- list\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        assert locate_module._declared_profile_slug(tmp_path) is None

    def test_declared_profile_slug_returns_none_when_fleet_block_is_not_a_mapping(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = tmp_path / "profile.yaml"
        profile.write_text("fleet: not-a-mapping\n", encoding="utf-8")
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

        assert locate_module._declared_profile_slug(tmp_path) is None

    def test_rung_l3_ignores_a_path_shaped_branch_name(self, tmp_path: Path) -> None:
        """A branch name containing '/' can never equal a plain candidate
        slug, but the safety check that rejects it before comparing is its
        own explicit branch (defense-in-depth, mirrors L2's)."""
        repo = tmp_path / "repo"
        _init_repo(repo, branch="main")
        _run(repo, "checkout", "-q", "-b", "feature/not-a-slug")
        _write_record(repo, "alpha")
        _write_record(repo, "beta")

        result, reason = locate_project_explain(repo)

        assert result is None
        assert "alpha" in reason and "beta" in reason

    def test_locate_refuses_when_cwd_is_not_inside_any_git_repository(
        self, tmp_path: Path
    ) -> None:
        """CHUNK-1-FINDINGS #4: `cwd` not in any repository at all is an
        ordinary refusal, not an exception."""
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        result, reason = locate_project_explain(not_a_repo)

        assert result is None
        assert "not inside a git repository" in reason

    def test_locate_refuses_with_a_reason_when_h0_prime_is_ambiguous(
        self, tmp_path: Path
    ) -> None:
        """The worktree's own branch matches none of the main worktree's
        several candidates, and cwd/profile don't narrow either — H0'
        finds candidates but no rung is unique, so the walk stops there
        (never falls through to H1)."""
        main = tmp_path / "main"
        _init_repo(main, branch="main")
        _write_record(main, "alpha")
        _write_record(main, "beta")
        worktree = tmp_path / "worktree"
        _run(main, "worktree", "add", "-q", "-b", "unrelated-branch", str(worktree))

        result, reason = locate_project_explain(worktree)

        assert result is None
        assert "alpha" in reason and "beta" in reason

    def test_locate_falls_through_to_h1_when_h0_prime_also_has_zero_candidates(
        self, tmp_path: Path
    ) -> None:
        """H0' can apply (it names a different root than H0) and still find
        zero candidates there — the walk must keep going to H1 rather than
        stopping, exactly like a zero-candidate H0 does."""
        main = tmp_path / "main"
        _init_repo(main, branch="main")
        worktree = tmp_path / "worktree"
        _run(main, "worktree", "add", "-q", "-b", "feature", str(worktree))

        result, reason = locate_project_explain(worktree)

        assert result is None
        assert reason

    def test_locate_refuses_on_an_h1_self_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guard 2 (no self-loop): if root discovery's outward hop somehow
        landed back on H0's own root — a `.git` layout D1 calls "unusual" —
        the locator must refuse rather than re-examine the same root. Real
        git never produces this for an ordinary repo, so it is exercised by
        monkeypatching `_show_toplevel`'s H1 call directly."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        real_show_toplevel = locate_module._show_toplevel

        def _fake_show_toplevel(cwd: Path) -> Path | None:
            if cwd == repo.parent:
                return real_show_toplevel(repo)  # pretend the hop looped back
            return real_show_toplevel(cwd)

        monkeypatch.setattr(locate_module, "_show_toplevel", _fake_show_toplevel)

        result, reason = locate_project_explain(repo)

        assert result is None
        assert "looped back" in reason

    def test_locate_never_attempts_h1_when_max_outward_hops_is_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guard 1: with `MAX_OUTWARD_HOPS` forced to 0, a layout that would
        otherwise resolve via H1 (TC-66's shape) must refuse instead —
        proving the hop-count guard is actually load-bearing, not just
        documented."""
        monkeypatch.setattr(locate_module, "MAX_OUTWARD_HOPS", 0)
        cwd = _build_nested_inner_repo(tmp_path)

        result, reason = locate_project_explain(cwd)

        assert result is None
        assert "H1" not in reason
        assert "resolved via" not in reason
