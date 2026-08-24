"""Chunk 4 — dropped-thread detection end-to-end, **the value gate** (W-FR-5).

TC-16/TC-17 (`docs/superhuman/fleet-wiring/TEST.md`): proves `observe
handoff-emit` (Chunk 2) and `observe launch` (Chunk 1) compose correctly
with the Phase-1 `handoff stale` read entry point, driving **only** the
public `fleet observe` verbs and `fleet handoff stale` — no direct `core` or
`handoff` internals, no fixture rows seeded by hand anywhere in this file.
Every row the stale report ever sees was written by the observe path itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.fleet import observe
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.cli import build_parser
from scripts.fleet.handoff import extract_handoff_id


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
    """A workspace with a resolvable `SUPERHUMAN.md` identity and fleet enabled.

    Mirrors `tests/fleet/test_observe.py`'s fixture of the same name exactly
    (same profile shape, same `SUPERHUMAN.md` layout) — this file is not
    inventing a new fixture convention for Chunk 4, just reusing the one
    Chunks 1-3 already established.
    """
    slug = "wiring-e2e"
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
        f"**Slug:** {slug}\n**Project-id:** fleet-e2e-999\n", encoding="utf-8"
    )
    return git_repo, slug


def _cli_handoff_stale(
    workspace: Path,
    slug: str,
    *,
    expiry_seconds: float,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Drive `fleet handoff stale` through the real CLI parser (`cli.build_parser`).

    This is the sanctioned read entry point named in the task brief
    ("whatever the existing `handoff stale` / `handoff.stale_handoffs` query
    entry point is called") — TC-16's own step wording is literally "Call
    `fleet handoff stale`", so this goes through the actual subcommand
    dispatch rather than calling `handoff.stale_report()` as a shortcut.

    Aging note: the CLI's `stale` subcommand has no `--now` override (only
    `--expiry-seconds`) — it always compares against the real wall clock.
    Rather than freezing/monkeypatching `datetime.now`, this drives the
    expiry threshold itself down (following the existing
    `_resolve_handoff_expiry_seconds`/`stale_report(expiry_seconds=...)`
    precedent in `tests/fleet/test_handoff.py`), so real elapsed time — even
    a few milliseconds — already exceeds it. A negative `expiry_seconds` is
    used for the "aged" call specifically to make that margin unconditional
    rather than racing the clock's resolution.

    Returns:
        str: everything the subcommand printed to stdout.
    """
    parser = build_parser()
    args = parser.parse_args(
        [
            "handoff",
            "stale",
            "--workspace",
            str(workspace),
            "--slug",
            slug,
            "--expiry-seconds",
            str(expiry_seconds),
        ]
    )
    exit_code = args.func(args)
    assert exit_code == 0
    return capsys.readouterr().out


class TestDroppedThreadDetectionEndToEnd:
    """TC-16 + TC-17, folded into one test function per TEST.md's own framing:

    TC-17's negative assertion is "the same test as TC-16, not a separate
    test that could be skipped independently."
    """

    def test_exactly_the_unlaunched_thread_is_reported_stale_and_the_launched_one_never_is(
        self,
        enabled_project: tuple[Path, str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace, slug = enabled_project
        adapter = PortableAdapter(workspace, slug)

        # --- Step 1 (TC-16): emit TWO handoffs via `observe handoff-emit`. ---
        # Both share the same (cwd, branch) anchors deliberately — this
        # means the *only* thing that can disambiguate them later is each
        # row's own FLEET-HANDOFF-ID, not a fuzzy (cwd, branch) match, which
        # is exactly what a real dropped-thread scenario looks like (two
        # handoffs off the same branch, one picked up, one not).
        emitted_a = observe.observe_handoff_emit(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="Continue thread A.\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        emitted_b = observe.observe_handoff_emit(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text="Continue thread B.\n",
            cwd=workspace,
            branch="trunk",
            writer_role="pm",
        )
        assert emitted_a.ok is True
        assert emitted_b.ok is True
        assert emitted_a.node_id != emitted_b.node_id
        assert "FLEET-HANDOFF-ID" in emitted_a.prompt_text
        assert "FLEET-HANDOFF-ID" in emitted_b.prompt_text

        handoff_id_a = extract_handoff_id(emitted_a.prompt_text)
        handoff_id_b = extract_handoff_id(emitted_b.prompt_text)
        assert handoff_id_a is not None
        assert handoff_id_b is not None
        assert handoff_id_a != handoff_id_b

        # --- Step 2 (TC-16): launch exactly ONE of them (A) via `observe launch`. ---
        # Anchored by A's own embedded prompt text -> id-anchored resolution,
        # the primary/authoritative path (never the fuzzy (cwd, branch)
        # fallback, which would be ambiguous here by construction).
        launched_a = observe.observe_launch(
            adapter,
            workspace=workspace,
            slug=slug,
            prompt_text=emitted_a.prompt_text,
            writer_role="session",
        )
        assert launched_a.ok is True
        assert launched_a.node_id == emitted_a.node_id
        assert launched_a.reason == "launched"

        # --- TC-17, part 1: the negative assertion BEFORE aging anything. ---
        # A wide expiry (1 hour) so neither fresh row is stale yet; the
        # point is that A must never appear, launched or not, aged or not.
        fresh_report = _cli_handoff_stale(
            workspace, slug, expiry_seconds=3600.0, capsys=capsys
        )
        assert handoff_id_a not in fresh_report
        assert emitted_a.node_id not in fresh_report
        assert handoff_id_b not in fresh_report  # B isn't aged past 1h yet either
        assert "no stale handoffs" in fresh_report

        # --- Step 3 (TC-16): age BOTH rows past the expiry window. ---
        # No row is touched directly (that would be a hand-seeded fixture) —
        # the expiry threshold is driven negative instead, so both rows'
        # real (however small) elapsed age already exceeds it.
        aged_report = _cli_handoff_stale(
            workspace, slug, expiry_seconds=-1.0, capsys=capsys
        )

        # --- Step 4 / TC-16: exactly the unlaunched thread (B) is reported. ---
        assert handoff_id_b in aged_report
        assert emitted_b.node_id in aged_report

        # --- TC-17, part 2: the negative assertion again, now that aging ---
        # would also have caught A if the launch flip hadn't excluded it —
        # this is the actual false-positive proof, not merely "B is present."
        assert handoff_id_a not in aged_report
        assert emitted_a.node_id not in aged_report

        # And nothing else snuck in: exactly one stale row, and it is B's.
        stale_lines = [line for line in aged_report.splitlines() if line.strip()]
        assert len(stale_lines) == 1
        assert emitted_b.node_id in stale_lines[0]
        assert handoff_id_b in stale_lines[0]
