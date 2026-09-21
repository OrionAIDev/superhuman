"""Role-tier policy tests (roadmap#275).

`adaptation/role-tiers.json` is authoritative; `adaptation/dispatch.md`
renders it (and the Claude Code tier-agent map) for humans. These tests fail
when the two drift apart, when a role file has no policy row, or when the
loader accepts a malformed policy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.fleet.role_tiers import (
    DISPATCH_CLASSES,
    RoleTierError,
    has_opt_in_reason,
    load_harness_class_map,
    load_policy,
)

ROOT = Path(__file__).resolve().parent.parent
DISPATCH_MD = ROOT / "adaptation" / "dispatch.md"
CC_MAP = ROOT / "templates" / "agents" / "claude-code" / "tier-agents.json"

# dispatch.md display label -> role-tiers.json key.
_LABELS = {
    "PM": "pm",
    "Architect": "architect",
    "Developer": "developer",
    "QA": "qa",
    "Business Expert": "business-expert",
    "surrogate-user": "surrogate-user",
    "Tester": "tester",
    "code-quality review": "code-quality-review",
    "security review": "security-review",
    "docs-sync": "docs-sync",
    "convention checks": "convention-check",
}


def _section(heading: str) -> str:
    """Return the body of the ``### <heading>`` section of dispatch.md."""
    text = DISPATCH_MD.read_text(encoding="utf-8")
    match = re.search(rf"^### {re.escape(heading)}\n(.*?)(?=^##)", text, re.M | re.S)
    assert match, f"dispatch.md lacks '### {heading}'"
    return match.group(1)


def _table_rows(body: str) -> list[list[str]]:
    """Return the data rows of the first markdown table in ``body``."""
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in body.splitlines()
        if line.strip().startswith("|")
    ]
    return rows[2:]  # drop header + separator


def _classes(cell: str) -> list[str]:
    """Return the backticked dispatch classes in a table cell."""
    return re.findall(r"`([^`]+)`", cell)


def test_every_role_file_has_a_policy_row() -> None:
    policy = load_policy()
    role_files = {p.stem for p in (ROOT / "roles").glob("*.md")}
    assert role_files == set(policy.roles)


def test_dispatch_md_role_table_matches_policy() -> None:
    policy = load_policy()
    rules = {**policy.roles, **policy.duties}
    rendered = {
        _LABELS[row[0]]: (_classes(row[1]), _classes(row[2]))
        for row in _table_rows(_section("Role → dispatch class"))
    }
    expected = {name: ([rule.default], list(rule.opt_in)) for name, rule in rules.items()}
    assert rendered == expected


def test_dispatch_md_claude_code_agent_table_matches_map() -> None:
    rendered = {
        _classes(row[0])[0]: _classes(row[1])[0]
        for row in _table_rows(_section("Claude Code — tier agent definitions"))
    }
    assert rendered == load_harness_class_map(CC_MAP)


def test_class_map_covers_every_class() -> None:
    assert set(load_harness_class_map(CC_MAP)) == set(DISPATCH_CLASSES)


@pytest.mark.parametrize(
    "roles",
    [
        {"pm": {"default": "opus"}},
        {"pm": {"default": "standard", "opt_in": ["bogus"]}},
        {"pm": {"default": "standard", "opt_in": ["standard"]}},
        {"pm": "standard"},
    ],
)
def test_malformed_policy_is_rejected(tmp_path: Path, roles: object) -> None:
    path = tmp_path / "role-tiers.json"
    path.write_text(json.dumps({"schema_version": 1, "roles": roles, "duties": {}}))
    with pytest.raises(RoleTierError):
        load_policy(path)


def test_class_map_rejects_shared_agent_name(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"agents": {c: "same" for c in DISPATCH_CLASSES}}))
    with pytest.raises(RoleTierError):
        load_harness_class_map(path)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("brief\nsuperhuman-tier-opt-in: auth boundary change\n", True),
        ("  superhuman-tier-opt-in:   x", True),
        ("superhuman-tier-opt-in:", False),
        ("superhuman-tier-opt-in:    \n", False),
        ("no marker here", False),
    ],
)
def test_opt_in_reason_detection(prompt: str, expected: bool) -> None:
    assert has_opt_in_reason(prompt) is expected
