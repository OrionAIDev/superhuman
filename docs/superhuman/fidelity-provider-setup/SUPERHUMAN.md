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
- **current state:** COMPLETE — G8 signed off 2026-08-16. All 8 gates fired; 4 external review rounds converged (correctness GO, security SHIP, Codex SHIP); every finding resolved with a regression test. VERSION 1.1.0. Branch `feat/superhuman-fidelity-provider-setup`, HEAD ~111c71e (33 commits), pushed. 297-300 tests green. #139 works end-to-end via the `models set` CLI.
- **next-3-actions:** POST-ACCEPTANCE (not superhuman gates): (1) land `.github/workflows/ci.yml` on main so PR CI fires (from origin/worktree-ci-workflow; its own small PR — needs user confirm); (2) mark PR #2 ready for review (needs user confirm); (3) emit PROJECT COMPLETE terminator once those are settled.
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
[2026-08-15] G5 (Chunks 6-8/8): C-KICK (6d8b325 — first-run provider-neutral tier elicitation wired to write_models_block), C-DISP (64b3fa5 — one-line non-blocking placeholder warning), C-HYG (a09cb3d — VERSION 1.1.0, CHANGELOG [1.1.0], vendor-free doc snippets, TC-17/TC-18). All ✓ spec ✓ full-suite-green ✓ pushed; no drift. Phase 3 implementation COMPLETE.
[2026-08-15] DONE_WITH_CONCERNS (Chunk 8): PM found chunk 7 (64b3fa5) had accidentally deleted the def line of test_license_and_notice_present, silently absorbing its LICENSE/NOTICE assertions into test_dispatch_documents_placeholder_warning. PM restored it as an independent test (bca25bb). Audit-integrity fix — the exact silent-corruption class this project exists to prevent.
[2026-08-15] Phase 3.2 docs-sync: all 8 declared artifacts present + current; DECISIONS.md gained ADR-7 (G6 writer); PLAN marked complete; README verified. Committed 6b29ccb.
[2026-08-15] G7: final docs review approved (declared-artifacts ✓ all present and current); user decision: approve & proceed to preflight + acceptance.
[2026-08-15] Phase 3.3 preflight: FINAL VERDICT GO (3 lenses; 1 Critical + 2 Major found and fixed with regression tests; all re-verified GO). See preflight decision block below.
[2026-08-16] G8 presented; user decision: NOT YET — requested an independent ChatGPT/Codex second-opinion review of the delivered branch before sign-off (consistent with Claude-first/Codex-secondary practice). Codex review dispatched; G8 held open pending its findings.
[2026-08-16] Codex (GPT-5) second-opinion review: DO-NOT-SHIP. PM triage — one REAL blocker the internal 3-lens preflight missed: write_models_block has no CLI subcommand, so phases/0-kickoff.md's "call write_models_block via <dispatch:bash>" is unreachable → #139 doesn't work end-to-end (unit tested in isolation, never integration-checked). Plus legitimate fidelity fixes: "absence=empty" can mask corruption in >=1.1.0 files (version-gate it); symlinked profile clobbered by os.replace; duplicate models: keys; column-0 comment inside block (verify). Headline "Critical agnosticism violation" assessed as FALSE POSITIVE — Codex couldn't run git diff, flagged pre-existing harness-mechanism (dispatch.md Claude-Code table, unchanged by this project) + clearly-marked anti-examples; this project's own snippets are generic placeholders.
[2026-08-16] Post-Codex decisions; user decision: (1) fix the blocker + the fidelity fixes, re-run correctness lens, then re-present G8; (2) LD-1 reading = KEEP internal scoping (pre-existing per-harness table is legitimate mechanism; no agnosticism purge). Hardening pass dispatched. Vindicates the second-opinion request — the CLI-wiring blocker was a genuine hole.
[2026-08-16] Post-Codex hardening COMPLETE (28019d6): FIX 1 `models set` CLI subcommand wired + kickoff recipe now calls it (#139 reachable end-to-end); FIX 2 symlink-resolve before os.replace; FIX 3 duplicate-models-key guard; FIX 4 (was a REAL bug) column-0 comment INSIDE a models block was truncating the span + orphaning a tier — fixed with a lookahead; FIX 5 version-gated the "absence=empty" resume fallback in SKILL.md/pm.md (≥1.1.0 missing section → G6 stale-state, not silent). 283 pass, 2 skip (1 pre-existing + 1 symlink-privilege-gated, monkeypatched companion covers the path). PM adversarially reviewed the CLI wiring + SKILL.md version-gate: sound, HARD-GATE validity condition untouched, this project's own 1.0.3 file + the legacy fixture still resume clean.
[2026-08-16] CONCURRENCY reconciliation: a separate session (the spawned temp-cleanup chip) committed 3f35203 (write_models_block finally-cleanup) to this same branch/worktree while the hardening pass ran. History is clean/linear (8c396ba → 3f35203 → 28019d6), local==remote, both changes' code coexist (finally-cleanup + symlink-resolve + models-set CLI all present), no revert. Open (separate, non-blocking): a cross-session ask to land .github/workflows/ci.yml onto main (it lives only on origin/worktree-ci-workflow; main has no .github, so PR CI can't fire) — deferred to after G8, needs user confirm. Draft PR #2 (whole project) is open, held at G8.
[2026-08-16] Correctness lens RE-VERIFIED all 5 post-Codex fixes + temp-cleanup: GO (286 pass, 2 environmental skips; finally-cleanup and symlink-resolve mutually consistent; HARD-GATE validity unchanged). Preflight now GO across original-3 + preflight-4 + post-Codex-5. Codex review fully addressed. Two new FYI residuals (temp 0600 perms propagate — a tightening; per-entry typo'd key → fail-safe PROMPT_ME) — non-blocking, accepted. Re-presenting G8.
[2026-08-16] G8 re-presented; user decision: NOT YET — requested ANOTHER review pass (the post-Codex hardening had only internal re-verification). Ran 2 in parallel: security lens on the new CLI surface = SHIP (Lows only: JSON shell-quoting, arbitrary --profile, symlink TOCTOU — all within single-operator trust model); Codex re-review of the hardening delta = DO-NOT-SHIP. Codex found (and PM VERIFIED) a real HIGH the internal correctness lens + PM adversarial review both missed: SKILL.md HARD-GATE bullet (line 20) still says "absence=empty" UNCONDITIONALLY, contradicting the version-gated rule in the later section — FIX 5 patched 2 of 3 places, missing the authoritative one. Plus Medium (recipe hard-codes python + unquoted profile path) and Low (YAML-equivalent "models": bypasses the regex dup-key guard). Second Codex pass earned its keep again.
[2026-08-16] Round-3 decisions; user decision: fix ALL three, re-verify. Fixes: A (High) version-gate the HARD-GATE bullet to match the later section; B (Medium+Low-sec) injection-proof the models-set invocation — new --answers-json-file/stdin option so the recipe writes JSON to a temp file (no operator data in a shell word, honoring dev-principle #5) + quote path + python3; C (Low) dup-key guard on canonical span count vs raw_models. Hardening pass dispatched.
[2026-08-16] Round-3 hardening COMPLETE (353e04f): FIX A HARD-GATE bullet now version-gated (matches the later section + pm.md; validity condition + gate-progression untouched — PM adversarially reviewed); FIX B `--answers-json-file`/stdin option + recipe writes JSON via a quoted heredoc to a temp file, quotes the profile path, resolves interpreter as `command -v python || command -v python3` (mirrors pre-commit) — no operator data in a shell word (dev-principle #5 honored at the boundary); FIX C dup-key guard raises ProfileError when raw_models present but canonical `^models:` span count != 1 (covers quoted-key `"models":`). 291 pass via gate, 2 skip. Branch clean/linear on 8d27070. Re-verification (correctness lens + final Codex confirm) dispatched.
[2026-08-16] Round-3 re-verify: correctness lens = GO (294 pass, clean across full history); Codex = DO-NOT-SHIP but its real HIGH (HARD-GATE) confirmed RESOLVED; 3 new findings were over-labeled edge cases (mixed canonical+quoted models key = Low/non-corrupting per correctness lens; interpreter-probe = repo-convention nit + Codex-sandbox artifact; temp-file leak = real small hygiene). PM assessed convergence (round 1 blockers → round 2 CLI blocker → round 3 HARD-GATE High → round 3.5 edge nits). user decision: fix ALL three thoroughly. Round-3.5 pass dispatched: (1) semantic yaml.compose-based dup-models detection (catches quoted "models":); (2) execute-probe interpreter resolution in the recipe (mirrors autonomous-precondition.sh, defeats Windows Store python stub); (3) recipe pipes JSON via stdin (--answers-json-file -), eliminating the temp file + leak.
[2026-08-16] Round-3.5 COMPLETE (bc6ba90): all 3 done — semantic yaml.compose dup-models guard (requires (semantic,canonical) count = (0,0) or (1,1); catches quoted "models":); execute-probe interpreter loop mirroring autonomous-precondition.sh; stdin heredoc (no temp file). 298 pass, 2 skip. Branch clean/linear on c53316a. Final re-verify (correctness lens + Codex confirm on c53316a..bc6ba90) dispatched.
[2026-08-16] Round-3.5 re-verify: correctness lens = GO (298 pass, clean across full history). Codex = SHIP-WITH-FIXES (upgraded from DO-NOT-SHIP): 2 findings — Medium `py -3` interpreter candidate stored as single string `PY="py -3"` then invoked as `"$PY"` → bash seeks a file literally named `py -3` (CONFIRMED real shell bug, breaks only the Windows py-launcher fallback path); Low top-level YAML merge-key could populate models without a direct key node, slipping past the (0,0) guard branch (esoteric). Both real + cheap. user preference = thorough. Round-3.6 dispatched: FIX X interpreter as bash array `PY=(...)` invoked `"${PY[@]}"` (py -3 works); FIX Y compute raw_models first, require (1,1) whenever raw_models is not None (closes merge-key bypass as defense-in-depth). Convergence: Codex now at SHIP-WITH-FIXES with only these two bounded items.
[2026-08-16] Round-3.6 COMPLETE (0f77b28): FIX X interpreter as bash array (py -3 execs correctly; bug + fix repro-confirmed); FIX Y raw_models-first (1,1) guard — the merge-key bypass WAS reachable (safe_load populates models via <<: while yaml.compose sees no node), now closed. 300 pass (297 via hook). Both Codex findings were real. Final Codex confirm dispatched.
[2026-08-16] G8: ACCEPTANCE SIGNED OFF — project complete. All 8 gates fired with HITL (G0/G1/G2/G3/G4/G5/G7/G8); preflight GO; 4 external review rounds converged (correctness GO, security SHIP, Codex SHIP). user decision: sign off — complete. Post-acceptance logistics (CI workflow on main, PR #2 ready) pending user confirmation before the PROJECT COMPLETE terminator.
[2026-08-16] Codex FINAL CONFIRM: **SHIP** — no new issues, both round-3.6 findings resolved. **FULL CONVERGENCE: all three reviewers agree — correctness lens GO, security SHIP, Codex SHIP.** Review arc across 4 external rounds fully resolved: round 1 (internal 3-lens) blockers → round 2 (Codex) CLI-wiring blocker → round 3 (Codex) HARD-GATE High → round 3.5 (Codex) edge nits → round 3.6 (Codex) py-3 shell bug + merge-key guard. Every finding fixed with a regression test and re-verified. HEAD 26c22d5, 32 commits vs main, 297-300 tests green, VERSION 1.1.0, #139 works end-to-end. Re-presenting G8 (final).
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
| 6 | Phase-0 #139 elicitation sub-flow (C-KICK) | phases/0-kickoff.md, roles/pm.md, tests/test_content.py | sonnet | done (6d8b325) | 2026-08-15 | 2026-08-15 |
| 7 | Dispatch-time placeholder warning (C-DISP) | adaptation/dispatch.md, roles/pm.md, tests/test_content.py | sonnet | done (64b3fa5) | 2026-08-15 | 2026-08-15 |
| 8 | Hygiene: VERSION + CHANGELOG + README + SKILL snippet (C-HYG) | VERSION (1.1.0), CHANGELOG.md, README.md, SKILL.md, tests/test_content.py | sonnet | done (a09cb3d); DONE_WITH_CONCERNS resolved by test-fix (bca25bb) | 2026-08-15 | 2026-08-15 |

## Drift notes
<!-- Append-only. Format: [<ISO timestamp>] Chunk <n>: <severity> — <one-line trigger>; action: <taken> -->

## Drift notes
[2026-08-15] Chunk 5: MODERATE — `write_models_block` (c42d7ec) full-round-trips profile.yaml via yaml.safe_dump, silently stripping ALL comments from an existing operator profile (incl. the ladder's load-bearing preset comments). Real regression risk for #139 setup on a pre-existing commented profile; against superhuman's fidelity ethos. Surfaced as G6 — decision pending (targeted-patch vs ruamel vs caller-guard). Chunk 6 (C-KICK) blocked on this decision. RESOLVED (7b41d8d): rewrote write_models_block as a targeted line-span splice; comments/ladder preserved byte-identical; 11 new tests incl. preservation against the real classic-3tier preset; 100% branch on write path. Chunk 6 unblocked.

## Preflight decision (Phase 3.3) — 2026-08-15

**Verdict: NO-GO** (resolved by fixes below; re-run pending). Three-lens adversarial fan-out over the delivered substrate (HEAD 6b29ccb).

### Blockers (must fix before acceptance)
- **[correctness] Critical — write-before-validate, non-atomic, no backup.** `write_models_block` (`scripts/superhuman_profile.py:1832`) writes the user's `profile.yaml` to disk, THEN `load_profile()` validates at :1834 — so a splice yielding invalid YAML corrupts a previously-valid config with no recovery (repro-confirmed). The exact fidelity-loss class this project exists to prevent. Fix: temp-render → validate → atomic `os.replace` (mirror `cmd_init`); optional `.bak`.
- **[correctness] Major — silent column-0 comment deletion.** `_find_models_span` (`:1697`) absorbs a column-0 comment sitting between the `models:` block and the next key into the replaced span and deletes it, violating ADR-7's byte-identical contract (repro-confirmed; realistic — presets comment before each section). Fix: terminate the span at the first column-0 comment.
- **[design-conformance] Major — surrogate-user.md contradicts the schema it references.** `roles/surrogate-user.md:97-99` states it does NOT emit the six-field report, dropping commands/risks/next-action — contradicts FR-4 ("no role silently omits it") and the schema doc's "do not drop fields." Not caught by presence-only tests. Fix: reconcile its strict verdict as the `conclusion` specialization emitting all six fields (design intent per FR-4).

### Recommended fixes (should fix)
- **[correctness] Minor** — uncaught `ValueError` on a non-mapping `models:` scalar (`:1806`); guard and raise `ProfileError` per the docstring.
- **[correctness] FYI/Windows** — `read_text`/`write_text` normalize newlines, so "byte-identical" fails on CRLF/mixed files; read/write with `newline=""` for true preservation (relevant: we author on Windows).

### Acknowledged risks (no action)
- **[security] FYI** — a `PROMPT_ME` tier degrades to the harness default with a non-blocking warning (intended FR-10 fail-safe). `_MODELS_KEY_RE` mis-anchor needs a self-inflicted malformed local file (not reachable from elicitation).
- Security lens overall: **GO** — no secrets, YAML-injection-resistant (empirically verified), hook `unset` doesn't weaken the gate, no new dependency.

### Rollback plan
- Trigger: a fix regresses the suite or the re-run lens still finds a Critical.
- Procedure: `git revert` the offending fix commit(s) on `feat/superhuman-fidelity-provider-setup`; the branch is unmerged, so `main` is untouched. Per-chunk commits isolate each change.
- Recovery objective: branch back to green (267 pass) at worst; `main` never affected (ceiling OrionTest, PR not yet opened).

### Resolution: fix the 3 blockers + 2 recommended (code/prose fixes to already-approved design — NOT design drift, so no G6), then re-run the correctness + design-conformance lenses. GO only after the re-run is clean.

**RESOLVED 2026-08-15:**
- Blockers 1 (Critical atomicity), 2 (Major column-0 comment), + should-fixes (ValueError guard, Windows newline preservation): fixed in `f330a93` (write_models_block now temp-renders → validates → `os.replace`; span terminates on any column-0 line; newline-preserving IO; 5 new tests; 100% line/branch on the write path).
- Blocker 3 (Major surrogate schema contradiction): resolved via ADR-8 — explicit recorded carve-out across `conventions/subagent-return-schema.md`, `roles/surrogate-user.md`, and `DECISIONS.md`; added regression test `test_surrogate_schema_exception_is_recorded` (closes the presence-only-test gap the lens exploited).
- Re-run of the correctness + design-conformance lenses COMPLETE — both **GO**. Correctness lens repro-confirmed all 4 fixes (atomicity, comment-span, ValueError guard, newline preservation); design-conformance lens confirmed ADR-8 closes the FR-4 gap with no vendor leak. 276 tests pass.

### FINAL PREFLIGHT VERDICT: **GO** (2026-08-15, HEAD 185ca7d)
All three lenses clear: correctness ✓, security ✓ (clean from first pass — no secrets, YAML-injection-resistant, hook unset safe, no new dep), design-conformance ✓.
**Acknowledged residuals (non-blocking, accepted):** (1) `write_models_block` leaks its `.tmp` sibling only if `load_profile` raised a non-`ProfileError` (e.g. OSError) — original file stays intact; tracked as a follow-up polish (`finally` cleanup). (2) The regenerated `models:` block is LF even inside a CRLF file (untouched regions are preserved byte-for-byte as promised; the rewritten block is YAML-valid). (3) `.tmp` name is fixed, not randomized — fine for single-threaded onboarding. None affect correctness, security, or the immutable constraint.

## Archive log
<!-- Append-only. Format: [<ISO timestamp>] archived <chunk> to archive/<dir>/; reason: <reason> -->

## Recommendation overrides
<!-- Append-only. Format: [<ISO timestamp>] G<n>: PM recommended <X>; user chose <Y>; reason: <if given> -->

## Retuning notes
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <observation about user pattern>; bias adjustment: <going-forward note> -->
[2026-08-15] G0/G1/G2: user approved PM recommendation as-is three times running (incl. the one flagged scope choice, taking the recommended option); bias adjustment: user trusts crisp recommend-first framing — keep gates tight, lead with a clear recommendation, avoid padding options the user is unlikely to want. Do not infer they want fewer gates (HITL-H is locked) — only fewer/tighter questions per gate.
[2026-08-15] G3: user approved the full design package + both flagged extras (DECISIONS.md, light README) as recommended (4th consecutive as-is approval); bias adjustment: pattern holds — continue leading with the recommended option and surfacing only genuine sub-choices. Watch for over-asking; the user has not overridden once.
[2026-08-15] G4/G6: user again took the PM recommendation (approve TEST.md; G6 Option A). 6 gates, 0 overrides. Bias adjustment holds — but the G6 surface was CORRECT to raise despite the streak: it was a real data-integrity fork, not a rubber-stamp candidate. Lesson: the no-override streak is not a signal to stop surfacing genuine moderate+ drift; it's a signal the recommendations are well-calibrated. Keep surfacing real forks; keep trimming trivial ones.
[2026-08-16] G8: user signed off as recommended, but ONLY after requiring 4 external review rounds (Codex + security) beyond the internal preflight — twice deferring G8 to get a second opinion. Those rounds caught 2 REAL defects internal review missed (CLI-wiring blocker, HARD-GATE inconsistency). Bias adjustment: for high-stakes substrate / public-shipping / self-improvement projects, PROACTIVELY build an external adversarial second-opinion pass (Codex + a fresh security lens on any new surface) into the preflight BEFORE the first G8 — don't treat the internal 3-lens preflight as sufficient for changes to superhuman's own substrate. The user's instinct that internal review has blind spots was correct twice.
