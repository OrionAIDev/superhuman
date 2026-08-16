# Requirements: Fidelity + first-run provider setup

**Created:** 2026-08-15
**Last revision:** 2026-08-15
**Source vision:** `VISION.md`

## Functional requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| **FR-1** | The `SUPERHUMAN.md` template (`templates/SUPERHUMAN.md.tpl`) carries a `## Resume packet` section positioned above the volatile logs, holding the seven handoff fields: objective, immutable constraints, decisions-locked (pointer to the locked list), ruled-out paths, current state, next-3-actions, evidence-pointers. | Template renders a Resume packet with all seven labelled fields; a resuming PM reads it first. Field set matches shared-core §5 exactly. |
| **FR-2** | The Resume packet is a **kept-current** artifact: the PM refreshes it at every gate (not append-only), and the resume path in `SKILL.md`/`roles/pm.md` reads the packet before reconstructing from logs. | `SKILL.md` (or `roles/pm.md`) documents the refresh-at-each-gate obligation and the read-packet-first resume order. Packet is prose-pruned (evidence by path, not pasted). |
| **FR-3** | A single canonical **subagent return schema** — conclusion → evidence → commands → assumptions → risks → next-action — is defined in exactly one authoritative location (a `conventions/` doc), provider- and role-neutral. | One canonical schema doc exists; the six ordered fields are named and defined once. No competing full definitions elsewhere. |
| **FR-4** | Every role in `roles/*.md` emits the canonical schema. Role-specific verdicts (QA/Tester PASS/FAIL, Surrogate ACCEPT/ESCALATE, Architect option-tables) are expressed as **specializations layered on** the canonical schema, not replacements for it. | Each of pm, architect, developer, qa, tester, business-expert, surrogate-user references the canonical schema by pointer; existing specialized verdicts are reconciled as extensions (e.g. verdict rides in `conclusion`). No role silently omits it. |
| **FR-5** | The PM's output-discipline accepts the canonical schema and continues to reject free-form prose from subagents. | `roles/pm.md` output-discipline names the canonical schema as the accepted shape; rejection-of-prose rule preserved. |
| **FR-6** | A first-class **"Decisions locked — do not relitigate"** construct exists in `SUPERHUMAN.md`, structurally distinct from the append-only `## Decisions log`. | Template has a dedicated locked-decisions section with its own semantics; a reader can tell "what happened" (Decisions log) from "what may not be reopened" (locked list) at a glance. |
| **FR-7** | Locked decisions are honored by the orchestration semantics: the PM resume path and drift watch treat a locked decision as not-to-be-reopened, and reopening one is itself a surfaced event, not a silent edit. | `SKILL.md`/`roles/pm.md` documents that locked decisions are not relitigated on resume; changing one requires an explicit surfaced action (gate/drift entry). |
| **FR-8** | First-run setup (Phase 0 kickoff / init) **elicits the operator's provider subscriptions/APIs** and, per capability tier (most-capable / standard / cheap), a **primary and a fallback** provider·model. | Kickoff has an elicitation step covering the three tiers × {primary, fallback}; question set is provider-neutral (no vendor pre-filled as the answer). |
| **FR-9** | Init **populates the operator's `~/.superhuman/profile.yaml` `models:` tier map** from the elicited stack (creating the file/section if absent), in the alias-based shape the dispatch layer already reads. | After setup, `profile.yaml` `models:` reflects the operator's answers; the dispatch layer resolves tiers from it without further edits. Writing is via code, not LLM free-text (dev-principle #5-adjacent: config generation is deterministic). |
| **FR-10** | Shipped defaults contain **no Anthropic-first / vendor-specific assumption**. If the operator declines or defers elicitation, the result is a provider-neutral placeholder that fails safe (prompts later) rather than silently assuming a vendor. | Grep of shipped setup/template defaults finds no concrete vendor baked in as the default tier occupant; the decline path yields a neutral, self-documenting placeholder. |

## Non-functional requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| **NFR-1** | **Provider/harness-agnostic** across every shipped file touched. Concrete vendor names appear only as *illustrative examples clearly marked as such*, never as defaults or required values. | A repo grep for vendor names in the changed files shows each occurrence is an explicit example, not a default. Matches the locked immutable constraint. |
| **NFR-2** | **Backward-compatible resume.** A pre-existing project whose `SUPERHUMAN.md` predates the Resume-packet / locked-decisions sections still resumes without error; missing new sections degrade gracefully (treated as empty), never as corruption. | A fixture SUPERHUMAN.md without the new sections resolves through the resume path with no error; absence ≠ failure. |
| **NFR-3** | The fast-test gate stays green; each new behavior (template shape, schema presence, provider-map generation) has a test or deterministic check. | `pytest tests/` passes; new tests cover FR-1/3/6/9 at minimum. |
| **NFR-4** | Skill hygiene: `VERSION` bumped and `CHANGELOG.md` updated, per the skill's own bump rule (any change to role prompts, phase recipes, gate semantics, templates, or orchestrator). | VERSION incremented; CHANGELOG has a dated entry naming the #165/#139 changes. |
| **NFR-5** | Deployment ceiling is **OrionTest**; no UAT/Prod promotion applies to superhuman itself. No change requires an R8 UAT gate. | Delivery targets a branch → PR → main; no UAT step in the plan. |
| **NFR-6** | No AI/provider attribution in commits, PR, or docs; role names only (Trapezia CEO, Project Manager). | Commit/PR/doc text contains no AI/model/provider signature; authorship reads as role-based. |

## Out-of-scope (explicit)

- No change to the phase/gate *count* or HARD-GATE semantics (inherited from VISION).
- No new provider *integrations* or live model shell-outs; #139 is elicitation + deterministic profile generation, not runtime routing changes.
- No change to the model-routing *mechanism* in `adaptation/dispatch.md` beyond what consuming the elicited profile requires (the primary→fallback tier structure already exists there).
- No deployment past OrionTest.

## Assumptions

- **A1.** `~/.superhuman/profile.yaml` is the established location the skill already reads for tier→model mapping; #139 writes to that same file/shape. (If false → REVISIT: init needs to define the location.)
- **A2.** The dispatch/adaptation layer already models per-tier primary→fallback (confirmed in `adaptation/dispatch.md` OpenClaw table); #139 feeds it, does not rebuild it.
- **A3.** The canonical return schema can wrap existing specialized verdicts without breaking the PM's current parsing of QA/Tester/Surrogate outputs (verdict becomes the `conclusion` field's content). (If false → REVISIT: schema design in Phase 2.)
- **A4.** Backward-compat only needs to cover *resume-without-error*, not auto-migration of old SUPERHUMAN.md files into the new sections.

## Open questions

- **OQ-1 (→ Design).** Enforcement strength of FR-3/FR-4: is the return schema an **advisory documented contract**, or an **enforced parseable block** every role must emit (and the PM validates)? Recommendation to be made by Architect.
- **OQ-2 (→ Design).** Where "decisions locked" (FR-7) is enforced: **template-only** (convention), or also a **soft check** in the PM resume path / drift watch. 
- **OQ-3 (→ Design).** #139 elicitation mechanism: an **interactive G1 sub-flow** in `phases/0-kickoff.md`, a **separate init script/helper**, or both; and how it behaves on a harness where `profile.yaml` does not yet exist.
- **OQ-4 (→ Design).** Resume packet vs existing front-matter/logs: which fields **reference** existing state (e.g. decisions-locked → the locked list; current state → chunk log) vs **restate**, to avoid duplication drift.
- **OQ-5 (→ Design).** Whether FR-10's decline/defer path should also surface a one-line warning at dispatch time when a tier is still an unfilled placeholder.

## Domain context (if applicable)

Not applicable — this is superhuman's own dev-tooling substrate, not an external business/regulated domain. No Business Expert consulted.
