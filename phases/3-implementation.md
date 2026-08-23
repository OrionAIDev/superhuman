---
phase: 3
title: Implementation
gates: ["G9?", "G10?"]
driver: pm
consulted: []
---

# Phase 3: Implementation (per chunk)

> **Driver note:** PM orchestrates Phase 3 — PM dispatches Developer subagents, monitors status, and runs drift watch. Developer is the dispatched subagent that executes each chunk; it is not the phase driver.
>
> **Gates note:** G9 (high-stakes parallelism approval) and G10 (BLOCKED escalation) are conditional / ad-hoc gates that can fire during Phase 3 but are not phase-exit gates. They fire on specific triggers (PM proposes parallel across an architecture seam → G9; subagent reports BLOCKED and PM cannot unblock via re-dispatch → G10). The `?` suffix indicates conditional occurrence.

## Inputs

- `PLAN.md` (current chunk)
- `DESIGN.md`, `TEST.md` (referenced by path)

## Steps (per chunk)

1. **PM dispatch decision.** Serial (default) or parallel (if PM's parallelism checklist clears; if high-stakes, G9 first).
   - **Serial:** dispatch Developer in the main repo working directory.
   - **Parallel:** for each chunk, create a worktree (`git worktree add .worktrees/chunk-N`) and dispatch Developer with that worktree as cwd. After Developer DONE, PM merges the worktree branch back and removes the worktree. See `roles/pm.md` "Per-chunk worktree" subsection.
2. **For each chunk to be dispatched:**
   - Choose model tier per `adaptation/dispatch.md`.
   - Dispatch Developer with: chunk text from PLAN.md + acceptance criteria + paths to DESIGN.md / TEST.md / declared conventions.
   - Developer executes the chunk per `roles/developer.md` (TDD; self-review; commit; push if remote).
3. **Collect status:** DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED.
4. **Handle status** per references/subagent-driven-development/SKILL.md "Handling Implementer Status" section.

## Drift watch

Per DESIGN.md §11.6. Triggers from §7. PM classifies severity; trivial → log; minor → fold/accumulate; moderate+ → G6 (interrupts dispatch).

## Chunk-boundary handoff emission (non-gating)

When a chunk boundary produces a next-session handoff/kickoff prompt — e.g. PM pauses here to
reassess restart-vs-continue and hands the next chunk to a fresh session — PM emits that prompt
through `fleet observe handoff-emit --prompt-file ... --output-file ...` rather than handing it
off by hand. This step never blocks the surrounding gate: it is purely observational, any failure
(fleet disabled, a manifest write that cannot complete, or any other fault) is logged and
execution proceeds exactly as if the step had not run, and the deliverable prompt handed to the
next session is produced regardless of whether the observation itself succeeds.

## Outputs

- Code commits (per chunk)
- Test commits (per chunk)
- (If remote) push successful

## Exit criteria

- All chunks in PLAN.md status = DONE or DONE_WITH_CONCERNS with surfaced concerns.

Next phase: 3.1-test-review (runs per chunk; this phase loops with 3.1 per-chunk before exiting).
