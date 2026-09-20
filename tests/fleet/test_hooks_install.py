"""Tests for `scripts.fleet.hooks_install` -- `fleet hooks install|uninstall|status`.

Per `TEST.md`'s Chunk 8 section and the chunk-8 PM ruling (SUPERHUMAN.md,
2026-09-16, R1..R6): TC-54..TC-62 plus TC-92 (owned by chunk 7a's design,
shipped here) and TC-94..TC-98 (this chunk's own additions, append-only
after TC-93).

**HARD RULE, enforced by this module's own fixtures, not merely intended:
every test here operates on a TEMPORARY `settings.json` under `tmp_path`.
No test may write to the real `~/.claude/settings.json`.**

Enforcement layers (TEST.md's Chunk 8 section, in full):
  1. `hooks_install.install()` / `uninstall()` / `status()` accept an
     explicit `settings_path: Path` parameter -- every call in this module
     passes it as an explicit `settings_path=` keyword, sourced from one of
     the two fixtures below. `TestEnforcementLayer1SettingsPathIsAlwaysExplicit`
     (TC-58) statically re-verifies this against the module's own source.
  2. `temp_settings_json` / `temp_settings_json_clean` seed a SYNTHETIC file
     (never a real operator command string) shape-matching the REAL file as
     re-counted by the PM on 2026-09-16 (R4) -- richer than this scaffold's
     original docstring, which assumed 5 SessionStart + 1 SubagentStart + 3
     PreToolUse. The real counts are: `PreToolUse` 3 matcher groups / 4
     commands (`Edit|Write` x1, `Bash` x2, a fourth group x1); `SessionStart`
     4 groups -- `startup` (7: 6 foreign + 1 PRE-EXISTING superhuman-owned
     entry pointing at a worktree, carrying `timeout: 10` -- the real
     2026-09-09 hand install), `resume` (6), `clear` (3), `compact` (2), no
     `fork` group; `SubagentStart` 1 group / 1 foreign command.
     `temp_settings_json_clean` is the same shape MINUS that pre-existing
     owned entry (R5: TC-57's exact-round-trip guarantee needs a seed with
     NO pre-existing superhuman entry, since uninstall legitimately removes
     a migrated one too -- the migration case gets its own test, TC-95).
  3. `_real_settings_untouched` is an autouse, module-scoped tripwire:
     hashes the REAL settings.json (or confirms its absence) before this
     module's tests run, and asserts the hash is unchanged after every
     test and at teardown. `TestEnforcementLayer3ExplicitAssertion` (TC-59)
     is the same guarantee again, as an explicit, readable assertion tied
     to a named test rather than only a fixture-teardown error.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.fleet import cli, hooks_install

_REAL_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

#: The real secure-plugin-installer guard script (TC-62, A3). Its own
#: PreToolUse contract only blocks a settings.json edit that ADDS a new,
#: unapproved `enabledPlugins` slug (see its module docstring) --
#: `hooks_install` never touches `enabledPlugins`, so this is fully
#: simulable rather than an integration gap.
_EDIT_WRITE_GUARD_SCRIPT = (
    Path.home() / ".claude" / "skills" / "secure-plugin-installer" / "scripts" / "hooks" / "pre-settings-edit.py"
)


def _settings_shape_summary(path: Path) -> str:
    """Describe `path`'s settings.json SHAPE for a tripwire failure message
    (chunk 9, routed-in from the chunk-8 review) -- top-level key names and,
    within `"hooks"`, its event names (`PreToolUse`/`SessionStart`/...) --
    never full command strings, so a failure message can never leak an
    operator path/token even incidentally.

    This exists to distinguish the tripwire's two possible causes at a
    glance: "this module wrote to the real file" (the thing it exists to
    catch) usually changes `"hooks"`'s own shape, while "another process
    edited the file during this run" (observed live, chunk 8 review --
    another session added a top-level `Stop` hook) usually changes the
    TOP-LEVEL key set instead. Never used to weaken the assertion itself,
    which stays a strict hash comparison.

    Args:
        path: the settings.json to describe.

    Returns:
        str: a short description, or a fixed string when the file is
        absent or unreadable (never raises).
    """
    if not path.is_file():
        return "<absent>"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "<unreadable/invalid JSON>"
    if not isinstance(data, dict):
        return f"<top-level JSON is a {type(data).__name__}, not an object>"
    top_level_keys = sorted(data.keys())
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        hook_events = sorted(hooks.keys())
    else:
        hook_events = []
    return f"top-level keys={top_level_keys!r}, hook events={hook_events!r}"


def _hash_real_settings() -> str | None:
    """Return the SHA-256 hex digest of the real settings.json, or None if
    it does not exist.

    Returns:
        The digest string, or None when the file is absent.
    """
    if not _REAL_SETTINGS_PATH.is_file():
        return None
    return hashlib.sha256(_REAL_SETTINGS_PATH.read_bytes()).hexdigest()


_recorded_real_hash: str | None = None
_recorded_real_shape: str = "<not yet recorded>"


@pytest.fixture(autouse=True, scope="module")
def _real_settings_untouched():  # type: ignore[no-untyped-def]
    """Module-scoped tripwire (enforcement layer 3).

    Records the real settings.json's hash (or its absence) before any test
    in this module runs, and asserts it is unchanged after the whole
    module completes. The recorded value is also stashed at module scope
    (`_recorded_real_hash`) so `test_real_settings_json_hash_unchanged_
    across_this_module` (TC-59) can re-assert it explicitly, independent
    of this fixture's own teardown assertion.

    The failure message (chunk 9, routed-in from the chunk-8 review) names
    WHICH top-level keys / hook events differ between the recorded and
    current shape -- see `_settings_shape_summary`. This cannot tell "this
    module wrote to the file" apart from "another process on this laptop
    edited it mid-run" on its own (both are still a hash mismatch, and the
    assertion below stays exactly as strict either way), but a human
    reading the message can now usually tell which happened at a glance,
    rather than the two causes being indistinguishable text as before.

    Yields:
        None.
    """
    global _recorded_real_hash, _recorded_real_shape
    before = _hash_real_settings()
    _recorded_real_hash = before
    _recorded_real_shape = _settings_shape_summary(_REAL_SETTINGS_PATH)
    yield
    after = _hash_real_settings()
    assert after == before, (
        "tests/fleet/test_hooks_install.py touched the REAL "
        f"{_REAL_SETTINGS_PATH} -- every test in this module must operate "
        "on the temp_settings_json fixture's path only. THIS ASSERTION "
        "CANNOT DISTINGUISH that from another process on this laptop "
        "editing the real file during this run (observed live, chunk 8 "
        "review) -- compare the shapes below to tell which happened: "
        f"recorded shape was [{_recorded_real_shape}]; current shape is "
        f"[{_settings_shape_summary(_REAL_SETTINGS_PATH)}]."
    )


def _seed_hooks_document(*, include_migration_entry: bool) -> dict[str, Any]:
    """Build the synthetic hooks document matching R4's re-counted live
    shape (SUPERHUMAN.md Decisions log, chunk-8 PM ruling, 2026-09-16).

    Args:
        include_migration_entry: when True, the `startup` group's foreign
            commands are joined by ONE pre-existing superhuman-owned
            `session-start` entry pointing at a synthetic worktree root
            (the real 2026-09-09 hand install's shape) -- required by
            TC-95's migration test. When False, `startup` carries only
            foreign commands, matching R5's requirement that TC-57's
            exact-round-trip seed hold no pre-existing superhuman entry.

    Returns:
        The full settings document (just the `"hooks"` top-level key --
        real files carry other keys too, but this installer must never
        touch them, which the round-trip tests verify).
    """
    startup_hooks: list[dict[str, Any]] = [
        {"type": "command", "command": f"synthetic-other-skill-startup-hook-{i}"} for i in range(1, 7)
    ]
    if include_migration_entry:
        startup_hooks.append(
            {
                "type": "command",
                "command": (
                    "C:/example/skills/superhuman/.claude/worktrees/"
                    "fleet-deterministic-seams/templates/hooks/claude-code/session-start"
                ),
                "timeout": 10,
            }
        )
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [{"type": "command", "command": "synthetic-other-skill-edit-write-hook"}],
                },
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "synthetic-other-skill-bash-hook-1"},
                        {"type": "command", "command": "synthetic-other-skill-bash-hook-2"},
                    ],
                },
                {
                    "matcher": "Bash|Edit|Write|NotebookEdit|AskUserQuestion|.*archive_session",
                    "hooks": [{"type": "command", "command": "synthetic-other-skill-overnight-guard-hook"}],
                },
            ],
            "SessionStart": [
                {"matcher": "startup", "hooks": startup_hooks},
                {
                    "matcher": "resume",
                    "hooks": [
                        {"type": "command", "command": f"synthetic-other-skill-resume-hook-{i}"}
                        for i in range(1, 7)
                    ],
                },
                {
                    "matcher": "clear",
                    "hooks": [
                        {"type": "command", "command": f"synthetic-other-skill-clear-hook-{i}"}
                        for i in range(1, 4)
                    ],
                },
                {
                    "matcher": "compact",
                    "hooks": [
                        {"type": "command", "command": f"synthetic-other-skill-compact-hook-{i}"}
                        for i in range(1, 3)
                    ],
                },
            ],
            "SubagentStart": [
                {"matcher": "*", "hooks": [{"type": "command", "command": "synthetic-other-skill-subagent-hook"}]},
            ],
        },
    }


def _write_seed(path: Path, *, include_migration_entry: bool) -> None:
    """Write a synthetic seed document to `path` (2-space indent, LF, one
    trailing newline -- matching `hooks_install._dump_settings`'s own
    shape, so a no-op install produces a byte-identical re-read).

    Args:
        path: destination file.
        include_migration_entry: see `_seed_hooks_document`.
    """
    text = json.dumps(_seed_hooks_document(include_migration_entry=include_migration_entry), indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def temp_settings_json(tmp_path: Path) -> Path:
    """A synthetic settings.json shape-matching the real file (R4),
    INCLUDING one pre-existing superhuman-owned `startup` entry pointing
    at a worktree -- the real 2026-09-09 hand install's shape, needed by
    the migration test (TC-95) and harmless to every other test here.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        Path to the synthetic settings.json file.
    """
    path = tmp_path / "settings.json"
    _write_seed(path, include_migration_entry=True)
    return path


@pytest.fixture
def temp_settings_json_clean(tmp_path: Path) -> Path:
    """Same shape as `temp_settings_json`, but with NO pre-existing
    superhuman entry -- required by TC-57's exact-round-trip guarantee
    (R5: uninstall legitimately removes a migrated entry too, because it
    is ours wherever it points, so an exact round trip needs a seed with
    none to begin with).

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        Path to the synthetic settings.json file.
    """
    path = tmp_path / "settings.json"
    _write_seed(path, include_migration_entry=False)
    return path


class TestInstall:
    def test_installer_adds_entries_to_temp_settings_only(self, temp_settings_json: Path) -> None:
        """TC-54: `install(settings_path=temp_settings_json)` adds exactly
        the superhuman `SessionStart`/`SubagentStart`/`PreToolUse` entries
        to the temp file; the real settings.json is never opened (see the
        module-level tripwire fixture)."""
        result = hooks_install.install(settings_path=temp_settings_json)
        data = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        for hook_event, matcher, basename in hooks_install._expected_entries():
            group = next(g for g in data["hooks"][hook_event] if g["matcher"] == matcher)
            commands = [c["command"] for c in group["hooks"]]
            assert hooks_install._command_for(result.root, basename) in commands

    def test_installer_idempotent_on_rerun(self, temp_settings_json: Path) -> None:
        """TC-55: running `install()` twice against the same temp file
        produces the same content -- no duplicate entries."""
        hooks_install.install(settings_path=temp_settings_json)
        after_first = temp_settings_json.read_text(encoding="utf-8")

        second_result = hooks_install.install(settings_path=temp_settings_json)
        after_second = temp_settings_json.read_text(encoding="utf-8")

        assert after_second == after_first
        assert second_result.changed is False

    def test_installer_refuses_to_touch_unowned_entries(self, temp_settings_json: Path) -> None:
        """TC-56: every seeded FOREIGN entry (i.e. excluding the one
        pre-existing superhuman entry this fixture also seeds for TC-95)
        survives install() byte-identical; only new superhuman entries are
        added."""
        before = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        foreign_before = sorted(
            c["command"]
            for groups in before["hooks"].values()
            for g in groups
            for c in g["hooks"]
            if hooks_install._owned_basename(c["command"]) is None
        )

        hooks_install.install(settings_path=temp_settings_json)

        after = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        foreign_after = sorted(
            c["command"]
            for groups in after["hooks"].values()
            for g in groups
            for c in g["hooks"]
            if hooks_install._owned_basename(c["command"]) is None
        )
        assert foreign_after == foreign_before

    def test_installer_writes_timeout_10_backstop(self, temp_settings_json: Path) -> None:
        """TC-60 (A3): every entry the installer adds carries `"timeout":
        10` -- the NFR-2 latency backstop for the case a hook wedges."""
        hooks_install.install(settings_path=temp_settings_json)
        data = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        for hook_event, matcher, basename in hooks_install._expected_entries():
            group = next(g for g in data["hooks"][hook_event] if g["matcher"] == matcher)
            entry = next(c for c in group["hooks"] if hooks_install._owned_basename(c["command"]) == basename)
            assert entry["timeout"] == 10


class TestUninstall:
    def test_uninstall_restores_prior_state_exactly(self, temp_settings_json_clean: Path) -> None:
        """TC-57: byte-diff of the temp file's content before install()
        vs. after install() then uninstall() is empty -- an exact round
        trip. Uses the CLEAN fixture (R5): a seed carrying a pre-existing
        superhuman entry would legitimately end up altered by uninstall
        too (that migration case is TC-95, not this one)."""
        before_text = temp_settings_json_clean.read_text(encoding="utf-8")

        hooks_install.install(settings_path=temp_settings_json_clean)
        hooks_install.uninstall(settings_path=temp_settings_json_clean)

        after_text = temp_settings_json_clean.read_text(encoding="utf-8")
        assert after_text == before_text

    def test_round_trip_preserves_foreign_empty_and_keyless_hook_groups(
        self, tmp_path: Path
    ) -> None:
        """Preflight B2, round-trip form: TC-57's exact-round-trip
        guarantee, re-run against a seed `temp_settings_json_clean` never
        exercises -- a `PostToolUse` group with an empty `"hooks": []`
        array, and a `Stop` group with no `"hooks"` key at all. Both must
        survive `install()` then `uninstall()` byte-identical, exactly
        like every other foreign entry (TC-56/TC-57's own guarantee,
        applied to the shape B2 found broken).
        """
        path = tmp_path / "settings.json"
        seed: dict[str, Any] = {
            "hooks": {
                "PostToolUse": [{"matcher": "Bash", "hooks": []}],
                "Stop": [{"matcher": "*"}],
            }
        }
        before_text = json.dumps(seed, indent=2) + "\n"
        path.write_text(before_text, encoding="utf-8")

        hooks_install.install(settings_path=path)
        after_install = json.loads(path.read_text(encoding="utf-8"))
        assert after_install["hooks"]["PostToolUse"] == [{"matcher": "Bash", "hooks": []}], (
            "install() altered the foreign PostToolUse group with an empty "
            "hooks array"
        )
        assert after_install["hooks"]["Stop"] == [{"matcher": "*"}], (
            "install() altered the foreign Stop group with no hooks key"
        )

        hooks_install.uninstall(settings_path=path)
        after_text = path.read_text(encoding="utf-8")
        assert after_text == before_text, (
            "uninstall() did not restore the seed exactly -- the foreign "
            "PostToolUse/Stop groups did not round-trip"
        )


class TestStatus:
    def test_status_reports_not_installed_before_install(self, temp_settings_json_clean: Path) -> None:
        """TC-61: `status(settings_path=...)` against a freshly-seeded
        temp file (no superhuman entries yet) reports not installed."""
        result = hooks_install.status(settings_path=temp_settings_json_clean)
        assert result.installed is False

    def test_status_reports_installed_after_install(self, temp_settings_json_clean: Path) -> None:
        """TC-61: `status()` after `install()` reports installed."""
        hooks_install.install(settings_path=temp_settings_json_clean)
        result = hooks_install.status(settings_path=temp_settings_json_clean)
        assert result.installed is True


_GUARDED_FUNCS = frozenset({"install", "uninstall", "status"})


def _call_target_name(node: ast.Call) -> str | None:
    """Return the called function's bare name, for `ast.Attribute` (e.g.
    `hooks_install.install(...)`) or `ast.Name` (e.g. `install(...)`)
    call forms.

    Args:
        node: an `ast.Call` node.

    Returns:
        The callee's simple name, or None for any other call shape.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


class TestEnforcementLayer1SettingsPathIsAlwaysExplicit:
    """Enforcement layer 1 from the module docstring, expressed as a
    static self-check over this file's own source."""

    def test_no_call_in_this_module_omits_settings_path(self) -> None:
        """TC-58: parse this test module's own source and assert every
        call site to `install(`, `uninstall(`, or `status(` includes a
        `settings_path=` keyword argument -- a regression guard against a
        future test in this file accidentally falling back to the real-
        file default."""
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(__file__))
        violations: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_target_name(node) not in _GUARDED_FUNCS:
                continue
            if not any(keyword.arg == "settings_path" for keyword in node.keywords):
                violations.append(node.lineno)
        assert not violations, (
            f"line(s) {violations} call install()/uninstall()/status() "
            "without an explicit settings_path= keyword argument"
        )


class TestEnforcementLayer3ExplicitAssertion:
    """A readable, explicit companion to the autouse tripwire fixture
    above -- same guarantee, but with a clear failure message tied
    directly to a named test rather than only a fixture teardown error."""

    def test_real_settings_json_hash_unchanged_across_this_module(self) -> None:
        """TC-59: re-hash the real settings.json (or re-confirm its
        absence) and compare against the value the module-scoped
        `_real_settings_untouched` fixture recorded at collection time.

        The failure message (chunk 9, routed-in from the chunk-8 review)
        names WHICH top-level keys / hook events differ -- the assertion
        itself stays exactly as strict a hash comparison as before; only
        the message widens, so a human reading it can usually tell "this
        module wrote to the file" apart from "another process on this
        laptop edited it mid-run" (both are still a hash mismatch, and
        this alone cannot distinguish them programmatically either -- see
        this test's docstring and the fixture's own)."""
        current = _hash_real_settings()
        assert current == _recorded_real_hash, (
            f"the REAL {_REAL_SETTINGS_PATH} changed during this test "
            f"module: was {_recorded_real_hash!r}, now {current!r}. Every "
            "test in test_hooks_install.py must operate on the "
            "temp_settings_json fixture's path only. Cannot distinguish "
            "that from another process editing the real file mid-run -- "
            f"recorded shape was [{_recorded_real_shape}]; current shape "
            f"is [{_settings_shape_summary(_REAL_SETTINGS_PATH)}]."
        )


class TestEditWriteGuardCompatibility:
    def test_installer_is_compatible_with_an_edit_write_guard_hook(self, temp_settings_json: Path) -> None:
        """TC-62 (A3): simulate secure-plugin-installer's PreToolUse
        `Edit|Write` guard against a well-formed installer write and
        assert it does not reject it.

        The guard only blocks a settings.json edit that ADDS a new,
        unapproved `enabledPlugins` slug; `hooks_install.install()` never
        touches `enabledPlugins`, so this is fully simulable rather than
        a `pytest.skip` gap -- build the guard's own PreToolUse payload
        shape for a `Write` of the post-install content, point its
        settings-path env override at the temp fixture, and assert exit 0
        (allow). Falls back to an explicit, reasoned `pytest.skip` only if
        the guard script is not present on this machine at all.
        """
        if not _EDIT_WRITE_GUARD_SCRIPT.is_file():
            pytest.skip(
                f"secure-plugin-installer's guard script is not present at "
                f"{_EDIT_WRITE_GUARD_SCRIPT} on this machine; cannot simulate "
                "its Edit|Write contract here (A3 integration-level gap, "
                "TEST.md TC-62 -- close at Phase 3.2/preflight instead)"
            )

        before_text = temp_settings_json.read_text(encoding="utf-8")
        hooks_install.install(settings_path=temp_settings_json, skill_root=Path("C:/example/skill-root"))
        after_text = temp_settings_json.read_text(encoding="utf-8")
        # The guard reads its "old" content from disk, so put the file
        # back to its pre-install state and let the guard's own diffing
        # (old on disk vs. new in tool_input) see the exact transition a
        # real Write tool call would have produced.
        temp_settings_json.write_text(before_text, encoding="utf-8")

        payload = json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(temp_settings_json), "content": after_text},
            }
        ).encode("utf-8")
        env = dict(os.environ)
        env["SECURE_PLUGIN_INSTALLER_SETTINGS"] = str(temp_settings_json)

        proc = subprocess.run(
            [sys.executable, str(_EDIT_WRITE_GUARD_SCRIPT)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=10,
        )

        assert proc.returncode == 0, (
            "the Edit|Write guard rejected a well-formed installer write: "
            f"exit={proc.returncode} stderr={proc.stderr.decode('utf-8', errors='replace')!r}"
        )
        temp_settings_json.write_text(after_text, encoding="utf-8")


class TestSkillRootResolution:
    def test_default_resolution_lands_outside_any_linked_worktree(self, temp_settings_json: Path) -> None:
        """TC-94 (roadmap#217): with no `--skill-root` override, every
        registered command's root resolves OUTSIDE any linked git
        worktree. `hooks_install.py` genuinely lives inside a linked
        worktree while this suite runs on this machine (the
        fleet-deterministic-seams branch) -- so `_default_skill_root()`
        resolving to the MAIN checkout is exercised for real here, not
        merely by construction."""
        result = hooks_install.install(settings_path=temp_settings_json)
        assert not hooks_install._is_linked_worktree(result.root)
        assert result.worktree_pinned is False

        status_result = hooks_install.status(settings_path=temp_settings_json)
        for entry in status_result.entries:
            assert entry.present
            assert not entry.worktree_pinned, f"{entry.hook_event}/{entry.matcher} resolved to a worktree"

    def test_refuses_when_resolved_root_is_a_linked_worktree(
        self, temp_settings_json: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-94, the failure-can-happen half: monkeypatch the git-based
        resolver to return a fixture directory shaped exactly like a
        linked worktree (a `.git` FILE, not a directory -- the same
        structural difference git itself uses) and assert install()
        refuses rather than silently registering a worktree path.
        Nothing is written."""
        fake_worktree = tmp_path / "fake-worktree"
        fake_worktree.mkdir()
        (fake_worktree / ".git").write_text(
            "gitdir: ../main-checkout/.git/worktrees/fake-worktree\n", encoding="utf-8"
        )
        monkeypatch.setattr(hooks_install, "_default_skill_root", lambda: fake_worktree)

        before_text = temp_settings_json.read_text(encoding="utf-8")
        with pytest.raises(hooks_install.SkillRootRefusedError) as excinfo:
            hooks_install.install(settings_path=temp_settings_json)

        assert str(fake_worktree) in str(excinfo.value)
        assert temp_settings_json.read_text(encoding="utf-8") == before_text


class TestOwnershipTightening:
    """Phase 3.3 preflight recommended-fix: R5's ownership test had a false
    NEGATIVE (a `.cmd` shim was never recognised as ours) and a false
    POSITIVE (any third party at the identical conventional layout was
    claimed as ours). TC-113/TC-114."""

    def test_cmd_shim_is_recognised_as_owned_and_migrated(self, temp_settings_json: Path) -> None:
        """TC-113: a pre-existing entry pointing at the `.cmd` form of one
        of our three wrapper basenames -- the most likely shape of a
        hand-install on Windows, per R2's own note that the `.cmd` shim is
        the fallback when the extensionless form is not executable -- is
        recognised as superhuman-owned and MIGRATED (replaced), not left
        beside a second, freshly-added entry as a duplicate."""
        before = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        startup_before = next(g for g in before["hooks"]["SessionStart"] if g["matcher"] == "startup")["hooks"]
        # Rewrite the fixture's pre-existing migration entry to the `.cmd`
        # form -- same root, same basename, `.cmd` suffix.
        for command in startup_before:
            if command["command"].endswith("/session-start"):
                command["command"] += ".cmd"
        temp_settings_json.write_text(json.dumps(before, indent=2) + "\n", encoding="utf-8")

        assert hooks_install._owned_basename(startup_before[-1]["command"]) == "session-start", (
            "a `.cmd`-suffixed command at our conventional layout must be recognised as owned"
        )

        hooks_install.install(settings_path=temp_settings_json)

        after = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        startup_after = next(g for g in after["hooks"]["SessionStart"] if g["matcher"] == "startup")["hooks"]
        owned_after = [
            c["command"]
            for c in startup_after
            if hooks_install._owned_basename(c["command"]) == "session-start"
        ]
        assert len(owned_after) == 1, (
            f"expected the `.cmd` entry to be migrated (replaced), not duplicated: {owned_after}"
        )

    def test_unrelated_tool_at_the_same_conventional_layout_is_not_claimed(
        self, temp_settings_json_clean: Path
    ) -> None:
        """TC-114: a THIRD-PARTY command that happens to sit at the exact
        same conventional layout (`.../templates/hooks/claude-code/
        session-start`) but under a root that is not a superhuman checkout
        at all (no `superhuman` path segment anywhere) must NOT be treated
        as ours -- install() must not replace it, and uninstall() must not
        remove it."""
        before = json.loads(temp_settings_json_clean.read_text(encoding="utf-8"))
        unrelated_command = "C:/tools/some-other-project/templates/hooks/claude-code/session-start"
        before["hooks"]["SessionStart"][0]["hooks"].append({"type": "command", "command": unrelated_command})
        temp_settings_json_clean.write_text(json.dumps(before, indent=2) + "\n", encoding="utf-8")

        assert hooks_install._owned_basename(unrelated_command) is None, (
            "a look-alike path with no `superhuman` segment must not be recognised as owned"
        )

        hooks_install.install(settings_path=temp_settings_json_clean)
        after_install = json.loads(temp_settings_json_clean.read_text(encoding="utf-8"))
        startup_after_install = next(
            g for g in after_install["hooks"]["SessionStart"] if g["matcher"] == "startup"
        )["hooks"]
        assert any(c["command"] == unrelated_command for c in startup_after_install), (
            "install() must leave the unrelated third-party entry untouched"
        )

        hooks_install.uninstall(settings_path=temp_settings_json_clean)
        after_uninstall = json.loads(temp_settings_json_clean.read_text(encoding="utf-8"))
        startup_after_uninstall = next(
            g for g in after_uninstall["hooks"]["SessionStart"] if g["matcher"] == "startup"
        )["hooks"]
        assert any(c["command"] == unrelated_command for c in startup_after_uninstall), (
            "uninstall() must not remove the unrelated third-party entry"
        )


class TestOwnershipQuotedAndWrappedSpellings:
    """TC-127 (Phase 3.3 preflight RE-RUN item C -- Major): `_OWNED_COMMAND_RE`
    was `$`-anchored on the basename, so any spelling that put ANYTHING
    after the basename in the command string -- a closing quote, trailing
    whitespace, or a wrapper prefix like `bash "..."` -- was classified
    FOREIGN. Consequence: `install()` duplicates instead of replacing, and
    `uninstall()` orphans the entry -- the rollback path this project's own
    rollback plan names. A quoted spelling is mandatory once the checkout
    path contains a space (this is a public repo, so that is not a
    hypothetical), so this is not a cosmetic edge case.

    Fixed by replacing the `$`-anchor with a negative lookahead for a
    continuing path/word character (`(?![\\w./-])`) -- the basename (plus
    optional `.cmd`) may be followed by a quote, whitespace, another shell
    token, or end-of-string, but NOT by more path characters. This still
    rejects a look-alike (TC-114's own `session-start-legacy`-shaped
    concern), verified below alongside the four required spellings.
    """

    @pytest.mark.parametrize(
        "wrap",
        [
            pytest.param(lambda cmd: f'"{cmd}"', id="quoted"),
            pytest.param(lambda cmd: f"{cmd} ", id="trailing_whitespace"),
            pytest.param(lambda cmd: f'bash "{cmd}"', id="bash_quoted_wrapper"),
        ],
    )
    def test_owned_basename_recognises_the_spelling(self, wrap) -> None:
        base = "C:/x/superhuman/templates/hooks/claude-code/session-start"
        assert hooks_install._owned_basename(wrap(base)) == "session-start"

    def test_owned_basename_recognises_a_quoted_cmd_shim(self) -> None:
        base = "C:/x/superhuman/templates/hooks/claude-code/session-start.cmd"
        assert hooks_install._owned_basename(f'"{base}"') == "session-start"

    def test_owned_basename_still_rejects_a_look_alike_suffix(self) -> None:
        """The lookahead must not weaken TC-114's false-positive guard: a
        command that merely STARTS WITH our basename but continues as a
        different path (an unrelated tool's own naming) is still foreign."""
        look_alike = "C:/x/superhuman/templates/hooks/claude-code/session-start-legacy"
        assert hooks_install._owned_basename(look_alike) is None

    def test_install_migrates_a_quoted_pre_existing_entry_instead_of_duplicating(
        self, temp_settings_json: Path
    ) -> None:
        """A quoted pre-existing entry -- the mandatory spelling once the
        checkout path contains a space -- is recognised as ours and
        MIGRATED (replaced) by `install()`, never left beside a second,
        freshly-added entry as a duplicate."""
        before = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        startup_before = next(g for g in before["hooks"]["SessionStart"] if g["matcher"] == "startup")["hooks"]
        for command in startup_before:
            if command["command"].endswith("/session-start"):
                command["command"] = f'"{command["command"]}"'
        temp_settings_json.write_text(json.dumps(before, indent=2) + "\n", encoding="utf-8")

        hooks_install.install(settings_path=temp_settings_json)

        after = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        startup_after = next(g for g in after["hooks"]["SessionStart"] if g["matcher"] == "startup")["hooks"]
        owned_after = [
            c["command"] for c in startup_after if hooks_install._owned_basename(c["command"]) == "session-start"
        ]
        assert len(owned_after) == 1, (
            f"expected the quoted entry to be migrated (replaced), not duplicated: {owned_after}"
        )

    def test_uninstall_removes_a_bash_wrapped_entry_instead_of_orphaning_it(
        self, tmp_path: Path
    ) -> None:
        """A `bash "..."`-wrapped entry -- the shape a POSIX operator might
        hand-install with -- is removed by `uninstall()`, never left
        behind orphaned (the rollback path this project's own rollback
        plan names)."""
        settings_path = tmp_path / "settings.json"
        seed = {
            "hooks": {
                "SubagentStart": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'bash "C:/x/superhuman/templates/hooks/claude-code/subagent-start"',
                            }
                        ],
                    }
                ]
            }
        }
        settings_path.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")

        result = hooks_install.uninstall(settings_path=settings_path)

        assert result.changed is True
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "hooks" not in data, "the bash-wrapped entry must be removed, not orphaned"


class TestSkillRootInsideGitDir:
    def test_refuses_when_resolved_root_is_inside_a_dot_git_directory(
        self, temp_settings_json: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-115 (Phase 3.3 preflight item 2): monkeypatch the git-based
        resolver to return a fixture directory shaped exactly like the
        submodule failure mode -- `<superproject>/.git/modules/<name>`,
        the value `git rev-parse --git-common-dir`'s `.parent` produces
        from inside a submodule (reproduced directly against a real git
        submodule during development: `git rev-parse --path-format=
        absolute --git-common-dir` from inside the submodule returned
        `<outer>/.git/modules/sub`) -- and assert install() refuses rather
        than silently registering a path inside git's own private
        directory. Nothing is written."""
        fake_root = tmp_path / "outer" / ".git" / "modules" / "sub"
        fake_root.mkdir(parents=True)
        monkeypatch.setattr(hooks_install, "_default_skill_root", lambda: fake_root)

        before_text = temp_settings_json.read_text(encoding="utf-8")
        with pytest.raises(hooks_install.SkillRootInsideGitDirError) as excinfo:
            hooks_install.install(settings_path=temp_settings_json)

        assert str(fake_root) in str(excinfo.value)
        assert temp_settings_json.read_text(encoding="utf-8") == before_text

    def test_not_confused_with_an_ordinary_main_checkout_root(self) -> None:
        """Companion sanity check: an ordinary main-checkout root (no
        `.git` path segment) must NOT be flagged by the new check."""
        assert hooks_install._is_inside_dot_git(Path("C:/example/skills/superhuman")) is False

    def test_flags_a_dot_git_modules_path(self) -> None:
        """`_is_inside_dot_git` on its own: the exact submodule shape."""
        assert hooks_install._is_inside_dot_git(Path("C:/example/outer/.git/modules/sub")) is True


class TestBomAndMalformedSettings:
    """Phase 3.3 preflight item 4: `hooks install` (and, fixed alongside
    it, `uninstall`/`status`) threw a raw traceback on a BOM-prefixed or
    malformed settings.json instead of a clean, file-naming error."""

    def test_bom_prefixed_valid_json_is_read_correctly(self, tmp_path: Path) -> None:
        """TC-117: a settings.json carrying a leading UTF-8 BOM, but
        otherwise valid JSON, is read correctly -- install() must not
        raise, and the pre-existing foreign entries must survive. The BOM
        is NOT preserved on write (stated in `_read_settings_text`'s
        docstring): this installer has never emitted one, so a
        BOM-prefixed file this installer writes to comes back out without
        it."""
        path = tmp_path / "settings.json"
        body = json.dumps(
            {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "foreign"}]}]}},
            indent=2,
        ) + "\n"
        path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

        result = hooks_install.install(settings_path=path)
        assert result.changed is True

        after_bytes = path.read_bytes()
        assert not after_bytes.startswith(b"\xef\xbb\xbf"), "the BOM must not be preserved on write"
        after = json.loads(after_bytes.decode("utf-8"))
        foreign = [
            c["command"]
            for g in after["hooks"]["SessionStart"]
            for c in g["hooks"]
            if hooks_install._owned_basename(c["command"]) is None
        ]
        assert foreign == ["foreign"], "the BOM-prefixed file's pre-existing foreign entry must survive"

    def test_malformed_json_raises_named_error_and_writes_nothing(self, tmp_path: Path) -> None:
        """TC-118: a settings.json that is not valid JSON at all raises
        `HooksInstallError` naming the file (never a raw
        `json.JSONDecodeError` traceback), and writes NOTHING -- the file
        is byte-identical after the failed call."""
        path = tmp_path / "settings.json"
        before_bytes = b"{ this is not valid json "
        path.write_bytes(before_bytes)

        with pytest.raises(hooks_install.HooksInstallError) as excinfo:
            hooks_install.install(settings_path=path)
        assert str(path) in str(excinfo.value)
        assert path.read_bytes() == before_bytes

    def test_cli_hooks_install_reports_one_line_error_and_exits_nonzero_on_malformed_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """TC-118 (CLI-level): `fleet hooks install` against a malformed
        settings.json prints ONE line naming the file to stderr and exits
        non-zero -- never a raw traceback."""
        path = tmp_path / "settings.json"
        before_bytes = b"not json at all"
        path.write_bytes(before_bytes)

        exit_code = cli.main(["hooks", "install", "--harness", "claude-code", "--settings-path", str(path)])

        assert exit_code == 1
        captured = capsys.readouterr()
        stderr_lines = [ln for ln in captured.err.splitlines() if ln.strip()]
        assert len(stderr_lines) == 1, f"expected exactly one stderr line, got: {stderr_lines!r}"
        assert str(path) in stderr_lines[0]
        assert path.read_bytes() == before_bytes

    def test_cli_hooks_uninstall_and_status_also_report_cleanly_on_malformed_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Companion coverage: `uninstall`/`status` share the same parsing
        path and must not raw-traceback either."""
        path = tmp_path / "settings.json"
        path.write_bytes(b"not json at all")

        uninstall_code = cli.main(["hooks", "uninstall", "--harness", "claude-code", "--settings-path", str(path)])
        assert uninstall_code == 1

        status_code = cli.main(["hooks", "status", "--harness", "claude-code", "--settings-path", str(path)])
        assert status_code == 1


#: TC-126 (Phase 3.3 preflight RE-RUN item B -- Major): item 4 above
#: hardened SYNTAX (malformed JSON) and a BOM, but not SHAPE --
#: structurally valid JSON whose `"hooks"` sub-tree does not match what
#: install()/uninstall()/status() assume. These three shapes have `"hooks"`
#: itself not a dict -- a container `_add_owned` cannot safely write
#: entries INTO without clobbering whatever the operator's `"hooks"`
#: actually held, so `install()` refuses outright (a clean
#: `HooksInstallError`, never a write); `uninstall()`/`status()` are
#: read-only and degrade to a no-op / "nothing installed" instead.
_MALFORMED_TOP_LEVEL_HOOKS_SHAPES: dict[str, Any] = {
    "hooks_is_empty_list": [],
    "hooks_is_null": None,
    "hooks_is_not_a_dict": "not-a-dict",
}

#: These three keep `"hooks"` itself a dict, but corrupt something nested
#: inside it -- a shape that IS gracefully handleable (the content is
#: simply foreign/not-ours), so all three verbs complete normally rather
#: than refusing.
_MALFORMED_NESTED_HOOKS_SHAPES: dict[str, Any] = {
    "event_value_is_list_of_strings": {"SomeEvent": ["not", "a", "group"]},
    "group_hooks_is_null": {"SomeEvent": [{"matcher": "*", "hooks": None}]},
    "command_entry_is_bare_string": {"SomeEvent": [{"matcher": "*", "hooks": ["bare-string-command"]}]},
}


class TestMalformedHooksShapesNeverCrash:
    """TC-126: both `install()` and `uninstall()` crashed with a raw
    `AttributeError`/`TypeError` on some of these shapes -- and
    `uninstall()` is the rollback path this project's own rollback plan
    names. Table-drives all six reported shapes through all three verbs."""

    @pytest.mark.parametrize("shape_name,hooks_value", sorted(_MALFORMED_TOP_LEVEL_HOOKS_SHAPES.items()))
    def test_install_refuses_and_writes_nothing(
        self, tmp_path: Path, shape_name: str, hooks_value: Any
    ) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"hooks": hooks_value}, indent=2) + "\n", encoding="utf-8")
        before_bytes = settings_path.read_bytes()

        with pytest.raises(hooks_install.HooksInstallError) as excinfo:
            hooks_install.install(settings_path=settings_path)
        assert str(settings_path) in str(excinfo.value), f"error must name the file ({shape_name})"
        assert settings_path.read_bytes() == before_bytes, f"a refused install must never write ({shape_name})"

    @pytest.mark.parametrize("shape_name,hooks_value", sorted(_MALFORMED_TOP_LEVEL_HOOKS_SHAPES.items()))
    def test_uninstall_never_crashes(self, tmp_path: Path, shape_name: str, hooks_value: Any) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"hooks": hooks_value}, indent=2) + "\n", encoding="utf-8")

        hooks_install.uninstall(settings_path=settings_path)  # must not raise

    @pytest.mark.parametrize("shape_name,hooks_value", sorted(_MALFORMED_TOP_LEVEL_HOOKS_SHAPES.items()))
    def test_status_never_crashes(self, tmp_path: Path, shape_name: str, hooks_value: Any) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"hooks": hooks_value}, indent=2) + "\n", encoding="utf-8")

        result = hooks_install.status(settings_path=settings_path)  # must not raise
        assert result.installed is False, f"a malformed hooks tree can never report as installed ({shape_name})"

    @pytest.mark.parametrize("shape_name,hooks_value", sorted(_MALFORMED_NESTED_HOOKS_SHAPES.items()))
    def test_install_never_crashes_on_a_malformed_nested_shape(
        self, tmp_path: Path, shape_name: str, hooks_value: Any
    ) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"hooks": hooks_value}, indent=2) + "\n", encoding="utf-8")

        hooks_install.install(settings_path=settings_path)  # must not raise

    @pytest.mark.parametrize("shape_name,hooks_value", sorted(_MALFORMED_NESTED_HOOKS_SHAPES.items()))
    def test_uninstall_never_crashes_on_a_malformed_nested_shape(
        self, tmp_path: Path, shape_name: str, hooks_value: Any
    ) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"hooks": hooks_value}, indent=2) + "\n", encoding="utf-8")

        hooks_install.uninstall(settings_path=settings_path)  # must not raise

    @pytest.mark.parametrize("shape_name,hooks_value", sorted(_MALFORMED_NESTED_HOOKS_SHAPES.items()))
    def test_status_never_crashes_on_a_malformed_nested_shape(
        self, tmp_path: Path, shape_name: str, hooks_value: Any
    ) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"hooks": hooks_value}, indent=2) + "\n", encoding="utf-8")

        result = hooks_install.status(settings_path=settings_path)  # must not raise
        assert result.installed is False, f"a malformed hooks tree can never report as installed ({shape_name})"


class TestMigration:
    def test_migrates_worktree_rooted_hand_install_to_exactly_one_entry(self, temp_settings_json: Path) -> None:
        """TC-95: the seeded `startup` group already carries ONE
        superhuman-owned entry pointing at a worktree (the real 2026-09-09
        hand install's shape). After install(), exactly ONE superhuman
        `startup` command remains, and it points at the resolved
        (main-checkout) root -- not two entries, and not the stale
        worktree one."""
        before = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        startup_before = next(g for g in before["hooks"]["SessionStart"] if g["matcher"] == "startup")["hooks"]
        owned_before = [
            c["command"] for c in startup_before if hooks_install._owned_basename(c["command"]) == "session-start"
        ]
        assert len(owned_before) == 1, "fixture must seed exactly one pre-existing owned startup entry"

        result = hooks_install.install(settings_path=temp_settings_json)

        after = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        startup_after = next(g for g in after["hooks"]["SessionStart"] if g["matcher"] == "startup")["hooks"]
        owned_after = [
            c["command"] for c in startup_after if hooks_install._owned_basename(c["command"]) == "session-start"
        ]

        assert len(owned_after) == 1
        assert owned_after[0] != owned_before[0]
        assert owned_after[0] == hooks_install._command_for(result.root, "session-start")


class TestSessionStartMatchers:
    def test_all_five_matchers_registered_and_fork_created(self, temp_settings_json: Path) -> None:
        """TC-96: install() registers all five FR-19 SessionStart
        matchers, creating the absent `fork` group, and the four
        pre-existing groups' FOREIGN commands survive byte-identical."""
        before = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        foreign_before = {
            g["matcher"]: sorted(
                c["command"] for c in g["hooks"] if hooks_install._owned_basename(c["command"]) is None
            )
            for g in before["hooks"]["SessionStart"]
        }

        hooks_install.install(settings_path=temp_settings_json)

        after = json.loads(temp_settings_json.read_text(encoding="utf-8"))
        matchers_after = {g["matcher"] for g in after["hooks"]["SessionStart"]}
        assert matchers_after == {"startup", "resume", "clear", "compact", "fork"}

        for group in after["hooks"]["SessionStart"]:
            foreign_after = sorted(
                c["command"] for c in group["hooks"] if hooks_install._owned_basename(c["command"]) is None
            )
            assert foreign_after == foreign_before.get(group["matcher"], []), group["matcher"]


class TestDryRun:
    def test_dry_run_prints_diff_and_writes_nothing(self, temp_settings_json: Path) -> None:
        """TC-97: `--dry-run` (here, `dry_run=True`) computes and returns
        the diff but writes nothing -- the file's sha256 is unchanged."""
        before_hash = hashlib.sha256(temp_settings_json.read_bytes()).hexdigest()

        result = hooks_install.install(settings_path=temp_settings_json, dry_run=True)

        after_hash = hashlib.sha256(temp_settings_json.read_bytes()).hexdigest()
        assert after_hash == before_hash
        assert result.changed is True
        assert result.diff != ""


class TestAtomicWrite:
    def test_interrupted_replace_leaves_original_file_intact(
        self, temp_settings_json: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-98: the write goes through a temp file in the same
        directory, then `os.replace()`. Simulate an interruption AT the
        replace step and assert the original file is untouched (never
        observed half-written), with no orphaned temp file left behind
        either."""
        before_text = temp_settings_json.read_text(encoding="utf-8")

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated interruption before replace")

        monkeypatch.setattr(hooks_install.os, "replace", _boom)

        with pytest.raises(OSError):
            hooks_install.install(settings_path=temp_settings_json)

        assert temp_settings_json.read_text(encoding="utf-8") == before_text
        leftover = list(temp_settings_json.parent.glob(f".{temp_settings_json.name}.*.tmp"))
        assert leftover == [], f"orphaned temp file(s) left behind: {leftover}"

    def test_non_os_error_during_write_still_cleans_up_the_temp_file(
        self, temp_settings_json: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TC-120 (Phase 3.3 preflight item 6, orphan half): the old code's
        cleanup was `except OSError: tmp_path.unlink(...); raise`, which
        only fires for `OSError`. A NON-`OSError` failure partway through
        the write (simulated here at `os.fsync`, after the temp file
        already exists on disk) must still leave no orphaned temp file --
        proving cleanup no longer depends on the failure being an
        `OSError`."""
        before_text = temp_settings_json.read_text(encoding="utf-8")

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise ValueError("simulated non-OSError failure mid-write")

        monkeypatch.setattr(hooks_install.os, "fsync", _boom)

        with pytest.raises(ValueError):
            hooks_install.install(settings_path=temp_settings_json)

        assert temp_settings_json.read_text(encoding="utf-8") == before_text
        leftover = list(temp_settings_json.parent.glob(f".{temp_settings_json.name}.*.tmp"))
        assert leftover == [], f"orphaned temp file(s) left behind: {leftover}"

    @pytest.mark.skipif(
        os.name == "nt",
        reason="POSIX file-permission bits (item 6's other half) are not meaningfully "
        "testable via os.chmod/stat over an NTFS working copy on Windows",
    )
    def test_original_file_permissions_are_preserved_across_a_write(
        self, temp_settings_json_clean: Path
    ) -> None:
        """TC-121 (Phase 3.3 preflight item 6, permissions half, POSIX
        only): `tempfile.mkstemp` creates its file `0600`, and
        `os.replace` carries the REPLACING file's mode over the replaced
        file's -- so writing through an untouched temp file silently
        tightens a pre-existing, more permissive settings.json (e.g.
        `0644`) down to `0600` on every install. The original file's mode
        must survive the write unchanged."""
        os.chmod(temp_settings_json_clean, 0o644)

        hooks_install.install(settings_path=temp_settings_json_clean)

        mode_after = stat.S_IMODE(temp_settings_json_clean.stat().st_mode)
        assert mode_after == 0o644, f"expected mode 0o644 preserved, got {oct(mode_after)}"


class TestGitResolutionFaults:
    """Branch coverage for `_default_skill_root`'s two failure paths --
    neither is exercised by the happy-path tests above, since this
    machine's real git invocation always succeeds."""

    def test_raises_when_git_invocation_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`subprocess.run` raising (git missing, not a repo, or a hang
        past the timeout) becomes a `HooksInstallError`, not an unhandled
        exception."""

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(hooks_install.subprocess, "run", _boom)

        with pytest.raises(hooks_install.HooksInstallError):
            hooks_install._default_skill_root()

    def test_raises_when_git_common_dir_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty `--git-common-dir` result (git ran but said nothing
        useful) also becomes a `HooksInstallError`, not a crash on
        `Path("").parent`."""

        class _FakeCompleted:
            stdout = b""

        monkeypatch.setattr(hooks_install.subprocess, "run", lambda *a, **k: _FakeCompleted())

        with pytest.raises(hooks_install.HooksInstallError):
            hooks_install._default_skill_root()


class TestRemoveOwnedEdgeCases:
    """Direct unit coverage of `_remove_owned`'s two less-common branches,
    neither reachable through a realistic seeded settings.json."""

    def test_ignores_a_malformed_non_list_event_value(self) -> None:
        """A `hooks["SomeEvent"]` that is not a list (malformed/foreign
        shape this installer has no business interpreting) is left alone
        rather than raising."""
        hooks: dict[str, object] = {"SomeEvent": "not-a-list"}
        hooks_install._remove_owned(hooks)
        assert hooks == {"SomeEvent": "not-a-list"}

    def test_drops_an_event_left_with_zero_groups(self) -> None:
        """An event whose ONLY group consists entirely of owned commands
        is dropped ENTIRELY (not left behind as `"Event": []`) once that
        group is stripped -- the shape every realistic seeded fixture
        never exercises, since every real event always carries at least
        one foreign command too."""
        hooks = {
            "PreToolUse": [
                {
                    "matcher": "Agent|Task",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "C:/x/superhuman/templates/hooks/claude-code/pre-tool-use-role-gate",
                        }
                    ],
                }
            ]
        }
        hooks_install._remove_owned(hooks)
        assert hooks == {}

    def test_preserves_a_foreign_empty_hooks_list_and_a_foreign_keyless_group(self) -> None:
        """Preflight B2: a foreign group with an empty `"hooks": []` array,
        or with no `"hooks"` key at all, must survive untouched. The OLD
        `_remove_owned` kept a group only if it had a SURVIVING command
        afterward (`if remaining:`), so a foreign group with NOTHING of
        OURS to remove -- because `commands` was empty or absent -- was
        indistinguishable from a group THIS installer had just emptied,
        and was dropped either way. An event left with only such groups
        was then popped entirely too. This falsified the round-trip
        guarantee (TC-57) against exactly this shape: PM-reproduced by
        seeding a `PostToolUse` group with `"hooks": []` and a `Stop`
        group with no `hooks` key and observing both vanish.
        """
        hooks: dict[str, Any] = {
            "PostToolUse": [{"matcher": "Bash", "hooks": []}],
            "Stop": [{"matcher": "*"}],
        }
        original = copy.deepcopy(hooks)
        hooks_install._remove_owned(hooks)
        assert hooks == original


class TestUninstallEdgeCases:
    """Branch coverage for `uninstall()`'s early-return and no-op paths."""

    def test_uninstall_on_a_missing_file_is_a_no_op(self, tmp_path: Path) -> None:
        """`uninstall()` against a settings path that does not exist at
        all returns `changed=False` rather than raising."""
        missing = tmp_path / "does-not-exist.json"
        result = hooks_install.uninstall(settings_path=missing)
        assert result.changed is False
        assert result.diff == ""

    def test_uninstall_with_nothing_installed_changes_nothing(self, temp_settings_json_clean: Path) -> None:
        """`uninstall()` against a file with zero superhuman entries
        (never installed) reports `changed=False` and never calls the
        atomic-write path."""
        before_text = temp_settings_json_clean.read_text(encoding="utf-8")
        result = hooks_install.uninstall(settings_path=temp_settings_json_clean)
        assert result.changed is False
        assert temp_settings_json_clean.read_text(encoding="utf-8") == before_text

    def test_uninstall_drops_the_hooks_key_entirely_when_it_empties_out(self, tmp_path: Path) -> None:
        """When EVERY event under `"hooks"` consists solely of
        superhuman-owned entries, `uninstall()` removes the `"hooks"` key
        entirely rather than leaving `"hooks": {}` behind."""
        settings_path = tmp_path / "settings.json"
        seed = {
            "hooks": {
                "SubagentStart": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "C:/x/superhuman/templates/hooks/claude-code/subagent-start",
                            }
                        ],
                    }
                ]
            }
        }
        settings_path.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")

        result = hooks_install.uninstall(settings_path=settings_path)

        assert result.changed is True
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "hooks" not in data
