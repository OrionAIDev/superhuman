# Superhuman: Fidelity + first-run provider setup

**Slug:** fidelity-provider-setup
**Started:** 2026-08-15
**Superhuman-version:** 1.0.3
**Vision (one-liner):** Harden superhuman's own cross-session fidelity (#165) + make first-run setup elicit each operator's provider stack (#139), provider/harness-agnostic throughout.
**Cadence:** on-divergence
**Value-vs-foundation:** foundation-first
**Parallelism preference:** PM-decides
**Git:** remote
**Remote:** https://github.com/OrionAIDev/superhuman.git
**Branch strategy:** feature branch `feat/superhuman-fidelity-provider-setup` off main; PR into main; ceiling OrionTest
**Value definition:** A chunk is valuable when it lands a substrate change — a template section, the schema doc, a role pointer, profile-generation code, or a phase-recipe step — that a human can read and verify against a named FR, with a deterministic check asserting its presence.
**Chunking strategy:** foundation-first (8 chunks; substrate C1/C2/C5 before consumers C3/C4/C6/C7; C8 hygiene)
**Conventions in effect:** git
**HITL-level:** H
**Modifies-existing-code:** yes

## Declared artifacts
<!-- PM appends one line per declared artifact at G3 -->
- VISION.md (PM)
- REQUIREMENTS.md (PM)
- DESIGN.md (Architect)
- PLAN.md (PM)
- TEST.md (QA)
- README.md (PM, light touch)
- DECISIONS.md (Architect) — added at G3
- SUPERHUMAN.md (PM, automatic)
- conventions/subagent-return-schema.md (deliverable C1, not a project artifact)

## Decisions locked — do not relitigate
<!-- Dogfooding the FR-6 construct on this very project. Distinct from the append-only Decisions log
     below (which records WHAT happened); this records WHAT MAY NOT BE REOPENED. To change a locked
     item, surface it explicitly as a gate/drift event — never a silent edit. -->
- **LD-1 (immutable, from invocation):** Provider- and harness-AGNOSTIC throughout. No Anthropic-first / vendor-specific defaults in any shipped file. Vendor names only as clearly-marked examples. Not open for relitigation.
- **LD-2 (G0):** #139 elicitation depth = primary + fallback per tier (all three tiers).
- **LD-3 (G1):** HITL-H, on-divergence cadence, foundation-first — locked for project lifetime.
- **LD-4 (G3):** OQ-1..OQ-5 resolved as Option A (advisory-schema-at-PM-boundary; template+soft-semantics lock; elicit-inference/write-code split; reference-volatile/restate-4 packet; one-line dispatch warning). Reopening requires a G6 drift event.
- **LD-5 (project constraint):** Deployment ceiling = OrionTest; no UAT/R8 gate applies to superhuman itself.

## Decisions log
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <one-line summary>; user decision: <decision> -->
[2026-08-15] G0: VISION approved (fidelity #165 + provider-setup #139 as one agnostic project); user decision: approve & proceed. #139 elicitation depth = primary + fallback per tier.
[2026-08-15] G1: Workflow prefs set — HITL-H, on-divergence cadence, foundation-first, git=remote, parallelism=PM-decides; user decision: approve.
[2026-08-15] Pre-flight: fixed pre-existing pre-commit hook GIT_DIR-leak defect (scripts/git-hooks/pre-commit) that blocked all commits and had corrupted shared core.bare=true; repaired shared config; committed c6933b8. Orthogonal to #165/#139; user decision: fix hook source now.
[2026-08-15] G2: REQUIREMENTS approved (10 FR / 6 NFR; 5 OQ deferred to Design); user decision: approve & proceed.
[2026-08-15] G3: DESIGN approved — foundation-first, 8 chunks; artifact set = baseline + DECISIONS.md + light README; OQ-1..OQ-5 resolved as Option A; ARCHITECTURE.md ruled out; user decision: approve all + DECISIONS.md + light README.
[2026-08-15] G4: TEST.md approved (18 TC / all FR-NFR traced; git-as-backup; inference-eval ruled out; TC-17 vendor-grep gate); user decision: approve & proceed to implementation.
[2026-08-15] Foundation decision: role schema references (C3/C-ROLES) — rework if standalone would be significant because every role would reference a schema doc that does not yet exist. Decision: C1 (C-RS schema doc) precedes.
[2026-08-15] Foundation decision: read-packet-first semantics (C4/C-ORCH) — rework if standalone would be significant because the semantics describe template sections that must exist to be read. Decision: C2 (C-TPL template sections) precedes.
[2026-08-15] Foundation decision: #139 elicitation wiring (C6/C-KICK) — rework if standalone would be significant because the phase recipe invokes the deterministic generator; eliciting into LLM-written YAML would violate dev-principle #5. Decision: C5 (C-PROF generator) precedes.
[2026-08-15] Foundation decision: dispatch-time placeholder warning (C7/C-DISP) — rework if standalone would be minimal (one-line rule) but reads the shape C-PROF writes. Decision: sequence after C5/C6; not a hard foundation dependency.

## Chunk log
<!-- Append-only table. -->
| # | Title | Files | Dev model | Status | Started | Ended |
|---|---|---|---|---|---|---|
| 1 | Canonical return-schema convention doc (C-RS) | conventions/subagent-return-schema.md, tests/test_content.py, tests/test_structure.py | sonnet | done (361331e) | 2026-08-15 | 2026-08-15 |
| 2 | Resume packet + Decisions-locked template sections (C-TPL) | templates/SUPERHUMAN.md.tpl, tests/test_content.py | sonnet | done (9542ee9) | 2026-08-15 | 2026-08-15 |
| 3 | Thread canonical schema through all roles (C-ROLES) | roles/*.md (7) | _tbd_ | pending | | |
| 4 | Orchestration semantics + backward-compat (C-ORCH) | SKILL.md, roles/pm.md | _tbd_ | pending | | |
| 5 | Profile models: generator + schema normalization (C-PROF) | scripts/superhuman_profile.py | _tbd_ | pending | | |
| 6 | Phase-0 #139 elicitation sub-flow (C-KICK) | phases/0-kickoff.md, roles/pm.md | _tbd_ | pending | | |
| 7 | Dispatch-time placeholder warning (C-DISP) | adaptation/dispatch.md, roles/pm.md | _tbd_ | pending | | |
| 8 | Hygiene: VERSION + CHANGELOG + README (C-HYG) | VERSION, CHANGELOG.md, README.md | _tbd_ | pending | | |

## Drift notes
<!-- Append-only. Format: [<ISO timestamp>] Chunk <n>: <severity> — <one-line trigger>; action: <taken> -->

## Archive log
<!-- Append-only. Format: [<ISO timestamp>] archived <chunk> to archive/<dir>/; reason: <reason> -->

## Recommendation overrides
<!-- Append-only. Format: [<ISO timestamp>] G<n>: PM recommended <X>; user chose <Y>; reason: <if given> -->

## Retuning notes
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <observation about user pattern>; bias adjustment: <going-forward note> -->
[2026-08-15] G0/G1/G2: user approved PM recommendation as-is three times running (incl. the one flagged scope choice, taking the recommended option); bias adjustment: user trusts crisp recommend-first framing — keep gates tight, lead with a clear recommendation, avoid padding options the user is unlikely to want. Do not infer they want fewer gates (HITL-H is locked) — only fewer/tighter questions per gate.
[2026-08-15] G3: user approved the full design package + both flagged extras (DECISIONS.md, light README) as recommended (4th consecutive as-is approval); bias adjustment: pattern holds — continue leading with the recommended option and surfacing only genuine sub-choices. Watch for over-asking; the user has not overridden once.
