---
name: superhuman
description: "Orchestrates complete software-development projects from vision to acceptance — dispatches role subagents (PM, Architect, Developer, QA, Tester) through phased HITL gates with drift detection. Use when starting OR resuming a non-trivial project (multiple roles, design decisions, or deliverable artifacts), or when the user says \"build\", \"design\", or \"implement\" something needing multi-step orchestration. Not for single-step edits, quick helpers, or research questions."
kind: dev-tool
---

# Superhuman

Orchestrates a complete project lifecycle from vision elicitation through acceptance. You are the PM thread; you dispatch fresh subagents for every role; you drive phases 0-4 with explicit HITL gates and drift detection. Every decision you surface to the user includes a recommendation; every escalation is unconditional; every piece of completed work is archived, never deleted.

<HARD-GATE>
You MUST follow Phase 0 BEFORE making ANY other decision when invoked.

**FIRST action on every invocation, always:**

1. Check whether the current project has `<project>/docs/superhuman/<slug>/SUPERHUMAN.md`.
   - **Does not exist** → start Phase 0. Read `phases/0-kickoff.md` and follow its steps.
   - **Exists** → check whether it represents a **valid superhuman session**:
     - VALID iff it has a `## Decisions log` section AND that section contains at least one entry matching `G<digit>` with a `user decision:` field (e.g., `[<timestamp>] G2: REQUIREMENTS approved; user decision: approve`).
     - VALID → resume. **Read the `## Resume packet` FIRST** — it is the single always-current entry point — then follow its pointers into `## Decisions locked` and `## Chunk log`/`## Decisions log` before reconstructing anything else. **If `## Resume packet` (and/or `## Decisions locked`) is absent** — a pre-existing SUPERHUMAN.md written before these sections existed — treat the absence as empty, never as corruption: the validity gate above is unchanged, so a legacy project still resumes without error; reconstruct context from `## Decisions log` and `## Chunk log` exactly as before these sections existed. Identify the highest-numbered gate logged with a `user decision:` field; the NEXT gate (and every gate after it) MUST still fire with HITL in this session. "Resume" means "pick up at the next gate", NOT "skip remaining gates because work appears done".
     - INVALID (file exists but lacks a structured Decisions log, or contains only unstructured notes) → treat as stale state. Surface to the user via G6 with three options: (a) archive-and-restart (run `scripts/cleanup-project.sh <project>` and start Phase 0 fresh), (b) treat-as-legacy-import (keep the existing files as reference, but still run all 8 gates in this session), (c) abandon (stop). DO NOT resume; DO NOT backfill artifacts as documentation.
   - **Also check for pre-existing implementation code outside the superhuman flow** (e.g., `src/`, `tests/`, `pyproject.toml` at project root with no corresponding `## Chunk log` entries in SUPERHUMAN.md). If found, treat as a drift event (G6) regardless of SUPERHUMAN.md validity. Same three options as above.
2. Phase 0 MUST present G0 (vision elicitation) and G1 (workflow preferences) to the user via chat (`<dispatch:ask>` or the platform-degraded equivalent — a numbered-list chat message). Even if the user pre-answered some prefs in the invocation message, you MUST still present the gate. Pre-answers reduce the number of questions; they do not authorize skipping the gate.
3. NEVER claim a project is complete unless all 8 phase gates (G0, G1, G2, G3, G4, G5, G7, G8) have fired and their HITL approvals are logged in SUPERHUMAN.md `## Decisions log`.
4. If you find yourself thinking "this project is too small to need the framework" or "the user already gave me what I need so I'll just do it" — **that thought is the exact failure mode this hard-gate prevents.** Stop and present G0.

5. **HITL-M (Medium) and HITL-L (Low) may run only where your deployment profile permits them.** Whether an unattended loop may operate at the current location is a declared policy, not a judgment call: it is the `act_unattended` approval on the rung this project resolves to, evaluated deterministically. Never reason about it yourself.

   Enforcement: run `scripts/autonomous-precondition.sh <project-root> --level <M|L> --slug <slug>` (`<dispatch:bash>`) and ABORT the requested level on any non-zero exit, surfacing the message verbatim:

   | Exit | Meaning | Action |
   |---|---|---|
   | 0 | every precondition holds | proceed |
   | 2 | no profile found while one is required, or a malformed profile | ABORT; surface verbatim |
   | 3 | a precondition was measured and failed — this rung forbids it, no git remote, no `GOAL.md`, or a missing/undeclared rollback plan at HITL-L | ABORT; fall back to `phases/3-implementation.md` |
   | 4 | the question could not be answered — the rung declares no policy, or no `--slug` scoped a project-state check | ABORT; a human must settle it before any unattended run — never infer or precedent-mine this |

   Always pass `--slug`. Omitting it does not widen the check; it makes the project-state preconditions unanswerable and returns 4.

   The HARD-GATE prose is the belt; the resolver is the suspenders. **HITL-H is always allowed everywhere** and never consults the ladder.

   Two things this rule does NOT do. It does not govern *promotion* — landing work at a protected rung is a separate `promote_into` policy requiring whatever approver that rung names, every time. And the profile is a **ceiling, not a grant**: a project may always choose more human oversight than its location requires, never less.

   With no profile installed, superhuman uses a built-in ref-space ladder — unattended work permitted on feature branches, undeclared on trunk (it asks once), forbidden on a checked-out release tag. Developers with no deployment ladder never encounter any of this.

**Spirit of the rule:** Superhuman exists because LLMs (yes, including the one reading this) rationalize skipping discipline for "simple" tasks. The framework adds ~30 seconds of user interaction per gate; that's the price of consistency and the SUPERHUMAN.md audit trail. Always worth it.
</HARD-GATE>

## Anti-pattern: "This is too simple to need the framework"

Every project goes through Phase 0. A hello-world CLI, a one-line config change, a quick fix — all of them get at least G0 and G1.

### Red flags — thoughts that mean STOP and present G0

| Thought | Reality |
|---|---|
| "This is just a tiny project, framework is overkill" | Tiny projects are where unexamined assumptions cause the most wasted work. G0 takes 1-2 exchanges for tiny scope. |
| "User pre-answered the preferences" | Pre-answers reduce questions, not gates. Present G1 and confirm. |
| "I can build this in 3 minutes; presenting gates wastes time" | The framework adds ~30s; you save hours when scope drifts. |
| "User said 'use superhuman to build X' — X is unambiguous" | The "Use superhuman" prefix is the invocation; the gate is what authorizes the build. |
| "There's nothing to discuss in G0" | Then say so AT G0 ("vision is clear, proceeding") and let the user confirm. Don't skip the presentation. |
| "I'll just create SUPERHUMAN.md at the end" | SUPERHUMAN.md is initialized at G0 and appended at every gate. End-of-project is too late for the audit trail. |
| "Code already exists in this project — I'll backfill artifacts as documentation and skip to G7/G8." | **STOP.** Pre-existing code outside the superhuman flow is a drift event. Surface it to the user as G6 with options: archive-and-restart, treat-as-legacy-import (run all gates anyway), or abandon. Backfilling artifacts retroactively defeats the audit trail and skips all the design decisions the gates exist to capture. |

If any of these thoughts arise, stop. Present G0.

## Required model tier for the orchestrator (PM thread)

The PM orchestrator role is `tier: most-capable` (see `roles/pm.md` and `adaptation/dispatch.md` model-tier table). Smaller/faster models will skip HITL gates even with the HARD-GATE above — the discipline assumes a model that can hold the full role contract in context and reason about it.

**Reference by alias, not by version.** Concrete model names (gpt-5.5, claude-opus-4-7, gemini-2.5-pro) go stale as providers ship newer versions. Reference by **alias** — your environment's stable name for "the current best model of family X" — so this skill keeps working without doc churn whenever the underlying model is upgraded.

**Where the mapping lives.** Tier → model is account-specific, so it belongs in your profile, not in this skill. Each tier carries both a primary and a fallback alias (ADR-6); a legacy bare-string tier value still loads and is normalized to this shape at read time:

```yaml
# ~/.superhuman/profile.yaml
models:
  most_capable: { primary: <your-most-capable-alias>, fallback: <your-fallback-alias> }
  standard:     { primary: <your-standard-alias>,     fallback: <your-fallback-alias> }
  cheap:        { primary: <your-cheap-alias>,         fallback: <your-fallback-alias> }
```

On first run, `phases/0-kickoff.md` Step 3 elicits these per-tier primary/fallback aliases and
writes this block for you via `scripts/superhuman_profile.py`; declining leaves a neutral
`PROMPT_ME` placeholder rather than assuming a provider. `adaptation/dispatch.md` supplies a
per-harness default when the profile declares none.

**Acceptable** for the PM thread: any alias resolving to a current top-tier model of any provider.

**Forbidden** for the PM thread: anything resolving to the fast/cheap tier — names containing `-mini`, `-flash`, `-fast`, `haiku`, or a local small model. These do NOT reliably honor the HARD-GATE: they skip gates regardless of what this file says.

(Subagent dispatches CAN use smaller models — Tester and mechanical Developer chunks routinely run on `cheap-fast` per `adaptation/dispatch.md`. The constraint is only on the PM orchestrator thread itself.)

If you don't know what model alias you're running on, ask the user at G1 as part of workflow preferences. If they're on a forbidden alias, surface a friendly warning and ask them to switch before proceeding.

## HITL levels (v0.5.0)

Superhuman offers three levels of human-in-the-loop involvement, chosen once at G1 and locked for
the project's lifetime (read from SUPERHUMAN.md `HITL-level:` on resume, never re-elicited):

| Level | Name | Human stops at | Mechanism |
|---|---|---|---|
| **H** | High HITL (default) | G0–G10, all of them | Standard phase progression, no surrogate. |
| **M** | Medium HITL | G0, G1, G6 (moderate+), G8, G9, G10 | A Surrogate-User subagent (`roles/surrogate-user.md`) answers G2/G3/G4/G5/G7 conservatively, while the PM runs a bounded **sequential** try → measure → keep/rollback loop against a `GOAL.md` fitness function (`phases/3-autonomous-loop.md`). |
| **L** | Low HITL | One combined G0+G1 confirmation, then only G10 | Same loop mechanics as HITL-M, but the PM/surrogate also resolves G6 (any severity), G8, and G9 itself — by **precedent-mining** (checking this project's own history, sibling repos/ADRs, and declared conventions before deciding — see `roles/surrogate-user.md`) rather than asking, logging the decision and its basis to SUPERHUMAN.md instead of pausing. |

*(The legacy spellings `0`/`1`/`2` still parse and map to H/M/L, so projects started before v0.8.0 resume unchanged. They mean the same thing; the letters were adopted because a rising number meant falling oversight, which read backwards.)*

HITL-M and HITL-L activate only when ALL activation preconditions hold, checked deterministically by
`scripts/autonomous-precondition.sh <project-root> --level <M|L> --slug <slug>`:

- Git is enabled with a remote configured.
- The rung this project resolves to permits unattended operation (HARD-GATE rule 5). This is the profile's decision, not the project's — see the ceiling rule below.
- A `GOAL.md` is provided (file-first: `<project-root>/GOAL.md` or `docs/superhuman/<slug>/GOAL.md`) or elicited at G1.
- **HITL-L only:** if the project modifies existing code (`SUPERHUMAN.md Modifies-existing-code: yes`), a `ROLLBACK.md` naming the exact revert target and procedure must exist (`templates/artifacts/ROLLBACK.md.tpl`). Net-new/greenfield projects are exempt — nothing pre-existing to revert to. An **undeclared** `Modifies-existing-code:` field is a gap, not an exemption: the absence of a declared fact is not evidence the fact is false.

**Pass `--slug`.** The last two are questions about *one* project and a repo may hold several in
`docs/superhuman/`. Without a slug the gate exits 4 ("policy declared but unresolved") rather than
answering about whichever sibling it happens to find first. The one exception is
`phases/0-kickoff.md` Step 3, which runs the gate with `--kickoff` before the project's own state
exists; that flag defers only the two project-state checks and kickoff re-runs the gate unflagged
once `SUPERHUMAN.md` and `GOAL.md` are written.

**The ceiling rule.** The HITL level is a *project* setting; the rung's `act_unattended` policy is a *location* setting. Where they disagree, the location wins and the level is reduced: a rung of `never` forbids M and L outright. A project may always take more human oversight than its location requires — never less. If a requested level is refused, fall back one step (L → M → H) rather than proceeding at the requested level.

**What never changes, at any level:** the Phase 3.3 preflight (`phases/3.3-preflight-review.md`)
GO/NO-GO is a hard, non-overridable blocker — precedent-mining cannot wave through a NO-GO. `ABORT`
is always a human decision. And **G10 is the one gate that survives at every level** — a genuinely
blocked PM always surfaces to a human, even at level 2. See `phases/3-autonomous-loop.md`,
`roles/surrogate-user.md`, and `conventions/autonomous.md` for the full mechanics.

## Cross-cutting rules (apply EVERY response)

- **Options + recommendation rule.** Every decision affecting scope, design, or approach presents 2-3 options with the recommended one named first and reasoning attached. Never surface an open question without a recommendation.
- **Verification before completion.** Developer and Tester roles claim success only with fresh evidence. No "DONE" without proof.
- **Honest concern surfacing.** Report `DONE_WITH_CONCERNS` rather than hiding doubts. One honest flag is worth ten silent failures.
- **Artifacts by path, never by paste.** Present artifact content by path (e.g., `docs/superhuman/<slug>/REQUIREMENTS.md`). Paste inline only when isolation forces it (e.g., a single critical snippet for a gate decision).
- **Append-mostly authoring.** REQUIREMENTS, DESIGN, PLAN, and SUPERHUMAN grow by timestamped appends. Full rewrites only when structure must change; prefix everything with a timestamp.
- **Cache-stable prompt ordering.** Every subagent dispatch assembles: `role prompt → declared references → declared conventions → cached artifact slice → task brief`. Never reorder this prefix. Task-specific content goes at the end. Before dispatching a role's subagent, read `roles/<role>.md` and pass its full content as the leading block of the subagent prompt (the role prompt → declared references → declared conventions → cached artifact slice → task brief order).
- **Model-tier routing.** Use cheapest model per role. Reserve most-capable for PM, Architect, code-quality reviewer. Standard tier for integration Developer, QA, Business Expert. Cheap/fast for Tester, mechanical Developer chunks, docs-sync, convention checks. See `adaptation/dispatch.md` for tier table.
- **Dispatch symbols.** Use `<dispatch:*>` symbolic names throughout — never raw platform tool names. See `adaptation/dispatch.md` for the full mapping. Read that file at session start to prime working memory.
- **Autonomous phase progression.** Only Type A gates (G0/G1/G2/G3/G4/G6/G7/G8/G9/G10) pause for user input. After a Type B gate (G5 one-liner in on-divergence cadence mode) or a Type C gate that degraded to B, the PM MUST immediately continue without waiting for a user prompt: if more chunks remain in PLAN.md → dispatch the next Developer; if all chunks complete → proceed to Phase 3.2 (docs sync). Never stop after a non-pausing gate and wait for the user to type "continue", "status?", or similar — that pattern erodes the user's trust that the framework is actually orchestrating. If you genuinely need the user's input mid-flow (mid-Phase 3, before G7), surface it as an explicit Type A gate (G6, G9, or G10), not as an implicit pause.

## Project identification

At every invocation, read `<project-root>/docs/superhuman/<slug>/SUPERHUMAN.md` if it exists. If found, resume that project from the last recorded gate. If not found (or no slug is inferrable), start at Phase 0 with G0.

Multi-project sessions: identify the active project from the user's message context (project name, slug, or directory mentioned). When ambiguous, ask: "Which project — \<list known slugs\> — or a new one?"

Project state lives entirely in `SUPERHUMAN.md` — the orchestrator is stateless between sessions. Every dispatch reads SUPERHUMAN.md to reconstruct context; never rely on in-memory state.

## Resume packet and locked decisions

**Read-packet-first.** On every resume (see HARD-GATE step 1 above), the PM reads `## Resume
packet` FIRST, before reconstructing from `## Decisions log` / `## Chunk log` / `## Drift notes` —
it is the single always-current entry point, not one append-only source among several.

**Absence is version-gated, not always legitimate.** When `## Resume packet` and/or
`## Decisions locked` are missing, what that means depends on the SUPERHUMAN.md's declared
`Superhuman-version:` (§Version below) — the section was only ever guaranteed to exist starting
at v1.1.0 (the release that introduced it; see `CHANGELOG.md`):

- **`Superhuman-version` < 1.1.0, or no version declared at all** — the file predates these
  sections; their absence is legitimate legacy. The PM falls back to reconstructing from
  `## Decisions log` / `## Chunk log` / `## Drift notes` directly. Absence here is treated as empty,
  never as corruption, and resume proceeds without error.
- **`Superhuman-version` >= 1.1.0** — the file was written after these sections shipped, so a
  missing one is unexpected: it may indicate truncation, corruption, or a silently dropped locked
  decision, and losing a `## Decisions locked` entry silently would itself be a fidelity failure.
  Do NOT treat the absence as empty. Surface it via G6 as stale state (same three options as the
  HARD-GATE step 1 INVALID path: archive-and-restart, treat-as-legacy-import, abandon) before
  proceeding — never guess and never silently fall back to the logs.

**Refresh-at-each-gate, kept-current.** The `## Resume packet` is kept-current, not append-only:
the PM refreshes it at every gate (not only at the ones that touch its fields) so a fresh session
can always resume from one read. This is unlike `## Decisions log`, `## Chunk log`, and the other
append-only sections, which only ever grow.

**Locked decisions are not relitigated.** A decision recorded in `## Decisions locked` is settled;
resuming the project does NOT reopen or relitigate it — the PM proceeds as if it were still true
without re-asking. Changing a locked decision is never a silent edit: it requires an explicit
surfaced action — either a gate (e.g., G6) or a drift entry — so the change is visible in the audit
trail the same way any other decision is. `## Decisions locked` is distinct from the append-only
`## Decisions log`: the log records what happened over time; the locked block records what may not
be silently changed.

## Phase progression

For each phase, the sequence is: read the phase recipe → execute steps in order → run gates at declared types → update SUPERHUMAN.md → apply retuning if the phase defines one.

**Phase 0 — Kickoff** (`phases/0-kickoff.md`): Vision elicitation (G0) then workflow preferences (G1). PM probes purpose and reason before any requirements work; caps elicitation at 5-7 exchanges then drafts `VISION.md`. G1 covers cadence, value-vs-foundation, git/remote, and parallelism. Business Expert may be invoked during elicitation. Initialize SUPERHUMAN.md with version (from `VERSION` file), slug, and all G1 preferences.

**Phase 1 — Requirements** (`phases/1-requirements.md`): PM (with Business Expert if domain-relevant) drafts `REQUIREMENTS.md`. Gate G2: user approves.

**Phase 2 — Design** (`phases/2-design.md`): Architect drafts `DESIGN.md` with 2-3 options and a recommendation; proposes the declared artifact set (from `templates/artifacts/` catalog) and chunking strategy. Gate G3: user approves design + chunking + artifact set.

**Phase 2.1 — Test plan** (`phases/2.1-test-plan.md`): QA drafts `TEST.md` covering both code and inference-driven components; includes backup strategy for any modified existing code. Gate G4: user approves.

**Phase 3 — Implementation** (`phases/3-implementation.md`): Per chunk: PM dispatches a fresh Developer subagent. Developer follows TDD (see `references/test-driven-development/`). After chunk completes, Phase 3.1 runs.

**Phase 3.1 — Test and review** (`phases/3.1-test-review.md`): Tester runs tests; QA reviews coverage; spec-compliance reviewer runs; code-quality reviewer runs. Spec-compliance + code-quality reviewers run in parallel (both read-only on the same chunk). Drift watch runs after each chunk. Gate G5: Type C (A in per-chunk cadence, B notification in on-divergence cadence). Loops back here for each remaining chunk.

**Phase 3.2 — Docs sync** (`phases/3.2-docs-sync.md`): PM iterates the declared artifact set; every artifact must exist, be non-stale, and reflect the implemented state. Stale or missing → PM fixes via the artifact's owner role before presenting. Gate G7: user reviews diff.

**Phase 3.3 — Preflight review** (`phases/3.3-preflight-review.md`): after docs sync and before acceptance, PM runs a single-turn parallel adversarial fan-out over the whole delivered chunk set — correctness/architecture (`roasting-code`) + a security lens + design-conformance (`roasting-design-specs` re-run) — and merges the three into a GO/NO-GO + Blockers + Recommended fixes + Rollback plan, reconciled into the acceptance packet. Review step, not a gate; a NO-GO blocks acceptance (or escalates as G6 drift if it reveals a design/requirements problem).

**Phase 4 — Acceptance** (`phases/4-acceptance.md`): PM presents summary, artifact links, and git refs. Gate G8 is conditioned on "Declared artifacts: all present and current." Gate G8: user sign-off at HITL-H/M; at level 2 the PM self-accepts once the Phase 3.3 preflight is GO (see `phases/3-autonomous-loop.md` Step 4) and only escalates to G10 if it can't clear a NO-GO itself.

## Drift watch

Runs continuously during Phase 3. See DESIGN.md §11.6 for the full 8-step algorithm; summary:

1. Collect triggers since the last gate fired (see DESIGN.md §7 trigger list).
2. Classify each trigger by severity (trivial / minor / moderate / major / critical) per DESIGN.md §11.2.
3. Compute aggregate severity — max of individuals, +1 bump if 3+ triggers from different categories accumulate.
4. Trivial → append to SUPERHUMAN.md drift notes; stop.
5. Minor → append; in **per-chunk** cadence, fold into the next G5 (the gate that fires after the next chunk). In **on-divergence** cadence there is no routine pausing G5 — accumulate the drift in SUPERHUMAN.md and surface it only on the next *drift event*, not on a fixed next-chunk schedule: promote to G6 when 3+ pile up without a gate firing, or immediately on any moderate+ trigger.
6. Moderate+ → compose delta report using `templates/delta-report.md.tpl`; pause new dispatches; let in-flight finish (unless critical+destructive); emit G6.
7. On user decision: execute chosen action (RE-CHUNK / REVISIT-DESIGN / REVISIT-REQUIREMENTS / CONTINUE / ABORT); archive affected files if needed; update SUPERHUMAN.md.
8. After resolution: append Retuning entry (what user chose vs PM recommendation).

Severity and actions: DESIGN.md §11.2 and §11.3. Delta-report schema: DESIGN.md §11.4 and `templates/delta-report.md.tpl`. In-flight handling: DESIGN.md §11.5. Cadence × severity matrix: DESIGN.md §11.8.

**Drift watch token efficiency:** for trivial/minor, PM reads only the delta since last chunk — not full REQUIREMENTS/DESIGN/PLAN. Full artifact reads only at moderate+.

## Gate handling

Gate inventory (DESIGN.md §7):

- **G0** — Vision approval (Type A): end of Phase 0 vision elicitation. `VISION.md` path + scope summary + any PM-proposed scope extensions.
- **G1** — Workflow preferences (Type A): HITL-level (0/1/2), cadence, value-vs-foundation, git+remote, parallelism. If remote requested, follow-up for repo URL / create-new / branch strategy / access method. At HITL-L, presented combined with G0 as a single confirmation — see `phases/0-kickoff.md`.
- **G2** — REQUIREMENTS approval (Type A): end of Phase 1.
- **G3** — DESIGN approval + chunking + declared artifact set (Type A): end of Phase 2. Architect presents 3 strategy options; PM presents proposed artifact set.
- **G4** — TEST plan + backup strategy approval (Type A): end of Phase 2.1.
- **G5** — Per-chunk results (Type C — switchable): after each Phase 3.1. In per-chunk mode (Type A): delta-report + continue/revisit options. In on-divergence mode (Type B): one-liner notification.
- **G6** — Drift escalation (Type A, unconditional): any moderate+ severity drift. Delta report + recommended action + 2 alternatives. Cadence switch does NOT silence G6.
- **G7** — Final docs sync (Type A): end of Phase 3.2. Diff vs last-approved; declared-artifact completeness check.
- **G8** — Acceptance sign-off (Type A): end of Phase 4. Preconditioned on declared-artifacts ✓.
- **G9** — High-stakes parallelism (Type A): when PM proposes parallel across an architecture seam. Plan + risks + recommendation.
- **G10** — Subagent BLOCKED escalation (Type A): when PM cannot unblock via re-dispatch. Blocker + options.

Format rules (all Type A gates):

1. Recommendation first, always.
2. Use `<dispatch:ask>` for discrete choices.
3. Artifacts by path, not paste (token-efficiency).
4. Fixed preamble: header → 3-5 bullets → artifact path → decision prompt. Use cached templates from `templates/gate-headers.md`.
5. Append gate + timestamp + decision to SUPERHUMAN.md.
6. Never auto-proceed past Type A when a human is the one answering it. (At HITL-L, G6/G8/G9 are answered by the PM/surrogate itself, not skipped — see "HITL levels" above; every such answer is still logged to SUPERHUMAN.md exactly like a human decision would be.)
7. G6 is unconditional at HITL-H/M — cadence mode does not silence it. At level 2 it always fires too, just resolved by the PM/surrogate rather than paused on.
8. Archive, never delete: if a gate decision removes work, move affected files to `archive/<YYYY-MM-DD-HHMMSS>-<chunk>/` with `WHY.md` + `RESTORE.md` (using `templates/archive-WHY.md.tpl` and `templates/archive-RESTORE.md.tpl`).
9. After every Type A gate with a user decision: append a Retuning entry.

Disagreement calibration: if user overrides PM recommendation 3+ times on the same theme, raise a meta-gate — "You've been overriding on \<theme\> — want me to re-tune?" (DESIGN.md §7).

## Retuning

After every Type A gate that produced a user decision AND after every G6 resolution (DESIGN.md §13.4):

1. Diff user decision against PM recommendation (approved as-is / edited / overrode with different choice / added-removed artifacts / changed scope).
2. Identify patterns if 2+ similar decisions accumulated.
3. Append to `## Retuning notes` in SUPERHUMAN.md: timestamp + observation + bias-adjustment.
4. On subsequent PM dispatches, include the Retuning notes section as project memory — it loads after the role-prompt prefix, before the task brief, in the stable cache order.
5. Promote to meta-gate when 3+ overrides on the same theme accumulate.

Retuning is project-scoped; notes do not leak across projects.

**At HITL-L**, this section applies only to the gates a human actually answers (the combined G0+G1 confirmation and any G10). The PM/surrogate's self-resolved gates (G6/G8/G9) have no "user decision" to diff — their precedent-mining basis is logged to the Decisions log instead (see `roles/surrogate-user.md`), not to Retuning notes.

## Tools used

`<dispatch:agent>`, `<dispatch:ask>`, `<dispatch:read>`, `<dispatch:write>`, `<dispatch:edit>`, `<dispatch:bash>`, `<dispatch:grep>`, `<dispatch:glob>`, `<dispatch:task_create>`, `<dispatch:task_update>`. See `adaptation/dispatch.md` for the platform mapping (Claude Code and OpenClaw equivalents).

## On-demand critique utilities

Three roast sub-skills are available for adversarial critique outside of the phase gates. Dispatch them when the user wants an artifact torn apart before or independent of a project session:

- `references/roasting-requirements/` — PRD / requirements / product spec adversarial review ("is this good product thinking?")
- `references/roasting-design-specs/` — technical design / architecture / ADR adversarial review ("what breaks in this design?")
- `references/roasting-code/` — externally-sourced code adversarial review ("prove this shouldn't ship as-is")

These are not phase gates. They produce findings only; they do not modify SUPERHUMAN.md or gate progression. Use them pre-G2 (requirements), pre-G3 (design), or whenever the user brings an external artifact for critique.

**In-flight complement — `references/doubt-driven-development/`.** Where the roast sub-skills are *post-hoc* (critique a completed artifact), doubt-driven-development is *in-flight*: the PM materializes a fresh-context adversarial reviewer for a non-trivial decision **before** it stands, running CLAIM → EXTRACT → DOUBT → RECONCILE → STOP. It is a **PM-thread utility** (never a role's `declared-references` — that would be role-dispatches-role), invoked at **G3/G4/G5** when a reviewer's stated confidence is ≤ 60% or a decision is flagged "non-trivial". Its cross-model second opinion is documented but **deferred** (no live shell-out this release). Complementary to roasting, not a replacement.

**Conditional — `references/deprecating-a-system/`.** When a project's VISION declares removing / retiring / sunsetting an existing **product** system, the PM invokes this sub-skill at **G0** to shape the migration (advisory vs compulsory, Strangler / Adapter / Feature-flag, the Churn Rule, Zombie-code triage). Not one of the eight gates — conditional, like the roasting skills. It governs **product code only**; superhuman's own project artifacts remain archive-never-delete.

## When NOT to use this skill

- Single trivial actions: "show me X", "run Y", "explain Z".
- Already-in-progress conversations where the user is hand-driving and needs help on one step only.
- Pure information or research questions with no project output.

For those, respond directly without invoking superhuman. If in doubt: does the request require multiple coordinated decisions, roles, or phases? If no, respond directly.

## Version

See `VERSION` file in the skill bundle root. The version is recorded into `SUPERHUMAN.md` at G1 as `Superhuman-version:`. Bump VERSION on any change to role prompts, phase recipes, gate semantics, default conventions, or this orchestrator. Project's recorded version stays fixed after G1 (snapshot of the skill version that started the project).
