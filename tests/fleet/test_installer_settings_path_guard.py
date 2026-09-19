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

  (a) a direct call to `install`/`uninstall`/`status` -- by bare name, by
      attribute (`hooks_install.install(...)`), or through a FUNCTION-
      level import alias (`from scripts.fleet.hooks_install import
      install as do_it`, then `do_it(...)`) -- supplies either a
      `settings_path=` keyword or at least one positional argument (the
      function's first parameter IS `settings_path`, so a positional call
      is equally explicit -- this module does not require the keyword
      spelling, only that a path was actually supplied, which is the
      property that matters); and

  (b) any call whose argument is a list literal shaped like a CLI argv
      (contains the consecutive string literals `"hooks"` then one of
      `"install"` / `"uninstall"` / `"status"`) also contains the string
      literal `"--settings-path"` somewhere in that same list -- this
      covers `cli.main(["hooks", "install", ...])` and an equivalent
      `subprocess.run([..., "hooks", "install", ...])` shape.

**PM review, 2026-09-19: check (a) missed an import alias.** The
original version matched only a call's bare/attribute name, and its own
docstring wrongly claimed a "locally-renamed import" could not dodge it.
The PM disproved that with a throwaway plant: `from
scripts.fleet.hooks_install import install as do_it` then
`do_it(dry_run=True)` passed check (a) uncaught (2 passed, should have
been 1 failed). Fixed by `_collect_installer_import_aliases`, which reads
every `from <...>hooks_install import install|uninstall|status as X` in
the file being scanned and resolves a later bare-name call through `X`
back to its canonical verb before checking for an explicit path. An
attribute call (`hooks_install.install(...)`, `hi.install(...)`,
`scripts.fleet.hooks_install.install(...)`) never needed this -- `.attr`
already names the guarded function regardless of what the OBJECT before
the dot is called, so only the module ALIAS varies, never the verb name.

**Why this still cannot cover everything -- and why it does not need
to.** A call site that builds its argument list dynamically, or that
rebinds a name via a plain assignment rather than an import
(`x = hooks_install.install; x()`), is not resolvable by static AST
inspection and is NOT caught by either check here -- this is the same
class of limitation `_call_target_name` in `test_hooks_install.py`
already accepts for TC-58, restated here because it now applies estate-
wide rather than to one file. Every call site in `tests/` today (verified
while writing this test) uses a literal list and an explicit keyword or
positional path, so this limitation is not presently exploited. TC-124c
(`tests/fleet/conftest.py`'s `_forbid_real_settings_file_io` autouse
fixture) is the deliberate, PM-directed answer to "an AST scan can always
be dodged": it patches `hooks_install.py`'s own lowest I/O seams
(`_read_settings_text`, `_atomic_write`), which `install()`/`uninstall()`/
`status()` call by their own internal, unaliased name no matter what an
external caller renamed the public function to -- so it catches a call
shape this module's static scan cannot enumerate, including the one that
motivated this note.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_DIR = _REPO_ROOT / "tests"

#: The installer's three verbs (`scripts/fleet/hooks_install.py`).
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


def _collect_installer_import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map a local name a `from ...hooks_install import X as Y` binds,
    for X in `install`/`uninstall`/`status`, back to its canonical name.

    Only `ImportFrom` aliasing needs tracking. An attribute call's `.attr`
    already identifies the guarded function regardless of what the
    module-level object is called (`hooks_install.install(...)`,
    `hi.install(...)`, and `scripts.fleet.hooks_install.install(...)` all
    resolve via `.attr == "install"` with no alias tracking needed) --
    only a FUNCTION-level import alias changes the CALLED name itself,
    which is what this resolves.

    Matches on the module name ending in `hooks_install` (or being
    exactly that), tolerating `scripts.fleet.hooks_install`,
    `fleet.hooks_install`, or a bare `hooks_install` -- whatever import
    root form a given test file uses.

    Args:
        tree: a parsed module.

    Returns:
        A dict mapping each local alias to its canonical verb
        (`"install"`, `"uninstall"`, or `"status"`). Empty if the file
        imports no verb under an alias.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if not (module == "hooks_install" or module.endswith(".hooks_install")):
            continue
        for alias in node.names:
            if alias.name in _GUARDED_INSTALLER_FUNCS:
                local_name = alias.asname or alias.name
                aliases[local_name] = alias.name
    return aliases


def _resolve_guarded_call(node: ast.Call, aliases: dict[str, str]) -> str | None:
    """Return the canonical guarded verb (`install`/`uninstall`/`status`)
    a `Call` node invokes, or `None` if it invokes something else.

    Args:
        node: an `ast.Call` node.
        aliases: this file's import-alias map, from
            `_collect_installer_import_aliases`.

    Returns:
        The canonical verb name for an `ast.Attribute` call whose `.attr`
        is a guarded verb, an `ast.Name` call whose bare name IS a
        guarded verb, or an `ast.Name` call whose bare name is a tracked
        import alias for one; `None` for any other call shape (including
        one through a subscript, a chained call, or an untracked
        reassignment -- see the module docstring's stated limitation).
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr in _GUARDED_INSTALLER_FUNCS:
            return func.attr
        return None
    if isinstance(func, ast.Name):
        if func.id in _GUARDED_INSTALLER_FUNCS:
            return func.id
        return aliases.get(func.id)
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
    """TC-124a: every direct call to `install`/`uninstall`/`status` --
    including through a function-level import alias -- anywhere under
    `tests/` supplies an explicit path (keyword or positional). The
    tests/-wide superset of TC-58."""

    def test_every_installer_call_in_tests_supplies_a_path(self) -> None:
        offenders: list[str] = []
        for path in _iter_test_files():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            rel = path.relative_to(_REPO_ROOT).as_posix()
            aliases = _collect_installer_import_aliases(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _resolve_guarded_call(node, aliases) is None:
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
