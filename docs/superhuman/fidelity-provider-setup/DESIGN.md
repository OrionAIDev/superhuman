# Design: Superhuman fidelity + first-run provider setup

**Created:** 2026-08-15
**Last revision:** 2026-08-15
**Source requirements:** `REQUIREMENTS.md`

## Approach summary

This project hardens superhuman's own orchestration substrate along two axes — fidelity across
*time* (#165) and fidelity across *operators* (#139) — entirely by editing documents, one
convention doc, and one existing deterministic Python surface. There is no new runtime, service, or
integration.

The spine is **substrate-before-consumer** (foundation-first, per the G1 preference). Two foundation
pieces come first: (1) a single canonical **subagent return schema** defined once in a new
`conventions/` doc (FR-3), and (2) the two new **SUPERHUMAN.md template sections** — a kept-current
`## Resume packet` and a first-class `## Decisions locked` block distinct from the append-only
`## Decisions log` (FR-1, FR-6). Only then do the consumers change: every `roles/*.md` output
contract references the canonical schema and reconciles its specialized verdict as a *specialization*
riding in the schema's `conclusion` field (FR-4/FR-5); and the orchestration semantics in `SKILL.md`
+ `roles/pm.md` gain read-packet-first resume, refresh-at-each-gate, and locked-decisions-not-
relitigated behavior with graceful degradation for pre-existing files (FR-2, FR-7, NFR-2).

The #139 half is deliberately split into deterministic code and interactive elicitation, honoring
dev-principle #5 (config generation is code, never LLM free-text). The existing onboarding surface in
`scripts/superhuman_profile.py` (`discover` / `propose_ladder` / `render_profile` / `init`) is
extended with a `models:` generator; the `models` field's shape is normalized to accept per-tier
`{primary, fallback}` (FR-8) while still parsing the legacy bare-string form documented in `SKILL.md`
(NFR-2). Phase 0 kickoff (`phases/0-kickoff.md`) gains a provider-neutral elicitation sub-flow that
feeds that generator; declining yields a neutral placeholder that fails safe (FR-10), and a one-line
dispatch-time warning surfaces any still-unfilled tier (OQ-5).

Everything stays provider- and harness-agnostic (NFR-1, the locked immutable constraint): vendor
names appear only as clearly-marked illustrative examples, never as defaults. The Claude-Code tier
table in `adaptation/dispatch.md` remains Anthropic-only *by nature of that single-provider harness*
— its mechanism is untouched; only the #139 elicitation and the profile it writes are made agnostic.

## Alternatives considered

Three project-level shapes were weighed before settling on the foundation-first substrate approach
above.

| | Substrate-first docs+one-script (recommended) | New "fidelity" helper module | Rip-and-replace role contracts |
|---|---|---|---|
| Approach | Edit template/roles/phase-recipes/SKILL; extend the one existing profile script; add one convention doc | Introduce a new `scripts/fidelity.py` owning packet + schema validation + profile writing | Replace every role's bespoke verdict schema with the one canonical block |
| Trade-offs | Minimal new surface; reuses `superhuman_profile.py` onboarding; all-prose constructs stay LLM-read | A second script duplicates onboarding plumbing; violates "recurring-MCP→skill" economy | Breaks A3 and the PM's current parsing of QA/Tester/Surrogate verdicts; large blast radius |
| Token/complexity cost | Low — additive edits, one new doc, one schema-normalization | Medium — new module + its own tests + wiring | High — every role + PM parse path rewritten |
| When to prefer | Default for a doc/template substrate change | If profile logic were genuinely separate from onboarding | Never here — FR-4 explicitly says *specialize, don't replace* |

The recommended approach wins because the requirements are almost entirely about **where a construct
is defined and who references it**, not about new computation. The only genuine code is the
profile-`models:` generator, and an onboarding renderer for it already exists — a second module would
duplicate its discovery/round-trip machinery for no gain. Rip-and-replace is ruled out by FR-4 and
assumption A3 (verdicts must survive as specializations).

## Design decisions (open questions resolved)

Each decision is presented recommendation-first with a comparison table, per the Architect contract.

### OQ-1 — Return-schema enforcement strength (FR-3/FR-4)

| | A. Advisory doc, validated at PM boundary (recommended) | B. Enforced machine-parseable block | C. Pure advisory, no validation |
|---|---|---|---|
| Mechanism | Schema is a `conventions/` doc; roles reference it; the PM's existing "reject free-form prose" rule rejects non-conforming returns and re-dispatches | A fenced, fixed-key block every role must emit; a parser validates it | Documented only; PM does not police shape |
| Trade-offs | Reuses the enforcement pattern already in `pm.md`/`qa.md`; no parser to build or maintain | Strongest guarantee, but LLM subagents cannot be *hard*-forced to emit exact syntax; parser churn on every schema tweak | Cheapest, but returns drift back to ad-hoc — the exact problem #165 fixes |
| Token/complexity cost | Low | Medium-high (parser + failure UX) | Lowest |
| When to prefer | Superhuman's roles are LLM subagents whose PM already gates output shape | A machine consumes the returns downstream | Never — it re-opens the drift |

**Recommendation: A.** The PM already rejects unstructured reviewer output (`roles/pm.md` Output
discipline; `roles/qa.md`). Making the canonical schema the *named accepted shape* at that same
boundary is enforcement enough and confirms A3 (verdicts wrap without breaking parsing). A hard
parseable block (B) over-engineers a contract between two LLM roles and would itself need a
degradation path the moment a subagent emits near-miss syntax.

### OQ-2 — "Decisions locked" enforcement locus (FR-7)

| | A. Template convention + soft PM-path semantics (recommended) | B. Template-only convention | C. Template + deterministic script check |
|---|---|---|---|
| Mechanism | New `## Decisions locked` section *plus* `SKILL.md`/`pm.md` text: not relitigated on resume, reopening is a surfaced gate/drift entry | Section exists; honoring it is left implicit | A script scans the section and flags reopened items |
| Trade-offs | Meets FR-7's "honored by orchestration semantics" clause; all-prose, LLM-read | Under-delivers FR-7 (structure without behavior) | Over-built for a prose construct nothing machine-consumes; another script to maintain |
| Token/complexity cost | Low | Lowest | Medium |
| When to prefer | FR-7 requires behavior, not just a heading | If FR-7 were structure-only | If a machine gated on lock state |

**Recommendation: A.** FR-7 explicitly requires the resume path and drift watch to treat a locked
decision as not-to-be-reopened, so template-only (B) fails the requirement. A script (C) is the wrong
tool: the lock list is read by the PM (an LLM) exactly like the Decisions log it sits beside, and
dev-principle #5 reserves code for irreversible/safety-critical paths — a "don't reopen this" note is
neither.

### OQ-3 — #139 elicitation mechanism + first-run behavior (FR-8/FR-9/FR-10)

| | A. Interactive G1 sub-flow **+** deterministic init helper (recommended) | B. Phase-recipe elicitation only (LLM writes YAML) | C. Helper script only, no interactive step |
|---|---|---|---|
| Mechanism | `phases/0-kickoff.md` elicits provider/tier answers; `superhuman_profile.py` writes `models:` deterministically | The PM prose-writes `profile.yaml` from the operator's answers | Operator hand-edits or the helper guesses from discovery |
| Trade-offs | Clean split: inference (elicit) vs code (write); reuses the existing renderer | Violates dev-principle #5 (config generation as LLM free-text) | No path to capture the operator's actual subscriptions |
| First-run (`profile.yaml` absent) | Helper creates the file/`models:` section with neutral placeholders | Same, but via LLM write | Helper creates from discovery only |
| Decline/defer | Neutral placeholder that fails safe (prompts later) | Same intent, weaker guarantee | N/A — no elicitation |
| Token/complexity cost | Low-medium | Low | Low |

**Recommendation: A.** The elicitation is inference (belongs in the phase recipe); the write is
config generation (belongs in code, per FR-9's explicit "writing is via code, not LLM free-text").
On first run the helper creates the `models:` block if absent (A1 holds — same file the skill already
reads). Decline → a self-documenting neutral placeholder (e.g. `most_capable: {primary: PROMPT_ME,
fallback: PROMPT_ME}`) with no vendor baked in (FR-10, NFR-1).

### OQ-4 — Resume packet: reference vs restate (FR-1/NFR-2)

| | A. Reference volatile, restate stable framing (recommended) | B. Restate all seven fields | C. Reference all seven fields |
|---|---|---|---|
| Mechanism | `decisions-locked` → pointer to `## Decisions locked`; `current state` → pointer to `## Chunk log` + last gate; `evidence-pointers` → paths. Restate only objective / immutable constraints / ruled-out paths / next-3-actions | Packet duplicates everything it summarizes | Packet is pure pointers |
| Trade-offs | Scannable *and* drift-resistant; volatile data lives once | Duplication drift — the failure mode FR-1 warns against | Packet loses standalone readability the resume path depends on |
| Token/complexity cost | Low | Low but rots | Low but opaque |
| When to prefer | Fields that already have a canonical home should point at it | Never | Never |

**Recommendation: A.** Four fields have no other home (objective, immutable constraints, ruled-out
paths, next-3-actions) and are restated; three already live elsewhere (decisions-locked, current
state, evidence) and are referenced. This keeps the packet a true one-read entry point without
becoming a second source of truth that drifts from the logs.

### OQ-5 — Dispatch-time placeholder warning (FR-10)

| | A. Emit a one-line non-blocking warning (recommended) | B. No warning; rely on elicitation |
|---|---|---|
| Mechanism | When a tier resolves to an unfilled placeholder at dispatch, the PM emits a Type-B one-liner naming the tier | Silent; placeholder only caught if setup is re-run |
| Trade-offs | Closes the fail-safe loop — a deferred setup can't silently resolve to nothing | A deferred tier stays invisible until it fails |
| Token/complexity cost | Negligible (one line) | Zero |

**Recommendation: A.** The cost is one line and it makes FR-10's "fails safe (prompts later)" real at
the exact moment the gap matters — dispatch. It is a notification, never a gate, so it does not
interrupt autonomous progression.

## Component breakdown

| Component | Responsibility | Public interface | Dependencies |
|---|---|---|---|
| **C-RS** canonical return-schema doc | Define the six ordered fields (conclusion → evidence → commands → assumptions → risks → next-action) once | New `conventions/subagent-return-schema.md` | none (foundation) |
| **C-TPL** SUPERHUMAN template sections | Add `## Resume packet` (7 fields, above volatile logs) and `## Decisions locked` (distinct from `## Decisions log`) | `templates/SUPERHUMAN.md.tpl` | none (foundation) |
| **C-ROLES** role output contracts | Every role references C-RS by pointer; specialized verdicts reconciled as `conclusion`-riding specializations | `roles/{pm,architect,developer,qa,tester,business-expert,surrogate-user}.md` | C-RS |
| **C-ORCH** orchestration semantics | Read-packet-first resume; refresh-at-each-gate; locked-not-relitigated; reopening is a surfaced event; graceful degradation for pre-existing files | `SKILL.md`, `roles/pm.md` | C-TPL |
| **C-PROF** profile `models:` generator | Deterministically write per-tier `{primary, fallback}` into `~/.superhuman/profile.yaml`; normalize legacy bare-string; neutral placeholder on decline | `scripts/superhuman_profile.py` (extend onboarding surface) | none (foundation for C-KICK) |
| **C-KICK** #139 elicitation sub-flow | Provider-neutral G1 step over 3 tiers × {primary, fallback}; invoke C-PROF; decline → neutral fail-safe | `phases/0-kickoff.md`, `roles/pm.md` (G1) | C-PROF |
| **C-DISP** dispatch-time placeholder warning | One-line non-blocking warning when a tier is an unfilled placeholder | `adaptation/dispatch.md`, `roles/pm.md` | C-PROF, C-KICK |
| **C-HYG** hygiene | VERSION bump; dated CHANGELOG entry naming #165/#139; README mention of the new constructs | `VERSION`, `CHANGELOG.md`, `README.md` | all |

## Data flow

**Resume (fidelity across time).** On invocation the PM reads `SUPERHUMAN.md`; the HARD-GATE validity
check still keys on `## Decisions log` (unchanged). If a `## Resume packet` is present, the PM reads
it *first* as the single always-current entry point, following its pointers into `## Decisions
locked` (what may not be reopened) and `## Chunk log` (current state); if it is absent, the PM
degrades gracefully and reconstructs from the logs exactly as today (NFR-2). At every gate the PM
refreshes the packet (kept-current, not append-only). A request to change a locked decision is not a
silent edit — it surfaces as a gate/drift entry.

**Subagent return (fidelity within a cycle).** A dispatched role does its work and returns the
canonical six-field schema; its specialized verdict (QA/Tester `approved|issues_found`, Surrogate
`ACCEPT|ESCALATE`, Architect option-tables) rides in `conclusion`. The PM parses the schema at its
output-discipline boundary, rejecting free-form prose (OQ-1 rec A).

**First-run provider setup (fidelity across operators).** At G1 the kickoff recipe elicits, per tier,
a primary and fallback provider·model with a provider-neutral question set. The answers are handed to
C-PROF, which deterministically writes the `models:` block of `~/.superhuman/profile.yaml` (creating
file/section if absent). On decline, C-PROF writes neutral placeholders. Thereafter the dispatch
layer resolves tiers from that block; if a tier is still a placeholder at dispatch, C-DISP emits a
one-line warning.

## Error handling

- **Pre-existing SUPERHUMAN.md without new sections (NFR-2):** absence of `## Resume packet` /
  `## Decisions locked` is treated as empty, never as corruption. The validity gate is unchanged, so
  legacy projects resume without error. A fixture asserts this.
- **`profile.yaml` absent or lacking `models:` (FR-9):** C-PROF creates the file/section; it never
  raises on a missing target.
- **Malformed / legacy `models:` shape:** the parser normalizes a bare string (`most_capable: opus`)
  to `{primary: opus, fallback: null}`; a mapping is taken as-is. An unrecognized value fails loud in
  the resolver (existing `_TOP_KEYS`/validation path), not silently.
- **Operator declines elicitation (FR-10):** neutral self-documenting placeholder; no vendor assumed;
  C-DISP warns at dispatch time.
- **Subagent returns off-schema (OQ-1):** PM rejects and re-dispatches — same path as today's
  free-form-prose rejection.

## Testing strategy

Deterministic checks carry the load (NFR-3); prose conventions are covered by presence/pointer
assertions rather than semantic tests. High-level split (details → TEST.md, QA-owned):

- **Deterministically testable:** template section presence + ordering + the seven labelled fields
  (C-TPL); the return-schema doc exists and names all six fields once (C-RS); each role references the
  schema doc and retains its verdict schema (C-ROLES, extend `tests/test_content.py`); backward-compat
  fixture resumes without error (C-ORCH); `profile.yaml` `models:` generation round-trips and parses,
  legacy bare-string still loads (C-PROF, extend `tests/test_profile_onboarding.py`); a repo grep over
  changed shipped files finds no vendor baked as a default (NFR-1/FR-10).
- **Prose-convention (assert presence, not behavior):** read-packet-first and locked-not-relitigated
  semantics in `SKILL.md`/`pm.md` (C-ORCH); the elicitation step and neutral-placeholder language in
  `phases/0-kickoff.md` (C-KICK); the dispatch-time warning rule (C-DISP).

## Open issues

- **Role-chunk size (C-ROLES).** Seven files in one chunk risks the ~300-line soft cap even though
  each edit is a small pointer addition. Mitigation: if it exceeds ~2× estimate, split by verdict
  class — {qa, tester, surrogate-user} (verdict-bearing) then {pm, architect, developer,
  business-expert} (looser). Logged as a chunk-planning note, not pre-split.
- **`models:` schema extension is a public-shape change.** `SKILL.md` documents `most_capable: opus`
  (bare string). The normalization keeps that parsing, but any other reader of `Profile.models` must
  tolerate both shapes. Mitigation: normalize at parse time so downstream always sees the mapping
  form; assert both in tests. Flagged for DECISIONS.md.
- **`README.md` scope.** The declared README here is superhuman's own; the change is a light mention
  of the two new constructs, not a rewrite. If the PM prefers CHANGELOG-only, README can drop from the
  set at G3.

## ARCHITECTURE.md trigger ruling

**ARCHITECTURE.md not required** — no multiple deployable units (superhuman is a single skill bundle
of docs plus one resolver script; nothing deploys independently), no external-API integration (#139
is elicitation + deterministic local file generation; live model shell-outs are explicitly out of
scope), and no cross-process IPC. Stated explicitly per the Architect contract; not included.

## Declared artifact set (recommended at G3)

Baseline VISION / REQUIREMENTS / DESIGN / PLAN / TEST / README / SUPERHUMAN, **plus DECISIONS.md**.

| Artifact | In? | Justification |
|---|---|---|
| VISION, REQUIREMENTS, DESIGN, PLAN, TEST, SUPERHUMAN | yes | Baseline; already in flight |
| README.md | yes (light) | NFR-4-adjacent; brief mention of the new constructs. Droppable at G3 (see Open issues) |
| **DECISIONS.md** | **recommend yes** | This project makes several cross-cutting, precedent-setting decisions (OQ-1 enforcement model, OQ-2 lock locus, the `models:` schema extension) that future substrate work will reference. They are ADR-shaped and durable — distinct from the runtime `SUPERHUMAN.md` Decisions log. Architect-owned |
| ARCHITECTURE.md | no | Trigger ruling above |
| API.md, DATA-MODEL.md, DEPLOYMENT.md | no | No external/public API, no persistent data model beyond the existing `models:` field, no new deployment topology |
| THREAT-MODEL.md | no | No new attack surface; no credentials handled (elicitation records provider *names/aliases*, not secrets) |
| DEVELOPING.md, USER-GUIDE.md, RUNBOOK.md | no | Substrate change to an existing skill; CHANGELOG + README mention suffice |

CHANGELOG.md and VERSION are hygiene deliverables (NFR-4), tracked via C-HYG, not declared artifacts.

## Value definition (one line)

**A chunk is valuable when it lands a substrate change — a template section, the schema doc, a role
pointer, profile-generation code, or a phase-recipe step — that a human can read and verify against a
named FR, with a deterministic check asserting its presence.**

## Chunking strategy

**Recommended: foundation-first** (matches the G1 preference), with two alternatives for the record.

| Strategy | Means here | Recommend? |
|---|---|---|
| **Foundation-first** | Ship C-RS (schema doc) and C-TPL (template sections) and C-PROF (generator) before the consumers that reference them (C-ROLES, C-ORCH, C-KICK, C-DISP) | **Yes** — consumers point at these substrates; building a consumer first would force significant rework |
| Value-first | Lead with the visible #139 elicitation flow, backfill the schema/template later | No — the elicitation is inert without the generator, and roles can't reference a schema doc that doesn't exist |
| Hybrid | 1–2 foundation chunks then value-first | Close second, but three of the eight chunks are genuine foundations, so a pure foundation-first spine is cleaner |

### Per-feature foundation decisions (SUPERHUMAN.md log format)

```
[2026-08-15] Foundation decision: role schema references (C-ROLES) — rework if standalone would be significant because every role would reference a schema doc that does not yet exist, forcing a second edit pass across 7 files. Decision: foundation chunk 1 (C-RS) precedes.
[2026-08-15] Foundation decision: read-packet-first semantics (C-ORCH) — rework if standalone would be significant because the semantics describe template sections that must exist to be read. Decision: foundation chunk 2 (C-TPL) precedes.
[2026-08-15] Foundation decision: #139 elicitation wiring (C-KICK) — rework if standalone would be significant because the phase recipe invokes the deterministic generator; eliciting into an LLM-written YAML would violate dev-principle #5 and be rewritten. Decision: foundation chunk 5 (C-PROF) precedes.
[2026-08-15] Foundation decision: dispatch-time placeholder warning (C-DISP) — rework if standalone would be minimal (a one-line rule keyed on a placeholder value), but it reads the shape C-PROF writes. Decision: sequence after C-PROF/C-KICK; not a hard foundation dependency.
```

### Chunk list

| # | Title | Strategy alignment | Foundation? | Est. size | Acceptance criteria |
|---|---|---|---|---|---|
| 1 | Canonical return-schema convention doc (C-RS) | foundation | yes | S | `conventions/subagent-return-schema.md` exists; names the six ordered fields once, provider/role-neutral; test asserts presence + field set (FR-3) |
| 2 | Resume packet + Decisions-locked template sections (C-TPL) | foundation | yes | S | `## Resume packet` (7 labelled fields) sits above volatile logs; `## Decisions locked` distinct from `## Decisions log`; test asserts sections, ordering, fields (FR-1, FR-6) |
| 3 | Thread canonical schema through all roles (C-ROLES) | value (consumer) | no | M | Each of the 7 roles references the schema by pointer; QA/Tester/Surrogate/Architect verdicts reconciled as `conclusion`-riding specializations; `pm.md` names the schema as accepted shape and keeps prose-rejection; `test_content.py` extended (FR-4, FR-5) |
| 4 | Orchestration semantics + backward-compat (C-ORCH) | value (consumer) | no | M | `SKILL.md`/`pm.md` document read-packet-first, refresh-at-each-gate, locked-not-relitigated, reopening-is-surfaced; fixture SUPERHUMAN.md without new sections resumes without error (FR-2, FR-7, NFR-2) |
| 5 | Profile `models:` generator + schema normalization (C-PROF) | foundation | yes | M | `superhuman_profile.py` writes per-tier `{primary, fallback}` deterministically, creates file/section if absent, normalizes legacy bare-string; neutral placeholder on decline; round-trip + legacy-load tests (FR-8, FR-9) |
| 6 | Phase-0 #139 elicitation sub-flow (C-KICK) | value (consumer) | no | S | `phases/0-kickoff.md` + `pm.md` G1 gain a provider-neutral elicitation over 3 tiers × {primary, fallback} that invokes C-PROF; decline → neutral fail-safe; grep finds no vendor default (FR-8, FR-10, NFR-1) |
| 7 | Dispatch-time placeholder warning (C-DISP) | value (consumer) | no | S | `adaptation/dispatch.md` + `pm.md` document a one-line non-blocking warning when a tier is an unfilled placeholder at dispatch (FR-10, OQ-5) |
| 8 | Hygiene: VERSION + CHANGELOG + README (C-HYG) | value | no | S | VERSION incremented; dated CHANGELOG entry naming #165/#139; README mentions the new constructs (NFR-4) |

## Requirements traceability (FR/NFR → chunk)

| Req | Owner chunk(s) |
|---|---|
| FR-1 Resume packet section | C2 |
| FR-2 kept-current + read-first | C4 |
| FR-3 canonical schema doc | C1 |
| FR-4 roles emit schema | C3 |
| FR-5 PM accepts schema, rejects prose | C3 |
| FR-6 Decisions-locked construct | C2 |
| FR-7 locked honored by semantics | C4 |
| FR-8 elicit 3 tiers × {primary, fallback} | C5 (schema) + C6 (elicitation) |
| FR-9 populate `profile.yaml` `models:` via code | C5 |
| FR-10 no vendor default; decline fails safe | C6 (placeholder) + C7 (dispatch warning) |
| NFR-1 provider/harness-agnostic | cross-cutting; verified in C6 grep + C8 |
| NFR-2 backward-compatible resume | C4 (fixture) |
| NFR-3 tests / deterministic checks | every chunk carries its own check |
| NFR-4 VERSION + CHANGELOG | C8 |
| NFR-5 ceiling OrionTest, no UAT | process (PM plan targets branch→PR→main) |
| NFR-6 role-not-AI attribution | process (commit/PR/doc text) |

Every FR and NFR has a responsible component/chunk (verification-before-completion satisfied).
