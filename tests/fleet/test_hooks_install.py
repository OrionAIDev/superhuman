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
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.fleet import hooks_install

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
                            "command": "C:/x/templates/hooks/claude-code/pre-tool-use-role-gate",
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
                                "command": "C:/x/templates/hooks/claude-code/subagent-start",
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
