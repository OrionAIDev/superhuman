"""Role -> dispatch-class policy loader (roadmap#275).

`adaptation/role-tiers.json` is the authoritative, harness-neutral table of
which dispatch class each role (and each non-role duty) runs at by default,
and which classes it may opt into with a stated reason. A *dispatch class*
is a model tier from the operator profile's `models:` block, optionally at
that tier's raised effort: ``most_capable``, ``most_capable+raised``,
``standard``, ``cheap``.

This module only parses and validates that file. It names no harness: the
Claude Code class -> `subagent_type` map lives beside the Claude Code agent
templates (`templates/agents/claude-code/tier-agents.json`) and is loaded by
:func:`load_harness_class_map`, which takes the path rather than knowing it.

Stdlib only (`json`), so the fail-soft role-gate hook can import it
without a third-party dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Every dispatch class the policy may name. A tier suffixed ``+raised``
#: runs at that tier's ``raised_effort`` instead of its ``effort``.
DISPATCH_CLASSES: tuple[str, ...] = (
    "most_capable",
    "most_capable+raised",
    "standard",
    "cheap",
)

#: The line a task brief carries to justify an opt-in dispatch class.
OPT_IN_MARKER = "superhuman-tier-opt-in:"

_SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_POLICY_PATH = _SKILL_ROOT / "adaptation" / "role-tiers.json"


class RoleTierError(Exception):
    """The role-tier policy (or a harness class map) is missing or malformed."""


@dataclass(frozen=True)
class TierRule:
    """The allowed dispatch classes for one role or duty.

    Attributes:
        default: The class the PM dispatches this role at.
        opt_in: Classes allowed only with an opt-in reason.
    """

    default: str
    opt_in: tuple[str, ...]

    @property
    def allowed(self) -> tuple[str, ...]:
        """Return the default class followed by every opt-in class."""
        return (self.default, *self.opt_in)


@dataclass(frozen=True)
class RoleTierPolicy:
    """The parsed `adaptation/role-tiers.json`.

    Attributes:
        roles: Role-file stem (``roles/<stem>.md``) -> rule.
        duties: Non-role duty name -> rule.
    """

    roles: dict[str, TierRule]
    duties: dict[str, TierRule]


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from ``path``.

    Args:
        path: The file to read.

    Returns:
        The decoded top-level object.

    Raises:
        RoleTierError: If the file is unreadable, not JSON, or not an object.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RoleTierError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RoleTierError(f"{path}: top level must be a JSON object")
    return data


def _parse_rules(raw: Any, where: str) -> dict[str, TierRule]:
    """Parse one ``roles`` or ``duties`` mapping.

    Args:
        raw: The raw JSON value.
        where: A label for error messages.

    Returns:
        Name -> validated rule.

    Raises:
        RoleTierError: On any structural problem or unknown dispatch class.
    """
    if not isinstance(raw, dict):
        raise RoleTierError(f"{where}: must be an object")
    rules: dict[str, TierRule] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise RoleTierError(f"{where}.{name}: must be an object")
        default = entry.get("default")
        opt_in = entry.get("opt_in", [])
        if not isinstance(default, str) or default not in DISPATCH_CLASSES:
            raise RoleTierError(f"{where}.{name}.default: unknown dispatch class {default!r}")
        if not isinstance(opt_in, list) or not all(
            isinstance(c, str) and c in DISPATCH_CLASSES for c in opt_in
        ):
            raise RoleTierError(f"{where}.{name}.opt_in: must list known dispatch classes")
        if default in opt_in:
            raise RoleTierError(f"{where}.{name}: default {default!r} repeated in opt_in")
        rules[name] = TierRule(default=default, opt_in=tuple(opt_in))
    return rules


def load_policy(path: Path | None = None) -> RoleTierPolicy:
    """Load and validate the role-tier policy.

    Args:
        path: Policy file; defaults to this checkout's `adaptation/role-tiers.json`.

    Returns:
        The parsed policy.

    Raises:
        RoleTierError: If the file is missing or malformed.
    """
    data = _read_json(path or DEFAULT_POLICY_PATH)
    if data.get("schema_version") != 1:
        raise RoleTierError("role-tiers.json: unsupported schema_version")
    return RoleTierPolicy(
        roles=_parse_rules(data.get("roles"), "roles"),
        duties=_parse_rules(data.get("duties"), "duties"),
    )


def load_harness_class_map(path: Path) -> dict[str, str]:
    """Load a harness's dispatch class -> harness agent name map.

    Args:
        path: e.g. `templates/agents/claude-code/tier-agents.json`.

    Returns:
        Dispatch class -> agent name, covering every class in
        :data:`DISPATCH_CLASSES`.

    Raises:
        RoleTierError: If the file is malformed, misses a class, or maps two
            classes to one agent name.
    """
    data = _read_json(path)
    agents = data.get("agents")
    if not isinstance(agents, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v for k, v in agents.items()
    ):
        raise RoleTierError(f"{path}: 'agents' must map class -> non-empty name")
    if set(agents) != set(DISPATCH_CLASSES):
        raise RoleTierError(f"{path}: must map exactly {list(DISPATCH_CLASSES)}")
    if len(set(agents.values())) != len(agents):
        raise RoleTierError(f"{path}: two dispatch classes share one agent name")
    return dict(agents)


def has_opt_in_reason(prompt: str) -> bool:
    """Return whether ``prompt`` carries a non-empty opt-in reason line.

    Args:
        prompt: The full dispatch prompt.

    Returns:
        True iff some line starts with :data:`OPT_IN_MARKER` followed by
        non-whitespace text.
    """
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith(OPT_IN_MARKER) and stripped[len(OPT_IN_MARKER):].strip():
            return True
    return False
