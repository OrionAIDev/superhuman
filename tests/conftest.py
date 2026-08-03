"""Pytest configuration and shared fixtures for the superhuman skill test suite.

This module provides the SKILL_ROOT fixture pointing at the installed skill,
and helpers for walking the skill structure.
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

# Let tests import sibling helper modules (publication_patterns).
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def skill_root() -> Path:
    """Return the skill root path.

    Resolution order:
        1. SUPERHUMAN_SKILL_ROOT env var (for CI / overrides)
        2. Two levels up from this conftest.py (tests/conftest.py -> skill root)

    Returns:
        Path: absolute path to the skill root directory.

    Raises:
        FileNotFoundError: if the resolved path does not contain SKILL.md.
    """
    env_root = os.environ.get("SUPERHUMAN_SKILL_ROOT")
    root = Path(env_root) if env_root else Path(__file__).resolve().parent.parent
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError(
            f"SKILL.md not found under {root}; "
            f"set SUPERHUMAN_SKILL_ROOT to the skill root."
        )
    return root
