# Test plan: Fidelity + first-run provider setup

**Created:** 2026-08-15
**Owner:** QA
**Source design:** `DESIGN.md`

## Coverage targets

| Surface | Target | Notes |
|---|---|---|
| Code: `scripts/superhuman_profile.py` new/changed functions (C-PROF) | ≥ 90% line coverage, ≥ 80% branch coverage | Above the conventions/testing.md 80%-line default because this is the one genuinely deterministic, safety-relevant surface in the project (dev-principle #5: config generation is code, not LLM free-text) — the `models:` generator, normalization, and placeholder writer must be exercised on both the happy path and the legacy/decline branches. |
| Doc/template/role/phase surfaces (C-RS, C-TPL, C-ROLES, C-ORCH, C-KICK, C-DISP, C-HYG) | 100% presence-tested | These are Markdown, not executable code — see "Presence vs line coverage" below. Every FR/NFR they own has at least one deterministic presence/grep assertion in `tests/test_content.py`; none are line-covered because there are no lines to execute. |
| Inference components | See `## Inference coverage` below | Ruling: no eval-suite is warranted for this project; presence checks carry the load. |

### Presence vs line coverage

Per DESIGN §Testing strategy, this project is almost entirely a documents-and-templates substrate
change (`conventions/`, `templates/`, `roles/*.md`, `phases/*.md`, `SKILL.md`,
`adaptation/dispatch.md`). None of that is executable Python, so "coverage" for those surfaces means
**deterministic presence/ordering/grep assertions**, not a line/branch percentage. The only surface
where a numeric coverage target is meaningful is `scripts/superhuman_profile.py` (C-PROF), which is
existing, already-tested Python being extended.

## Test cases

### TC-1: Canonical return-schema doc exists and names the six fields once
- **Requirement:** FR-3
- **Setup:** Read `conventions/subagent-return-schema.md` after Chunk 1 lands.
- **Steps:** 1. Assert the file exists. 2. Assert it contains, in order, `conclusion`, `evidence`, `commands`, `assumptions`, `risks`, `next-action` (or the header-cased equivalents used as field labels). 3. Grep the rest of the shipped tree for a second full six-field definition (only pointers/references are allowed elsewhere).
- **Expected:** File present; six fields named once, in the canonical order; no competing full definition found.
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_return_schema_doc_defines_six_fields_once`

### TC-2: Resume packet section — position and seven labelled fields
- **Requirement:** FR-1
- **Setup:** Read `templates/SUPERHUMAN.md.tpl` after Chunk 2 lands.
- **Steps:** 1. Assert `## Resume packet` exists. 2. Assert it appears before the volatile log sections (`## Decisions log`, `## Chunk log`, or equivalent — whichever the template names as append-only/volatile). 3. Assert all seven labels are present: objective, immutable constraints, decisions-locked, ruled-out paths, current state, next-3-actions, evidence-pointers.
- **Expected:** Section present, correctly ordered, all seven fields labelled and matching the shared-core §5 field set exactly (per FR-1 acceptance criteria).
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_resume_packet_section_present_and_ordered`

### TC-3: Decisions-locked is structurally distinct from Decisions log
- **Requirement:** FR-6
- **Setup:** Read `templates/SUPERHUMAN.md.tpl`.
- **Steps:** 1. Assert `## Decisions locked` exists as its own H2, distinct from `## Decisions log`. 2. Assert both headings are present simultaneously (one does not replace the other).
- **Expected:** Two distinct H2 sections; a reader can tell "what happened" from "what may not be reopened" by heading alone.
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_decisions_locked_distinct_from_decisions_log`

### TC-4: Every role references the canonical schema doc by pointer
- **Requirement:** FR-4
- **Setup:** Read all of `roles/{pm,architect,developer,qa,tester,business-expert,surrogate-user}.md` after Chunk 3 lands.
- **Steps:** For each role file, assert it contains the string `conventions/subagent-return-schema.md`.
- **Expected:** All seven role files reference the schema doc; none silently omit it (FR-4 acceptance criteria).
- **Type:** unit (content/presence), parametrized over the 7 role files
- **New test:** `tests/test_content.py::test_role_references_canonical_schema[role_file]`

### TC-5: Specialized verdict schemas are retained, not replaced
- **Requirement:** FR-4, A3
- **Setup:** Read `roles/qa.md`, `roles/tester.md`, `roles/surrogate-user.md`, `roles/architect.md`.
- **Steps:** 1. Assert `qa.md` and `tester.md` still define `approved | issues_found` (or the shared QA/Tester verdict schema). 2. Assert `surrogate-user.md` still defines `ACCEPT | ESCALATE`. 3. Assert `architect.md` still documents its option-table contract. 4. Assert each of these is described as riding in / specializing the canonical schema's `conclusion` field (e.g. the word "conclusion" or "specializ-" appears adjacent to the verdict definition).
- **Expected:** No existing verdict schema is deleted or silently replaced; each is reconciled as a specialization per FR-4/A3.
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_verdict_schemas_specialize_canonical_conclusion`

### TC-6: PM output discipline names the canonical schema and keeps prose-rejection
- **Requirement:** FR-5
- **Setup:** Read `roles/pm.md` § Output discipline (existing required section, asserted by `test_pm_has_required_sections`).
- **Steps:** 1. Assert the Output discipline section references `conventions/subagent-return-schema.md`. 2. Assert the existing free-form-prose rejection language is still present (do not regress the pre-existing assertion this test plan inherits).
- **Expected:** Output discipline names the canonical schema as the accepted shape; rejection-of-prose rule preserved verbatim in spirit.
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_pm_output_discipline_names_canonical_schema`

### TC-7: Read-packet-first resume order and refresh-at-each-gate are documented
- **Requirement:** FR-2
- **Setup:** Read `SKILL.md` and `roles/pm.md` after Chunk 4 lands.
- **Steps:** 1. Assert at least one of the two files states that the PM reads the Resume packet first on resume (before reconstructing from logs). 2. Assert at least one of the two files states the packet is refreshed at every gate (not append-only).
- **Expected:** Both obligations documented; docs match DESIGN §Data flow "Resume" paragraph.
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_orch_documents_read_packet_first_and_refresh`

### TC-8: Locked decisions are not relitigated; reopening is a surfaced event
- **Requirement:** FR-7
- **Setup:** Read `SKILL.md` and `roles/pm.md`.
- **Steps:** 1. Assert language stating a locked decision is not reopened/relitigated on resume. 2. Assert language stating that changing a locked decision requires an explicit surfaced action (gate or drift entry), i.e. is never a silent edit.
- **Expected:** Both semantics present per FR-7 acceptance criteria.
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_orch_documents_locked_not_relitigated`

### TC-9: Backward-compatible resume — pre-existing SUPERHUMAN.md without new sections
- **Requirement:** NFR-2
- **Setup:** New fixture file `tests/fixtures/superhuman_legacy_no_resume_packet.md` (or equivalent) — a `SUPERHUMAN.md` containing a valid `## Decisions log` (so the existing HARD-GATE validity check passes) but **no** `## Resume packet` and **no** `## Decisions locked`.
- **Steps:** 1. Run the same validity/resume check the HARD-GATE describes against the fixture (via whatever deterministic surface exists — if resume is currently pure-prose/PM-judgment with no script, assert instead that `SKILL.md`/`pm.md` explicitly states absence of the new sections is treated as empty, never as corruption, AND keep the fixture as a regression anchor for any future script-based resume check). 2. Assert no exception/error condition is raised or documented as required.
- **Expected:** Fixture resolves through the resume path with no error; absence of the new sections is explicitly documented as "treated as empty," not corruption (NFR-2 acceptance criteria).
- **Type:** unit (content/presence + fixture)
- **New test:** `tests/test_content.py::test_backward_compat_fixture_resumes_without_error`

### TC-10: `models:` generator writes per-tier `{primary, fallback}` and round-trips
- **Requirement:** FR-9
- **Setup:** Use the existing `project` fixture pattern from `tests/test_profile_onboarding.py`; call the new C-PROF writer function with elicited answers for all three tiers.
- **Steps:** 1. Generate/write a `profile.yaml` `models:` block via the new writer (creating the file/section if absent, per FR-9). 2. Load it back with `sp.load_profile`. 3. Assert each of `most_capable`, `standard`, `cheap` resolves to a mapping with `primary` and `fallback` keys.
- **Expected:** Written shape round-trips through the existing loader without modification to `_TOP_KEYS`/parsing beyond the `models` normalization; dispatch can resolve tiers from it without further edits (FR-9 acceptance criteria).
- **Type:** unit
- **New test:** `tests/test_profile_onboarding.py::test_models_generator_round_trips_primary_fallback`

### TC-11: Legacy bare-string `models:` shape still loads
- **Requirement:** NFR-2 (schema evolution), DESIGN Error handling
- **Setup:** A `profile.yaml` with `models: {most_capable: opus}` (bare string per current `SKILL.md` documentation).
- **Steps:** 1. Load via `sp.load_profile`. 2. Assert the loaded `Profile.models["most_capable"]` normalizes to `{"primary": "opus", "fallback": None}` (or the DESIGN-specified normalized shape) rather than raising or staying an un-normalized string.
- **Expected:** Legacy bare-string form parses; downstream always sees the mapping form (DESIGN §Error handling "Malformed / legacy `models:` shape").
- **Type:** unit
- **New test:** `tests/test_profile_onboarding.py::test_legacy_bare_string_models_normalizes_to_mapping`

### TC-12: Decline path writes a neutral, vendor-free placeholder
- **Requirement:** FR-10
- **Setup:** Call the C-PROF writer with all three tiers declined/deferred.
- **Steps:** 1. Generate the `models:` block. 2. Assert every tier's `primary`/`fallback` is a neutral self-documenting placeholder token (e.g. `PROMPT_ME`) rather than any concrete vendor/model name. 3. Assert the written file still loads via `sp.load_profile` (fails safe, not fails loud).
- **Expected:** No vendor assumed on decline; the placeholder is self-documenting and the file remains valid/loadable (FR-10 acceptance criteria).
- **Type:** unit
- **New test:** `tests/test_profile_onboarding.py::test_decline_path_writes_neutral_placeholder`

### TC-13: Google-style docstrings on all new C-PROF functions
- **Requirement:** conventions/python.md (mechanical, unconditional)
- **Setup:** Diff `scripts/superhuman_profile.py` for Chunk 5.
- **Steps:** QA (Phase 3.1 reviewer) reads every new/modified module, class, method, and function and checks for `Args:`/`Returns:`/`Raises:` per Google style.
- **Expected:** Zero missing docstrings. This is a manual QA-review check (Phase 3.1), not an automated content test — flagged here so the Developer chunk-plans for it up front.
- **Type:** review (Phase 3.1 coverage review), not an automated pytest case

### TC-14: Kickoff elicitation — 3 tiers × {primary, fallback}, no vendor pre-filled
- **Requirement:** FR-8, NFR-1
- **Setup:** Read `phases/0-kickoff.md` after Chunk 6 lands.
- **Steps:** 1. Assert Step 3 (workflow preferences / G1) contains an elicitation covering all three tiers (`most_capable`/`most-capable`, `standard`, `cheap`) crossed with `primary` and `fallback`. 2. Assert no concrete vendor/model name (e.g. a specific provider's product name) appears as a pre-filled default answer in the elicitation text — vendor names may appear only as clearly-marked illustrative examples (per the immutable constraint).
- **Expected:** Elicitation step present and provider-neutral; no vendor pre-filled as *the* answer (FR-8 acceptance criteria).
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_kickoff_elicits_three_tiers_primary_fallback`

### TC-15: Decline/first-run-absent path documents the neutral placeholder
- **Requirement:** FR-10
- **Setup:** Read `phases/0-kickoff.md` and `roles/pm.md` (G1) after Chunk 6 lands.
- **Steps:** Assert the decline/defer path is documented and names the neutral placeholder behavior (fails safe, prompts later) rather than silently assuming a vendor.
- **Expected:** Decline/defer language present and consistent with C-PROF's placeholder shape (TC-12).
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_kickoff_decline_path_is_neutral_and_fails_safe`

### TC-16: Dispatch-time placeholder warning is documented
- **Requirement:** FR-10, OQ-5
- **Setup:** Read `adaptation/dispatch.md` and `roles/pm.md` after Chunk 7 lands.
- **Steps:** 1. Assert a one-line, non-blocking (Type B) warning rule is documented for when a tier resolves to an unfilled placeholder at dispatch time. 2. Assert the rule is explicitly non-blocking/non-pausing (does not interrupt autonomous progression, per DESIGN OQ-5 recommendation).
- **Expected:** Warning rule present in both files (dispatch mechanism + PM behavior), named as Type B.
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_dispatch_documents_placeholder_warning`

### TC-17: No vendor baked in as a default anywhere in the changed shipped files (NFR-1/FR-10 grep gate)
- **Requirement:** NFR-1, FR-10, the locked immutable constraint
- **Setup:** The full set of files changed by this project: `conventions/subagent-return-schema.md`, `templates/SUPERHUMAN.md.tpl`, `roles/*.md` (7 files), `SKILL.md`, `scripts/superhuman_profile.py`, `phases/0-kickoff.md`, `adaptation/dispatch.md`, `VERSION`, `CHANGELOG.md`, `README.md`.
- **Steps:** 1. Grep the changed files for a fixed list of concrete vendor/product names (e.g. specific model-family names). 2. For every hit, assert it sits in a line/context that is clearly marked as an illustrative example (e.g. "or your harness's alias for...", "e.g.", "for example") rather than being assigned as *the* default/required value in a template, generator, or elicitation default. 3. Specifically assert the C-PROF placeholder writer (TC-12) and the C-KICK elicitation defaults (TC-14) never emit a concrete vendor as the unfilled-tier value.
- **Expected:** Every vendor-name occurrence in the diff is an explicit, clearly-marked example; none function as a default or required value (NFR-1/FR-10 acceptance criteria — this is the IMMUTABLE CONSTRAINT test named explicitly in this project's charter).
- **Type:** unit (grep/content)
- **New test:** `tests/test_content.py::test_no_vendor_baked_as_default_in_changed_files`
- **Note:** `SKILL.md` already contains a pre-existing, correctly-marked example (`models: most_capable: opus  # or your harness's alias for "current best"`) predating this project — this test must accept that pattern (comment-marked example) as compliant, and must fail only on an *unmarked* default.

### TC-18: Skill hygiene — VERSION bump and dated CHANGELOG entry
- **Requirement:** NFR-4
- **Setup:** Read `VERSION` and `CHANGELOG.md` after Chunk 8 lands.
- **Steps:** 1. Assert `VERSION` is incremented relative to the pre-project value and is valid semver (reuses `test_version_is_semver`). 2. Assert `CHANGELOG.md` has a dated entry naming both the #165 (fidelity) and #139 (provider setup) changes. 3. Assert the entry text contains no AI/model/provider attribution — role names only (NFR-6).
- **Expected:** VERSION bumped per the skill's own bump rule; CHANGELOG entry present, dated, correctly attributed.
- **Type:** unit (content/presence)
- **New test:** `tests/test_content.py::test_changelog_entry_names_165_and_139`

## Inference coverage
<!-- For projects with inference-driven components (skills, prompts, LLM-integration). Skip section if not applicable. -->

**Ruling: a full `## Inference coverage` eval suite (eval-suite bank, embedding-similarity/rubric
scoring, output-quality benchmark) is NOT warranted for this project. Documenting this explicitly per
the QA role contract, not omitting the section.**

Rationale: the only "inference-driven component" here is superhuman's own LLM orchestration — the PM
and role subagents honoring prose conventions (read-packet-first, locked-not-relitigated, the
elicitation step, neutral-placeholder language). Per DESIGN §Testing strategy, these are explicitly
covered by **presence/pointer assertions**, not behavioral evals: "prose-convention (assert presence,
not behavior)." Building an LLM-eval bank to check whether a PM subagent *actually* reads the Resume
packet first in a live session would require live multi-turn harness runs with no cheap ground truth,
and duplicates what `tests/test_content.py`'s existing pattern already does cheaply and
deterministically (it has covered analogous prose-convention regressions — e.g.
`test_skill_md_has_autonomous_progression_rule`, `test_pm_has_phase3_heartbeat` — since v0.1.3/v0.2.x
without an eval suite).

If a future live-run smoke test (of the kind that produced the v0.1.3-rc1/rc2/rc3 fixes referenced in
`test_content.py`) surfaces a PM that skips read-packet-first or silently reopens a locked decision in
practice, that becomes a **new presence assertion plus a regression note**, not a retroactive eval
suite — consistent with how every prior fidelity regression in this codebase has been fixed.

### Eval suites
| Suite | What it measures | Model under test | Pass criteria |
|---|---|---|---|
| _(none — see ruling above)_ | | | |

### Edge-case coverage

Not applicable as an eval-suite concept here; edge cases for the deterministic C-PROF surface are
covered as ordinary unit tests (TC-11 legacy bare-string, TC-12 decline/placeholder) rather than as
inference edge cases.

### Regression tests

No prior regressions exist for this project (it is new). TC-9 (backward-compat fixture) is written as
a standing regression anchor from the start: a `SUPERHUMAN.md` predating the Resume-packet/
Decisions-locked sections must always resume without error, and this fixture must never be deleted
per the testing-conventions "never delete a regression test" rule, even after the new sections become
universal in practice.

### Output-quality benchmarks

Not applicable — no generative/user-facing output is produced by this project's components.

## Backup strategy
<!-- For chunks that modify existing code, what gets backed up (per the file-backup safety check at G4). -->

This project modifies existing git-tracked files on feature branch `feat/superhuman-fidelity-provider-setup`
off `main`. **Git is the backup mechanism** — no file-copy backup directory is created or needed. Every
chunk lands as its own commit (per PLAN.md's TDD-per-chunk discipline), so any single chunk's changes
can be isolated and reverted independently.

| Files to be modified | Backup location | Restore command |
|---|---|---|
| `SKILL.md` | version control (`main` branch tip, pre-project) | `git checkout main -- SKILL.md` or `git revert <chunk-4-sha>` |
| `roles/pm.md`, `roles/architect.md`, `roles/developer.md`, `roles/qa.md`, `roles/tester.md`, `roles/business-expert.md`, `roles/surrogate-user.md` | version control (`main` branch tip, pre-project) | `git checkout main -- roles/<file>.md` or `git revert <chunk-3-sha>` |
| `templates/SUPERHUMAN.md.tpl` | version control (`main` branch tip, pre-project) | `git checkout main -- templates/SUPERHUMAN.md.tpl` or `git revert <chunk-2-sha>` |
| `scripts/superhuman_profile.py` | version control (`main` branch tip, pre-project) | `git checkout main -- scripts/superhuman_profile.py` or `git revert <chunk-5-sha>` |
| `phases/0-kickoff.md` | version control (`main` branch tip, pre-project) | `git checkout main -- phases/0-kickoff.md` or `git revert <chunk-6-sha>` |
| `adaptation/dispatch.md` | version control (`main` branch tip, pre-project) | `git checkout main -- adaptation/dispatch.md` or `git revert <chunk-7-sha>` |
| `VERSION`, `CHANGELOG.md`, `README.md` | version control (`main` branch tip, pre-project) | `git checkout main -- VERSION CHANGELOG.md README.md` or `git revert <chunk-8-sha>` |
| `conventions/subagent-return-schema.md` (new file, not a modification) | version control (does not exist pre-project) | `git rm conventions/subagent-return-schema.md` or `git revert <chunk-1-sha>` |

If any chunk needs to be dropped after landing rather than reverted, superhuman's own
**archive-never-delete** convention applies to any removed work product (per
`references/deprecating-a-system/SKILL.md`'s product-vs-archive scoping) — not applicable here since
nothing in this project is a deployable product artifact, but noted for completeness per the QA
contract's backup-strategy instruction.

## Test execution

```bash
# Full suite
pytest tests/

# Per-component (used in Phase 3.1)
pytest tests/test_content.py -k "return_schema or resume_packet or decisions_locked or role_references_canonical_schema or verdict_schemas or pm_output_discipline or orch_documents or backward_compat or kickoff_elicits or kickoff_decline or dispatch_documents or vendor_baked or changelog_entry"
pytest tests/test_profile_onboarding.py -k "models_generator or legacy_bare_string or decline_path"
```
