"""Tests for `scripts.fleet.hooks_install` — `fleet hooks install|uninstall|status`.

TDD scaffold only (Phase 2.1). Stubs with `pytest.mark.skip`; the Chunk 8
Developer implements each, TDD-first, per `TEST.md` TC-54..TC-62.

**HARD RULE, enforced by this module's own fixtures, not merely intended:
every test here operates on a TEMPORARY `settings.json` under `tmp_path`.
No test may write to the real `~/.claude/settings.json`.**

Enforcement layers (see TEST.md's Chunk 8 section for the full rationale):
  1. `hooks_install.install()` / `uninstall()` / `status()` must accept an
     explicit `settings_path: Path` parameter — every call in this module
     passes it, sourced from the `temp_settings_json` fixture below.
  2. `temp_settings_json` seeds a SYNTHETIC file (never real operator
     command strings) with the 5 SessionStart + 1 SubagentStart +
     3 PreToolUse shape the real file carries, so "refuses to touch
     unowned entries" is tested against a realistic foreign-entry count.
  3. `_real_settings_untouched` is an autouse, module-scoped tripwire:
     hashes the REAL settings.json (or confirms its absence) before this
     module's tests run, and asserts the hash is unchanged after every
     test and at teardown. This is independent of (1)/(2) — a mistake in
     either still fails loudly here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_REAL_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _hash_real_settings() -> str | None:
    """Return the SHA-256 hex digest of the real settings.json, or None if
    it does not exist.

    Returns:
        The digest string, or None when the file is absent.
    """
    if not _REAL_SETTINGS_PATH.is_file():
        return None
    return hashlib.sha256(_REAL_SETTINGS_PATH.read_bytes()).hexdigest()


@pytest.fixture(autouse=True, scope="module")
def _real_settings_untouched():  # type: ignore[no-untyped-def]
    """Module-scoped tripwire (enforcement layer 3, see module docstring).

    Records the real settings.json's hash (or its absence) before any test
    in this module runs, and asserts it is unchanged after the whole
    module completes. TDD scaffold note: this fixture itself is real and
    active immediately (not skipped) — it is cheap, self-contained, and
    should be implemented in the SAME PR as the first real test in this
    file, ahead of the individual TC-54..TC-62 stubs below.

    Yields:
        None.
    """
    before = _hash_real_settings()
    yield
    after = _hash_real_settings()
    assert after == before, (
        "tests/fleet/test_hooks_install.py touched the REAL "
        f"{_REAL_SETTINGS_PATH} — every test in this module must operate "
        "on the temp_settings_json fixture's path only."
    )


@pytest.fixture
def temp_settings_json(tmp_path: Path) -> Path:
    """A synthetic settings.json seeded with foreign hook entries.

    Shape-matches the real file's 5 SessionStart + 1 SubagentStart +
    3 PreToolUse entries WITHOUT reusing any real operator command
    string, so 'refuses to touch entries it does not own' is exercised
    against a realistic foreign-entry count.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        Path to the synthetic settings.json file.
    """
    raise NotImplementedError(
        "TDD scaffold - Chunk 8: seed a synthetic settings.json here with "
        "5 SessionStart + 1 SubagentStart + 3 PreToolUse foreign entries "
        "using placeholder command strings, then write it under tmp_path "
        "and return its Path."
    )


class TestInstall:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-54")
    def test_installer_adds_entries_to_temp_settings_only(
        self, temp_settings_json: Path
    ) -> None:
        """`install(settings_path=temp_settings_json)` adds exactly the
        superhuman `SessionStart` and `SubagentStart` entries to the temp
        file; the real settings.json is never opened (see the module-level
        tripwire fixture)."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-55")
    def test_installer_idempotent_on_rerun(self, temp_settings_json: Path) -> None:
        """Running `install()` twice against the same temp file produces
        the same content — no duplicate entries."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-56")
    def test_installer_refuses_to_touch_unowned_entries(
        self, temp_settings_json: Path
    ) -> None:
        """All 9 seeded foreign entries (5 SessionStart + 1 SubagentStart +
        3 PreToolUse) survive install() byte-identical; only the 2 new
        superhuman entries are added."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-60")
    def test_installer_writes_timeout_10_backstop(
        self, temp_settings_json: Path
    ) -> None:
        """Every entry the installer adds carries `"timeout": 10` — the
        NFR-2 latency backstop for the case a hook wedges."""


class TestUninstall:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-57")
    def test_uninstall_restores_prior_state_exactly(
        self, temp_settings_json: Path
    ) -> None:
        """Byte-diff of the temp file's content before install() vs. after
        install() then uninstall() is empty — an exact round trip."""


class TestStatus:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-61")
    def test_status_reports_not_installed_before_install(
        self, temp_settings_json: Path
    ) -> None:
        """`status(settings_path=...)` against a freshly-seeded temp file
        (no superhuman entries yet) reports 'not installed'."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-61")
    def test_status_reports_installed_after_install(
        self, temp_settings_json: Path
    ) -> None:
        """`status()` after `install()` reports 'installed'."""


class TestEnforcementLayer1SettingsPathIsAlwaysExplicit:
    """Enforcement layer 1 from the module docstring, expressed as a
    static self-check over this file's own source."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-58")
    def test_no_call_in_this_module_omits_settings_path(self) -> None:
        """Parse this test module's own source text (via `inspect` /
        `ast`) and assert every call site to `install(`, `uninstall(`, or
        `status(` includes a `settings_path=` keyword argument — a
        regression guard against a future test in this file accidentally
        falling back to the real-file default."""


class TestEnforcementLayer3ExplicitAssertion:
    """A readable, explicit companion to the autouse tripwire fixture
    above — same guarantee, but with a clear failure message tied
    directly to a named test rather than only a fixture teardown error."""

    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-59")
    def test_real_settings_json_hash_unchanged_across_this_module(self) -> None:
        """Re-hash the real settings.json (or re-confirm its absence) and
        compare against the value the module-scoped `_real_settings_untouched`
        fixture recorded at collection time."""


class TestEditWriteGuardCompatibility:
    @pytest.mark.skip(reason="TDD scaffold - Chunk 8, TC-62")
    def test_installer_is_compatible_with_an_edit_write_guard_hook(
        self, temp_settings_json: Path
    ) -> None:
        """Where feasible, simulate `secure-plugin-installer`'s
        `PreToolUse` `Edit|Write` guard contract (A3) against a
        well-formed installer write and assert it is not rejected. If the
        guard's exact contract cannot be simulated deterministically in
        this suite, this test documents that as an integration-level gap
        to close at Phase 3.2/preflight, via `pytest.skip` with a reason
        naming the gap — not a silent pass."""
