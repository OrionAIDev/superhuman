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


RESUME_PACKET_FIELDS = [
    "objective",
    "immutable constraints",
    "decisions-locked",
    "ruled-out paths",
    "current state",
    "next-3-actions",
    "evidence-pointers",
]


def test_resume_packet_section_present_and_ordered(skill_root: Path) -> None:
    """SUPERHUMAN.md template carries a Resume packet above the volatile logs (FR-1).

    The Resume packet must appear before the append-only log sections (Decisions
    log, Chunk log, Drift notes, ...) so a resuming session reads the kept-current
    packet first. All seven labelled fields must be present.
    """
    text = (skill_root / "templates" / "SUPERHUMAN.md.tpl").read_text(encoding="utf-8")
    assert "## Resume packet" in text, "template missing '## Resume packet' section"

    resume_pos = text.find("## Resume packet")
    for heading in ("## Decisions log", "## Chunk log", "## Drift notes"):
        heading_pos = text.find(heading)
        assert heading_pos != -1, f"template missing '{heading}' section"
        assert resume_pos < heading_pos, (
            f"'## Resume packet' must appear before '{heading}'"
        )

    for field in RESUME_PACKET_FIELDS:
        assert field in text, f"Resume packet missing field label '{field}'"


def test_decisions_locked_distinct_from_decisions_log(skill_root: Path) -> None:
    """'## Decisions locked' is a distinct H2 from the append-only '## Decisions log' (FR-6).

    Both sections must coexist: Decisions locked records what may not be
    reopened; Decisions log records what happened. One does not replace the
    other.
    """
    text = (skill_root / "templates" / "SUPERHUMAN.md.tpl").read_text(encoding="utf-8")
    sections = _h2_sections(text)
    assert "Decisions locked" in sections, "template missing '## Decisions locked' section"
    assert "Decisions log" in sections, "template missing '## Decisions log' section"


def test_orch_documents_read_packet_first_and_refresh(skill_root: Path) -> None:
    """SKILL.md and/or pm.md document read-packet-first resume + refresh-at-each-gate (FR-2).

    On resume, the PM must read the '## Resume packet' FIRST as the single
    always-current entry point before reconstructing from the logs; the PM must
    also refresh (keep current) the packet at every gate — not merely append to
    it. This extends the existing resume path (HARD-GATE step 1 in SKILL.md); it
    does not replace it.
    """
    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    pm_text = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    combined = skill_text + "\n" + pm_text
    lower = combined.lower()

    assert "resume packet" in lower, (
        "SKILL.md and/or pm.md must reference the '## Resume packet' section on resume"
    )
    assert "first" in lower and "resume packet" in lower, (
        "must document reading the Resume packet FIRST on resume"
    )
    assert "refresh" in lower, (
        "must document that the PM refreshes the Resume packet at every gate"
    )
    assert "kept-current" in lower or "kept current" in lower, (
        "must state the packet is kept-current (not append-only) at every gate"
    )


def test_orch_documents_locked_not_relitigated(skill_root: Path) -> None:
    """SKILL.md and/or pm.md document locked-decisions-not-relitigated semantics (FR-7).

    A decision in '## Decisions locked' must not be relitigated/reopened on
    resume; changing one requires an explicit surfaced action (a gate or a
    drift entry) — never a silent edit. This is distinct from the append-only
    '## Decisions log'.
    """
    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    pm_text = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    combined = skill_text + "\n" + pm_text
    lower = combined.lower()

    assert "decisions locked" in lower, (
        "SKILL.md and/or pm.md must reference the '## Decisions locked' section"
    )
    assert "relitigat" in lower or "reopen" in lower, (
        "must document that locked decisions are not relitigated/reopened on resume"
    )
    assert "silent edit" in lower, (
        "must document that changing a locked decision is never a silent edit"
    )
    assert "drift" in lower and ("gate" in lower), (
        "must document that changing a locked decision requires a surfaced gate/drift event"
    )


def test_backward_compat_fixture_resumes_without_error(skill_root: Path) -> None:
    """A pre-existing SUPERHUMAN.md lacking the new sections still resumes cleanly (NFR-2).

    Resume is prose/PM-judgment — there is no resume script to call — so this is
    a presence assertion on the documented rule plus a durable regression-anchor
    fixture. The fixture has a structurally VALID '## Decisions log' (the
    HARD-GATE validity check would pass) but lacks both '## Resume packet' and
    '## Decisions locked'. SKILL.md and/or pm.md must explicitly document that
    absence of the new sections is treated as empty, never as corruption.
    """
    fixture_path = skill_root / "tests" / "fixtures" / "superhuman_legacy_no_resume_packet.md"
    assert fixture_path.is_file(), "missing backward-compat fixture superhuman_legacy_no_resume_packet.md"
    fixture_text = fixture_path.read_text(encoding="utf-8")

    # Fixture has a valid Decisions log per the HARD-GATE rule (G<digit> + 'user decision:').
    assert "## Decisions log" in fixture_text
    assert re.search(r"G\d+:.*user decision:", fixture_text), (
        "fixture must contain a G<n> entry with a 'user decision:' field so the "
        "HARD-GATE validity check would pass"
    )
    # Fixture genuinely lacks the two new sections.
    assert "## Resume packet" not in fixture_text
    assert "## Decisions locked" not in fixture_text

    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    pm_text = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    combined_lower = (skill_text + "\n" + pm_text).lower()

    assert "not as corruption" in combined_lower or "never as corruption" in combined_lower, (
        "SKILL.md and/or pm.md must state that absent new sections are treated as "
        "empty, not corruption"
    )
    assert "treated as empty" in combined_lower, (
        "SKILL.md and/or pm.md must explicitly say the absent sections are 'treated as empty'"
    )


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


# --- C-ROLES: canonical return-schema threaded through all roles ---

CANONICAL_SCHEMA_ROLE_FILES = [
    "pm.md", "architect.md", "developer.md", "qa.md",
    "tester.md", "business-expert.md", "surrogate-user.md",
]


@pytest.mark.parametrize("role_file", CANONICAL_SCHEMA_ROLE_FILES)
def test_role_references_canonical_schema(skill_root: Path, role_file: str) -> None:
    """Every role points at the canonical six-field return schema by pointer (FR-4)."""
    text = (skill_root / "roles" / role_file).read_text(encoding="utf-8")
    assert "conventions/subagent-return-schema.md" in text, (
        f"{role_file} must reference conventions/subagent-return-schema.md"
    )


def test_verdict_schemas_specialize_canonical_conclusion(skill_root: Path) -> None:
    """Existing role verdict schemas are retained, reconciled as conclusion specializations (FR-4/A3).

    A3: rip-and-replace is forbidden. QA/Tester keep approved|issues_found, Surrogate keeps
    ACCEPT|ESCALATE, Architect keeps its option-table output — each must additionally say its
    verdict rides in / specializes the canonical schema's `conclusion` field.
    """
    qa = (skill_root / "roles" / "qa.md").read_text(encoding="utf-8")
    tester = (skill_root / "roles" / "tester.md").read_text(encoding="utf-8")
    surrogate = (skill_root / "roles" / "surrogate-user.md").read_text(encoding="utf-8")
    architect = (skill_root / "roles" / "architect.md").read_text(encoding="utf-8")

    for name, text in (("qa.md", qa), ("tester.md", tester)):
        assert "approved" in text and "issues_found" in text, (
            f"{name} must retain its approved|issues_found verdict"
        )
        low = text.lower()
        assert "conclusion" in low or "specializ" in low, (
            f"{name} must note its verdict rides in / specializes the canonical conclusion field"
        )

    assert "ACCEPT" in surrogate and "ESCALATE" in surrogate, (
        "surrogate-user.md must retain its ACCEPT|ESCALATE verdict"
    )
    low = surrogate.lower()
    assert "conclusion" in low or "specializ" in low, (
        "surrogate-user.md must note its verdict rides in / specializes the canonical conclusion field"
    )

    assert "option" in architect.lower() and "recommend" in architect.lower(), (
        "architect.md must retain its option-table output"
    )
    low = architect.lower()
    assert "conclusion" in low or "specializ" in low, (
        "architect.md must note its recommendation rides in / specializes the canonical conclusion field"
    )


def test_pm_output_discipline_names_canonical_schema(skill_root: Path) -> None:
    """pm.md Output discipline names the canonical schema and keeps the prose-rejection rule (FR-5)."""
    text = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")
    sections = _h2_sections(text)
    assert "Output discipline" in sections, "pm.md missing '## Output discipline' section"

    start = text.find("## Output discipline")
    end = text.find("\n## ", start + 1)
    body = text[start:end if end != -1 else len(text)]

    assert "conventions/subagent-return-schema.md" in body, (
        "pm.md Output discipline must reference conventions/subagent-return-schema.md "
        "as the accepted shape for subagent returns"
    )
    assert "free-form prose" in body.lower() or "free form prose" in body.lower(), (
        "pm.md Output discipline must keep its existing free-form-prose rejection rule"
    )


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


RETURN_SCHEMA_FIELDS = ["conclusion", "evidence", "commands", "assumptions", "risks", "next-action"]


def test_return_schema_doc_defines_six_fields_once(skill_root: Path) -> None:
    """conventions/subagent-return-schema.md is the sole full six-field definition (C-RS/FR-3).

    The doc names conclusion -> evidence -> commands -> assumptions -> risks -> next-action,
    in that order, exactly once. Every other shipped markdown file may point at the doc but
    must not redefine the full ordered set itself — a light heuristic (all six labels present
    as a defined list) is enough to catch a second competing definition.
    """
    conv = skill_root / "conventions" / "subagent-return-schema.md"
    assert conv.is_file(), "conventions/subagent-return-schema.md missing"
    text = conv.read_text(encoding="utf-8")

    positions = [text.find(f"**{field}**") for field in RETURN_SCHEMA_FIELDS]
    assert all(p != -1 for p in positions), (
        f"subagent-return-schema.md must define all six fields as bold labels: {RETURN_SCHEMA_FIELDS}"
    )
    assert positions == sorted(positions), (
        "the six fields must appear in canonical order: "
        + " -> ".join(RETURN_SCHEMA_FIELDS)
    )

    offenders: list[str] = []
    for path in skill_root.rglob("*.md"):
        if path == conv:
            continue
        rel = path.relative_to(skill_root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        other_text = path.read_text(encoding="utf-8", errors="replace")
        if all(f"**{field}**" in other_text for field in RETURN_SCHEMA_FIELDS):
            offenders.append(str(rel))
    assert not offenders, (
        "these files redefine the full six-field schema instead of pointing at "
        "conventions/subagent-return-schema.md:\n  " + "\n  ".join(offenders)
    )


# --- C-KICK: #139 elicitation sub-flow ---

#: Vendor/model name fragments that must never appear as a pre-filled default
#: answer in the kickoff elicitation (NFR-1, provider-neutral). A vendor name
#: may still appear as a clearly-marked illustrative example.
_VENDOR_NAME_FRAGMENTS = (
    "anthropic", "claude", "opus", "sonnet", "haiku",
    "openai", "chatgpt", "gpt-",
    "google", "gemini",
    "meta", "llama",
    "mistral", "cohere", "grok", "xai",
)

#: Markers that make a vendor mention a clearly-marked example rather than a
#: pre-filled default (TC-14).
_EXAMPLE_MARKERS = ("e.g.", "for example", "such as")


def test_kickoff_elicits_three_tiers_primary_fallback(skill_root: Path) -> None:
    """Kickoff Step 3 elicits primary+fallback provider*model for all 3 tiers.

    FR-8: the elicitation must cover all three capability tiers
    (`most_capable`, `standard`, `cheap`) crossed with primary AND fallback,
    invoke the deterministic writer (dev-principle #5 — elicit is inference,
    write is code), and never pre-fill a concrete vendor/model as *the*
    answer. A vendor name may appear only inside a clearly-marked example.
    """
    text = (skill_root / "phases" / "0-kickoff.md").read_text(encoding="utf-8")
    lower = text.lower()

    for tier in ("most_capable", "standard", "cheap"):
        assert tier in text, f"kickoff must name model tier '{tier}'"
    assert "primary" in lower and "fallback" in lower, (
        "kickoff elicitation must ask for both a primary and a fallback per tier"
    )
    assert "write_models_block" in text, (
        "kickoff must hand elicited answers to superhuman_profile.write_models_block "
        "(config generation is code, not LLM free-text — dev-principle #5)"
    )

    for lineno, line in enumerate(text.splitlines(), 1):
        line_lower = line.lower()
        for vendor in _VENDOR_NAME_FRAGMENTS:
            idx = line_lower.find(vendor)
            if idx == -1:
                continue
            window = line_lower[max(0, idx - 80):idx]
            assert any(m in window for m in _EXAMPLE_MARKERS), (
                f"0-kickoff.md:{lineno}: vendor fragment {vendor!r} appears without a "
                f"clearly-marked example prefix (e.g./for example/such as): {line.strip()!r}"
            )


def test_kickoff_decline_path_is_neutral_and_fails_safe(skill_root: Path) -> None:
    """Decline/defer path is documented and names the neutral placeholder (FR-10).

    Consistent with C-PROF's MODEL_PLACEHOLDER ("PROMPT_ME"): declining or
    deferring the elicitation must never fall back to a vendor assumption, and
    must not block kickoff from proceeding.
    """
    text = (skill_root / "phases" / "0-kickoff.md").read_text(encoding="utf-8")
    lower = text.lower()

    assert "decline" in lower or "defer" in lower, (
        "kickoff must document the decline/defer path for the model-tier elicitation"
    )
    assert "PROMPT_ME" in text, (
        "kickoff must name the neutral placeholder (PROMPT_ME) written on decline/defer"
    )
    assert "fail" in lower and "safe" in lower, (
        "kickoff must state that declining fails safe rather than assuming a vendor"
    )
    assert "first run" in lower or "first-run" in lower, (
        "kickoff must scope the elicitation to first run / unset profile tiers, "
        "not every project kickoff"
    )


# --- C-DISP: dispatch-time placeholder warning ---


def test_dispatch_documents_placeholder_warning(skill_root: Path) -> None:
    """Dispatch-time placeholder warning is documented in both consumers (OQ-5, FR-10).

    When a dispatch's resolved tier is still C-PROF's unfilled placeholder
    (``PROMPT_ME``) in the operator's profile, the PM must emit a one-line
    warning naming the tier and PROCEED — never pause or gate. The rule must
    be documented in both `adaptation/dispatch.md` (the model-selection
    mechanism) and `roles/pm.md` (the PM behavior), and explicitly
    characterized as Type B (notification, non-blocking).
    """
    dispatch_text = (skill_root / "adaptation" / "dispatch.md").read_text(encoding="utf-8")
    pm_text = (skill_root / "roles" / "pm.md").read_text(encoding="utf-8")

    for label, text in (("adaptation/dispatch.md", dispatch_text), ("roles/pm.md", pm_text)):
        lower = text.lower()
        assert "prompt_me" in lower, (
            f"{label} must name the unfilled placeholder (PROMPT_ME) the warning keys on"
        )
        assert "one-line" in lower or "one line" in lower, (
            f"{label} must characterize the warning as one-line"
        )
        assert "type b" in lower or "type-b" in lower, (
            f"{label} must characterize the warning as Type B (notification, no pause)"
        )
        assert "non-blocking" in lower or "does not" in lower or "not a gate" in lower, (
            f"{label} must state the warning does not pause or gate autonomous progression"
        )

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


# --- C-HYG: hygiene — VERSION/CHANGELOG/README (TC-17, TC-18) ---

#: Narrower than `_VENDOR_NAME_FRAGMENTS` above. TC-14 scans a single
#: provider-neutral elicitation doc where even bare "google"/"meta"/"claude"
#: are suspect. TC-17 scans a much wider file set that legitimately says
#: "Claude Code" (the harness name), "Google-style docstrings" (a style
#: name), "meta-gate" (an unrelated PM concept), etc. — bare substrings there
#: would be false positives unrelated to NFR-1. TC-17 instead matches actual
#: model-FAMILY product names, word-bounded: that is what the immutable
#: constraint (LD-1) cares about — a concrete *model* baked in as a default,
#: not the word "Google" or "Claude" appearing inside an unrelated compound
#: term or the harness's own product name.
_TC17_VENDOR_TOKENS = (
    "opus", "sonnet", "haiku",
    r"gpt-\d", "chatgpt",
    "gemini",
    "llama",
    "mistral", "cohere", "grok", "xai",
)
_TC17_TOKEN_RE = re.compile(r"\b(?:" + "|".join(_TC17_VENDOR_TOKENS) + r")\b", re.IGNORECASE)

#: In addition to `_EXAMPLE_MARKERS` (e.g./for example/such as), these phrases
#: also clearly mark a vendor mention as illustrative/non-default across the
#: wider TC-17 file set: "go stale" (SKILL.md/README.md's staleness warning
#: about concrete model names), "or your harness's alias" (the profile.yaml
#: snippet's own inline comment marking the value as swappable), "legacy"
#: (the ADR-6 bare-string-normalization docstring example), and
#: "forbidden"/"do not reliably honor" (the PM-tier denylist, which names
#: what NOT to use — structurally the opposite of a default).
_TC17_EXTRA_MARKERS = (
    "go stale",
    "or your harness's alias",
    "legacy",
    "forbidden",
    "do not reliably honor",
    "does not reliably honor",
    "not reliably honor",
)

#: The full set of files this project (#165 fidelity + #139 provider setup)
#: created or modified, per PLAN.md's "File structure" list — minus
#: `adaptation/dispatch.md`, excluded for the reason below, and
#: `CHANGELOG.md`, handled separately because only its NEW section is this
#: project's content (see the docstring).
_TC17_FILES = (
    "conventions/subagent-return-schema.md",
    "templates/SUPERHUMAN.md.tpl",
    "roles/pm.md",
    "roles/architect.md",
    "roles/developer.md",
    "roles/qa.md",
    "roles/tester.md",
    "roles/business-expert.md",
    "roles/surrogate-user.md",
    "SKILL.md",
    "scripts/superhuman_profile.py",
    "phases/0-kickoff.md",
    "VERSION",
    "README.md",
)

#: README.md subsections documenting Claude-Code-native config, pre-existing
#: since v0.1.3. Claude Code is single-provider (`adaptation/dispatch.md`:
#: "no fallback path") and its `settings.json` literally only accepts
#: Anthropic's own shortnames — this is harness-specific MECHANISM, not a
#: superhuman-authored default, exactly analogous to the
#: `adaptation/dispatch.md` tier table this test excludes outright below.
#: Relitigating either is out of this project's charter.
_TC17_EXCLUDED_README_HEADINGS = ("### Claude Code config",)


def _tc17_strip_excluded_readme_sections(text: str) -> str:
    """Remove `_TC17_EXCLUDED_README_HEADINGS` subsections from README.md text.

    Args:
        text: full README.md contents.

    Returns:
        The text with each excluded H3 subsection (heading through the line
        before the next H2/H3 heading) removed.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if stripped in _TC17_EXCLUDED_README_HEADINGS:
            skipping = True
            continue
        if skipping and (stripped.startswith("## ") or stripped.startswith("### ")):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


def _tc17_scan(label: str, text: str, offenders: list[str]) -> None:
    """Scan text for unmarked vendor/model defaults and record offenders.

    Args:
        label: file label used in the offender message.
        text: text to scan.
        offenders: list to append `"label:pos: detail"` strings to.
    """
    for match in _TC17_TOKEN_RE.finditer(text):
        start, end = match.span()
        window = text[max(0, start - 160):end + 80].lower()
        if any(m in window for m in _EXAMPLE_MARKERS) or any(
            m in window for m in _TC17_EXTRA_MARKERS
        ):
            continue
        lineno = text.count("\n", 0, start) + 1
        offenders.append(f"{label}:{lineno}: {match.group(0)!r} not clearly marked as an example")


def test_no_vendor_baked_as_default_in_changed_files(skill_root: Path) -> None:
    """No concrete vendor/model name is baked in as an unmarked default (TC-17).

    Full-project grep gate over every file this project (#165 fidelity +
    #139 provider setup) created or modified, per NFR-1/FR-10/LD-1: a vendor
    name may appear only as a clearly-marked illustrative example, never as
    *the* default/required value in a template, generator, or elicitation.

    Scoping decisions (documented per the chunk-8 spec's explicit ask):

    - `adaptation/dispatch.md` is excluded entirely. Its "Tier -> model
      (Claude Code -- Anthropic only)" table is pre-existing, single-harness
      MECHANISM -- the concrete model aliases Claude Code itself resolves a
      tier to on a single-provider harness with "no fallback path" -- not a
      superhuman-authored default. `test_dispatch_documents_placeholder_warning`
      (TC-16) already covers the content this project actually added to that
      file (the placeholder-warning rule). Re-scanning the harness table
      would relitigate a pre-existing, intentionally concrete, per-harness
      fact table outside this project's charter.
    - `CHANGELOG.md` is checked only for the section this chunk adds (the
      dated `## [1.1.0]` release notes), not the file's full history.
      Historical dated entries document real past incidents (e.g. a prior
      smoke run's model choice) and are factual record, not a default this
      project proposes -- scanning them would produce noise unrelated to
      NFR-1.
    - Within `README.md`, the pre-existing (v0.1.3) "### Claude Code config"
      subsection is excluded for the same single-harness-mechanism reason as
      `adaptation/dispatch.md` (see `_TC17_EXCLUDED_README_HEADINGS`).
    - The vendor-token list (`_TC17_VENDOR_TOKENS`) is deliberately narrower
      than `_VENDOR_NAME_FRAGMENTS` (used by TC-14 against a single
      provider-neutral doc): bare "claude"/"google"/"meta" collide with this
      wider file set's legitimate, unrelated prose ("Claude Code" the
      harness, "Google-style docstrings", "meta-gate") and would be noise,
      not signal, for the "baked in as a default" question this test asks.
    """
    offenders: list[str] = []

    for rel in _TC17_FILES:
        path = skill_root / rel
        assert path.is_file(), f"expected shipped file missing: {rel}"
        text = path.read_text(encoding="utf-8", errors="replace")
        if rel == "README.md":
            text = _tc17_strip_excluded_readme_sections(text)
        _tc17_scan(rel, text, offenders)

    changelog_text = (skill_root / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(
        r"^## \[1\.1\.0\].*?(?=^## \[|\Z)", changelog_text, re.MULTILINE | re.DOTALL
    )
    assert section, "CHANGELOG.md must have a ## [1.1.0] release section (see TC-18)"
    _tc17_scan("CHANGELOG.md (## [1.1.0] section)", section.group(0), offenders)

    detail = "\n  ".join(offenders)
    assert not offenders, (
        "vendor/model name(s) appear as an unmarked default in shipped files:\n  " + detail
    )


def test_changelog_entry_names_165_and_139(skill_root: Path) -> None:
    """VERSION is bumped and CHANGELOG.md documents both #165 and #139 (TC-18, NFR-4/NFR-6).

    VERSION must be a valid semver strictly greater than the pre-project
    value (1.0.3); CHANGELOG.md must carry a dated release entry naming both
    roadmap items; and that entry must carry no AI/model/provider
    attribution — role names only, per NFR-6.
    """
    version_text = (skill_root / "VERSION").read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-[A-Za-z0-9.-]+)?", version_text)
    assert match, f"Invalid semver: {version_text}"
    assert tuple(int(g) for g in match.groups()[:3]) > (1, 0, 3), (
        f"VERSION must be bumped above the pre-project value 1.0.3, got {version_text}"
    )

    changelog_text = (skill_root / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(
        r"^## \[" + re.escape(version_text) + r"\].*? - \d{4}-\d{2}-\d{2}.*?(?=^## \[|\Z)",
        changelog_text,
        re.MULTILINE | re.DOTALL,
    )
    assert section, (
        f"CHANGELOG.md must have a dated '## [{version_text}] - YYYY-MM-DD' entry matching VERSION"
    )
    entry = section.group(0)

    assert "#165" in entry, "CHANGELOG entry must name #165 (fidelity)"
    assert "#139" in entry, "CHANGELOG entry must name #139 (provider setup)"

    entry_lower = entry.lower()
    for forbidden in (
        "anthropic", "claude", "openai", "chatgpt", "gpt-", "gemini",
        "generated with", "generated by", "co-authored-by", "written by claude",
    ):
        assert forbidden not in entry_lower, (
            f"CHANGELOG #165/#139 entry must carry no AI/model/provider attribution "
            f"(NFR-6) — found {forbidden!r}"
        )
