"""TC-124 (DECISIONS.md D4 clause 3, reworded 2026-09-19 G6, part b): no
test anywhere under `tests/` can reach the REAL settings file.

`hooks_install.install()` / `uninstall()` / `status()` each declare
`settings_path: Path = DEFAULT_SETTINGS_PATH` -- the default is bound at
`def` time, so monkeypatching the module-level `DEFAULT_SETTINGS_PATH`
constant does NOT guard a call that omits the argument; the function
object already closed over the real value. `test_hooks_install.py`'s own
TC-58 (`TestEnforcementLayer1SettingsPathIsAlwaysExplicit`) already proves
this pattern works, but only over its OWN module's source -- a new test
file added anywhere else under `tests/` that calls `install()` /
`uninstall()` / `status()`, or drives the CLI's `hooks install|uninstall|
status` verbs, without an explicit path would not be caught by it.

This module is the tests/-wide superset: it parses every `tests/**/*.py`
file's AST (not `hooks_install.py`'s own tests only) and asserts, for
every candidate call:

  (a) a direct call to `install`/`uninstall`/`status` (by bare or
      attribute name -- `hooks_install.install(...)` or a bare
      `install(...)` after an `from ... import install`) supplies either
      a `settings_path=` keyword or at least one positional argument
      (the function's first parameter IS `settings_path`, so a
      positional call is equally explicit -- this module does not
      require the keyword spelling, only that a path was actually
      supplied, which is the property that matters); and

  (b) any call whose argument is a list literal shaped like a CLI argv
      (contains the consecutive string literals `"hooks"` then one of
      `"install"` / `"uninstall"` / `"status"`) also contains the string
      literal `"--settings-path"` somewhere in that same list -- this
      covers `cli.main(["hooks", "install", ...])` and an equivalent
      `subprocess.run([..., "hooks", "install", ...])` shape.

**Why this cannot be bypassed by the obvious call shapes.** Both checks
are pure static AST inspection of every file `tests/` ships -- they do
not import or execute the scanned files, so a test that is skipped,
xfailed, or never collected by the current pytest invocation is still
caught. Bare-name matching (rather than requiring a specific import
alias) means neither `from scripts.fleet.hooks_install import install`
nor `hooks_install.install` nor a locally-renamed import can dodge check
(a) -- the guard matches on the CALLED name, not on how it was imported.
Check (b) matches on the literal argv shape regardless of which function
receives it (`cli.main`, a `subprocess.run`/`subprocess.check_output`
call, or a test helper wrapping either), so a new call site does not need
to be enumerated here by name.

**Known, stated limitation.** A call site that builds its argument list
dynamically (e.g. `args = base_args + ["hooks", "install"]`, or passes a
variable rather than a literal to `install(...)`) is not resolvable by
static AST inspection and is NOT caught by either check -- this is the
same class of limitation `_call_target_name` in `test_hooks_install.py`
already accepts for TC-58, restated here because it now applies estate-
wide rather than to one file. Every call site in `tests/` today (verified
while writing this test) uses a literal list and an explicit keyword or
positional path, so this limitation is not presently exploited.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_DIR = _REPO_ROOT / "tests"

#: The installer's three verbs (`scripts/fleet/hooks_install.py`). Matched
#: by bare/attribute name only -- see the module docstring's "cannot be
#: bypassed" note.
_GUARDED_INSTALLER_FUNCS = frozenset({"install", "uninstall", "status"})

#: The CLI verbs the `hooks` subcommand group exposes for the same three
#: operations (`scripts/fleet/cli.py::_add_hooks_subparsers`).
_GUARDED_CLI_VERBS = frozenset({"install", "uninstall", "status"})


def _iter_test_files() -> list[Path]:
    """Every `.py` file under `tests/`, excluding `__pycache__`.

    Returns:
        A sorted list of paths, for deterministic failure-message ordering.
    """
    return sorted(
        path
        for path in _TESTS_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _call_target_name(node: ast.Call) -> str | None:
    """Return a `Call` node's called function's bare name.

    Mirrors `test_hooks_install.py`'s own `_call_target_name` helper
    (TC-58) -- duplicated rather than imported, so this guard keeps
    working even if that module's helper is ever renamed or removed.

    Args:
        node: an `ast.Call` node.

    Returns:
        The callee's simple name for an `ast.Attribute` (e.g.
        `hooks_install.install`) or `ast.Name` (e.g. `install`) call
        shape; `None` for any other call shape (e.g. a call through a
        subscript or a chained call).
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _string_constants(node: ast.AST) -> list[str]:
    """Return every literal string element of a `List`/`Tuple` node, in order.

    Non-string elements (e.g. a variable) are omitted, not errored on --
    see the module docstring's stated limitation.

    Args:
        node: an `ast.List` or `ast.Tuple` node.

    Returns:
        The literal string values of the node's constant string elements.
    """
    values: list[str] = []
    for element in getattr(node, "elts", []):
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.append(element.value)
    return values


class TestNoInstallerFunctionCallOmitsAnExplicitSettingsPath:
    """TC-124a: every direct call to `install`/`uninstall`/`status`
    anywhere under `tests/` supplies an explicit path (keyword or
    positional) -- the tests/-wide superset of TC-58."""

    def test_every_installer_call_in_tests_supplies_a_path(self) -> None:
        offenders: list[str] = []
        for path in _iter_test_files():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_target_name(node) not in _GUARDED_INSTALLER_FUNCS:
                    continue
                has_positional = len(node.args) >= 1
                has_keyword = any(
                    keyword.arg == "settings_path" for keyword in node.keywords
                )
                if not (has_positional or has_keyword):
                    offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, (
            "call(s) to install()/uninstall()/status() with no explicit "
            f"settings_path (D4 clause 3): {offenders}. Every such call "
            "must pass settings_path= (or an equivalent positional "
            "argument) pointing at a temporary path -- never the default, "
            "which is the REAL settings.json."
        )


class TestNoCliHooksInvocationOmitsAnExplicitSettingsPathFlag:
    """TC-124b: every CLI-shaped invocation of `hooks install|uninstall|
    status` anywhere under `tests/` (e.g. `cli.main([...])`, a
    `subprocess` call) carries an explicit `--settings-path` in the same
    argv list."""

    def test_every_cli_hooks_invocation_in_tests_carries_settings_path_flag(self) -> None:
        offenders: list[str] = []
        for path in _iter_test_files():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, (ast.List, ast.Tuple)):
                    continue
                values = _string_constants(node)
                is_hooks_verb_invocation = any(
                    values[i] == "hooks" and values[i + 1] in _GUARDED_CLI_VERBS
                    for i in range(len(values) - 1)
                )
                if not is_hooks_verb_invocation:
                    continue
                if "--settings-path" not in values:
                    lineno = getattr(node, "lineno", "?")
                    offenders.append(f"{rel}:{lineno}: {values}")
        assert not offenders, (
            "CLI invocation(s) of `hooks install|uninstall|status` with no "
            f"explicit --settings-path (D4 clause 3): {offenders}. Every "
            "such invocation must pass --settings-path pointing at a "
            "temporary path -- never the default, which is the REAL "
            "settings.json."
        )
