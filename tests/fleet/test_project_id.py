"""Tests for `scripts.fleet.project_id` — fail-closed minting + validation.

Chunk 4, per `TEST.md` TC-25..TC-34.

Deliberately a separate module and a separate test file from
`scripts/fleet/project.py` / `tests/fleet/test_project.py` — `project.py`'s
"never invents an id" read contract must stay literally true, so
`project_id.py` (mint/check) is never imported by `observe.py` and its
tests never import `project.py` internals either.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from scripts.fleet.cli import build_parser
from scripts.fleet.project_id import check_project_id, mint_project_id

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 16 lowercase hex characters — `uuid4().hex[:16]`'s exact shape.
_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")


def _write_superhuman_md(
    workspace: Path, slug: str, *, project_id: str | None = None, file_slug: str | None = None
) -> Path:
    """Write `<workspace>/docs/superhuman/<slug>/SUPERHUMAN.md`.

    Matches `test_project.py`'s `_write_superhuman_md` idiom, extended
    with keyword control over whether/what `**Project-id:**` and
    `**Slug:**` carry, since this module's tests need both "absent" and
    "already present" record shapes.

    Args:
        workspace: the working tree root.
        slug: the project directory name under `docs/superhuman/`.
        project_id: the `**Project-id:**` value to write, or `None` to
            omit the line entirely.
        file_slug: the `**Slug:**` value to write (defaults to `slug`).

    Returns:
        Path: the written `SUPERHUMAN.md` path.
    """
    project_dir = workspace / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    record = project_dir / "SUPERHUMAN.md"
    lines = [f"# Superhuman: {slug}\n\n", f"**Slug:** {file_slug or slug}\n"]
    if project_id is not None:
        lines.append(f"**Project-id:** {project_id}\n")
    lines.append("**Started:** 2026-09-08T00:00:00Z\n")
    record.write_text("".join(lines), encoding="utf-8")
    return record


class TestMinting:
    def test_mint_project_id_is_16_hex_from_uuid4(self, tmp_path: Path) -> None:
        """A newly minted id is 16 lowercase hex characters
        (`uuid4().hex[:16]`), and contains neither the slug string nor any
        substring derived from a git remote URL."""
        slug = "wobbly-widget-forge"
        _write_superhuman_md(tmp_path, slug)

        minted = mint_project_id(tmp_path, slug)

        assert _HEX16_RE.fullmatch(minted), f"{minted!r} is not 16 lowercase hex chars"
        assert slug not in minted
        assert "github.com" not in minted

    def test_mint_project_id_differs_for_same_slug_different_remote(
        self, tmp_path: Path
    ) -> None:
        """Direct regression against the forbidden 'hash of repo-remote +
        slug' scheme (D3's correction to `SUPERHUMAN.md.tpl`): two records
        sharing the SAME slug in two different repos (different remotes)
        must not collide, and neither id must be derivable from the
        other's remote+slug pair."""
        slug = "shared-slug-name"
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        for repo, remote in ((repo_a, "https://example.invalid/org/repo-a.git"),
                              (repo_b, "https://example.invalid/org/repo-b.git")):
            repo.mkdir()
            _write_superhuman_md(repo, slug)
            # The remote is recorded on disk only to prove mint_project_id
            # never consults it — the function's signature takes no remote
            # argument at all, so this is a belt-and-braces regression
            # anchor rather than an exercised code path.
            (repo / ".git-remote-for-test").write_text(remote, encoding="utf-8")

        id_a = mint_project_id(repo_a, slug)
        id_b = mint_project_id(repo_b, slug)

        assert id_a != id_b
        assert "repo-a" not in id_a and "repo-b" not in id_a
        assert "repo-a" not in id_b and "repo-b" not in id_b

    def test_mint_is_a_noop_on_a_record_that_already_has_an_id(
        self, tmp_path: Path
    ) -> None:
        """Re-running `mint_project_id` against a `SUPERHUMAN.md` that
        already carries a `**Project-id:**` line leaves that value
        unchanged (FR-11: an id is minted once, never re-derived)."""
        slug = "already-has-one"
        _write_superhuman_md(tmp_path, slug, project_id="fixed1234567890a")

        first = mint_project_id(tmp_path, slug)
        second = mint_project_id(tmp_path, slug)

        assert first == "fixed1234567890a"
        assert second == "fixed1234567890a"

        record = tmp_path / "docs" / "superhuman" / slug / "SUPERHUMAN.md"
        assert record.read_text(encoding="utf-8").count("**Project-id:**") == 1

    def test_mint_replaces_a_blank_project_id_value_in_place(self, tmp_path: Path) -> None:
        """A record whose `**Project-id:**` key exists but carries a blank
        value (e.g. a copied template whose value was never filled in) is
        treated as absent: the existing blank line is replaced in place
        with the freshly minted id, rather than a second line being
        inserted alongside it.

        The blank `**Project-id:**` line is deliberately the LAST line in
        the fixture (matching `test_project.py`'s own
        `test_blank_project_id_value_returns_none` precedent): `_PROJECT_ID_RE`'s
        `\\s*` is greedy across `re.MULTILINE` line boundaries, so a blank
        value immediately followed by another `**field:**` line would
        consume into it and match that field's key as if it were the id
        value — a pre-existing characteristic of the unmodified
        `project.py` regex (D3, TestModuleIsolation), not something this
        module's fixture should exercise here.
        """
        slug = "blank-value-record"
        project_dir = tmp_path / "docs" / "superhuman" / slug
        project_dir.mkdir(parents=True)
        record = project_dir / "SUPERHUMAN.md"
        record.write_text(
            f"**Slug:** {slug}\n**Project-id:**   \n", encoding="utf-8"
        )

        minted = mint_project_id(tmp_path, slug)

        assert _HEX16_RE.fullmatch(minted)
        text = record.read_text(encoding="utf-8")
        assert text.count("**Project-id:**") == 1
        assert f"**Project-id:** {minted}" in text

    def test_mint_prepends_when_no_slug_line_exists_to_anchor_on(
        self, tmp_path: Path
    ) -> None:
        """A badly malformed record with no `**Slug:**` line at all still
        gets an id minted (degrades to prepending) rather than failing to
        mint."""
        slug = "no-slug-line-at-all"
        project_dir = tmp_path / "docs" / "superhuman" / slug
        project_dir.mkdir(parents=True)
        record = project_dir / "SUPERHUMAN.md"
        record.write_text("# Superhuman: malformed\n\nNo Slug line here.\n", encoding="utf-8")

        minted = mint_project_id(tmp_path, slug)

        assert _HEX16_RE.fullmatch(minted)
        text = record.read_text(encoding="utf-8")
        assert text.startswith(f"**Project-id:** {minted}\n")


class TestFailClosedValidator:
    def test_check_project_id_exits_nonzero_when_absent(self, tmp_path: Path) -> None:
        """`fleet project check` against a record with no `Project-id:`
        exits with a NON-ZERO code — a fail-closed assertion, deliberately
        outside the `observe.py` fail-soft façade (FR-12)."""
        slug = "no-id-yet"
        _write_superhuman_md(tmp_path, slug)

        parser = build_parser()
        args = parser.parse_args(
            ["project", "check", "--workspace", str(tmp_path), "--slug", slug]
        )
        exit_code = args.func(args)

        assert exit_code != 0

    def test_check_project_id_exits_zero_when_present(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`fleet project check` against a fully-configured record exits 0
        and reports the id."""
        slug = "fully-configured"
        _write_superhuman_md(tmp_path, slug, project_id="abcdef0123456789")

        parser = build_parser()
        args = parser.parse_args(
            ["project", "check", "--workspace", str(tmp_path), "--slug", slug]
        )
        exit_code = args.func(args)

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "abcdef0123456789" in captured.out


class TestModuleIsolation:
    """D3: `project_id.py` is a strictly separate module from
    `project.py`, and `project.py` stays unmodified by this chunk."""

    #: Golden SHA-256 of `scripts/fleet/project.py` as of Chunk 3 (this
    #: chunk must not modify it — D3). Regenerate ONLY if `project.py` is
    #: deliberately changed by a later, unrelated chunk.
    _PROJECT_PY_SHA256 = "b0e2d3af6aac54d09e49c7b51875028bfcbf99462b534a134982f2c68dc36556"

    def test_project_py_is_unmodified(self) -> None:
        """Content-hash guard on `scripts/fleet/project.py`, matching the
        `TestCoreUntouched` idiom already used for `scripts/fleet/core/` —
        a golden SHA-256 of the file, asserted unchanged."""
        project_py = _REPO_ROOT / "scripts" / "fleet" / "project.py"
        actual = hashlib.sha256(project_py.read_bytes()).hexdigest()
        assert actual == self._PROJECT_PY_SHA256, (
            "scripts/fleet/project.py changed. D3 requires it stay byte-unchanged so "
            "its 'never invents an id' read contract stays literally true; minting "
            "and validation belong in scripts/fleet/project_id.py instead."
        )

    #: Matches any import spelling that would pull in `project_id.py`:
    #: `import project_id`, `from . import project_id`, `from .project_id
    #: import ...`, or the fully-qualified `scripts.fleet.project_id`
    #: form. Deliberately NOT a bare substring check on `"project_id"` —
    #: `observe.py` legitimately names an unrelated `project_id` keyword
    #: argument (the owning project's id, per W-FR-6) throughout its
    #: source, which a substring check would false-positive on.
    _IMPORTS_PROJECT_ID_MODULE_RE = re.compile(
        r"^\s*(?:from\s+\.(?:\s+import\s+project_id\b|project_id\s+import\b)"
        r"|import\s+(?:scripts\.fleet\.)?project_id\b)",
        re.MULTILINE,
    )

    def test_project_id_py_never_imported_by_observe_py(self) -> None:
        """Static source check: neither `import project_id` nor `from .
        import project_id` (nor any equivalent import spelling) appears
        anywhere in `scripts/fleet/observe.py`'s source text — the
        fail-soft façade must never gain a dependency on the fail-closed
        minting module."""
        observe_py = _REPO_ROOT / "scripts" / "fleet" / "observe.py"
        text = observe_py.read_text(encoding="utf-8")
        match = self._IMPORTS_PROJECT_ID_MODULE_RE.search(text)
        assert match is None, (
            f"scripts/fleet/observe.py imports scripts/fleet/project_id ({match.group(0)!r} "
            "found) — D3: the fail-soft façade must not depend on the fail-closed module"
        )


def _merge_base_with_main() -> str | None:
    """Return the merge-base commit of `HEAD` and `origin/main`, or `None`.

    Mirrors `test_seams.py`'s `_merge_base_with_main` helper (same
    additive-diff idiom, TC-24), duplicated here rather than imported —
    this module's own tests should not depend on another chunk's test
    module staying importable/unchanged.
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


class TestKickoffSeam:
    def test_kickoff_seam_calls_check_then_mint_additively(self) -> None:
        """Content test on `phases/0-kickoff.md`: the new step text is
        present, additive (diff against merge-base has zero removed lines,
        per the existing additive-edit idiom in `test_seams.py`)."""
        kickoff = _REPO_ROOT / "phases" / "0-kickoff.md"
        text = kickoff.read_text(encoding="utf-8")

        assert "project check" in text
        assert "project mint" in text
        assert "python -m scripts.fleet.cli project check" in text
        assert "python -m scripts.fleet.cli project mint" in text

        merge_base = _merge_base_with_main()
        if merge_base is None:
            pytest.skip("git or origin/main merge-base unavailable in this environment")

        result = subprocess.run(
            ["git", "diff", merge_base, "--", "phases/0-kickoff.md"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"git diff against {merge_base} failed in this environment")

        removed_lines = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("-") and not line.startswith("---")
        ]
        assert removed_lines == [], (
            f"phases/0-kickoff.md: diff against merge-base {merge_base} removed lines:\n"
            + "\n".join(removed_lines)
        )

    def test_superhuman_md_tpl_no_longer_suggests_remote_plus_slug_hash(
        self,
    ) -> None:
        """`templates/SUPERHUMAN.md.tpl` no longer contains the phrase
        suggesting 'a stable hash of repo-remote + slug' as a minting
        option — D3's correction, since that scheme is slug-derived and
        forbidden by FR-11."""
        tpl = _REPO_ROOT / "templates" / "SUPERHUMAN.md.tpl"
        text = tpl.read_text(encoding="utf-8")
        assert "stable hash of repo-remote" not in text
        assert "Decision F" in text, (
            "the correction must keep the rule and its Decision F reference, "
            "only drop the slug-derived suggestion"
        )
        assert "never re-minted" in text or "never derived from the slug" in text


class TestThisProjectsOwnException:
    def test_this_projects_own_project_id_is_not_reminted(
        self, tmp_path: Path
    ) -> None:
        """Regression anchor for PLAN.md Chunk 4's explicit note: this
        project's own `SUPERHUMAN.md` (`Project-id: 7124ce46ccd0a49a`,
        minted under the now-forbidden scheme before this rule existed)
        must NOT be silently re-minted by any mint/backfill pass — a
        `Project-id` is never re-minted once assigned (Phase 1 Decision
        F). A FIXTURE reproduces the real record's shape (the real record
        itself lives outside this worktree, in the private docs mount,
        and must never be edited by a test)."""
        slug = "fleet-deterministic-seams"
        live_exception_id = "7124ce46ccd0a49a"
        _write_superhuman_md(tmp_path, slug, project_id=live_exception_id)
        record = tmp_path / "docs" / "superhuman" / slug / "SUPERHUMAN.md"
        before = record.read_bytes()

        result = mint_project_id(tmp_path, slug)

        after = record.read_bytes()
        assert result == live_exception_id
        assert before == after, "mint must not touch a record that already has an id"
        assert check_project_id(tmp_path, slug) == live_exception_id


class TestSentinelIdsAreNotIdentities:
    """The unfilled-template-placeholder gap, found during chunk 4 review.

    `project.py`'s reader matches any non-blank `\\S+`, so a record straight
    out of `templates/SUPERHUMAN.md.tpl` used to read as though it already
    carried an id. The consequence was worse than a missing id: `check`
    passed, `mint` no-opped, and **every** new project would have written
    manifest rows under the same literal ``{{project_id}}`` — collapsing
    unrelated projects into one phantom project.

    Fixed by making `is_sentinel_id` treat a placeholder as absent, so
    `check` stays fail-closed and `mint` replaces it. `project.py` itself is
    deliberately untouched — D3 keeps its read contract literal.

    The template deliberately KEEPS its visible placeholder. Blanking the
    value was tried first and measured to be strictly worse: `project.py`'s
    `\\s*` crosses the newline under `re.MULTILINE`, so a blank makes the
    reader capture the following comment line and return `'<!--'` as the id.
    """

    @pytest.mark.parametrize(
        "value",
        # NB: the blank-value cases are deliberately absent. `project.py`'s
        # `\s*` crosses the newline under re.MULTILINE, so a blank value never
        # reaches this guard as a blank — the reader captures the NEXT line and
        # returns its leading token (measured: `'<!--'`). That capture is covered
        # by the `<!--` case below, which is the shape actually observed.
        ["{{project_id}}", "{{ project_id }}", "TODO", "todo", "REPLACE_ME", "<project-id>", "<!--"],
    )
    def test_sentinel_values_are_reported_as_absent(self, tmp_path: Path, value: str) -> None:
        """A placeholder is present-but-not-an-identity, so `check` says absent."""
        _write_superhuman_md(tmp_path, "tpl-proj", project_id=value)
        assert check_project_id(tmp_path, "tpl-proj") is None

    def test_mint_replaces_a_placeholder_rather_than_no_opping(self, tmp_path: Path) -> None:
        """`mint` must REPLACE a sentinel — no-op here would leave the record unwritable."""
        record = _write_superhuman_md(tmp_path, "tpl-proj", project_id="{{project_id}}")
        minted = mint_project_id(tmp_path, "tpl-proj")
        assert _HEX16_RE.match(minted), f"expected a random 16-hex id, got {minted!r}"
        assert "{{project_id}}" not in record.read_text(encoding="utf-8")
        assert check_project_id(tmp_path, "tpl-proj") == minted

    def test_a_real_id_is_never_mistaken_for_a_sentinel(self, tmp_path: Path) -> None:
        """The guard must not swallow legitimate ids, including the legacy non-uuid ones.

        `7124ce46ccd0a49a` is this project's own id, minted from the
        now-forbidden remote+slug hash before the rule existed. Re-minting
        it would orphan the history already written under it.
        """
        for real in ("7124ce46ccd0a49a", "fleet-f48a2e71a7bd", "3a483d02-8651-4ae3-85c1-7471ae47c189"):
            _write_superhuman_md(tmp_path, "real-proj", project_id=real)
            assert check_project_id(tmp_path, "real-proj") == real
            assert mint_project_id(tmp_path, "real-proj") == real, "mint must stay a no-op"

    def test_the_shipped_template_no_longer_carries_a_placeholder_value(self) -> None:
        """Root-cause guard: the template must not ship a fillable id VALUE.

        A content test rather than a behaviour test, because the defect was a
        property of the shipped tree, not of any code path.
        """
        tpl = (_REPO_ROOT / "templates" / "SUPERHUMAN.md.tpl").read_text(encoding="utf-8")
        line = next(ln for ln in tpl.splitlines() if ln.startswith("**Project-id:**"))
        assert line.strip() == "**Project-id:** {{project_id}}", (
            f"template must ship a VISIBLE placeholder value, got {line!r}. Blanking it is "
            "strictly worse and was measured: project.py's `\\s*` crosses the newline, so a "
            "blank value makes the reader capture the comment leader and return '<!--' as the "
            "project id, which no guard would naturally flag."
        )
