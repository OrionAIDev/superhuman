# Changelog

All notable changes to this project will be documented in this file. Format adapted from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning per semver.

## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

## [1.1.0] - 2026-08-15

### Added

- **`--slug` / `--project` on `superhuman_profile.py check`**, threaded through
  `scripts/autonomous-precondition.sh`. Project-state preconditions are questions about one
  project, and a repo may hold several under `docs/superhuman/`; the slug says which.
- **`--kickoff`** on the same pair, for `phases/0-kickoff.md` Step 3 only, where the project's own
  state is still being written. It defers the two project-state checks and nothing else — the rung
  and git+remote are still enforced — and kickoff now re-runs the gate unflagged once
  `SUPERHUMAN.md` and `GOAL.md` exist. That re-run is the one that authorizes the level.
- `tests/test_precondition_scope.py` — the multi-project regression fixture whose absence let all
  of the below ship: two project dirs in one repo, one compliant and one not, asserting the gate
  answers about the one it was named.
- **`## Resume packet`** in the SUPERHUMAN template (`templates/SUPERHUMAN.md.tpl`) — a
  kept-current, single-read handoff block (objective, immutable constraints, decisions-locked
  pointer, ruled-out paths, current state, next-3-actions, evidence-pointers) positioned above the
  volatile logs. Three of the seven fields point at their existing canonical home instead of
  restating it (decisions-locked, current state, evidence-pointers); the other four have no other
  home and are restated in place (#165).
- **`## Decisions locked`** — a first-class template section, structurally distinct from the
  append-only `## Decisions log`, naming decisions that are not relitigated on resume. Reopening a
  locked decision is now a surfaced gate/drift event, never a silent edit (#165).
- **`conventions/subagent-return-schema.md`** — the canonical six-field subagent return schema
  (conclusion, evidence, commands, assumptions, risks, next-action), referenced by pointer from
  every role file (PM, Architect, Developer, QA, Tester, Business Expert, Surrogate User). Existing
  verdict schemas — QA/Tester's `approved|issues_found`, Surrogate User's `ACCEPT|ESCALATE`,
  Architect's option-table contract — now ride in `conclusion` as named specializations rather than
  competing definitions (#165).
- **Read-packet-first resume + locked-not-relitigated orchestration semantics**, documented in
  `SKILL.md` and `roles/pm.md`: on resume the PM reads the Resume packet before reconstructing
  state from the logs, and refreshes the packet at every gate rather than letting it go stale. A
  pre-existing `SUPERHUMAN.md` written before this change, with neither new section, still resumes
  without error — their absence is treated as empty, never as corruption (#165).
- **First-run, provider-neutral model-tier setup (#139).** `phases/0-kickoff.md` Step 3 now elicits,
  per capability tier (`most_capable` / `standard` / `cheap`), both a primary and a fallback
  provider·model, and hands the answers to a new deterministic writer,
  `scripts/superhuman_profile.py::write_models_block`, rather than letting inference free-text the
  config (dev-principle #5: config generation is code). Declining or deferring the elicitation
  writes the neutral, self-documenting `PROMPT_ME` placeholder instead of assuming any provider,
  and the resulting file still loads.
- **Dispatch-time placeholder warning (#139).** `adaptation/dispatch.md` and `roles/pm.md` document
  a one-line, non-blocking (Type B) warning for when a dispatch's resolved tier is still an unfilled
  `PROMPT_ME` placeholder — it names the tier and proceeds; it never pauses autonomous progression.

### Changed

- **`profile.yaml` `models:` public shape (#139, ADR-6).** Each tier's value grows from a bare
  string to a mapping, `{primary, fallback}`, so a tier can carry both a primary and a fallback
  provider·model. Normalization happens once, at parse time, in `scripts/superhuman_profile.py`: a
  legacy bare string (`most_capable: opus`) is read as `{"primary": "opus", "fallback": None}`, and
  a mapping passes through unchanged — every downstream reader of `Profile.models` sees the mapping
  form regardless of which shape the file on disk uses. Existing profiles keep loading unchanged;
  no migration is forced. **Blast-radius note for downstream integrations:** any code that indexed
  a tier and expected a bare string must be updated to read `.["primary"]` instead.
- **`SKILL.md`'s and `README.md`'s illustrative `models:` snippets** now show the current per-tier
  `{primary, fallback}` shape with generic, harness-neutral placeholder aliases, replacing the
  superseded bare-string illustration (#139, ADR-6 supersedes-note).

### Deprecated

### Removed

### Fixed

- **The publication guard could not see 11 of the 163 tracked files.** `SCANNED_SUFFIXES` was an
  *allowlist* of twelve extensions, so every file type nobody thought to add was invisible —
  including `hooks/session-start` and `scripts/git-hooks/pre-commit`, both shipped executables and
  exactly where an absolute server path would hide, plus `LICENSE`, `VERSION`, `.gitignore`, two
  `pyproject.toml`s and `examples/promote.sh.example`. The suite's silence about them was
  suffix-blindness, not a clean read. Replaced with `SKIPPED_SUFFIXES`, a binary denylist, behind an
  `is_scanned()` predicate: the default is now *scanned*, so a new file type is covered the day it
  lands. Coverage went 152 → 161 of 163; the only two exclusions are the guard's own pattern and
  test modules, each exempt for a stated reason. `test_guard_covers_every_tracked_file_but_binaries`
  pins it so the allowlist cannot creep back.
- **The ssh/email pattern flagged RFC 8375 `.internal` placeholders.** `deploy@prod.example.internal`
  in `examples/promote.sh.example` is a deliberate fixture; `.internal` now joins `.test`,
  `.invalid` and `.localhost` in the reserved-name exclusion. A guard that fires on its own
  documentation is a guard people learn to bypass.
- **`scripts/release.sh` printed the original author's release page from every fork.** `REPO_SLUG`
  was a hardcoded constant feeding `RELEASE_URL`, so a fork that ran the release driver was told
  its release lived in a repository it does not own — a functional genericization defect,
  contradicting v1.0.0's central claim. The slug is now derived from the cwd repo's `origin`
  remote (both HTTPS and scp-style forms), overridable with `SUPERHUMAN_REPO_SLUG` when `origin`
  is not the publishing remote. A repo with no origin omits the link instead of guessing one.
  Covered by `tests/test_release_sh.py`, including a property-shaped guard that fails on any
  literal `<owner>/<repo>` reappearing in the script.
- **The activation gate enforced one and a half of the four preconditions `SKILL.md` claimed it
  checked deterministically** (roadmap #143). All four now hold, and the belt-and-suspenders
  design intent is true rather than asserted:
  - **The rollback-plan check answered about the wrong project.** `rollback_plan_gap()` took only a
    repo root and `rglob`-ed every `SUPERHUMAN.md` beneath it, returning on the first sibling that
    declared `Modifies-existing-code: yes` with no `ROLLBACK.md`. In a repo with concurrent
    projects that produced a false BLOCK on a compliant project — and, worse, a **vacuous PASS**
    when no sibling tripped it, authorizing a HITL-L run having inspected nothing about itself. It
    is now scoped to `--slug`, and with no slug it exits 4 rather than guessing.
  - **A missing `Modifies-existing-code:` field is now a gap, not a pass.** The absence of a
    declared fact is not evidence that the fact is false. Hand-written, pre-v0.8.0, and
    mid-Phase-0 manifests all previously slipped through unexamined.
  - **`GOAL.md` is enforced again at HITL-M/L.** The resolver contained zero references to it; a
    project root with no `GOAL.md` exited 0, so a loop could start with no fitness function.
  - **git-with-a-remote is enforced again at HITL-M/L.** `has_remote` was computed but reached only
    profile *inference*, never the `act_unattended` decision, so a repo with no remote passed at
    both levels.

  Both of the last two were enforced by the pre-v0.7.0 gate and were lost when the ladder moved
  into the resolver; this restores them rather than adding them. Exit codes 0/2/3/4 keep their
  meanings — 3 for a precondition measured and failed, 4 for one that could not be answered.

- `scripts/superhuman_profile.py` and the Test promotion script were committed without the
  executable bit despite carrying shebangs, so direct invocation failed with "Permission denied".
  Neither was functionally broken — both are invoked through an explicit interpreter by their
  callers — but the mode is now correct and consistent with every other script in `scripts/`.

### Security

## [1.0.3] - 2026-07-25

### Fixed

- **The precondition shim could select a non-working interpreter.** It tested candidates with
  [ -x ], which on Windows happily accepts the Microsoft Store *app execution alias* — a stub
  that exists, is executable, and exits 49 with an advert instead of running Python. Anyone cloning
  the repo without first creating a venv hit this on their first gate. Candidates are now executed
  (python -c 'import sys') rather than inspected, and the failure message says what was tried.

  Found by running the built publication candidate's own suite in a clean checkout, where no venv
  masks the problem — which is the point of building the candidate rather than trusting the
  development tree.

## [1.0.2] - 2026-07-25

### Fixed

- **The infrastructure-leak guard had never worked.** LEAK_PATTERNS was written with literal
  backspace bytes where word-boundary escapes were intended, so the IP, ssh-target, and key-material
  patterns matched nothing. Only the /opt/ pattern — which happens to contain no word boundary —
  was ever live. The suite passed vacuously for several releases.

  With the patterns repaired, the guard immediately found a real leak that had been shipping: a
  maintainer email address in docs/superpowers/specs/PLAN_HISTORICAL.md. Scrubbed.

### Added

- tests/test_publication_guard.py — tests for the guard itself. Every pattern now carries a
  known-positive and a known-negative sample which the suite runs, plus a check that no pattern
  contains a control character (the exact defect above). Also asserts the candidate set is
  non-trivial, since a filter bug that emptied it would likewise produce a green suite.
- Patterns narrowed against real false positives found in the tree: loopback and wildcard binds
  (127.0.0.1, 0.0.0.0) are legitimate in shipped code, and RFC 2606 reserves
  example.com and the .test/.invalid/.localhost TLDs precisely so they can be
  written down. An over-broad guard trains people to ignore it.

### Notes

- The lesson is general enough to state plainly: **a guard with no test of its own is not a guard.**
  This one was found by scanning a built publication candidate by hand rather than trusting the
  suite — which is also why the candidate is built and scanned independently before any publish.

## [1.0.1] - 2026-07-25

### Fixed

- **The publication guard was exempting too much, and it was hiding real leaks.** test_content.py
  was skipped wholesale because it defines the leak patterns — which also skipped every docstring in
  it, four of which named a specific internal environment. The patterns now live in
  tests/publication_patterns.py and only that file is exempt, so the test file itself is scanned
  like everything else. Found by scanning a built publication candidate rather than trusting the
  suite, which is the lesson: a guard that exempts a file cannot vouch for it.

## [1.0.0] - 2026-07-25

**Publication readiness.** The framework is now organisation-neutral end to end, and the guards
that keep it that way are tests rather than good intentions.

### Added

- `LICENSE` — MIT, matching what `NOTICE.md` already claimed. Its absence was an outright blocker.
- `test_no_infrastructure_leaks` — scans every shipped file for IP addresses, absolute server
  paths, ssh targets, credential filenames, and key material. Patterns, not names, so it protects
  anyone publishing a fork.
- `test_operator_tokens_are_absent` — rejects any token listed in a gitignored
  `.publication-tokens` file. This is how an operator keeps their own environment vocabulary out of
  the published tree without baking it into a shared test. Skips when the file is absent.
- `test_license_and_notice_present` — a publishable repo must carry both, and `NOTICE.md` must say
  which paths the upstream licence covers.

### Changed

- `CHANGELOG.md`, `MIGRATION.md`, `docs/`, and the modified files under `references/`
  (`deprecating-a-system`, `definition-of-done`, `orchestration-patterns`) re-expressed without
  organisation-specific names, paths, or policy citations. The design docs keep their section
  numbering, which `SKILL.md` cites.
- The publication guard no longer exempts `CHANGELOG.md`. A changelog is shipped content: exempting
  it would have published every internal environment name the release notes happened to mention.

### Removed

- `tests/fixtures/golden/` and `tests/test_golden_verdicts.py` — these pin one installation's ladder
  against the pre-0.7.0 bash gate, so they are installation policy rather than framework behaviour.
  Moved to the operator's private profile repository, where they still run.
- `docs/superhuman/addyosmani-harvest/` — artifacts of a completed internal project run, archived
  privately rather than published.

### Notes

- **This release does not itself publish anything.** Making a repository public is irreversible in
  practice — content is cached and indexed regardless of later changes — so it stays an explicit,
  separate human action. What v1.0.0 provides is a tree that is *safe* to publish, and tests that
  fail if it stops being so.
- The vendored upstream skills under `references/` were reviewed and kept: they are MIT, attributed
  in `NOTICE.md`, and several have been modified enough that de-vendoring would lose work. Making
  them optional remains a possible future change.

## [0.9.0] - 2026-07-25

**Phase 3 of the portable-profile programme: onboarding.** v0.7.0 made the ladder data and v0.8.0
made the prose neutral; this release makes a profile something you can get without hand-writing
YAML. Purely additive — no existing behaviour changes.

### Added

- **`init`** — proposes a ladder from what it can discover, and optionally writes it.
  `--dry-run` prints without writing, `--preset <name>` starts from a shipped preset,
  `--offline` skips every network probe, `--force` overwrites deliberately. Refuses to clobber an
  existing profile otherwise. Whatever it renders is validated before it is handed over.
- **Discovery / CI importer** — reads `.env.<name>` files, `docker-compose-<env>.yml` files,
  `environment:` keys in CI workflow files, and (unless `--offline`) hosted deployment environments
  and branch-protection rules via `gh`. Every probe degrades to empty rather than failing: a
  missing `gh`, an unauthenticated host, or no network never stops `init` from producing a usable
  profile.
- **`doctor`** — one-screen health report: profile path and hash, require-profile mode, git
  coordinates, the resolved rung and the rule that matched it, every undeclared policy cell, the
  agent-only-approver warning, and the honest §7.4 note that a ref-only ladder cannot see a
  deployment target.

### Notes

- **The proposal encodes both authoring rules, and is tested on them.** Deny rungs are emitted
  before permissive ones so an equal-specificity tie resolves to the safer verdict, and `env_marker`
  is emitted *only* on protected rungs — a marker on a permissive rung would let
  `## Environment: dev` inside a production path override the production block. Both are asserted
  directly rather than left to review.
- **Anything not inferable is left `null`, not guessed.** Trunk's `promote_into` stays undeclared
  unless a hosted branch-protection rule is actually found. An undeclared cell halts an unattended
  run (exit 4), which is the intended failure direction.
- **Deviation from D-21, deliberate.** The spec has answers "written back" to the profile on first
  encounter. `init` materialises a whole profile where none exists, but a *partial* write-back into
  an existing profile is not implemented: these files carry load-bearing comments (the two ordering
  rules above), and round-tripping YAML while preserving comments needs a dependency this skill
  does not otherwise want. The resolver instead reports the exact undeclared cell and the operator
  edits it. Revisit if the manual step proves annoying in practice.

## [0.8.0] - 2026-07-25

**Phase 2 of the portable-profile programme.** v0.7.0 moved the ladder into data; this release
removes the organisation-specific *prose* that surrounded it. Superhuman now describes the
mechanism only — the ladder, the environments, the release policy, and the preferred-library picks
all live in the operator's profile.

### Changed

- **HITL levels renamed `0/1/2` → `H/M/L`** (design decision D-26). A rising number meant falling
  oversight, which read backwards. `HITL-H` (High) / `HITL-M` (Medium) / `HITL-L` (Low). The legacy
  numeric spellings still parse everywhere — `--level 1`, `HITL-level: 2` — and map to the letters,
  so projects started before this release resume unchanged.
- **`SKILL.md` HARD-GATE rule 5 rewritten.** It no longer names environments. It states that
  unattended operation is governed by the resolved rung's `act_unattended` policy, documents the
  full exit-code contract (0/2/3/4) including the unresolved case, and adds the **ceiling rule**:
  the profile bounds the project's HITL level, so a project may always take more human oversight
  than its location requires, never less.
- **Tier → model mapping moved to the profile** (`models:`), out of `SKILL.md`, `README.md`, and
  `adaptation/dispatch.md`. Symbol mapping is a property of the *harness*; model choice is a
  property of the *account*. `dispatch.md` keeps the former and supplies a default for the latter.
- **`conventions/source-cited.md`** no longer inlines a preferred-library table. Which library to
  reach for is now answered by the profile's `conventions:` overlays; the convention itself covers
  only the DETECT → FETCH → IMPLEMENT → CITE loop, which is universal.
- **`phases/3.3-preflight-review.md`** security lens is now a baseline checklist that a profile
  `review_checklist` overlay may *extend* but never shrink.
- Prose across `conventions/`, `phases/`, `roles/`, `templates/`, `README.md`, `TROUBLESHOOTING.md`
  and `MIGRATION.md` re-expressed in terms of rungs and policies rather than named environments.
- `templates/SUPERHUMAN.md.tpl`: `## Environment:` is now optional and documented as a marker for
  locations whose path does not identify them; gained a resolved-rung audit snapshot.

### Added

- `profiles/presets/solo-git.yaml` — ref-space ladder for a developer with no deployment
  environments: feature branches allow unattended work, trunk and release tags do not.
- `profiles/presets/classic-3tier.yaml` — dev → staging → production, detected by path *and*
  marker, with a co-signed production gate.
- `examples/promote.sh.example` — the shape of a promotion driver (fetch a released tag, verify at
  the destination, fail loudly), for adaptation in the operator's own repo.
- `tests/fixtures/ladder-generic.yaml` — a generic ladder so *mechanism* tests no longer depend on
  any organisation's environment names.
- **`test_shipped_prose_is_organisation_neutral`** — asserts no shipped file contains an
  organisation-specific string. This is the guard that keeps superhuman publishable without a fork:
  if org policy leaks back into the framework, the suite fails.

### Removed

- the Lab promotion script, the Test promotion script and its test.
  Deployment mechanics are organisation-specific and belong in the operator's profile repo, not in
  a published skill. Replaced by `examples/promote.sh.example` plus each rung's `promote:` key.

### Notes

- Design decision **D-25 superseded by D-25a**: HITL levels do *not* dissolve into the profile's
  approvals map. They are different scopes — ladder approvals are machine-level, HITL is
  project-level — and collapsing them would force one oversight level across every project on a
  machine. They interact in exactly one direction (the ceiling rule above), which was already
  enforced. `HITL-level:` therefore stays a first-class `SUPERHUMAN.md` field.
- Still to come in 0.9.0: the `init` wizard, `doctor`, and the CI-environment importer.

## [0.7.1] - 2026-07-25

### Fixed

- the Test promotion script targeted `<workspace>/skills/superhuman/`, a directory
  that does not exist on the host. The real path is `<test-env-root>/...`, which is also what
  `docker-compose-test.yml` bind-mounts to `<workspace>`. The Lab→Test deploy had
  therefore never succeeded — the Test checkout carried no tags at all. Corrected, with a comment
  recording why the name differs.

## [0.7.0] - 2026-07-24

**Phase 1 of the portable-profile programme** (design spec
`docs/superhuman/specs/2026-07-24-portable-profile-and-ladder.md`). Extracts the deployment-ladder
policy out of `scripts/autonomous-precondition.sh` and into a declarative profile resolved by
deterministic Python.

This release is **behaviour-identical by construction**. No prose, phase recipe, role prompt or
gate semantic changed; the existing installation's allow/deny surface is preserved and proven so by
a golden-verdict equivalence suite. Genericising the prose is phase 0.8.0.

### Added

- `scripts/superhuman_profile.py` — the deployment-profile resolver. Loads a declarative profile,
  probes the current location on two axes (filesystem path *and* git ref), resolves it to a single
  rung by a fixed precedence, and evaluates that rung's approval policy. Sub-commands: `resolve`
  (JSON), `explain` (precedence trace), `check` (exit-code verdict), `validate` (schema + unresolved
  cells). Dependencies: PyYAML plus the standard library.
- Profile schema v1: rungs with `detect` / `approvals` / `labels` / `tests` / `promote`, plus
  top-level `citation`, `require_profile`, `defaults`, `conventions` and `models`. Unknown keys are
  rejected loudly rather than ignored.
- Approval policies replace the old boolean autonomy flag: `none` | `never` | `null` (unresolved) |
  an any-of list | `{all_of: [...]}`, over approver tokens `human`, `human:<name>`, `agent:<name>`,
  `self`.
- Built-in zero-config ladder (`stable` / `trunk` / `work` / `local`), ref-space only — no path
  heuristics — so a fresh install is safe *and* silent. Behaviour is unchanged at HITL-H.
- `SUPERHUMAN_PROFILE` (pin a profile) and `SUPERHUMAN_REQUIRE_PROFILE=1` (make a missing profile a
  hard error instead of a fall-through to the permissive default).
- Exit code `4` — policy declared but unresolved; halt and escalate. Safe for existing callers,
  which already abort on any non-zero exit.
- `tests/test_golden_verdicts.py` — 65 assertions proving the resolver reaches the same verdict as
  the snapshotted v0.6.0 gate across 21 representative paths at both autonomous levels, including
  the legacy `*prod*` glob's known false positives (`my-products`, `reproduce`).
- `tests/test_profile_resolver.py` — 41 unit tests covering schema validation, approval parsing,
  conjunctive matching, precedence and tie-breaking, fail-closed behaviour and the built-in ladder.
- `tests/fixtures/golden/` — the snapshotted v0.6.0 gate and the extracted ladder under test.
  **Both are on the pre-publication removal list.**

### Changed

- `scripts/autonomous-precondition.sh` is now a compatibility shim over the resolver. Its command
  line and exit codes 0/2/3 are unchanged, so `phases/3-autonomous-loop.md`, `phases/0-kickoff.md`
  and the `SKILL.md` HARD-GATE keep working without edits.
- `--level` accepts `H`/`M`/`L` in addition to the legacy `0`/`1`/`2` (see decision D-26; the full
  rename lands in 0.8.0).
- `tests/test_autonomous_mode.py` pins `SUPERHUMAN_PROFILE` to the golden fixture. Those tests
  asserted *policy* that now lives in a profile; pinning keeps them deterministic and independent
  of whether the machine running them has a profile installed.

### Fixed

- Profile discovery no longer escapes the project. The project-local upward walk is bounded by the
  enclosing git repository root (or the home directory), so `~/.superhuman/profile.yaml` can no
  longer be matched as though it were project-local — which had collapsed search tiers 2 and 3 and
  made a genuine "no profile" state unconstructable.

### Notes

- The ladder now lives at `~/.superhuman/profile.yaml`, owned by no skill. Version it as its own
  private repository and set `SUPERHUMAN_REQUIRE_PROFILE=1` on machines with protected
  environments.
- Not yet done, by design: prose still names specific environments (0.8.0), and there is no `init`
  wizard, `doctor`, or CI-environment importer (0.9.0).

## [0.6.0] - 2026-07-24

Makes **laptop / pre-lab authoring** a first-class allowed environment for the autonomous/authoring
loop (HITL levels 1 and 2), alongside Lab and the Test environment. Laptop/pre-lab is the primary the organisation
development surface (per global CLAUDE.md) and sits *upstream* of Lab, so the loop should run there
exactly like Lab and the Test environment. UAT and Production stay hard-forbidden. This change is scoped to
*where the authoring loop may run* — the promotion-approval policy's approval gate for *promoting* work into any
UAT/Production environment is untouched and still requires an explicit human "yes," every time.

Surfaced 2026-07-22 when autonomous/low-HITL execution was wanted for laptop authoring of the
a downstream sub-project, but the precondition wording framed laptop as
forbidden.

### Changed

- **`scripts/autonomous-precondition.sh`** — header, block message, OK message, and usage examples
  reframed from "Lab/the Test environment-only" to "Lab, the Test environment, and laptop/pre-lab authoring — never
  UAT/Production." Behavior of the gate is unchanged (it already failed open for neutral checkouts):
  the deny conditions (forbidden path segment; `## Environment: uat|prod`) are identical, and the
  fail-open path is now documented as an intentional first-class allowance rather than an implicit
  side effect. Added worked examples showing a laptop path passing and a UAT path aborting.
- **`SKILL.md`** — HARD-GATE rule 5 reworded to "run in Lab, the Test environment, and laptop/pre-lab
  authoring — never UAT/Production," with an explicit note that the promotion-time the promotion-approval policy gate is
  separate and unchanged. Activation-precondition bullet updated to match.
- **`conventions/autonomous.md`** — new "Where autonomous mode may run" section stating the allowed
  surfaces and the UAT/Prod hard-block, and clarifying the loop-location vs promotion-approval split.
- **`phases/3-autonomous-loop.md`** — Step 0 "on exit 0" note now names Lab / the Test environment / laptop
  pre-lab as the passing surfaces and reiterates the promotion-gate separation.
- **`README.md`** — autonomous-mode preconditions and the UAT/Prod hard-block section updated to the
  three-surface framing.
- **`tests/smoke/autonomous/SMOKE.md`** — Step-0 hard-gate note updated.

### Added

- **`tests/test_autonomous_mode.py`** — `test_precondition_allows_laptop_prelab_checkout` (a
  `~/.claude/skills/<name>/` path with no env marker passes, exit 0) and
  `test_precondition_still_blocks_uat_beside_laptop_allow` (a UAT path still aborts with a the promotion-approval policy
  citation), locking the intended allow/deny split.
- **`tests/test_content.py`** — `test_skill_md_has_autonomous_uat_prod_block` now also asserts
  SKILL.md names laptop/pre-lab as an allowed surface while still forbidding UAT/Production.

## [0.5.0] - 2026-07-24

Replaces the v0.2.0 boolean "autonomous mode" (on/off) with three explicit **HITL levels**,
chosen once at G1 and locked for the project's lifetime. Framed by human-oversight amount (High/
Medium/Low), not by "autonomy" amount, to avoid implying less oversight is strictly better.

### Added

- **HITL-level 2 (Low)** — a new tier beyond the old autonomous mode. The PM/surrogate resolves
  drift (G6, any severity), acceptance (G8), and high-stakes parallelism (G9) itself, via
  **precedent-mining** (this project's own Decisions log, sibling repos/ADRs via
  codebase-memory-mcp, declared conventions) instead of asking — logging the decision and its
  basis to SUPERHUMAN.md rather than pausing. Only **G10** (BLOCKED) and an `ABORT`
  recommendation still always reach a human. The Phase 3.3 preflight GO/NO-GO remains a hard,
  non-overridable blocker at every level.
- **Combined G0+G1 confirmation at level 2** — `phases/0-kickoff.md` now presents vision and
  workflow preferences (including the HITL-level choice) as a single approval when level 2 is
  requested, instead of two separate gates.
- **Rollback-plan precondition for level 2** — `scripts/autonomous-precondition.sh --level 2`
  additionally requires a `ROLLBACK.md` (new `templates/artifacts/ROLLBACK.md.tpl`, naming the
  exact revert target + procedure) whenever `SUPERHUMAN.md` declares
  `Modifies-existing-code: yes`. Net-new/greenfield projects are exempt. `SUPERHUMAN.md.tpl` gained
  the `HITL-level:` and `Modifies-existing-code:` front-matter fields.
- Level-2-specific precedent-mining policy and gate tables in `roles/surrogate-user.md`.

### Changed

- Renamed the old boolean "autonomous mode" to **HITL-level 1 (Medium)** — identical mechanics and
  gate ownership to v0.2.0, just renumbered inside the new 0/1/2 scheme (0 = today's full-HITL
  default, unchanged).
- `scripts/autonomous-precondition.sh` gained a `--level 1|2` flag (defaults to `1` for backward
  compatibility with existing callers); the UAT/Prod guard logic is unchanged.
- `phases/3-autonomous-loop.md`, `phases/4-acceptance.md`, `roles/pm.md`, `conventions/
  autonomous.md`, `references/orchestration-patterns.md`, `SKILL.md`, and `README.md` updated to
  describe the level split instead of a single on/off toggle.

### Deprecated

### Removed

### Fixed

### Security

## [0.4.0] - 2026-07-02

Harvests 8 quality-gate and workflow items from the third-party `addyosmani/agent-skills` pack
(pinned commit `aba7c4e9695c…`, MIT), each adapted to the organisation context. Scoped by the gap-analysis
report an internal gap-analysis report §3; the
report §4 "superhuman does this better" list was deliberately NOT touched. Purely additive — no
gate semantics, role behavior, or existing test was removed. This release was itself run **through
superhuman** (dogfood); the gate log lives at `docs/superhuman/addyosmani-harvest/SUPERHUMAN.md`.

### Added

- **`references/definition-of-done.md`** (report §3.1) — a standing, project-wide Definition of
  Done, distinct from per-chunk acceptance criteria. the organisation-tuned: Quality cites `conventions/
  python.md` + library-first; Ship-readiness cites PHI/sensitive-data (the sensitive-data guard guard) and **the promotion-approval policy**
  (UAT/Prod human approval). Wired into `phases/3.1-test-review.md` (QA/G5 floor),
  `phases/4-acceptance.md` (pre-G8 sanity), and `roles/developer.md` self-review.
- **Anti-rationalization anatomy invariant** (report §3.4) — every roasting sub-skill SKILL.md and
  all 7 `roles/*.md` gained a `## Common Rationalizations` table and a `## Red Flags` section, each
  role/skill-specific. Enforced deterministically by `tests/test_content.py::test_anatomy_invariant`
  (12 files, incl. the two new sub-skills).
- **`references/orchestration-patterns.md`** (report §3.8) — endorsed patterns + 4 anti-patterns
  with rationale, the organisation-adapted: foreign single-harness tools (OpenCode/Kiro/Antigravity/…)
  dropped; `adaptation/dispatch.md` added as the cross-harness dispatch layer; the
  "PM is the only orchestrator" rule anchored to G9. Pointer added from `adaptation/dispatch.md`.
- **`conventions/source-cited.md`** (report §3.3) — source-driven-development as a the organisation
  convention (DETECT→FETCH→IMPLEMENT→CITE, `UNVERIFIED` flag) with a stack table
  (`pyproject.toml` → Python + FastAPI + O365 + google-* per `preferred-libraries.md`). Declared and
  referenced by `roles/developer.md` (frontmatter + Process step 2 + convention enforcement).
- **`references/doubt-driven-development/SKILL.md`** (report §3.2) — an in-flight adversarial
  sub-skill (CLAIM→EXTRACT→DOUBT→RECONCILE→STOP), complementary to the post-hoc roasting suite; a
  PM-thread utility invoked at G3/G4/G5 when reviewer confidence is ≤ 60% or "non-trivial". The
  cross-model second opinion is documented but **deferred** (no live shell-out this release); the
  captured requirement (shell to `gemini-best` only when available; unavailability is a warning,
  not a show-stopper) is recorded for when it is built. Referenced from `SKILL.md`.
- **`phases/3.3-preflight-review.md`** (report §3.5) — a pre-acceptance parallel adversarial
  fan-out (roasting-code + an inline security lens + roasting-design-specs re-run) issued in a
  single assistant turn, emitting a GO/NO-GO + Blockers + Recommended fixes + Rollback plan the PM
  reconciles into the acceptance packet. Wired into `phases/3.2-docs-sync.md`,
  `phases/4-acceptance.md`, and the `SKILL.md` phase progression.
- **`references/deprecating-a-system/SKILL.md`** (report §3.6) — a conditional sub-skill (invoked
  at G0 when a VISION declares "remove X") covering code-as-liability, Hyrum's Law, advisory vs
  compulsory, Strangler/Adapter/Feature-flag, the Churn Rule, and Zombie Code. Scoped to **product
  code**, explicitly NOT superhuman's own artifacts (archive-never-delete). Wired into
  `phases/0-kickoff.md` and `SKILL.md`.

### Changed

- **`roles/pm.md`** — added a `## Chunk sizing` section (report §3.7a): ~100 good / ~300 ok /
  ~1000 too large, file-size-vs-diff-size signal, and Stack/File-group/Horizontal/Vertical
  splitting strategies.
- **`references/roasting-shared/roast-framework.md`** — added a "Categorize findings" prefix rule
  (report §3.7b): Critical / (blank required) / Optional / Nit / FYI, mapped onto the existing
  severity model, with the "lead with what matters" ordering rule.

### Notes

- Test suite grew 94 → 113 (Windows), integration_smoke excluded; all green.
- `conventions/testing.md` was intentionally NOT edited — item 7 (chunk sizing) is scoped to
  `roles/pm.md` only (G3 decision OQ-3).
- Cross-model doubt-driven handshake and any live external-CLI shell-out remain deferred pending a
  separate decision.
- VERSION bumped to 0.4.0 here; the release script re-verifies before tagging.

## [0.3.0] - 2026-06-30

Adds three on-demand adversarial critique sub-skills (roast family) and a shared roast framework. These are independent utilities — not new phase gates — that let the PM dispatch adversarial review of PRDs, design specs, and externally-sourced code before or independent of a project session.

### Added

- **`references/roasting-requirements/`** — adversarial PRD/requirements critique. Challenges problem definition, assumptions, success criteria, personas, scope, contradictions, and feasibility. Inspired by `product-on-purpose/pm-skills pm-critic` (P0–P3 severity model, concrete fix language requirement) and `zscole/adversarial-spec` PRD dimensions.
- **`references/roasting-design-specs/`** — adversarial technical design/architecture critique. Eight attack lenses: assumptions, failure modes, interface completeness, data model, scalability, security, implementation risk, test coverage. Inspired by `dementev-dev/adversarial-review` 4-question per-finding structure and `zscole/adversarial-spec` lens taxonomy.
- **`references/roasting-code/`** — adversarial implementation critique for externally-sourced code. Stance: "break confidence, not validate." Eight attack surfaces: auth/permissions, data integrity, race conditions, rollback safety, error handling, null/boundary state, schema compatibility, observability gaps. Inspired by `openai/codex-plugin-cc adversarial-review.md` framing and `dementev-dev/adversarial-review` finding structure.
- **`references/roasting-shared/roast-framework.md`** — shared severity model (critical/major/minor/nitpick), per-finding structure (what fails/why/impact/fix), output spine, calibration rules, and clarifying question gate. Used by all three roast sub-skills.
- **`SKILL.md` "On-demand critique utilities" section** — tells the PM orchestrator about the roast sub-skills and when to dispatch them (pre-G2 for requirements, pre-G3 for design, any time for external code).

## [0.2.3] - 2026-06-25

Cross-platform fix for the v0.2.2 `autonomous-iter.sh` measurement — caught by the the Lab environment (Linux) deploy verify, which `test_iter_pytest_mode_measures_pass_rate` failed even on green code (the Windows release passed). Semver patch of 0.2.2 (the informal "v0.2.2.1" is not valid semver and would fail the strict `X.Y.Z` checks).

### Fixed

- **Deterministic fitness measurement in `scripts/autonomous-iter.sh`.** The `--measure-pytest` run now sets `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`. Without it, the `pre` measurement wrote a `__pycache__/*.pyc`; a same-second source edit (common when an iteration's attempt and the `decide` measurement happen within one second) left that bytecode considered up-to-date, so the post-attempt measurement imported **stale** code and misreported `fitness_after` (observed on Linux as `0.5000` instead of `1.0`, turning a real KEEP into a spurious ROLLBACK). Measurements are now bytecode-cache-free and read fresh source every run.

## [0.2.2] - 2026-06-25

Hardens the v0.2.0 autonomous loop so its per-iteration audit trail is code-enforced rather than LLM-remembered. Motivated by the v0.2.0 live smoke (gemini-2.5-pro), where the orchestrator reached the fitness goal and left `main` untouched but skipped the snapshot→commit→tag-or-rollback discipline, leaving no iteration tags/commits.

### Added

- **`scripts/autonomous-iter.sh`** — deterministic per-iteration driver (skill-design Rule 5 applied to the loop body). Subcommands: `pre` (tag `…iter-N-pre` + measure `fitness_before`), `decide` (measure `fitness_after`; KEEP on strict improvement → commit + tag `…iter-N`; else ROLLBACK → archive diff + `WHY.md` + `git reset --hard` to the snapshot; append the SUPERHUMAN iterations-log row), and `final` (tag `…-beta-<run-id>`). Measurement via `--measure-pytest <dir>` (pass-rate) or `--measure '<cmd>'` (generic float). Backed by 5 new tests in `tests/test_autonomous_mode.py`.

### Changed

- **`phases/3-autonomous-loop.md` Step 2 / Step 4** now drive the loop through `autonomous-iter.sh` (`pre` → one Developer/Tester attempt → `decide`, then `final`) and state the git discipline is non-skippable — the model never runs the tag/commit/reset dance or edits the log by hand.
- **`roles/pm.md` "Autonomous mode behavior"** updated to match: the audit trail is code-enforced via the driver.

### Notes

- Test suite 88 → 94 (Windows). A full re-smoke on a true most-capable model (<most-capable alias>/<fallback alias>) remains pending Codex/Anthropic auth restoration; gemini-2.5-pro is a substitute tier and flaky at tool-call emission on the current OpenClaw build.

## [0.2.1] - 2026-06-25

Concrete tier→model mapping in `adaptation/dispatch.md` for both Claude Code (Anthropic-only) and OpenClaw (alias-based with Anthropic-primary, Gemini-fallback). Addresses the v0.1.5 the Lab environment smoke failure where missing explicit `model=` caused subagents to default to an out-of-credits Anthropic model. Role prompts and phase recipes are unchanged — only the resolution layer becomes concrete.

### Changed

- **`adaptation/dispatch.md` § Model-tier selection** — replaced the single symbolic tier→roles table with three sub-sections: Role→tier (unchanged), Tier→model (Claude Code: `opus`/`sonnet`/`haiku`), Tier→model (OpenClaw: `<fallback alias>`+`gemini-best`, `claude-better`+`gemini-better`, `claude-fast`+`gemini-good`). Added an explicit fallback rule for OpenClaw plus a worked example.

### Added

- **OpenClaw fallback semantics** — on primary auth/credits/rate-limit failure, retry once with the same-tier fallback alias and log the event to `SUPERHUMAN.md` `## Decisions log`. Within a single dispatch, no retry back to primary; each new dispatch starts at primary.

## Known issues (queued for v0.1.7)

- **Automated full-dispatch-behavior smoke.** End-to-end gate firing is still verified manually via `tests/smoke/` checklists; a scripted harness that drives the 8 gates is deferred. (e2e against a live OpenClaw daemon belongs in the Test environment via a UUID-bearing qa-channel, not the `openclaw agent` CLI.)
- **Auto-cron for `the Lab promotion script`.** Deploy/verify to the Lab environment is still run by hand after each release; auto-triggering on push is deferred.
- **Cross-project retuning.** Retuning notes remain project-scoped; aggregating patterns into a user profile is deferred.
- **Convention extensibility.** `conventions/` covers python/testing/git; a documented path for users to add new convention files (TypeScript, Rust, IaC) is deferred.
- **Visual companion** for Architect design-phase mockups. Deferred.
## [0.2.0] - 2026-06-25

Autonomous Karpathy-loop mode. Adds opt-in autonomous operation where a surrogate-user role answers conservative HITL gates while the PM runs a bounded sequential try→measure→keep/rollback loop against `GOAL.md`. All normal (non-autonomous) behavior is unchanged.

### Added

- **`scripts/autonomous-precondition.sh`** — deterministic UAT/Prod hard-block (the promotion-approval policy): exits non-zero if target environment is uat/uat/Prod; also validates git+remote present and GOAL.md exists. Lab/the Test environment only. (Decision D2: safety-critical check is code, not prose.)
- **`roles/surrogate-user.md`** — surrogate-user role (`tier=standard`): answers G2/G3/G4/G5/G7 autonomously using GOAL.md as authority; escalates G0/G1/G6 (moderate+)/G8/G9/G10 to the human. G8 is always human.
- **`phases/3-autonomous-loop.md`** — bounded sequential implementation loop recipe: replaces `phases/3-implementation.md` when autonomous mode is active; iterates try→measure→keep/rollback until goal met or cap hit.
- **`conventions/autonomous.md`** — strictly-improving rule (ties → rollback), sequential execution, loop bounds, no drift-widening, keep-tags-forever archive policy.
- **`templates/artifacts/GOAL.md.tpl`** — skeleton for the per-project goal declaration file.
- **`templates/autonomous-run-summary.md.tpl`** — end-of-loop report template presented at G8 for human acceptance.
- **`scripts/autonomous-rollback.sh`** — slug-scoped rollback via git tag archaeology; archive-by-tag, never deletes.
- **`scripts/autonomous-summary.sh`** — generates the autonomous-run-summary artifact from the loop's tag trail.
- **`tests/test_autonomous_mode.py`** — autonomous mode test module (structure, content, and precondition guard tests).
- **`tests/smoke/autonomous/`** — synthetic-bug smoke fixture for manual autonomous-loop end-to-end verification.

### Changed

- **`SKILL.md`** — added HARD-GATE rule 5 (autonomous mode precondition check) and an "Autonomous mode" section documenting the opt-in flow, surrogate scope, and loop semantics.
- **`roles/pm.md`** — added "Autonomous mode behavior" section: PM activates loop when G1 opt-in is confirmed and precondition passes; PM never bypasses G8.
- **`phases/0-kickoff.md`** — G1 now offers autonomous mode as an opt-in option; GOAL.md elicitation integrated.
- **`templates/SUPERHUMAN.md.tpl`** — added autonomous-mode sections: `## Autonomous run log`, `## Environment:` marker, iteration tracking.
- **`adaptation/dispatch.md`** — added surrogate dispatch pattern (Decision D1: surrogate is a role via `<dispatch:agent>`, not a new verb).
- **`templates/delta-report.md.tpl`** — added surrogate note field for autonomous-mode iteration reports.

### Notes

- Locked decisions: sequential loop (not parallel); strictly-improving (tie = rollback).
- Decision D1: surrogate dispatched as a role via `<dispatch:agent>` — no new dispatch verb needed.
- Decision D2: UAT/Prod safety check is deterministic shell code, not model-compliance prose.
- Open-Q resolutions: Q1 file-first GOAL override, Q2 surrogate = standard tier, Q3 per-branch concurrent runs + slug-scoped rollback, Q4 no drift-widening, Q5 keep tags forever.
- Branch/tag scheme: `autonomous/<slug>/<run-id>`; tags `v<X.Y.Z>-alpha-<run-id>.iter-N-pre`, `…iter-N`, `…-beta-<run-id>`; never `main`, never a stable release tag until human G8.
- VERSION not bumped here — the release script bumps it to 0.2.0 in the next task.
- Test suite grew 62 → 88 cases.

## [0.1.6] - 2026-06-23

Docs patch. Documents two OpenClaw `sessions_spawn` constraints surfaced by the v0.1.5 the Lab environment smoke (gemini-2.5-pro), which the orchestrator had to discover and work around at runtime. No code, gate-semantics, or test change.

### Changed
- **`adaptation/dispatch.md`** — document two OpenClaw subagent-dispatch constraints (new "OpenClaw `sessions_spawn` constraints" section + notes on the `<dispatch:agent>` row and the Model-tier section):
  - **agentId allowlist:** `sessions_spawn` rejects a role-named agentId when the instance restricts spawnable ids (the Lab environment permits only `main`); the role must be conveyed via the dispatched prompt (role prompt as the leading block per cache-stable ordering), never via a role-named agentId.
  - **explicit model:** always pass an explicit `model=` on OpenClaw `sessions_spawn` (don't rely on default/inherited routing); if the tier's preferred provider is unavailable (auth expired / out of credits), fall back to another acceptable most-capable model (e.g. a Gemini Pro) rather than failing.

## [0.1.5] - 2026-06-23

Patch release. The v0.1.4 dual-platform smoke surfaced a cross-platform defect in the new CI scripts; this ships the fix so the bundle's test suite passes on the Lab environment/Linux as well as the Windows laptop. (Semver patch of 0.1.4; the informal "v0.1.4.1" in the plan is not valid semver and would fail the strict `X.Y.Z` checks in `test_version_is_semver` / the release scripts.)

### Fixed
- **`python` vs `python3` portability in the CI scripts.** `scripts/git-hooks/pre-commit` and `scripts/release.sh` now resolve the interpreter with `command -v python || command -v python3` (prefer `python` on the laptop/Windows, fall back to `python3` on Linux); the Lab promotion script defers `$(command -v python3 || command -v python)` to the **server** so its remote pytest uses `python3`. Before this fix the hardcoded `python` made the pre-commit hook report "fast tests FAILED" on the Lab environment even on green code (exit 127, interpreter-not-found), so `test_pre_commit_passes_on_green_tests` failed there and `the Lab promotion script` aborted its remote verify. Caught by the v0.1.4 the Lab environment smoke.

### Notes
- No skill-runtime/orchestrator behavior changed — the fix is confined to the maintainer CI scripts. Test count unchanged at 62; the suite now passes on both Claude Code (Windows) and the Lab environment (Linux).

## [0.1.4] - 2026-06-23

Consolidation release: closes the communication/progress gaps surfaced by the v0.1.3 the Lab environment smoke, adds content regression nets, and lands the lightweight CI/CD scripts (the single-author repo's "pipeline": `release.sh`, `the Lab promotion script`, `pre-commit`). No gate semantics changed; all additions are additive to the v0.1.3 HITL framework.

### Added
- **G8 PROJECT COMPLETE terminator (D10).** PM emits `✅ PROJECT COMPLETE — superhuman is done; reply '/new' to start another` on its own line as the final output after G8 sign-off, exactly once, never at an earlier gate. Specified in both `phases/4-acceptance.md` and `roles/pm.md`; backed by `test_g8_emits_project_complete_terminator`.
- **Phase 3 progress heartbeat (D11).** New `roles/pm.md` "Phase 3 progress heartbeat" section: append-only Type B one-liner `[<HH:MM>] <phase> chunk <n>/<N> — <subagent> in flight (<elapsed>)` every ~3 min while a subagent is in flight; never pauses, never spams per-event. Backed by `test_pm_has_phase3_heartbeat`.
- **Class-definition idioms (C9)** in `conventions/python.md`: the dataclass → attrs → pydantic escalation order (frozen+slots default; attrs for validators; pydantic only when (de)serialization of external input drives the model), one paragraph + example each.
- **Content regression tests (A1–A3):** `test_dispatch_md_completeness_for_in_use_symbols` (every in-use `<dispatch:*>` symbol has both platform cells filled), `test_phase_recipe_frontmatter_gates_match_body` (declared gates ⊆ body; body gates ⊆ declared ∪ {G6,G7} cross-cutting), `test_platform_only_features_have_degradation` (a "no direct equivalent" OpenClaw cell must document a degradation path).
- **Cleanup root-doc tests (A4–A5):** `--include-code` archives the five root docs but never `.env`; the `.cmd` shim handles native Windows paths.
- **`scripts/release.sh` (F13):** laptop release driver — reads `VERSION`, optional `--bump`, runs pytest + hook smoke, signed tag, push, `--dry-run`; refuses a dirty tree or an `autonomous/*` branch (v0.2.0 safety rail).
- **the Lab promotion script (F14):** idempotent SSH deploy/verify of a released tag onto the Lab environment; default Tailscale host, `--host` override, network-free `--dry-run`.
- **`scripts/git-hooks/pre-commit` + `scripts/install-hooks.sh` (F15):** fast-test commit gate (integration smoke excluded) and its installer (symlink with copy fallback, idempotent, backs up unrelated hooks).
- **`MIGRATION.md` (F16):** end-to-end maintainer workflow — three-script CI/CD model, setup, release loop, "rules for adding a feature" checklist, troubleshooting.
- **`tests/smoke/` (F17):** per-platform smoke checklists (`claude-code/SMOKE.md`, `openclaw/SMOKE.md`) and the canonical `fixtures/hello-cli/` project; `conftest.py` keeps the fixture out of the skill's own suite.
- **Archive-never-delete tests for `cleanup-project.sh`** (archive created, slug content moved, nothing deleted) and a README `## Known limitations` section (carried from the post-v0.1.3 unreleased work).

### Changed
- **On-divergence "next G5" wording (B6)** in `SKILL.md` + `roles/pm.md`: in on-divergence cadence there is no routine pausing G5 — minor drift accumulates and surfaces on the next *drift event* (G6 on 3+ pile-up or moderate+), not on a fixed next-chunk schedule.
- **Inlined the artifact-ownership table (B7)** into `phases/3.2-docs-sync.md` so the docs-sync recipe is self-contained for its owner-role dispatch.
- **`TROUBLESHOOTING.md` hook-size note (B8):** large SessionStart prime can be truncated on small models; remediation is a most-capable tier (OpenClaw exempt — lazy discovery).
- **Tightened the SKILL.md trigger description** (start/resume cases + not-for-single-step carve-out) per the the organisation conformance audit (carried from unreleased work).
- **`.gitattributes`:** enforce LF on the new scripts (`release.sh`, `the Lab promotion script`, `git-hooks/pre-commit`, `install-hooks.sh`).

### Fixed
- **HIGH #1 — root docs not archived (E12).** `cleanup-project.sh --include-code` now also archives `README.md`, `CHANGELOG.md`, `LICENSE`, `.gitignore`, `.env.example` so a fresh run starts genuinely clean; a real `.env` is never archived (secrets stay in place).

### Notes
- Test suite grew 40 → 62 cases.
- All v0.1.4 known-issues from the v0.1.3 queue (communication terminator + heartbeat; migration/workflow tooling) are resolved here; remaining deferrals moved to the v0.1.5 queue above.

## [0.1.3] - 2026-05-27

**Promoted from rc3.** Content identical to v0.1.3-rc3 — same commit, retagged as final after the the Lab environment smoke confirmed:

- All 8 gates fire end-to-end (G0 → G1 → G2 → G3 → G4 → G5 → G7 → G8), plus G6 when stale-state is detected at kickoff.
- Autonomous progression between Type B gates (G5 on-divergence) works — no user "continue" prompts needed.
- Subagent dispatch via `sessions_spawn` (the OpenClaw mapping of `<dispatch:agent>`) functions correctly.
- Drift handled inline at trivial severity without unnecessary G6 escalations.
- Acceptance summary includes git refs, chunk count, drift events, and declared-artifact verification.

This is the first version of superhuman validated end-to-end on both Claude Code and OpenClaw with the full HITL framework engaged. The journey: v0.1.2 smoke completely skipped the framework → v0.1.3-rc1 added HARD-GATE (G0/G1 fired but stale state confused resume logic) → rc2 strict resume validation + cleanup script (all gates fired but PM stalled between phases) → rc3 autonomous-progression rule → smoke clean → tagged final.

See [0.1.3-rc3], [0.1.3-rc2], [0.1.3-rc1] below for the rc-by-rc changelog. No further content changes; v0.1.3 = rc3.

## [0.1.3-rc3] - 2026-05-27

**Release candidate 3.** Fixes the inter-phase stall that the rc2 the Lab environment smoke surfaced.

### What rc2 missed

The rc2 the Lab environment smoke (`<most-capable alias>`, fresh start after `cleanup-project.sh`) successfully fired all 8 gates (G0/G1/G2/G3/G4/G5/G7/G8) plus the new G6 for stale-state — the framework's HITL discipline worked end-to-end for the first time. **But**: after G5 (Type B one-liner in on-divergence cadence), the PM stopped and waited for user prompts to advance. User had to type "status?" several times and then "continue" before the PM moved to Phase 3.2 and presented G7. The framework had no explicit rule about autonomous progression past non-pausing gates, so the PM defaulted to "wait for input" behavior even when no input was needed.

### Added
- **New `SKILL.md` cross-cutting rule: "Autonomous phase progression."** Only Type A gates (G0/G1/G2/G3/G4/G6/G7/G8/G9/G10) pause for user input. After Type B (G5 one-liner in on-divergence) or Type C degraded to B, the PM MUST immediately continue — dispatch the next Developer if chunks remain, or proceed to Phase 3.2 if all done. Mid-flow user input requires an explicit Type A gate (G6/G9/G10), never an implicit pause.
- **Regression test** `test_skill_md_has_autonomous_progression_rule` in `tests/test_content.py`: asserts SKILL.md contains the rule and that `phases/3.1-test-review.md` explicitly marks G5 on-divergence as `DO NOT PAUSE`. Suite grows from 39 → 40 cases.

### Changed
- **`phases/3.1-test-review.md` Step 5 reinforced**: G5 per-chunk mode marked **PAUSE for user input**; G5 on-divergence mode marked **DO NOT PAUSE** with explicit pointer to the SKILL.md autonomous-progression rule; G6 path marked **PAUSE for user input**.

### Fixed
- The rc2 the Lab environment smoke stall pattern. Whether this actually fixes the live behavior requires another smoke run.

### Notes
- Test suite grew from 39 → 40 cases.
- v0.1.3 final is gated on a fresh the Lab environment smoke (no cleanup needed; cleanup was the rc2 prerequisite, not a recurring need) showing all 8 gates fire AND autonomous progression between G5 and G7.

## [0.1.3-rc2] - 2026-05-26

**Release candidate 2.** Hardens HARD-GATE resume-detection logic and adds a cleanup utility. Issued after the rc1 the Lab environment smoke surfaced a more subtle failure mode than rc1 was designed to catch.

### What rc1 missed

On the rc1 the Lab environment smoke (a most-capable-tier model), G0 and G1 fired correctly — proof that the HARD-GATE works for fresh sessions. But the prior v0.1.2 failed run had left behind `<workspace>/hello-cli/` containing built code and a `SUPERHUMAN.md` with kickoff/implementation prose (no structured `## Decisions log`). The rc1 HARD-GATE rule "Exists → resume from the last gate logged" took that as a resume signal. After firing G0/G1, the model saw existing code, decided the project was structurally complete, and backfilled REQUIREMENTS/DESIGN/PLAN/TEST as documentation — skipping G2/G3/G4/G5/G7/G8.

### Added
- **`scripts/cleanup-project.sh`** (POSIX) + **`scripts/cleanup-project.cmd`** (Windows Git Bash shim): archives a project's `docs/superhuman/<slug>/` to `docs/superhuman/archive/<slug>-pre-cleanup-<timestamp>/` so a fresh run starts clean. Optional `--include-code` flag also archives `src/`, `tests/`, `pyproject.toml`, etc. Writes `WHY.md` + `RESTORE.md` per archive-never-delete principle. Executable bit set in git index; LF enforced via `.gitattributes`.
- **SKILL.md anti-pattern row**: "Code already exists in this project — I'll backfill artifacts as documentation and skip to G7/G8" → **STOP**. Pre-existing code is a drift event (G6), not a resume signal.
- **`phases/0-kickoff.md` Step 0.5**: stale-state and pre-existing-code detection before initialization. Both conditions escalate to G6 with three options: archive-and-restart, treat-as-legacy-import (run all 8 gates anyway), abandon.
- **New regression test** `test_skill_md_resume_logic_is_strict` in `tests/test_content.py`: asserts SKILL.md HARD-GATE encodes the strict resume-validity definition. Suite grows from 38 → 39 cases.

### Changed
- **SKILL.md HARD-GATE rule 1 strengthened.** OLD: `Exists → resume from the last gate logged`. NEW: `Exists → validate`. VALID requires `## Decisions log` with at least one `G<digit>` entry containing `user decision:`. VALID resume "means pick up at the next gate, NOT skip remaining gates because work appears done." INVALID surfaces G6 with three options (archive-and-restart / treat-as-legacy-import / abandon). Pre-existing implementation code without `## Chunk log` entries is also a G6 drift event regardless of SUPERHUMAN.md validity.

### Fixed
- The rc1 the Lab environment smoke failure mode: model treats stale-but-existing SUPERHUMAN.md as resume state and backfills artifacts instead of running gates. rc2 makes "valid resume state" a precise condition the model can evaluate, and treats anything else as a G6 escalation requiring user input.

### Notes
- Test suite grew from 38 → 39 cases.
- SessionStart hook output: 4794 → 4800 lines.
- v0.1.3 final is gated on a fresh the Lab environment smoke (after `scripts/cleanup-project.sh` is run against the existing `hello-cli` state) showing all 8 gates fire.

## [0.1.3-rc1] - 2026-05-25

**Release candidate.** Ships the framework-enforcement fix that the v0.1.2 the Lab environment smoke surfaced. Tag the full v0.1.3 only after a fresh the Lab environment smoke confirms the model now honors all 8 phase gates.

### Added
- **`<HARD-GATE>` block at top of `SKILL.md`** with the 4 non-negotiable rules: read SUPERHUMAN.md to resume or start Phase 0, MUST present G0 and G1 even with pre-answered prefs, NEVER claim complete without all 8 gates logged, recognize-and-stop the "too simple to need framework" rationalization. Mirrors the pattern from `references/brainstorming/SKILL.md` which prevents the same failure mode.
- **`## Anti-pattern: "This is too simple to need the framework"` section** in SKILL.md with a 6-row red-flags table covering the common rationalizations and what to do instead.
- **`## Required model tier for the orchestrator (PM thread)` section** in SKILL.md: explicit list of minimum + recommended models per provider; explicit list of forbidden models (`-mini`, `-flash`, `haiku-3`, fast/reliable tier aliases) that will not reliably honor the HARD-GATE.
- **`## Required orchestrator model` section** in README.md: same model table, plus `~/.claude/settings.json` snippet for Claude Code and `<config>/openclaw.json` snippet for the Lab environment.
- **New regression test** `test_skill_md_has_hard_gate` in `tests/test_content.py` asserting the HARD-GATE block, anti-pattern section, required-model-tier section, and first-action mandate are all present in SKILL.md. Suite grows from 37 → 38 cases.

### Fixed
- v0.1.2 the Lab environment smoke failure: `openai-codex/gpt-5.4-mini` loaded the framework but skipped every HITL gate and declared the project complete in 3.5 minutes. Root cause: framework lacked the explicit HARD-GATE pattern that smaller/cheaper models need to honor discipline. v0.1.3-rc1 adds that gate. (Confirmation pending fresh the Lab environment smoke on a most-capable tier model.)

### Notes
- Subagent dispatches still use their per-tier model selection per `adaptation/dispatch.md`. The model-tier requirement applies ONLY to the PM orchestrator (the user-facing thread).

## [0.1.2] - 2026-05-25

### Added
- **`.gitignore`** now includes `.worktrees/` to prevent accidental commits of per-chunk Developer worktrees.
- **`roles/pm.md`** new "Per-chunk worktree (parallel only)" subsection (5-step lifecycle: add worktree → dispatch → merge → remove → G10 on conflict). Follows the pattern in `references/using-git-worktrees/SKILL.md`. Serial dispatches still use the main repo directly.
- **New regression test** `test_gitignore_includes_worktrees` in `tests/test_structure.py`. Suite now 37 cases.

### Changed
- **`phases/3-implementation.md`** PM dispatch decision step: serial path uses main repo; parallel path creates a worktree per chunk and merges back after Developer DONE.
- **`roles/developer.md`** working-directory note: explicit parallel-vs-serial distinction. Removed the v0.1.1 "best-effort, file-disjoint only" note now that real isolation exists.
- **`README.md`** OpenClaw install section rewritten. SessionStart-hook tracking issue closed: OpenClaw uses lazy skill discovery + on-demand role/phase loading (per `docs/tools/skills.md`), so no SessionStart hook is required. Documented the first-use cache-warm cost and provided an optional `boot-md` + workspace `BOOT.md` path for gateway-startup priming if users want it.

### Fixed
- Both v0.1.2 candidates from the prior CHANGELOG resolved:
  - the Lab environment SessionStart hook registration → **intentionally no-op required** (OpenClaw idiom differs from Claude Code's; explained in README).
  - True worktree-per-Developer isolation → **implemented** for parallel dispatches. (No longer deferred to v0.2.0; v0.2.0 freed up for other future work.)

### Notes
- Test suite grew from 36 → 37 cases.
- SessionStart hook output grew from 4780 → 4794 lines (new content from README/pm.md/phases/3 updates).

## [0.1.1] - 2026-05-25

### Added
- **`adaptation/dispatch.md`** OpenClaw column populated for all 10 dispatch symbols. Direct mappings: `<dispatch:agent>` → `sessions_spawn(runtime="subagent", ...)`; `<dispatch:read>` → `read(path=...)`; `<dispatch:write>` → `apply_patch` with `*** Add File:`; `<dispatch:edit>` → `apply_patch` with `*** Update File:`; `<dispatch:bash>` → `exec(command=..., workdir=...)`; `<dispatch:grep>` → `exec(command="rg ...")`; `<dispatch:glob>` → `exec(command="find ...")`. Three degradations documented as such (not TBDs): `<dispatch:ask>` (no equivalent — assistant chat message with numbered options), `<dispatch:task_create>` and `<dispatch:task_update>` (no equivalent — `apply_patch` SUPERHUMAN.md chunk-log table directly).
- **`phases/0-kickoff.md`** seed-commit substep (Step 3.5): after G1 git setup, commits VISION.md + SUPERHUMAN.md so subagents that query git state (`git rev-parse HEAD`) don't fail on empty-repo state.
- **`roles/pm.md`** G1 git subsection: PM now asks for `user.name` and `user.email` and sets them **repo-local** (never global) when git is enabled. Eliminates the per-commit `-c user.email=…` inline overrides seen in the v0.1.0 smoke run.
- **`TROUBLESHOOTING.md`** at skill root: documents the `.claude/worktrees` EEXIST workaround, stale `.git/worktrees/agent-<hash>` cleanup, and a resolved credential-access issue.
- **`README.md`** new "Install / register on the Lab environment (OpenClaw)" H2 section pointing at `<lab-env-root>/config/openclaw.json` as the registration site.
- **Regression test `test_role_and_phase_files_use_dispatch_symbols`** in `tests/test_content.py`: scans `roles/*.md` and `phases/*.md` for raw `Agent`/`AskUserQuestion`/`TaskCreate`/`TaskUpdate` outside prohibition contexts. Test suite now 36 cases (up from 35), all green.

### Changed
- **`phases/3-implementation.md`** frontmatter: `gates: []` → `gates: ["G9?", "G10?"]` with body note explaining these are conditional ad-hoc gates that can fire during the phase but are not phase-exit gates. (`?` suffix indicates conditional; quoted to keep YAML happy.)
- **`phases/3-implementation.md`** frontmatter: `driver: developer` → `driver: pm` with body note clarifying that PM orchestrates the phase and Developer is the dispatched per-chunk subagent.
- **`templates/delta-report.md.tpl`**: header comment added pointing at `roles/pm.md` "Severity classification" as the canonical source for the trivial/minor/moderate/major/critical vocabulary.
- **`SKILL.md`** cross-cutting rules: explicit bullet that the orchestrator must read `roles/<role>.md` and pass its content as the leading block of the subagent prompt, in the established `role prompt → declared references → declared conventions → cached artifact slice → task brief` order.
- **`README.md`** "Install / register" section: noted that POSIX `~` does not reliably expand inside Claude Code's `settings.json` on Windows; recommended the `.cmd` shim or full path.
- **`phases/3.1-test-review.md`** and **`phases/4-acceptance.md`**: both gain a `git worktree prune` step to clean stale per-chunk worktree registrations.
- **`roles/developer.md`** "What you own" section: notes that v0.1.x parallel Developer dispatches are best-effort + file-disjoint, with true worktree-per-Developer isolation deferred to v0.2.0.

### Fixed
- All 6 documentation-gap items from the v0.1.0 final code review (Phase 3 gates frontmatter and driver, delta-report severity-source comment, SKILL.md role-read rule, README Windows-path note, dispatch-symbol regression test).
- 4 of 5 operational-friction items from the v0.1.0 manual smoke run (Phase 0 seed commit, repo-local git identity, `git worktree prune`, parallel-isolation scope note). The 5th (`.claude/worktrees` EEXIST) is documented as a workaround in TROUBLESHOOTING.md since the underlying issue is in the Claude Code Agent harness, not superhuman.

### Notes
- Test suite expanded from 35 → 36 cases.
- SessionStart hook output grew from 4764 → 4780 lines (new content from updated files).

## [0.1.0] - 2026-05-24

### Added
- Initial skill bundle: SKILL.md orchestrator, 6 role prompts (PM, Business Expert, Architect, Developer, QA, Tester), 8 phase recipes (0-kickoff through 4-acceptance), 3 conventions files (python, testing, git), 16 artifact templates, dispatch adaptation layer, SessionStart hook (POSIX + Windows shim), 14 reference bundles forked verbatim from superpowers v5.1.0.
- Test suite: 35 pytest cases across structure validation, content validation, and integration smoke.
- Manual end-to-end smoke test verified on a tiny CLI project.
- the Lab environment deployment of v0.1.0 (2026-05-24). Bundle cloned from GitHub into `<workspace>/skills/superhuman/` (bind-mounted into the lab container at `<workspace>/skills/superhuman/`). Existing 63-line wrapper archived to `<workspace>/skills/archive/superhuman-pre-v0.1.0-20260524-204706/`. All 35 pytest cases passed on the server; SessionStart hook emitted 4764 lines, exit 0.
- that identity added as a `read` (pull-only) collaborator on the skill's GitHub repository so the Lab environment server can `git pull` with its own PAT.

### Fixed
- `hooks/session-start.cmd`: detects Git Bash at `C:\Program Files\Git\bin\bash.exe` directly instead of using `where bash`, which on Windows machines with WSL installed would resolve to WSL bash (which cannot handle Windows paths). PowerShell fallback retained.
- `hooks/session-start` executable bit, lost when cloning the Windows-built repo on Linux. Mode now 100755 in git.

### Notes
- Forked from superpowers v5.1.0 (commit `f2cbfbefebbfef77321e4c9abc9e949826bea9d7`, MIT, Anthropic). See `NOTICE.md`.

## Provenance

This project is a fork of superpowers v5.1.0 (Anthropic, MIT). The intent of the fork is to evolve the content into a phased, role-driven orchestrator. References under `references/` are verbatim from superpowers; everything else is novel work. See `NOTICE.md` for attribution.
