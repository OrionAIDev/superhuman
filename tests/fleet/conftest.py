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
