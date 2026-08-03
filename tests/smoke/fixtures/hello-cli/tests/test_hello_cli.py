"""Trivial tests for the hello-cli smoke fixture.

These belong to the fixture project, NOT to the superhuman skill's own suite —
``tests/smoke/conftest.py`` keeps pytest from collecting them during the main run.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the fixture's hello_cli module importable when run standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hello_cli import greet  # noqa: E402


def test_greet_basic() -> None:
    """greet() formats a simple name."""
    assert greet("World") == "Hello, World!"


def test_greet_preserves_input() -> None:
    """greet() inserts the name verbatim."""
    assert greet("Ada Lovelace") == "Hello, Ada Lovelace!"
