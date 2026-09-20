"""TC-125 (Phase 3.3 preflight RE-RUN item A -- Critical, B1 NOT closed):
the three bash hook wrappers under `templates/hooks/claude-code/` stopped
honouring `$SUPERHUMAN_ROOT` (`test_hook_wrapper_root_trust.py`, B1), but
every one of them still invokes a bare `"$PYTHON"` with the CALLER's
environment intact -- so an inherited `PYTHONPATH` lets attacker-planted
code get imported into the hook process regardless of where the real
skill checkout lives.

**PM-reproduced finding:** a `yaml.py` planted on `PYTHONPATH` got
imported and printed into `session-start`'s own stdout, which becomes
session context (model-visible). This module reproduces the same class of
defect with a module every one of these wrappers imports UNCONDITIONALLY
at the very top of the target script -- `argparse` (`scripts/fleet/cli.py`,
`pre_tool_use_role_gate.py`, and `subagent_dispatch_filter.py` all `import
argparse` as their first executable statement) -- so the exploit fires
regardless of hook-payload shape or of which project-local files (a
profile, a role file) happen to exist on this machine; the payload sent is
just `{}`.

Python's own sys.path construction puts `PYTHONPATH` entries BEFORE the
standard library's own directory
(https://docs.python.org/3/using/cmdline.html#envvar-PYTHONPATH, checked
2026-09-19: "the default search path is augmented... Augment the default
search path for module files. ... it is inserted in the search path in
front of the compiled-in default"), so a `PYTHONPATH`-planted `argparse.py`
shadows the real standard-library module.

Each wrapper's stdout is left deliberately unredirected (only stderr is
discarded, per every wrapper's own comment), so a planted module's stdout
output reaching the harness is not just arbitrary code execution -- it is
a direct prompt-injection channel exactly like B1's `SUPERHUMAN_ROOT`
finding. This module asserts BOTH halves: no side effect (the marker file
must never be written) and clean stdout (the poison line must never
appear) -- across all three wrappers, per hook stdin as RAW BYTES (never
`text=True`), matching this project's hook-stdin testing convention
(`test_hooks.py`, `test_role_gate_hook.py`, `test_hook_wrapper_root_trust.py`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_GIT_BASH_PATH = r"C:\Program Files\Git\bin\bash.exe"


def _find_bash() -> "str | None":
    """Return a bash executable that accepts native (drive-letter) paths.

    Mirrors `test_hooks.py`'s/`test_role_gate_hook.py`'s/
    `test_hook_wrapper_root_trust.py`'s identical `_find_bash` precedent
    exactly.
    """
    if sys.platform == "win32":
        return _GIT_BASH_PATH if Path(_GIT_BASH_PATH).is_file() else None
    return shutil.which("bash")


_BASH = _find_bash()

#: Every wrapper under test. `pre-tool-use-role-gate` and
#: `subagent-start`'s FIRST python invocation (`subagent_dispatch_filter.py`)
#: both `import argparse` as their first statement, before any payload
#: parsing; `session-start` reaches it one level down, via `-m
#: scripts.fleet.cli`, whose own top of file also `import argparse`s
#: unconditionally. `subagent-start`'s SECOND invocation (`-m
#: scripts.fleet.cli observe dispatch`) shares the identical fix in the
#: same source line range as the first -- proving the first is poisoned is
#: enough to prove the environment is trusted at all, matching
#: `test_hook_wrapper_root_trust.py`'s identical reasoning for its own
#: two-invocation case.
_WRAPPER_NAMES = ("session-start", "subagent-start", "pre-tool-use-role-gate")

#: Planted "malicious" module: writes a marker file the instant it is
#: imported, and prints a distinctive line to stdout -- proving both the
#: side effect (arbitrary code execution) and the prompt-injection channel
#: (unredirected stdout) in one shot. No attempt is made to keep working
#: as a real `argparse` afterward: these wrappers are fail-soft by
#: construction (every one traps to `exit 0` on any fault), so a
#: subsequent `AttributeError` inside the poisoned module does not affect
#: this test's assertions -- the marker write and the stdout print both
#: already happened before that point.
_MARKER_SCRIPT = (
    "import os, pathlib\n"
    "pathlib.Path(os.environ['EVIL_MARKER_FILE']).write_text('planted', encoding='utf-8')\n"
    "print('PYTHONPATH-POISON-MARKER')\n"
)


def _plant_evil_pythonpath_dir(base: Path) -> Path:
    """Build a directory holding a marker-writing `argparse.py`.

    Args:
        base: directory to build the poisoned PYTHONPATH entry under.

    Returns:
        Path: the directory to put on `PYTHONPATH` (contains `argparse.py`
        directly, matching how a real `PYTHONPATH` entry names a directory
        of top-level importable modules).
    """
    evil_dir = base / "evil-pythonpath"
    evil_dir.mkdir(parents=True, exist_ok=True)
    (evil_dir / "argparse.py").write_text(_MARKER_SCRIPT, encoding="utf-8")
    return evil_dir


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestWrapperIgnoresPoisonedPythonpath:
    """Item A: an inherited `PYTHONPATH` must never let attacker-planted
    code run inside a hook process. Confirmed by mutation the other
    direction too: temporarily dropping this chunk's `-E -s` flags
    reproduces both the marker file and the stdout line for every wrapper
    below; restoring the flags makes all three pass again.
    """

    @pytest.mark.parametrize("wrapper_name", _WRAPPER_NAMES)
    def test_no_side_effect_and_clean_stdout(
        self,
        skill_root: Path,
        tmp_path: Path,
        wrapper_name: str,
    ) -> None:
        evil_pythonpath = _plant_evil_pythonpath_dir(tmp_path)
        marker_file = tmp_path / "marker.txt"
        assert not marker_file.exists()

        env = os.environ.copy()
        env["PYTHONPATH"] = str(evil_pythonpath)
        env["EVIL_MARKER_FILE"] = str(marker_file)

        script = skill_root / "templates" / "hooks" / "claude-code" / wrapper_name
        result = subprocess.run(
            [_BASH, str(script)],
            input=b"{}",  # raw bytes on stdin -- never text=True (hook stdin convention)
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            timeout=15.0,
        )

        assert result.returncode == 0, (
            f"{wrapper_name} exited {result.returncode} (stderr={result.stderr!r})"
        )
        assert not marker_file.exists(), (
            f"{wrapper_name} honoured an inherited PYTHONPATH ({evil_pythonpath}) -- "
            "a planted argparse.py ran instead of the standard library's"
        )
        assert b"PYTHONPATH-POISON-MARKER" not in result.stdout, (
            f"{wrapper_name}'s stdout carried the poisoned module's output -- a direct "
            f"prompt-injection channel (stdout={result.stdout!r})"
        )
