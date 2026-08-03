---
phase: 3
title: Autonomous loop
gates: ["G5", "G6?", "G8?", "G9?", "G10?"]
driver: pm
consulted: [surrogate-user, developer, tester, qa]
---

# Phase 3 (autonomous): Iteration controller

> **Driver note:** PM orchestrates the autonomous loop — PM sets up the run, drives the bounded try→measure→keep/rollback loop one iteration at a time, dispatches Developer/Tester per iteration, and decides keep vs rollback deterministically. This recipe runs identically for HITL-M (Medium) and HITL-L (Low); the two levels differ only in **who resolves G6/G8/G9** — see `roles/surrogate-user.md` and SKILL.md "HITL levels" — never in the loop mechanics themselves.
>
> **Gates note:** This recipe drives **G5** (per-iteration result — Type B, no pause) at both levels, with conditional **G6** (drift) and **G10** (BLOCKED, the one gate that is always human). At HITL-M, G6 moderate+ and the end-of-run G8 escalate to a human. At HITL-L, G6 (any severity) and G8 are resolved by the PM/surrogate via precedent-mining (`roles/surrogate-user.md`) instead — only a NO-GO that survives the PM's own fix attempt, or a genuinely blocked state, still routes to G10. The `?` suffix marks conditional occurrence.

## When this phase replaces Phase 3

This recipe runs **instead of** `phases/3-implementation.md` (and its per-chunk `3.1`/`3.2` loop) when **both** hold:

1. HITL-M or 2 was selected at the workflow-preferences gate, AND
2. Step 0's precondition guard passed.

Otherwise — HITL-H, or the precondition fails — the standard human-in-the-loop `phases/3-implementation.md` runs as normal. There is no partial autonomy within a single run: the level is locked at kickoff (workflow-preferences gate) for the project's lifetime.

## Inputs

- `GOAL.md` — the fitness function, the measurement command, the success criterion, and the budget envelope (per `templates/artifacts/GOAL.md.tpl`).
- `SUPERHUMAN.md` — the session state (Decisions log, declared artifacts).
- `conventions/autonomous.md` — the locked loop decisions (these may not be loosened by project config).
- `roles/surrogate-user.md` — the conservative stand-in's decision policy.
- `scripts/autonomous-iter.sh` — the deterministic per-iteration driver (snapshot / measure / keep-or-rollback / commit / tag / archive / log row). The loop MUST call it; the audit trail is never produced by hand.

## Steps

### Step 0 — precondition (fail-fast)

Run the deterministic guard via `<dispatch:bash>`, passing the project's declared HITL-level:

```
scripts/autonomous-precondition.sh <project-root> --level <1|2> --slug <slug>
```

**`--slug` is mandatory here.** The project-state preconditions (`GOAL.md`, the rollback plan) are questions about *one* project, and a repo may hold several. Without it the gate exits 4 rather than guessing across siblings — it does not fall back to a repo-wide scan.

- **On non-zero exit:** ABORT the autonomous loop immediately. Surface the resolver's message verbatim, then fall back to standard HITL `phases/3-implementation.md`. **ZERO surrogate dispatches occur** if this step fails — the surrogate-user is never spawned where the profile forbids unattended operation, and HITL-L never runs against existing code without a checked-in rollback plan.
  - exit 2 — no profile found while one is required, or the profile is malformed. Fix the configuration; do not proceed.
  - exit 3 — a precondition was measured and failed: this rung forbids unattended operation, git has no `origin` remote, no `GOAL.md` exists, or at HITL-L the project declares `Modifies-existing-code: yes` with no `ROLLBACK.md` (or fails to declare the field at all).
  - exit 4 — the question could not be answered: this rung declares no policy for the action, or no `--slug` was given so a project-state precondition has no scope. A human must settle it first; this is explicitly NOT precedent-mineable, at any level.
- **On exit 0:** continue to Step 1. The guard passes wherever the resolved rung's `act_unattended` policy permits it — and, with no profile declared, on any non-trunk branch under the built-in ladder. Passing this gate governs only *where the loop runs*; it does NOT grant approval to promote the result anywhere. Landing work at a protected rung still requires that rung's declared `promote_into` approver, separately and explicitly.

### Step 1 — set up the run

1. Create the run branch: `autonomous/<slug>/<run-id>`, where `<run-id>` = an ISO-compact timestamp plus a 4-char nonce (e.g. `20260625T143055-a4b9`). Use `<dispatch:bash>` (`git checkout -b autonomous/<slug>/<run-id>`).
2. Read `GOAL.md` via `<dispatch:read>`: capture the fitness function, the measurement command, `min_delta`, the success criterion, and the budget envelope.
3. Record a `## Autonomous run config` block in SUPERHUMAN.md (run-id, branch, fitness definition, measurement command, min_delta, all active bounds). This is the run's audit header.

### Step 2 — the bounded sequential loop

Iterations run **strictly sequentially** — one runs to completion (keep) or rollback before the next begins.

> **The per-iteration git discipline is code-enforced — DO NOT perform it by hand.** Snapshot, fitness measurement, the keep/rollback decision, the commit, the tags, the rollback `reset`, the archive, and the SUPERHUMAN log row are ALL owned by the deterministic driver `scripts/autonomous-iter.sh`. The PM's only manual job inside an iteration is dispatching the one improvement attempt. This is a v0.2.2 hardening: the v0.2.0 live smoke showed a capable model reach the fitness goal but SKIP a hand-run tag/commit dance, leaving no audit trail. Calling the script makes the audit trail non-skippable. **Never `git commit`/`git tag`/`git reset` or edit the iterations log yourself here.**

For iteration **N**, in order:

1. **Snapshot + measure-before (MANDATORY — run the driver).** Via `<dispatch:bash>`:
   ```
   scripts/autonomous-iter.sh pre --project-root <root> --version <X.Y.Z> --run-id <run-id> --iter <N> --measure-pytest <tests-dir>
   ```
   (For a non-pytest goal use `--measure '<cmd that prints the fitness scalar>'` instead of `--measure-pytest`.) This tags `v<X.Y.Z>-alpha-<run-id>.iter-<N>-pre` and prints `fitness_before=<f>` — capture that number for Step 3.
2. **Attempt one improvement.** Dispatch Developer (and Tester, for the TDD pair) via `<dispatch:agent>` for **exactly one** improvement attempt against the goal — TDD per `roles/developer.md`. One iteration = one bounded attempt; do not chain multiple attempts inside an iteration.
3. **Decide keep/rollback (MANDATORY — run the driver).** Via `<dispatch:bash>`:
   ```
   scripts/autonomous-iter.sh decide --project-root <root> --slug <slug> --version <X.Y.Z> --run-id <run-id> --iter <N> --measure-pytest <tests-dir> --fitness-before <f> [--min-delta <d>]
   ```
   The driver measures `fitness_after` and acts deterministically:
   - **KEEP** iff `fitness_after > fitness_before + min_delta`: it commits the work and tags `v<X.Y.Z>-alpha-<run-id>.iter-<N>`. This is the **G5**-equivalent gate (Type B — no human pause); on accept the **surrogate-user** ratifies via `<dispatch:agent>` per `roles/surrogate-user.md` (accept `PASS`; accept `PASS_WITH_CONCERNS` only if non-architectural; on `FAIL` — at HITL-M, escalate to G6/human; at HITL-L, resolve via precedent-mining per `roles/surrogate-user.md` unless the FAIL reveals a genuinely blocked state, in which case escalate to G10).
   - **ROLLBACK** otherwise — including exact ties (`conventions/autonomous.md`: strictly improving, ties roll back): it archives the rejected diff + `WHY.md` (fitness numbers) under `docs/superhuman/<slug>/archive/<…>-iter-<N>-rolled-back/`, then `git reset --hard` to the `-pre` snapshot. A rollback still **counts toward plateau**.
   The driver appends the iteration row to SUPERHUMAN.md `## Autonomous iterations log` in both cases.
4. **Check bounds.** After the decide step, evaluate every bound from GOAL.md / `conventions/autonomous.md`: max iterations, max wall-clock, max tokens, per-iteration time cap, and plateau (3 consecutive iters with fitness delta < 1%). **Exit the loop** on the GOAL success criterion OR any bound reached.

### Step 3 — escalation (conditional, during the loop)

**HITL-M (Medium):**

- **Drift.** Any **moderate-or-worse** drift escalates as **G6** to the human, unconditionally. The surrogate-user never absorbs moderate+ drift; only trivial/minor drift may be folded.
- **Blocked.** A blocked iteration the PM cannot re-dispatch escalates as **G10** to the human.
- A `FAIL` per-iteration verdict escalates per the surrogate policy in `roles/surrogate-user.md` (the surrogate hands a FAIL back to the human rather than absorbing it).

All level-1 escalations pause the loop for human input; the loop resumes only on the human's resolution.

**HITL-L (Low):**

- **Drift, any severity.** The PM/surrogate resolves it via precedent-mining (`roles/surrogate-user.md`) — pick RE-CHUNK / REVISIT-DESIGN / REVISIT-REQUIREMENTS / CONTINUE, log the delta report + decision + precedent basis to SUPERHUMAN.md, and continue without pausing. `ABORT` is the one exception — that still goes to a human.
- **Blocked.** Still escalates as **G10** — this is the one condition that always pauses the loop, at any level.
- A `FAIL` per-iteration verdict is resolved the same way as drift (precedent-mining, logged, continue) unless it reveals a genuinely blocked state, in which case it's G10.

Only a **G10** (or an `ABORT` recommendation) pauses the level-2 loop for human input; everything else is resolved and logged in-flight.

### Step 4 — end of run

When the loop exits (success or bound):

1. Tag the run result via the driver (`<dispatch:bash>`): `scripts/autonomous-iter.sh final --project-root <root> --version <X.Y.Z> --run-id <run-id>` — this creates `v<X.Y.Z>-beta-<run-id>`.
2. Run the Phase-3.2 declared-artifact completeness logic (the **G7**-relevant check) against SUPERHUMAN.md `## Declared artifacts` — confirm every declared artifact is present and current.
3. Run the **Phase 3.3 preflight** (`phases/3.3-preflight-review.md`) — this always runs before acceptance, at every HITL level; it is a hard, non-overridable blocker, never something precedent-mining can wave through.
4. Generate the run summary via `<dispatch:bash>`: `scripts/autonomous-summary.sh`.
5. **Hand off, level-dependent:**
   - **HITL-M (Medium):** hand off to the **human acceptance gate** in `phases/4-acceptance.md` unchanged. Acceptance is human-only — do NOT self-accept. This recipe's responsibility ends at producing the beta-tagged result plus summary and surrendering control to the acceptance phase.
   - **HITL-L (Low):** the PM self-accepts, per `phases/4-acceptance.md`'s level-2 branch — do not present G8 to a human.
     - **Preflight GO (or all Blockers closed):** the PM composes the acceptance summary itself, appends `[<timestamp>] G8: signed off (autonomous, HITL-L); basis: Phase 3.3 GO + <precedent/rationale for any residual judgment call>` to the Decisions log, and emits the PROJECT COMPLETE terminator per `phases/4-acceptance.md` Step 5.
     - **Preflight NO-GO:** the PM attempts to fix the Blockers itself (re-dispatch Developer/Tester, re-run the relevant lens) — this is not an escalation, just another bounded attempt. If that resolves the NO-GO, proceed to the GO path above. If it doesn't — the PM genuinely cannot self-resolve it — escalate via **G10**, presenting the unresolved Blockers and what was tried. This is the only point in a level-2 run where acceptance pauses for a human.

## Invariants

- **Never commit to `main`.** All autonomous work lives on the `autonomous/<slug>/<run-id>` branch.
- **Never cut a stable `vX.Y.Z` tag from this phase.** Only pre-release tags (`-pre`, `-alpha-…iter-N`, `-beta-…`) are produced here; a stable release is a human decision downstream.
- **All tags are kept forever** — `-pre`, `-alpha-…iter-N`, `-beta-…`. They form the iteration audit trail; no pruning, automated or manual (`conventions/autonomous.md`).
- **Strictly improving keeps only.** Ties and non-improvements roll back. `min_delta` may be tightened in GOAL.md but never relaxed below its default.

## Outputs

- The `autonomous/<slug>/<run-id>` branch with one commit per kept iteration.
- Per-iteration tags: `…-pre` (snapshot) and `…iter-<N>` (kept), plus the final `…-beta-<run-id>`.
- Rollback archives under `docs/superhuman/<slug>/archive/` (diff + WHY.md per rolled-back iteration).
- `## Autonomous run config` and the per-iteration log in SUPERHUMAN.md.
- The run summary from `scripts/autonomous-summary.sh`.

## Exit criteria

- The loop terminated on the GOAL success criterion or a declared bound (never silently).
- The final state is tagged `v<X.Y.Z>-beta-<run-id>`.
- The declared-artifact completeness check is ✓ (all declared artifacts present and current).
- The Phase 3.3 preflight has run and reached GO (or all Blockers closed).
- The run summary is generated, and G8 has been resolved — either handed to a human (`phases/4-acceptance.md`, HITL-M) or self-accepted by the PM with the basis logged (HITL-L), or escalated to G10 if a level-2 self-accept couldn't clear a NO-GO.

Next phase: 4-acceptance (human-only at level 1; PM self-accept at level 2, see Step 4 above).
