"""Content validation tests for the superhuman skill bundle.

Verifies frontmatter completeness on roles, phase recipes, conventions,
and SKILL.md; verifies required H2 sections per role contract.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from publication_patterns import (  # noqa: E402
    LEAK_PATTERNS,
    TOKENS_FILE,
    is_scanned,
)


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown file's text.

    Args:
        text: full file contents.

    Returns:
        Parsed frontmatter as a dict, or empty dict if no frontmatter present.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    return yaml.safe_load(match.group(1)) or {}


def _h2_sections(text: str) -> set[str]:
    """Return the set of H2 section titles in a markdown file.

    Args:
        text: full file contents.

    Returns:
        Set of stripped section title strings (without the leading '## ').
    """
    return {line[3:].strip() for line in text.splitlines() if line.startswith("## ")}


# --- SKILL.md ---


def test_skill_frontmatter(skill_root: Path) -> None:
    """SKILL.md has the required name + description frontmatter."""
    fm = _parse_frontmatter((skill_root / "SKILL.md").read_text(encoding="utf-8"))
    assert fm.get("name") == "superhuman"
    assert isinstance(fm.get("description"), str)
    assert len(fm["description"]) >= 50, "description should be substantive for trigger matching"


def test_skill_has_cross_cutting_rules(skill_root: Path) -> None:
    """SKILL.md includes the cross-cutting rules section."""
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    sections = _h2_sections(text)
    assert "Cross-cutting rules (apply EVERY response)" in sections or "Cross-cutting rules" in sections


def test_skill_md_has_hard_gate(skill_root: Path) -> None:
    """SKILL.md must contain the HARD-GATE block and anti-pattern section.

    These are the framework's teeth. Removing them is a regression that allows
    models to skip HITL gates for 'simple' projects (see the v0.1.2 deployed-environment smoke
    failure that motivated v0.1.3-rc1).
    """
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert "<HARD-GATE>" in text, "SKILL.md missing the HARD-GATE block"
    assert "</HARD-GATE>" in text, "SKILL.md missing the </HARD-GATE> closing tag"
    assert "Anti-pattern" in text, "SKILL.md missing the anti-pattern section"
    assert "Required model tier" in text, "SKILL.md missing the required-model-tier section"
    assert "FIRST action on every invocation" in text, "HARD-GATE missing first-action mandate"


# --- Roles ---


@pytest.mark.parametrize("role_file", [
    "pm.md", "business-expert.md", "architect.md",
    "developer.md", "qa.md", "tester.md", "surrogate-user.md",
])
def test_role_frontmatter(skill_root: Path, role_file: str) -> None:
    """Every role has name + tier frontmatter."""
    text = (skill_root / "roles" / role_file).read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    assert "name" in fm, f"{role_file} missing name"
    assert fm.get("tier") in {"cheap-fast", "standard", "most-capable"}, (
        f"{role_file} has invalid tier: {fm.get('tier')}"
    )
    assert "declared-references" in fm, f"{role_file} missing declared-references"
    assert "declared-conventions" in fm, f"{role_file} missing declared-conventions"


def test_pm_has_required_sections(skill_root: Path) -> None:
    """PM role has all 11 required H2 sections per its contract."""
    text = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    sections = _h2_sections(text)
    required = {
        "Cross-cutting behaviors",
        "Phase responsibilities",
        "Vision elicitation (G0)",
        "Workflow preferences (G1)",
        "Per-feature foundation decisions",
        "Parallelism decisions",
        "Drift watch and re-evaluation",
        "Gate handling",
        "Artifact-set declaration and enforcement",
        "Milestone retuning",
        "Output discipline",
    }
    missing = required - sections
    assert not missing, f"PM missing sections: {missing}"


def test_surrogate_user_role_opens_with_refusal(skill_root: Path) -> None:
    """surrogate-user.md must open with a self-refusal and enumerate its gates.

    The role prompt is the in-context belt that complements the deterministic
    resolver: if a surrogate is ever dispatched somewhere the profile forbids
    unattended operation, it must refuse and escalate rather than proceed.
    """
    text = (skill_root / "roles" / "surrogate-user.md").read_text(encoding="utf-8")
    head = text[:800]
    assert "act_unattended" in head, "refusal must name the policy it defers to"
    assert "never" in head, "refusal must name the forbidding policy value"
    assert "escalate" in head.lower(), "refusal must escalate, not merely decline"
    assert "no policy at all" in head or "declares no policy" in head, (
        "an undeclared policy must refuse too, not just an explicit `never`"
    )
    # decision policy must enumerate the gates it may answer and those it never answers
    for g in ("G2", "G3", "G4", "G5", "G7"):
        assert g in text, f"surrogate policy must cover {g}"
    for g in ("G0", "G1", "G8"):
        assert g in text, f"surrogate must name human-only gate {g}"


# --- Phases ---


@pytest.mark.parametrize("phase_file", [
    "0-kickoff.md", "1-requirements.md", "2-design.md", "2.1-test-plan.md",
    "3-implementation.md", "3.1-test-review.md", "3.2-docs-sync.md",
    "3.3-preflight-review.md", "4-acceptance.md",
])
def test_phase_frontmatter(skill_root: Path, phase_file: str) -> None:
    """Every phase recipe has phase + title + driver frontmatter."""
    text = (skill_root / "phases" / phase_file).read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    assert "phase" in fm, f"{phase_file} missing phase"
    assert "title" in fm, f"{phase_file} missing title"
    assert "driver" in fm, f"{phase_file} missing driver"
    assert "gates" in fm, f"{phase_file} missing gates"


# --- Conventions ---


@pytest.mark.parametrize("conv_file", ["python.md", "testing.md", "git.md"])
def test_convention_nonempty(skill_root: Path, conv_file: str) -> None:
    """Every convention file is non-empty and has at least one H2 section."""
    text = (skill_root / "conventions" / conv_file).read_text(encoding="utf-8")
    assert len(text) > 200, f"{conv_file} is suspiciously short"
    sections = _h2_sections(text)
    assert sections, f"{conv_file} has no H2 sections"


# --- Dispatch adaptation ---


def test_dispatch_table_present(skill_root: Path) -> None:
    """adaptation/dispatch.md contains the symbol table."""
    text = (skill_root / "adaptation" / "dispatch.md").read_text(encoding="utf-8")
    assert "<dispatch:agent>" in text
    assert "<dispatch:ask>" in text
    assert "Claude Code" in text


# --- VERSION ---


def test_version_is_semver(skill_root: Path) -> None:
    """VERSION file is a valid semver string."""
    text = (skill_root / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?", text), f"Invalid semver: {text}"


def test_skill_md_resume_logic_is_strict(skill_root: Path) -> None:
    """SKILL.md HARD-GATE must require structured Decisions log for valid resume.

    A SUPERHUMAN.md that exists but lacks a structured decisions log should
    NOT be treated as valid resume state — that bug was the root cause of
    the v0.1.3-rc1 deployed-environment smoke failure. The HARD-GATE wording must encode
    the distinction.
    """
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    # The strengthened wording must reference the key concepts
    assert "valid superhuman session" in text or "VALID iff" in text or "## Decisions log" in text, (
        "SKILL.md HARD-GATE must define valid resume state by reference to structured Decisions log"
    )
    assert "INVALID" in text or "stale state" in text, (
        "SKILL.md HARD-GATE must explicitly handle invalid/stale state"
    )
    assert "pre-existing" in text.lower() or "pre-existing implementation code" in text, (
        "SKILL.md must address pre-existing code outside superhuman flow"
    )
    assert "backfill" in text.lower(), (
        "SKILL.md anti-pattern table must call out the 'I'll backfill artifacts' rationalization"
    )


def test_role_and_phase_files_use_dispatch_symbols(skill_root: Path) -> None:
    """Roles and phases must use <dispatch:*> symbolic names, not raw tool names.

    Raw 'Agent' / 'AskUserQuestion' / etc. are only allowed inside explicit
    prohibition sentences (e.g., 'never use raw `Agent`').
    """
    raw_pattern = re.compile(r"\b(Agent|AskUserQuestion|TaskCreate|TaskUpdate)\b")
    prohibition_indicators = ("never", "not", "no raw", "do not use", "instead of")
    violations: list[str] = []
    for d in ("roles", "phases"):
        for f in (skill_root / d).glob("*.md"):
            for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if raw_pattern.search(line):
                    line_lower = line.lower()
                    if not any(ind in line_lower for ind in prohibition_indicators):
                        # Also skip code-block-fenced lines that look like dispatch symbols already
                        if "<dispatch:" in line_lower:
                            continue
                        violations.append(f"{f.name}:{lineno}: {line.strip()}")
    assert not violations, (
        "Raw tool names found outside prohibition contexts:\n  " + "\n  ".join(violations)
    )


def _parse_dispatch_table(text: str) -> dict[str, dict[str, str]]:
    """Parse adaptation/dispatch.md symbol table into {symbol: {cc, openclaw}}.

    Only data rows (col 0 contains a ``<dispatch:...>`` symbol) are returned;
    the header row and the ``|---|`` separator are skipped. Column order is
    fixed by the table contract: symbol | description | Claude Code | OpenClaw.

    Args:
        text: full contents of adaptation/dispatch.md.

    Returns:
        Mapping from each dispatch symbol to its Claude Code and OpenClaw cells
        (stripped of surrounding whitespace).
    """
    table: dict[str, dict[str, str]] = {}
    symbol_re = re.compile(r"<dispatch:[a-z_]+>")
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        m = symbol_re.search(cells[0])
        if not m:
            continue
        table[m.group(0)] = {"cc": cells[2], "openclaw": cells[3]}
    return table


def _dispatch_symbols_in_use(skill_root: Path) -> set[str]:
    """Collect every ``<dispatch:*>`` symbol referenced across the live bundle.

    Scans SKILL.md plus every role and phase recipe — the files that drive
    actual orchestration behavior.

    Args:
        skill_root: skill bundle root.

    Returns:
        Set of symbol strings (e.g. ``{"<dispatch:agent>", "<dispatch:ask>"}``).
    """
    symbol_re = re.compile(r"<dispatch:[a-z_]+>")
    files = [skill_root / "SKILL.md"]
    for d in ("roles", "phases"):
        files.extend((skill_root / d).glob("*.md"))
    symbols: set[str] = set()
    for f in files:
        symbols.update(symbol_re.findall(f.read_text(encoding="utf-8")))
    return symbols


def test_dispatch_md_completeness_for_in_use_symbols(skill_root: Path) -> None:
    """Every in-use <dispatch:*> symbol has both platform cells filled (A1).

    Scans SKILL.md + roles/*.md + phases/*.md for symbols actually referenced,
    then asserts each has a row in adaptation/dispatch.md with non-empty Claude
    Code AND OpenClaw cells — no ``_to be filled_`` placeholders. Guards against
    a symbol entering active use before its OpenClaw mapping is documented,
    which would silently break the port.
    """
    table = _parse_dispatch_table(
        (skill_root / "adaptation" / "dispatch.md").read_text(encoding="utf-8")
    )
    placeholder = re.compile(r"_to be filled_|_tbd_|_not documented_", re.IGNORECASE)
    missing: list[str] = []
    for symbol in sorted(_dispatch_symbols_in_use(skill_root)):
        row = table.get(symbol)
        if row is None:
            missing.append(f"{symbol}: no row in dispatch.md")
            continue
        for platform in ("cc", "openclaw"):
            cell = row[platform]
            if not cell or placeholder.search(cell):
                missing.append(f"{symbol}: {platform} cell empty/placeholder ({cell!r})")
    assert not missing, "Incomplete dispatch mappings for in-use symbols:\n  " + "\n  ".join(missing)


def test_platform_only_features_have_degradation(skill_root: Path) -> None:
    """Symbols with no OpenClaw equivalent must document a degradation path (A3).

    For any dispatch row whose OpenClaw cell announces ``(no direct equivalent``,
    the cell must also spell out a fallback (a ``degrade``/``degradation``
    instruction) so the orchestrator never hits a dead end on the port. Catches
    a half-documented platform gap.
    """
    table = _parse_dispatch_table(
        (skill_root / "adaptation" / "dispatch.md").read_text(encoding="utf-8")
    )
    offenders: list[str] = []
    for symbol, row in sorted(table.items()):
        cell = row["openclaw"]
        if "no direct equivalent" in cell.lower():
            if "degrad" not in cell.lower():
                offenders.append(f"{symbol}: no-equivalent cell lacks a degradation path ({cell!r})")
    assert not offenders, "Platform-only features without degradation guidance:\n  " + "\n  ".join(offenders)


def test_phase_recipe_frontmatter_gates_match_body(skill_root: Path) -> None:
    """Each phase's declared gates are documented and its body stays in scope (A2).

    Two directions, asymmetric on purpose:
      * strict — every gate in ``gates:`` frontmatter (``?`` suffix stripped)
        must be discussed (``G<n>``) in the phase body. A declared-but-undocumented
        gate is a recipe drift.
      * lenient — every ``G<n>`` mentioned in the body must be either declared
        or in CROSS_CUTTING. G6 (unconditional drift escalation) and G10
        (BLOCKED — the one gate that survives at every HITL level, from any
        phase, per v0.5.0) can fire in any phase; G7 is the one legitimate
        backward reference (Phase 4 diffs against the last G7-approved docs).
        Anything else mentioned but undeclared is a gate the recipe drives
        without owning.
    """
    cross_cutting = {"G6", "G7", "G10"}
    gate_re = re.compile(r"\bG\d+\b")
    problems: list[str] = []
    for phase_file in sorted((skill_root / "phases").glob("*.md")):
        text = phase_file.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        declared = {str(g).strip().strip("\"'").rstrip("?") for g in (fm.get("gates") or [])}
        body = FRONTMATTER_RE.sub("", text, count=1)
        mentioned = set(gate_re.findall(body))

        for g in sorted(declared - mentioned):
            problems.append(f"{phase_file.name}: declares {g} but body never mentions it")
        for g in sorted(mentioned - declared - cross_cutting):
            problems.append(f"{phase_file.name}: body mentions {g} but it is neither declared nor cross-cutting")
    assert not problems, "Phase gate frontmatter/body mismatches:\n  " + "\n  ".join(problems)


def test_g8_emits_project_complete_terminator(skill_root: Path) -> None:
    """G8 sign-off must emit a distinct PROJECT COMPLETE terminator (D10).

    The v0.1.3 deployed-environment smoke surfaced that users could not tell when superhuman
    had actually finished — the acceptance summary read like any other update.
    The fix: an unmistakable final line at G8 sign-off, specified in BOTH the
    Phase 4 recipe and the PM role so neither can drift from the other.
    """
    phase4 = (skill_root / "phases" / "4-acceptance.md").read_text(encoding="utf-8")
    pm = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    for name, text in (("phases/4-acceptance.md", phase4), ("roles/pm.md", pm)):
        assert "PROJECT COMPLETE" in text, f"{name} missing the PROJECT COMPLETE terminator"
        assert "/new" in text, f"{name} terminator must point the user at /new to start another"
        assert "own line" in text.lower() or "final line" in text.lower(), (
            f"{name} must specify the terminator is emitted on its own/final line"
        )


def test_pm_has_phase3_heartbeat(skill_root: Path) -> None:
    """PM role must specify a Phase 3 progress heartbeat (D11).

    The v0.1.3 smoke flagged long silent stretches during implementation chunks
    and parallel reviews. PM must emit a periodic append-only (Type B, no-pause)
    heartbeat so the user knows the orchestrator is still driving.
    """
    pm = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    sections = _h2_sections(pm)
    assert any("heartbeat" in s.lower() for s in sections), (
        "pm.md must have a Heartbeat H2 section"
    )
    lower = pm.lower()
    assert "in flight" in lower, "heartbeat spec must use the 'in flight' progress wording"
    assert "type b" in lower, "heartbeat must be declared Type B (no pause)"
    assert "3 min" in lower or "3-min" in lower or "~3" in pm, (
        "heartbeat must state the ~3 min cadence"
    )


def test_skill_md_gates_autonomy_on_the_profile(skill_root: Path) -> None:
    """SKILL.md must gate autonomy on the resolver, not on named environments.

    Since v0.8.0 the ladder is data. The HARD-GATE must therefore point at the
    deterministic guard and spell out its exit-code contract, rather than naming
    any particular environment.
    """
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert "autonomous" in text.lower(), "SKILL.md must mention autonomous mode"
    assert "autonomous-precondition.sh" in text, (
        "SKILL.md must point at the deterministic precondition guard"
    )
    assert "act_unattended" in text, "must name the policy that governs the loop"
    # the full exit-code contract must be stated, including the unresolved case
    for code in ("0", "2", "3", "4"):
        assert f"| {code} |" in text, f"HARD-GATE must document exit {code}"
    assert "ceiling" in text.lower(), (
        "must state that the profile is a ceiling on the project's HITL level"
    )
    assert "HITL-H" in text and "HITL-M" in text and "HITL-L" in text


def test_dispatch_md_documents_surrogate_pattern(skill_root: Path) -> None:
    """dispatch.md documents the surrogate-user dispatch pattern (role via agent).

    Decision D1: surrogate-user is a ROLE dispatched through the existing
    <dispatch:agent> verb, not a new dispatch symbol. The OpenClaw constraints
    (agentId=main, explicit model) must be restated for this dispatch.
    """
    text = (skill_root / "adaptation" / "dispatch.md").read_text(encoding="utf-8")
    assert "surrogate-user" in text, "dispatch.md must document the surrogate dispatch"
    lower = text.lower()
    assert "agentid" in lower and "main" in lower
    assert "explicit model" in lower or "explicit `model`" in lower


def test_goal_template_has_required_sections(skill_root: Path) -> None:
    """GOAL.md template carries fitness, measurement, and budget envelope."""
    text = (skill_root / "templates" / "artifacts" / "GOAL.md.tpl").read_text(encoding="utf-8")
    for needle in ("Fitness", "Measurement command", "Budget", "Max iterations"):
        assert needle in text, f"GOAL.md.tpl missing '{needle}'"


def test_autonomous_convention_locks_in_decisions(skill_root: Path) -> None:
    """conventions/autonomous.md encodes the locked loop decisions."""
    text = (skill_root / "conventions" / "autonomous.md").read_text(encoding="utf-8").lower()
    assert "strictly" in text and "rollback" in text, "ties must be rollback"
    assert "sequential" in text, "iterations are sequential"
    assert "plateau" in text
    assert "min_delta" in text or "min delta" in text


def test_autonomous_loop_phase_step0_runs_precondition(skill_root: Path) -> None:
    """The autonomous loop recipe's Step 0 must invoke the precondition guard."""
    text = (skill_root / "phases" / "3-autonomous-loop.md").read_text(encoding="utf-8")
    assert "autonomous-precondition.sh" in text, "Step 0 must call the guard"
    assert "iter-" in text, "must document per-iteration snapshot/keep tags"
    assert "rollback" in text.lower()
    for needle in ("-pre", "-alpha-", "-beta-"):
        assert needle in text, f"branch/tag strategy must include '{needle}'"


def test_autonomous_loop_phase_uses_iter_script(skill_root: Path) -> None:
    """The loop recipe must drive the audit trail through autonomous-iter.sh (v0.2.2).

    The v0.2.0 live smoke showed a capable model SKIP the hand-run per-iteration
    tag/commit dance. v0.2.2 makes it code-enforced: the recipe must call the
    script's pre/decide/final subcommands rather than describing manual git steps.
    """
    text = (skill_root / "phases" / "3-autonomous-loop.md").read_text(encoding="utf-8")
    assert "autonomous-iter.sh" in text, "recipe must reference the deterministic driver"
    for sub in ("pre", "decide", "final"):
        assert f"autonomous-iter.sh {sub}" in text, f"recipe must call autonomous-iter.sh {sub}"


def test_kickoff_offers_autonomous_when_preconditions_met(skill_root: Path) -> None:
    """Phase 0 G1 offers autonomous mode (gated) and elicits/accepts GOAL.md."""
    text = (skill_root / "phases" / "0-kickoff.md").read_text(encoding="utf-8")
    low = text.lower()
    assert "autonomous" in low
    assert "goal.md" in low
    assert "autonomous-precondition.sh" in text, "the option must be gated by the guard"
    assert "off" in low and "opt" in low, "default OFF, explicit opt-in"


def test_pm_has_autonomous_mode_section(skill_root: Path) -> None:
    """pm.md documents when to dispatch the surrogate vs human, and loop tracking."""
    text = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    sections = {l[3:].strip() for l in text.splitlines() if l.startswith("## ")}
    assert any("HITL-M" in s or "HITL-L" in s for s in sections), "pm.md needs an HITL-M/L behavior H2"
    assert "surrogate-user" in text
    assert "iter-" in text and "rollback" in text.lower()


def test_superhuman_template_has_autonomous_sections(skill_root: Path) -> None:
    """SUPERHUMAN.md template carries the optional autonomous run config + iterations log."""
    text = (skill_root / "templates" / "SUPERHUMAN.md.tpl").read_text(encoding="utf-8")
    assert "## Autonomous run config" in text
    assert "## Autonomous iterations log" in text
    assert "## Environment:" in text, "needed by the precondition guard's marker check"


def test_skill_md_has_autonomous_progression_rule(skill_root: Path) -> None:
    """SKILL.md must include the autonomous-phase-progression cross-cutting rule.

    Motivation: the v0.1.3-rc2 deployed-environment smoke ran all 8 gates correctly but
    stalled between G5 (Type B one-liner) and G7, waiting for user 'continue'
    prompts to advance. The rule encoded in v0.1.3-rc3 fixes this by mandating
    autonomous progression past non-pausing gates.
    """
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert "Autonomous phase progression" in text, (
        "SKILL.md must include the 'Autonomous phase progression' cross-cutting rule"
    )
    # Must distinguish Type A (pause) from Type B (continue) explicitly
    assert "Type A" in text and "Type B" in text, (
        "Autonomous-progression rule must distinguish Type A vs Type B gate behavior"
    )
    # Phase 3.1 recipe must echo the rule for G5 on-divergence path
    phase31 = (skill_root / "phases" / "3.1-test-review.md").read_text(encoding="utf-8")
    assert "DO NOT PAUSE" in phase31 or "do not pause" in phase31.lower(), (
        "phases/3.1-test-review.md must explicitly mark G5 on-divergence as non-pausing"
    )


# --- v0.4.0 harvest: anti-rationalization anatomy invariant ---

# Every roasting sub-skill and every role doc — plus the two new v0.4.0 sub-skills — must carry
# the anti-rationalization anatomy: a "## Common Rationalizations" section AND a "## Red Flags"
# section. This is the structural invariant harvested from addyosmani's skill-anatomy (report
# §3.4). Forked-verbatim superpowers references (brainstorming, tdd, etc.) are intentionally NOT
# enumerated here — the invariant covers the locally-authored roast/role/sub-skill surface only.
ANATOMY_FILES = [
    "references/roasting-requirements/SKILL.md",
    "references/roasting-design-specs/SKILL.md",
    "references/roasting-code/SKILL.md",
    "roles/pm.md",
    "roles/architect.md",
    "roles/developer.md",
    "roles/qa.md",
    "roles/tester.md",
    "roles/business-expert.md",
    "roles/surrogate-user.md",
    "references/doubt-driven-development/SKILL.md",
    "references/deprecating-a-system/SKILL.md",
]


@pytest.mark.parametrize("rel_path", ANATOMY_FILES)
def test_anatomy_invariant(skill_root: Path, rel_path: str) -> None:
    """Each enumerated roast/role/sub-skill file carries both anatomy sections.

    'Common Rationalizations' names the excuses an agent uses to skip the skill;
    'Red Flags' is a checkable list of observable violations. Both are mandatory
    (report §3.4) so the framework hardens against 'I'll skip this once' drift.
    """
    text = (skill_root / rel_path).read_text(encoding="utf-8")
    sections = _h2_sections(text)
    assert "Common Rationalizations" in sections, (
        f"{rel_path} missing '## Common Rationalizations' section"
    )
    assert "Red Flags" in sections, f"{rel_path} missing '## Red Flags' section"


# --- v0.4.0 harvest: per-item wiring assertions ---


def test_definition_of_done_wired(skill_root: Path) -> None:
    """DoD reference exists and is wired into G5 (3.1), pre-G8 (4), and developer self-review."""
    dod = skill_root / "references" / "definition-of-done.md"
    assert dod.is_file(), "references/definition-of-done.md missing"
    text = dod.read_text(encoding="utf-8")
    for section in ("Correctness", "Quality", "Integration", "Documentation", "Ship-readiness"):
        assert section in text, f"definition-of-done.md missing '{section}'"
    assert "promote_into" in text, "DoD Ship-readiness must cite the promotion policy"
    assert "sensitive" in text.lower(), "DoD Ship-readiness must cover sensitive data"
    for rel in ("phases/3.1-test-review.md", "phases/4-acceptance.md", "roles/developer.md"):
        assert "definition-of-done" in (skill_root / rel).read_text(encoding="utf-8"), (
            f"{rel} must reference references/definition-of-done.md"
        )


def test_source_cited_convention_wired(skill_root: Path) -> None:
    """source-cited convention exists and is declared + referenced by the developer role."""
    conv = skill_root / "conventions" / "source-cited.md"
    assert conv.is_file(), "conventions/source-cited.md missing"
    dev = (skill_root / "roles" / "developer.md").read_text(encoding="utf-8")
    assert "conventions/source-cited.md" in dev, "developer.md frontmatter/body must reference source-cited"
    assert "UNVERIFIED" in conv.read_text(encoding="utf-8"), "source-cited must define the UNVERIFIED flag"


def test_orchestration_patterns_catalog(skill_root: Path) -> None:
    """Orchestration catalog has endorsed + anti-patterns, dispatch layer, and the G9 anchor."""
    text = (skill_root / "references" / "orchestration-patterns.md").read_text(encoding="utf-8")
    assert "Endorsed patterns" in text and "Anti-patterns" in text
    assert "adaptation/dispatch.md" in text, "must add dispatch.md as the cross-harness pattern"
    assert "G9" in text, "PM-only-orchestrator rule must be anchored against G9"
    # foreign single-harness tools must be dropped
    for foreign in ("OpenCode", "Kiro", "Antigravity"):
        assert foreign not in text, f"foreign tool '{foreign}' should not appear in the local catalog"


def test_pm_chunk_sizing_section(skill_root: Path) -> None:
    """pm.md carries the Chunk sizing section with the line-count norms + splitting strategies."""
    text = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    assert "## Chunk sizing" in text, "pm.md must have a Chunk sizing section"
    assert "~100" in text and "~1000" in text, "chunk sizing must state the line-count norms"
    for strat in ("Stack", "Horizontal", "Vertical"):
        assert strat in text, f"chunk sizing must list the '{strat}' splitting strategy"


def test_roast_framework_categorize_findings(skill_root: Path) -> None:
    """roast-framework gains the Categorize-findings prefix rule with the label set + ordering."""
    text = (skill_root / "references" / "roasting-shared" / "roast-framework.md").read_text(encoding="utf-8")
    assert "Categorize findings" in text, "roast-framework must add the Categorize-findings rule"
    for label in ("Critical", "Optional", "Nit", "FYI"):
        assert label in text, f"categorize rule must include the '{label}' prefix"
    assert "Lead with what matters" in text, "must include the lead-with-what-matters ordering rule"


def test_doubt_driven_development_content(skill_root: Path) -> None:
    """doubt-driven sub-skill documents the loop, complementary stance, and DEFERRED handshake."""
    text = (skill_root / "references" / "doubt-driven-development" / "SKILL.md").read_text(encoding="utf-8")
    for step in ("CLAIM", "EXTRACT", "DOUBT", "RECONCILE", "STOP"):
        assert step in text, f"doubt-driven must document the {step} step"
    assert "complementary" in text.lower() and "roasting" in text.lower(), (
        "must state it is complementary to (not a replacement for) roasting"
    )
    assert "DEFERRED" in text and "gemini-best" in text, (
        "cross-model handshake must be documented as deferred, naming gemini-best"
    )
    assert "warning, not a" in text.lower(), "must capture the availability-gated-warning requirement"


def test_preflight_review_phase_content(skill_root: Path) -> None:
    """phases/3.3 specifies the single-turn fan-out, security lens, and GO/NO-GO output."""
    text = (skill_root / "phases" / "3.3-preflight-review.md").read_text(encoding="utf-8")
    assert "GO" in text and "NO-GO" in text, "must emit a GO/NO-GO decision"
    assert "single" in text.lower() and "turn" in text.lower(), "must state the single-assistant-turn rule"
    assert "Rollback plan" in text, "GO/NO-GO block must include a rollback plan"
    assert "security" in text.lower(), "must include the inline security lens"


def test_deprecation_skill_content(skill_root: Path) -> None:
    """deprecating-a-system covers the required concepts and the product-code scoping."""
    text = (skill_root / "references" / "deprecating-a-system" / "SKILL.md").read_text(encoding="utf-8")
    for needle in ("Hyrum", "Strangler", "Adapter", "Feature flag", "Churn Rule", "Zombie"):
        assert needle in text, f"deprecation skill missing '{needle}'"
    assert "archive-never-delete" in text, "must distinguish product code from archive-never-delete artifacts"
    assert "product" in text.lower(), "must scope to product code, not superhuman's own artifacts"


# --- Publication readiness ---


def _publication_candidates(skill_root: Path) -> list[str]:
    """List tracked files subject to the publication scan.

    Args:
        skill_root: Repository root.

    Returns:
        Repo-relative paths.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=skill_root, capture_output=True, text=True, check=True
    ).stdout.split()
    return [rel for rel in tracked if is_scanned(rel)]


def test_no_infrastructure_leaks(skill_root: Path) -> None:
    """No shipped file may carry an IP, server path, ssh target, or key material.

    This is the guard that makes publication safe to repeat. Scrubbing once is
    not a property; a test is.
    """
    offenders: dict[str, set[str]] = {}
    for rel in _publication_candidates(skill_root):
        path = skill_root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label, _pos, _neg in LEAK_PATTERNS:
            if re.search(pattern, text):
                offenders.setdefault(rel, set()).add(label)

    detail = "\n".join(f"  {f}: {sorted(v)}" for f, v in sorted(offenders.items()))
    assert not offenders, "infrastructure details leaked into shipped files:\n" + detail


def test_operator_tokens_are_absent(skill_root: Path) -> None:
    """No shipped file may contain a token listed in `.publication-tokens`.

    Skips silently when the file is absent, which is the normal case for anyone
    who is not maintaining a private fork with its own vocabulary.
    """
    tokens_path = skill_root / TOKENS_FILE
    if not tokens_path.is_file():
        pytest.skip(f"no {TOKENS_FILE} — nothing operator-specific to check")

    tokens = [
        line.strip().lower()
        for line in tokens_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert tokens, f"{TOKENS_FILE} exists but lists no tokens"

    offenders: dict[str, list[str]] = {}
    for rel in _publication_candidates(skill_root):
        path = skill_root / rel
        if not path.is_file() or rel == TOKENS_FILE:
            continue
        lowered = path.read_text(encoding="utf-8", errors="replace").lower()
        hits = [t for t in tokens if t in lowered]
        if hits:
            offenders[rel] = hits

    detail = "\n".join(f"  {f}: {sorted(set(v))}" for f, v in sorted(offenders.items()))
    assert not offenders, (
        "operator-specific strings leaked into shipped files — this belongs in "
        "~/.superhuman/profile.yaml, not in the skill:\n" + detail
    )


def test_license_and_notice_present(skill_root: Path) -> None:
    """A publishable repo must carry both a LICENSE and upstream attribution."""
    licence = skill_root / "LICENSE"
    notice = skill_root / "NOTICE.md"
    assert licence.is_file(), "LICENSE missing — required before publication"
    assert "MIT" in licence.read_text(encoding="utf-8")
    assert notice.is_file(), "NOTICE.md missing — upstream attribution is required"
    text = notice.read_text(encoding="utf-8")
    assert "MIT" in text and "references/" in text, (
        "NOTICE.md must state the upstream licence and which paths it covers"
    )
