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

## Resume packet
<!-- KEPT-CURRENT (refreshed by PM at every gate), not append-only. Dogfoods the FR-1 construct on
     this very project. References volatile sections rather than restating them. -->
- **objective:** Harden superhuman's own cross-session fidelity (#165: Resume packet, canonical subagent return schema, first-class Decisions-locked) and make first-run setup elicit each operator's provider stack (#139), provider/harness-agnostic throughout.
- **immutable constraints:** LD-1 provider/harness-agnostic (no vendor defaults in shipped files); OrionTest deployment ceiling (no UAT/R8); role-not-AI attribution in commits/PR/docs. See `## Decisions locked`.
- **decisions-locked:** see `## Decisions locked` below (LD-1..LD-5).
- **ruled-out paths:** rip-and-replace of role verdict schemas (FR-4 requires *specialize*, not replace); a machine parser for the return schema (OQ-1 chose advisory-at-PM-boundary); LLM-written profile YAML (dev-principle #5 → deterministic code writer); baking a vendor default into #139 (LD-1).
- **current state:** Phase 3 implementation — chunks 1-4 of 8 DONE (C-RS return-schema doc, C-TPL template sections, C-ROLES role reconciliation, C-ORCH orchestration semantics). See `## Chunk log` (latest rows) and the G4 + G5 entries in `## Decisions log`. Branch `feat/superhuman-fidelity-provider-setup`, HEAD `79ad783`, pushed. Fast-test gate green.
- **next-3-actions:** (1) Chunk 5 — C-PROF: `models:` generator + `{primary,fallback}` normalization in `scripts/superhuman_profile.py` (the one real code chunk); (2) Chunk 6 — C-KICK: #139 elicitation sub-flow in `phases/0-kickoff.md`; (3) Chunks 7-8 (C-DISP dispatch warning, C-HYG VERSION/CHANGELOG/README) → Phase 3.2 docs-sync → 3.3 preflight → G7 → G8.
- **evidence-pointers:** `docs/superhuman/fidelity-provider-setup/{DESIGN,PLAN,TEST,DECISIONS}.md`; this file's `## Decisions log` + `## Chunk log`; `conventions/subagent-return-schema.md`.

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
[2026-08-15] G5 (on-divergence, Type B): chunks 1-4/8 landed — C-RS (361331e), C-TPL (9542ee9), C-ROLES (08b3e5f), C-ORCH (79ad783); each ✓ spec ✓ quality ✓ full-suite-green ✓ pushed; no drift. Chunk 4 (SKILL.md orchestrator semantics) PM-adversarially reviewed: HARD-GATE validity rule byte-for-byte intact, backward-compat handled in the resume path. Autonomous progression — no pause.
[2026-08-15] Session-limit interruption during first Chunk-3 dispatch (no changes made, clean working tree); re-dispatched cleanly. Recovery is why this project's own SUPERHUMAN.md now carries a Resume packet (dogfood + cold-restart resilience).
[2026-08-15] G5 (Chunk 5/8): C-PROF landed (c42d7ec) — write_models_block + {primary,fallback} normalization + PROMPT_ME placeholder; 100% branch on new fns; 258 tests pass; pushed.
[2026-08-15] G6 (moderate drift): write_models_block strips comments from an existing profile.yaml via full YAML round-trip; PM recommended Option A (targeted patch, safe primitive, no new dep); user decision: Option A. Fix dispatched as a follow-up to Chunk 5.
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
| 3 | Thread canonical schema through all roles (C-ROLES) | roles/*.md (7), tests/test_content.py | sonnet | done (08b3e5f) | 2026-08-15 | 2026-08-15 |
| 4 | Orchestration semantics + backward-compat (C-ORCH) | SKILL.md, roles/pm.md, tests/test_content.py, tests/fixtures/superhuman_legacy_no_resume_packet.md | sonnet | done (79ad783) | 2026-08-15 | 2026-08-15 |
| 5 | Profile models: generator + schema normalization (C-PROF) | scripts/superhuman_profile.py, tests/test_profile_onboarding.py, tests/fixtures/profile_with_comments_and_models.yaml | sonnet | done (c42d7ec; G6 fix 7b41d8d) | 2026-08-15 | 2026-08-15 |
| 6 | Phase-0 #139 elicitation sub-flow (C-KICK) | phases/0-kickoff.md, roles/pm.md | _tbd_ | pending | | |
| 7 | Dispatch-time placeholder warning (C-DISP) | adaptation/dispatch.md, roles/pm.md | _tbd_ | pending | | |
| 8 | Hygiene: VERSION + CHANGELOG + README (C-HYG) | VERSION, CHANGELOG.md, README.md | _tbd_ | pending | | |

## Drift notes
<!-- Append-only. Format: [<ISO timestamp>] Chunk <n>: <severity> — <one-line trigger>; action: <taken> -->

## Drift notes
[2026-08-15] Chunk 5: MODERATE — `write_models_block` (c42d7ec) full-round-trips profile.yaml via yaml.safe_dump, silently stripping ALL comments from an existing operator profile (incl. the ladder's load-bearing preset comments). Real regression risk for #139 setup on a pre-existing commented profile; against superhuman's fidelity ethos. Surfaced as G6 — decision pending (targeted-patch vs ruamel vs caller-guard). Chunk 6 (C-KICK) blocked on this decision. RESOLVED (7b41d8d): rewrote write_models_block as a targeted line-span splice; comments/ladder preserved byte-identical; 11 new tests incl. preservation against the real classic-3tier preset; 100% branch on write path. Chunk 6 unblocked.

## Archive log
<!-- Append-only. Format: [<ISO timestamp>] archived <chunk> to archive/<dir>/; reason: <reason> -->

## Recommendation overrides
<!-- Append-only. Format: [<ISO timestamp>] G<n>: PM recommended <X>; user chose <Y>; reason: <if given> -->

## Retuning notes
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <observation about user pattern>; bias adjustment: <going-forward note> -->
[2026-08-15] G0/G1/G2: user approved PM recommendation as-is three times running (incl. the one flagged scope choice, taking the recommended option); bias adjustment: user trusts crisp recommend-first framing — keep gates tight, lead with a clear recommendation, avoid padding options the user is unlikely to want. Do not infer they want fewer gates (HITL-H is locked) — only fewer/tighter questions per gate.
[2026-08-15] G3: user approved the full design package + both flagged extras (DECISIONS.md, light README) as recommended (4th consecutive as-is approval); bias adjustment: pattern holds — continue leading with the recommended option and surfacing only genuine sub-choices. Watch for over-asking; the user has not overridden once.
[2026-08-15] G4/G6: user again took the PM recommendation (approve TEST.md; G6 Option A). 6 gates, 0 overrides. Bias adjustment holds — but the G6 surface was CORRECT to raise despite the streak: it was a real data-integrity fork, not a rubber-stamp candidate. Lesson: the no-override streak is not a signal to stop surfacing genuine moderate+ drift; it's a signal the recommendations are well-calibrated. Keep surfacing real forks; keep trimming trivial ones.
