"""Shared fixtures for the fleet-manifest test suite.

Puts the skill root on ``sys.path`` so ``scripts.fleet.core...`` imports
resolve under a bare ``pytest`` invocation (not just ``python -m pytest``,
which adds the cwd itself). ``scripts/`` has no ``__init__.py`` — it stays an
implicit namespace package, matching the existing flat-module convention used
by ``scripts/superhuman_profile.py`` — but ``scripts/fleet/`` and
``scripts/fleet/core/`` are regular packages, so ``import scripts.fleet.core.schema``
works once the skill root is importable.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402

from scripts.fleet import hooks_install as _hooks_install_module  # noqa: E402

#: TC-124c (D4 clause 3, PM review 2026-09-19): the runtime companion to
#: TC-124a/b's static AST scan. An AST scan can always be dodged -- the PM
#: proved it by planting `from scripts.fleet.hooks_install import install
#: as do_it` then `do_it(dry_run=True)`, which TC-124a's ORIGINAL name-
#: matching missed (fixed separately, in the same commit, by resolving
#: import aliases) -- but a deeper alias (a further reassignment, a
#: decorator, a partial) can always be invented. This fixture instead
#: patches the installer's own LOWEST I/O seams, `_read_settings_text`
#: (every read) and `_atomic_write` (every write): `install()`,
#: `uninstall()`, and `status()` all call these by their own bare,
#: module-global name internally, regardless of what name or alias an
#: EXTERNAL caller used to reach the public function, so patching them
#: here catches every call shape, including ones no static scan could
#: ever enumerate.
_REAL_SETTINGS_PATH_CANDIDATES = frozenset(
    {
        _hooks_install_module.DEFAULT_SETTINGS_PATH.resolve(),
        (Path.home() / ".claude" / "settings.json").resolve(),
    }
)


@pytest.fixture(autouse=True)
def _forbid_real_settings_file_io(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail loudly if any test under `tests/fleet/` causes
    `scripts.fleet.hooks_install` to read or write the REAL settings file.

    Every legitimate test passes an explicit temporary `settings_path`
    (TC-58, TC-124a/b), so this should never fire on the existing suite --
    confirmed by a full-suite run in the same chunk that added it. It
    exists for the test that TRIES to omit the path and slips past the
    static scan some other way.

    Args:
        monkeypatch: pytest's monkeypatch fixture; patches auto-revert at
            the end of each test, so no explicit teardown is needed here.

    Yields:
        None. The patching happens as a side effect before the test body
        runs; nothing is returned for the test to use.
    """
    original_read = _hooks_install_module._read_settings_text
    original_write = _hooks_install_module._atomic_write

    def _guarded_read(settings_path: Path) -> str:
        if settings_path.resolve() in _REAL_SETTINGS_PATH_CANDIDATES:
            pytest.fail(
                f"a test caused scripts.fleet.hooks_install to READ the "
                f"REAL settings file at {settings_path} -- every call to "
                "install()/uninstall()/status(), under whatever name, "
                "must pass an explicit temporary settings_path (D4 "
                "clause 3)"
            )
        return original_read(settings_path)

    def _guarded_write(settings_path: Path, text: str) -> None:
        if settings_path.resolve() in _REAL_SETTINGS_PATH_CANDIDATES:
            pytest.fail(
                f"a test caused scripts.fleet.hooks_install to WRITE the "
                f"REAL settings file at {settings_path} -- every call to "
                "install()/uninstall(), under whatever name, must pass "
                "an explicit temporary settings_path (D4 clause 3)"
            )
        return original_write(settings_path, text)

    monkeypatch.setattr(_hooks_install_module, "_read_settings_text", _guarded_read)
    monkeypatch.setattr(_hooks_install_module, "_atomic_write", _guarded_write)
    yield
