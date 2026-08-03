---
phase: 0
title: Kickoff
gates: [G0, G1]
driver: pm
consulted: [business-expert]
---

# Phase 0: Kickoff

## Inputs

- User's invocation message (the "build/design/implement <task>" text).

## Steps

0.5. **Stale-state and pre-existing-code detection.**
   - Look for `<project>/docs/superhuman/<slug>/SUPERHUMAN.md`.
   - If it exists but is INVALID (no `## Decisions log` section, or no `G<n>: ...; user decision:` entries), escalate to G6 with three options: (a) archive-and-restart (`scripts/cleanup-project.sh <project>`), (b) treat-as-legacy-import (still run all 8 gates), (c) abandon. Do not proceed past this step until user picks.
   - Look for pre-existing implementation code (`<project>/src/`, `<project>/tests/`, `pyproject.toml`, `setup.py`, `*.py` at project root) that lacks corresponding entries in any valid SUPERHUMAN.md `## Chunk log`. If found, same three-option G6.
   - Only after stale-state is resolved → proceed to Step 1 (Initialize project state).

1. **Initialize project state.**
   - Choose project slug from user message (or ask if ambiguous).
   - Create `<project>/docs/superhuman/<slug>/` if not present.
   - Copy `templates/SUPERHUMAN.md.tpl` → `<project>/docs/superhuman/<slug>/SUPERHUMAN.md`.
   - Fill the front-matter fields (slug, started, superhuman-version from `VERSION`).
   - **Detect `Modifies-existing-code`.** Check whether the project root already has tracked
     content unrelated to this superhuman scaffold (existing git history predating this session,
     or source/config files beyond `docs/superhuman/`). If so, set `Modifies-existing-code: yes`;
     for an empty/fresh repo, `no`. This reuses the same signal as the Step 0.5 pre-existing-code
     check above, just recorded as a declared fact for later gating (HITL-L's rollback-plan
     precondition reads this field — see Step 3 below).

2. **Vision elicitation (conversational; G0's approval is presented in Step 3, not here).**
   - Probe purpose and reason. Use the patterns from DESIGN.md §2 G0 (e.g. "stock trading" → backtest+paper+live+strategy discovery; "health app" → probe for personal health context).
   - May dispatch Business Expert in parallel if domain is clear; multiple parallel invocations OK for multi-domain projects.
   - **If the vision declares removing / retiring / sunsetting / consolidating an existing product
     system** (e.g. "decommission X", "migrate off Y"), invoke `references/deprecating-a-system/`
     (a conditional sub-skill, not a gate) to shape the migration scope, pattern (Strangler /
     Adapter / Feature-flag), and cutover chunks. It applies to **product code**, never to
     superhuman's own artifacts (those stay archive-never-delete).
   - Cap at 5-7 exchanges before drafting.
   - Draft `VISION.md` from `templates/artifacts/VISION.md.tpl`. Do not present its approval prompt
     yet — whether G0 is presented alone or combined with G1 depends on the HITL-level chosen in
     Step 3, so drafting and approving are split across these two steps.

3. **Workflow preferences, including HITL-level — then present G0 (and G1, combined at level 2).**
   - Ask via `<dispatch:ask>`:
     - **HITL-level: 0 (High) | 1 (Medium) | 2 (Low).** Ask this first, since it determines how
       the rest of this step (and G0's presentation) proceeds. Default/recommend **0**. Briefly
       characterize each: 0 = every gate pauses for you; 1 = a surrogate answers the routine
       implementation gates, you still see drift/acceptance/parallelism decisions; 2 = the PM
       resolves nearly everything itself (researching how comparable decisions were made
       elsewhere and reporting its choice), you're only interrupted if it's genuinely stuck.
     - Cadence: per-chunk | on-divergence
     - Value-vs-foundation: value-first | foundation-first | hybrid
     - Git: none | local | remote (if remote, follow-ups per conventions/git.md)
     - Parallelism: PM-decides | gate-each | serial-only
   - If user picks per-chunk cadence, display the token-cost advisory from DESIGN.md §9.
   - If git=remote, run the remote-sync flow per conventions/git.md.
   - **If HITL-M or 2 was chosen, validate the precondition** before accepting it:
     - Require git + remote to already be selected above — refuse level 1/2 without it, falling
       back to asking again with level 0 as the only option until git/remote is set.
     - Run `scripts/autonomous-precondition.sh <project> --level <1|2> --slug <slug> --kickoff`
       (`<dispatch:bash>`). `--kickoff` is correct **only here**: the project's own `SUPERHUMAN.md`
       front-matter and `GOAL.md` are written further down this same step, so those two checks
       cannot yet be answered. It defers exactly those two; the rung and git+remote are still
       enforced. On non-zero exit, surface the script's message verbatim and fall back one level
       (2→1→0) rather than silently proceeding — never continue at a level whose precondition failed.
     - **Level 2 only, when `Modifies-existing-code: yes`:** elicit the revert target + procedure
       from the user now and write `docs/superhuman/<slug>/ROLLBACK.md` from
       `templates/artifacts/ROLLBACK.md.tpl`. Net-new/greenfield projects
       (`Modifies-existing-code: no`) skip this — there's nothing pre-existing to roll back to.
   - **If HITL-M or 2, handle GOAL.md** (file-first override): if
     `<project-root>/GOAL.md` or `<project>/docs/superhuman/<slug>/GOAL.md` already exists, use it
     verbatim and skip elicitation. Otherwise elicit the objective, fitness function, measurement
     command, and budget interactively, then write `GOAL.md` from `templates/artifacts/GOAL.md.tpl`.
     Phase 3 then uses `phases/3-autonomous-loop.md` instead of `phases/3-implementation.md`.
   - Update SUPERHUMAN.md front-matter with the chosen values, including `HITL-level:` and
     `Modifies-existing-code:`. Declare the latter explicitly as `yes` or `no` — leaving it blank
     is not an implicit `no`, and the gate treats an undeclared field as a gap.
   - **If HITL-M or 2, re-run the gate WITHOUT `--kickoff`:**
     `scripts/autonomous-precondition.sh <project> --level <1|2> --slug <slug>`. Everything the
     deferred checks needed now exists, so this is the run that actually authorizes the level. On
     non-zero exit, fall back one level as above. This re-run is not optional — skipping it is how
     a level gets accepted with no fitness function and no rollback plan.
   - **Present G0 (and G1):**
     - **HITL-H or 1:** present G0 (vision) using gate-headers Type A — recommendation
       "approve and proceed to G1" with alternatives "refine VISION further" / "narrow scope" /
       "expand scope" — then, once approved, present G1 (workflow prefs) as its own gate, as before.
     - **HITL-L:** present G0 and G1 as **one combined confirmation** — VISION.md's summary
       plus the full set of workflow preferences (cadence, value-vs-foundation, git, parallelism,
       HITL-level: 2, and the GOAL.md summary) in a single `<dispatch:ask>` exchange. This is the
       one lightweight human checkpoint before the PM goes fully unattended except for G10. On
       approval, append **both** a G0 and a G1 entry to the Decisions log, timestamped from this
       one exchange.

   **3.5 Seed commit (if git enabled).** If G1 selected git (local or remote), make an initial commit so subagents can query git state without errors: `git -c user.email="$EMAIL" -c user.name="$NAME" add VISION.md SUPERHUMAN.md && git -c user.email="$EMAIL" -c user.name="$NAME" commit -m "chore: project kickoff (VISION + SUPERHUMAN initialized)"`. Use the repo-local identity set per `conventions/git.md` (see also B2 in roles/pm.md — set repo-local config first, then the `-c` overrides are not needed for subsequent commits).

## Outputs

- `<project>/docs/superhuman/<slug>/SUPERHUMAN.md` (initialized + G1 prefs filled, including HITL-level)
- `<project>/docs/superhuman/<slug>/VISION.md` (approved at G0)
- (HITL-L + `Modifies-existing-code: yes` only) `<project>/docs/superhuman/<slug>/ROLLBACK.md`

## Exit criteria

- G0 approved.
- G1 prefs recorded, including a validated HITL-level.
- (If git=remote) initial push succeeded.

Next phase: 1-requirements.
