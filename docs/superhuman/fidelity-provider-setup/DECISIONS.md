# Architecture decision records: Superhuman fidelity + first-run provider setup

ADRs in append-only order. Each record is immutable once accepted; supersede with a new ADR.
All six below were settled at design time and accepted at G3 (2026-08-15). Where `DESIGN.md`
already carries the full comparison, the ADR points at it rather than restating it.

**LD-1 (immutable, governs every ADR):** provider- and harness-agnostic throughout. Any model
alias shown in this file is a clearly-marked illustrative example only — never a default, never a
required value. No Anthropic-first (or any-vendor-first) assumption in shipped files.

## ADR-1: Return-schema enforcement = advisory doc validated at the PM boundary

- **Status:** accepted (G3, 2026-08-15)
- **Date:** 2026-08-15
- **Context:** FR-3/FR-4 need one canonical subagent return schema honored across all roles. The
  roles are LLM subagents; the question (OQ-1) was whether to enforce the schema as an advisory
  documented contract or as a machine-parseable block the PM validates with a parser.
- **Decision:** Option A — the schema lives once in `conventions/subagent-return-schema.md` and is
  enforced at the PM's *existing* output-discipline boundary (the "reject free-form prose" rule
  already in `roles/pm.md` and `roles/qa.md`). No parser is built. See `DESIGN.md` §Design
  decisions OQ-1 for the full comparison.
- **Consequences:** (+) Reuses the enforcement pattern already in place; zero parser to build or
  maintain; confirms assumption A3 (verdicts wrap without breaking PM parsing). (−) Enforcement is
  as strong as the PM's judgment, not a hard syntactic gate — accepted because a hard block between
  two LLM roles would itself need a near-miss degradation path.
- **Alternatives considered:** B enforced machine-parseable block (over-engineered, parser churn);
  C pure advisory with no PM validation (re-opens the drift #165 fixes). Both rejected.

## ADR-2: "Decisions locked" enforcement = template convention + soft PM-path/drift semantics

- **Status:** accepted (G3, 2026-08-15)
- **Date:** 2026-08-15
- **Context:** FR-6/FR-7 require a first-class "Decisions locked — do not relitigate" construct
  distinct from the append-only Decisions log, *and* orchestration semantics that honor it. OQ-2
  asked where the honoring lives: template-only, or also in the PM resume path / drift watch.
- **Decision:** Option A — a dedicated `## Decisions locked` template section **plus** prose in
  `SKILL.md`/`roles/pm.md` making it behavior: not relitigated on resume, and reopening a locked
  decision is a surfaced gate/drift entry rather than a silent edit. No script gate. See `DESIGN.md`
  §Design decisions OQ-2.
- **Consequences:** (+) Meets FR-7's "honored by orchestration semantics" clause; all-prose,
  LLM-read, consistent with how the adjacent Decisions log is consumed. (−) Relies on the PM
  honoring the semantics rather than a deterministic check — accepted because the lock list is
  read by an LLM, and dev-principle #5 reserves code for irreversible/safety-critical paths, which
  a "don't reopen" note is not.
- **Alternatives considered:** B template-only (under-delivers FR-7); C template + deterministic
  script check (wrong tool, nothing machine-consumes the list). Both rejected.

## ADR-3: #139 setup = interactive elicitation + deterministic code writer

- **Status:** accepted (G3, 2026-08-15)
- **Date:** 2026-08-15
- **Context:** FR-8/FR-9/FR-10 require first-run setup to elicit the operator's provider stack and
  write their `profile.yaml` `models:` tier map. OQ-3 asked whether the mechanism is an interactive
  phase-recipe sub-flow, a separate helper, or both — and how it behaves when `profile.yaml` is
  absent or the operator declines.
- **Decision:** Option A — split by nature of the work: **elicitation is inference** (a
  provider-neutral G1 sub-flow in `phases/0-kickoff.md`) and **the write is config generation**
  (deterministic code extending the existing onboarding surface in
  `scripts/superhuman_profile.py`), per dev-principle #5. First run creates the `models:` section if
  absent; decline yields a neutral, vendor-free placeholder that fails safe. See `DESIGN.md` §Design
  decisions OQ-3 and §Component C-PROF/C-KICK.
- **Consequences:** (+) Honors dev-principle #5 (no LLM free-text config); reuses the existing
  `discover`/`propose_ladder`/`render_profile`/`init` machinery instead of a second script.
  (−) Two components must stay wired (the recipe invokes the helper) — accepted as a foundation
  dependency (C-PROF precedes C-KICK).
- **Alternatives considered:** B phase-recipe elicitation only, LLM writes the YAML (violates
  dev-principle #5); C helper-only, no interactive step (no way to capture the operator's
  subscriptions). Both rejected.

## ADR-4: Resume packet reference-vs-restate — reference volatile, restate the homeless four

- **Status:** accepted (G3, 2026-08-15)
- **Date:** 2026-08-15
- **Context:** FR-1 adds a kept-current `## Resume packet` with seven handoff fields. OQ-4 asked
  which fields should point at existing state versus restate it, to avoid duplication drift.
- **Decision:** Option A — reference the three fields that already have a canonical home
  (decisions-locked → `## Decisions locked`; current state → `## Chunk log` + last gate;
  evidence-pointers → paths) and restate only the four with no other home (objective, immutable
  constraints, ruled-out paths, next-3-actions). See `DESIGN.md` §Design decisions OQ-4.
- **Consequences:** (+) Packet stays a scannable single-read entry point without becoming a second
  source of truth that drifts from the logs. (−) A reader must follow one hop for the referenced
  fields — accepted as the correct trade against duplication rot.
- **Alternatives considered:** B restate all seven (duplication drift — the failure FR-1 warns of);
  C reference all seven (packet loses standalone readability). Both rejected.

## ADR-5: Dispatch-time placeholder warning = one non-blocking line

- **Status:** accepted (G3, 2026-08-15)
- **Date:** 2026-08-15
- **Context:** FR-10 requires the decline/defer path to fail safe. OQ-5 asked whether an unfilled
  tier placeholder should also surface a warning at dispatch time.
- **Decision:** Option A — when a tier resolves to an unfilled placeholder at dispatch, the PM emits
  a single non-blocking (Type-B) one-line warning naming the tier. It is a notification, never a
  gate, so it does not interrupt autonomous progression. See `DESIGN.md` §Design decisions OQ-5 and
  §Component C-DISP.
- **Consequences:** (+) Closes the fail-safe loop at the moment the gap matters (dispatch), for the
  cost of one line. (−) A deferred setup produces a recurring warning until filled — intended, not a
  defect.
- **Alternatives considered:** B no warning, rely on elicitation only (a deferred tier stays
  invisible until it fails). Rejected.

## ADR-6: `profile.yaml` `models:` public-shape change — per-tier {primary, fallback}, back-compat via parse-time normalization

- **Status:** accepted (G3, 2026-08-15)
- **Date:** 2026-08-15
- **Context:** This is the load-bearing decision of the project. FR-8 requires the elicitation and
  the stored profile to carry, **per capability tier**, both a *primary* and a *fallback*
  provider·model. The current shape read by the skill is a bare string per tier — documented in
  `SKILL.md` as, illustratively, `most_capable: <alias>` (example only, per LD-1) — and parsed by
  `scripts/superhuman_profile.py` as `Profile.models: dict[str, str]` (dataclass ~line 270; parsed
  at ~line 493). A bare string cannot hold a primary **and** a fallback, so the public shape of the
  `models:` block must change. The change must not break any pre-existing profile (NFR-2) or any
  operator whose file still uses the bare-string form.
- **Decision:** Extend the value of each `models:` tier from a bare string to a mapping
  `{primary, fallback}`. Preserve backward compatibility by **normalizing at parse time**: a legacy
  bare string is read as `{primary: <that value>, fallback: null}`, and a mapping is taken as-is.
  Normalization happens once, at load, so **every downstream reader of `Profile.models` sees the
  mapping form** and never has to branch on shape. The deterministic writer (C-PROF) always emits
  the mapping form. Both shapes are asserted in tests
  (`tests/test_profile_onboarding.py`). Illustrative only (LD-1), never a shipped default:
  `most_capable: {primary: <alias>, fallback: <alias>}`.
- **Consequences:**
  - (+) Existing profiles and the bare-string form documented in `SKILL.md` keep loading unchanged
    (NFR-2 satisfied); no migration step is forced (A4).
  - (+) Callers are insulated: because normalization is at the parse boundary, no consumer of
    `Profile.models` needs a shape check.
  - (−) **`Profile.models`'s in-memory type changes** from `dict[str, str]` to (effectively)
    `dict[str, dict[str, str | None]]`. Any code or test that today indexes a tier and expects a
    bare string must be updated to read `.["primary"]`. The blast radius is currently small —
    `models` is parsed and stored but consumed by no resolver logic in the script today — but this
    is a public-shape change and MUST be called out in the CHANGELOG (C-HYG) so downstream harness
    integrations that read the profile are warned.
  - (−) The `models:` block now permits a partially-specified tier (primary set, fallback null),
    which is why ADR-5's dispatch-time placeholder warning exists as the safety net.
  - **Supersedes-note:** the `SKILL.md` illustrative snippet showing the bare-string form should be
    updated to the mapping form (still as an example, per LD-1) in C-HYG/C-ORCH so the shipped docs
    show the current shape; the bare string remains *accepted input*, just no longer the shape the
    docs teach.
- **Alternatives considered:** flat compound keys (`most_capable_primary` / `most_capable_fallback`)
  — rejected: breaks the documented `most_capable: <alias>` reading and reads poorly; tier → list
  `[primary, fallback]` — rejected: terser but positionally opaque and less self-documenting than a
  named mapping. See `DESIGN.md` §Open issues ("`models:` schema extension is a public-shape
  change") and §Component C-PROF.

## ADR-7: `write_models_block` preserves comments via a targeted line-span splice, not a full YAML round-trip

- **Status:** accepted (G6, 2026-08-15)
- **Date:** 2026-08-15
- **Context:** Raised as a moderate drift event (G6) during Chunk 5 implementation. The first
  `write_models_block` implementation read the whole `profile.yaml` with `yaml.safe_load` and rewrote
  it with `yaml.safe_dump`, which discards ALL comments in the file. The shipped presets
  (`profiles/presets/*.yaml`) are ~40 lines of load-bearing comments (ladder-ordering rationale,
  install steps) and their own text instructs operators to hand-edit, so real operators do comment
  their profiles. On a #139 first-run write against a pre-existing commented profile, those comments —
  including the `ladder:` documentation — would vanish silently. That is the exact silent-corruption
  class this project (#165 fidelity) exists to prevent; leaving it would have contradicted the
  charter and dev-principle #5 (config-writing is a safety-relevant path → safety in code).
- **Decision:** Option A — make the writer safe by construction. `write_models_block` performs a
  **targeted line-span splice**: it locates the top-level `models:` key's own text span (`_find_models_span`)
  and replaces/inserts only that span (`_splice_models_block`), leaving every other byte —
  comments, `ladder:`, `version:`, blank lines — untouched. No full-document re-serialization. Reading
  still uses `yaml.safe_load` (parsing strips nothing); only the write path changed. Kept PyYAML — no
  new dependency.
- **Consequences:**
  - (+) An operator's hand-authored comments and unrelated config survive a #139 write byte-identical;
    safety lives in the primitive, independent of how the caller (C-KICK) invokes it.
  - (+) No third-party dependency added (ruamel.yaml was the alternative).
  - (−) Line-span detection carries edge-case assumptions (a column-0 comment block between `models:`
    and the next key is absorbed into the span; a file lacking `version:` gets it prepended) — untested
    territory with no trigger in any shipped preset; documented in the Chunk-5 fix report.
  - Fix landed as commit `7b41d8d`; 11 new tests incl. comment-preservation against the real
    `classic-3tier.yaml` preset; 100% line/branch on the rewritten write path.
- **Alternatives considered:** B adopt `ruamel.yaml` for round-trip comment preservation — rejected:
  robust but adds a third-party dependency across every environment superhuman runs, heavier than
  warranted for one block. C leave the writer and guard in the caller (only write when `models:` absent,
  warn before overwrite) — rejected: still strips the ladder's comments on any real update and pushes
  safety onto the caller rather than the primitive (weaker per dev-principle #5).
