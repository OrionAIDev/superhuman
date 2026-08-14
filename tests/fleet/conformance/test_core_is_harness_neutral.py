"""TC-13: ``scripts/fleet/core/`` imports nothing harness-specific (NFR-2, static half).

An `ast`-based import scan (no execution) over every `.py` file under
`scripts/fleet/core/`, asserting none of them import anything shaped like an
adapter, `session-relay`, or a native Claude tool. A deliberately-broken
fixture proves the guard can actually fail, not just vacuously pass — the
same "prove the test can fail" discipline this skill's own test suite already
uses elsewhere (e.g. `tests/test_publication_guard.py`).

The runtime half of NFR-2 (the whole create/update/validate/query surface
genuinely working with no Claude APIs importable, not just no Claude APIs
*referenced*) is TC-35/TC-32, landing with Chunk 2's `PortableAdapter` and
Chunk 7's conformance suite.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Substrings that mark an import as harness-specific. Matched case-insensitively
#: against the full dotted module string of every import statement.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "adapter",
    "session_relay",
    "session-relay",
    "mcp__",
)

_CORE_DIR = Path(__file__).resolve().parents[3] / "scripts" / "fleet" / "core"


def _collect_import_modules(source: str) -> set[str]:
    """Return every dotted module name imported by `source`.

    Args:
        source: Python source text to scan (never executed — parsed only).

    Returns:
        set[str]: one entry per `import x.y` or `from x.y import ...`
        statement, using the full dotted module string (e.g. `"a.b.c"`), plus
        each individually-imported name for `from x import a, b` style.
    """
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
            for alias in node.names:
                modules.add(alias.name)
    return modules


def _harness_specific_offenders(modules: set[str]) -> set[str]:
    """Return the subset of `modules` matching the forbidden-substring list.

    Args:
        modules: dotted module names, as returned by `_collect_import_modules`.

    Returns:
        set[str]: modules whose lowercased name contains any forbidden token.
    """
    return {
        m
        for m in modules
        if any(token in m.lower() for token in _FORBIDDEN_SUBSTRINGS)
    }


def test_core_files_exist_to_scan() -> None:
    # Guards against a vacuous pass if the directory were ever empty/renamed.
    core_files = sorted(_CORE_DIR.glob("*.py"))
    assert core_files, f"no .py files found under {_CORE_DIR} — nothing was scanned"


def test_core_imports_nothing_harness_specific() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(_CORE_DIR.glob("*.py")):
        modules = _collect_import_modules(path.read_text(encoding="utf-8"))
        bad = _harness_specific_offenders(modules)
        if bad:
            offenders[str(path.relative_to(_CORE_DIR))] = bad

    assert not offenders, (
        "harness-specific imports found under scripts/fleet/core/ (NFR-2 "
        f"violation): {offenders}"
    )


def test_guard_can_actually_fail_on_a_broken_fixture() -> None:
    """Prove the guard is not vacuous: an injected adapter import IS caught."""
    real_files = sorted(_CORE_DIR.glob("*.py"))
    victim_source = real_files[0].read_text(encoding="utf-8")
    broken_source = "from ..adapter.claude import ClaudeAdapter\n" + victim_source

    modules = _collect_import_modules(broken_source)
    offenders = _harness_specific_offenders(modules)

    assert offenders, (
        "the import guard failed to detect a deliberately broken fixture — "
        "it would vacuously pass on real violations too"
    )
    assert "..adapter.claude" in offenders or "adapter.claude" in offenders or any(
        "adapter" in o for o in offenders
    )
