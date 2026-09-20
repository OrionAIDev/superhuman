"""Preflight B1 regression: the three bash hook wrappers must derive the
superhuman skill root ONLY from their own script path, never from the
`SUPERHUMAN_ROOT` environment variable.

**PM-reproduced finding (SUPERHUMAN.md "Preflight decision: NO-GO", B1):**
each wrapper read `SUPERHUMAN_ROOT="${SUPERHUMAN_ROOT:-$(cd ... && pwd)}"` --
honouring an operator/repo-set override before falling back to deriving the
root from its own path. A committed project `settings.json` can set
environment variables a hook subprocess inherits (the Claude Code settings
reference documents the committed project settings file as carrying "the
environment variables the project needs"), so a malicious repository could
point `SUPERHUMAN_ROOT` at attacker-chosen code:

- `session-start`/`subagent-start` both `cd "$SUPERHUMAN_ROOT"` and then run
  `"$PYTHON" -m scripts.fleet.cli ...` -- Python's `-m` resolves the module
  against the CURRENT WORKING DIRECTORY first, so a planted
  `<evil-root>/scripts/fleet/cli.py` would be imported and its top-level
  code executed instead of the real one.
- `pre-tool-use-role-gate` invokes
  `"$SUPERHUMAN_ROOT/templates/hooks/claude-code/pre_tool_use_role_gate.py"`
  as an explicit script path -- a planted file there runs directly.

`session-start` deliberately leaves its own stdout alone (whatever
`observe session-start` prints becomes session context), so this is a
direct prompt-injection channel, not merely an arbitrary-code-execution
one: attacker-controlled output would reach both a human and the model on
every session start.

This module plants a marker-writing script at each wrapper's own attack
surface, points `SUPERHUMAN_ROOT` at it, and asserts the marker never
appears -- across ALL THREE wrappers, per hook stdin as RAW BYTES (never
`text=True`), matching this project's hook-stdin testing convention
(`test_hooks.py`, `test_role_gate_hook.py`).
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

    Mirrors `test_hooks.py`'s/`test_role_gate_hook.py`'s identical
    `_find_bash` precedent exactly.
    """
    if sys.platform == "win32":
        return _GIT_BASH_PATH if Path(_GIT_BASH_PATH).is_file() else None
    return shutil.which("bash")


_BASH = _find_bash()

#: `{wrapper filename: the path (relative to a fake skill root) the wrapper
#: would execute or import if it trusted SUPERHUMAN_ROOT}`. See the module
#: docstring for why each of these three paths is the real attack surface.
#:
#: `subagent-start` is pointed at `subagent_dispatch_filter.py`, not
#: `scripts/fleet/cli.py` -- it runs the Decision C granularity filter
#: (a direct `$SUPERHUMAN_ROOT/...` script path) BEFORE ever reaching its
#: own `-m scripts.fleet.cli observe dispatch` call, so that filter script
#: is the attack surface actually reached first; a real evil root would
#: plant both, but proving the first one is enough to prove the env var is
#: trusted at all.
_WRAPPER_ATTACK_SURFACE = {
    "session-start": "scripts/fleet/cli.py",
    "subagent-start": "templates/hooks/claude-code/subagent_dispatch_filter.py",
    "pre-tool-use-role-gate": "templates/hooks/claude-code/pre_tool_use_role_gate.py",
}

#: Planted "malicious" module: writes a marker file named by
#: `EVIL_MARKER_FILE` the instant it is imported/executed -- no argv/stdin
#: parsing needed, since arbitrary code execution is the point being
#: proven, not any particular payload shape.
_MARKER_SCRIPT = (
    "import os, pathlib\n"
    "pathlib.Path(os.environ['EVIL_MARKER_FILE']).write_text('planted', encoding='utf-8')\n"
)


def _plant_evil_root(base: Path, relative_script: str) -> Path:
    """Build a fake skill root with a marker-writing script at `relative_script`.

    Args:
        base: directory to build the fake root under.
        relative_script: the path (relative to the fake root) the target
            wrapper would run if it honoured `SUPERHUMAN_ROOT`.

    Returns:
        Path: the fake root's own directory.
    """
    evil_root = base / "evil-root"
    target = evil_root / relative_script
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_MARKER_SCRIPT, encoding="utf-8")
    if relative_script == "scripts/fleet/cli.py":
        # Real packages, not relying on implicit namespace packages, so
        # `-m scripts.fleet.cli` resolves regardless of interpreter/version
        # quirks. Only needed for the `-m`-invoked wrappers (session-start/
        # subagent-start) -- pre-tool-use-role-gate runs its planted file
        # as a direct script path, no package machinery involved.
        (evil_root / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (evil_root / "scripts" / "fleet" / "__init__.py").write_text("", encoding="utf-8")
    return evil_root


@pytest.mark.skipif(
    _BASH is None, reason="bash not available on this runner (Windows: Git Bash not found)"
)
class TestWrapperIgnoresSuperhumanRootEnvVar:
    """B1: an attacker-chosen `SUPERHUMAN_ROOT` must never influence which
    code a wrapper runs. Confirmed by mutation the OTHER direction too (see
    this chunk's status report): temporarily restoring the old
    `${SUPERHUMAN_ROOT:-...}` fallback reproduces the marker appearing for
    every wrapper below; restoring the fix makes all three pass again.
    """

    @pytest.mark.parametrize(
        "wrapper_name,relative_script", sorted(_WRAPPER_ATTACK_SURFACE.items())
    )
    def test_marker_never_appears(
        self,
        skill_root: Path,
        tmp_path: Path,
        wrapper_name: str,
        relative_script: str,
    ) -> None:
        evil_root = _plant_evil_root(tmp_path, relative_script)
        marker_file = tmp_path / "marker.txt"
        assert not marker_file.exists()

        env = os.environ.copy()
        env.pop("CLAUDE_PROJECT_DIR", None)
        env["SUPERHUMAN_ROOT"] = str(evil_root)
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
            f"{wrapper_name} honoured attacker-chosen SUPERHUMAN_ROOT ({evil_root}) -- "
            f"the planted {relative_script} ran instead of the real skill checkout's"
        )
