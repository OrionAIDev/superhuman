---
phase: 4
title: Acceptance
gates: [G8]
driver: pm
consulted: []
---

# Phase 4: Acceptance

> **HITL-L (Low) note:** this phase file describes the **human** acceptance path, used at
> HITL-H and 1. At level 2, the PM self-accepts inside `phases/3-autonomous-loop.md` Step 4
> instead — G8 is not presented to a human there. The steps below (acceptance summary, pre-G8
> sanity, PROJECT COMPLETE terminator) are the same content the PM produces for itself at level 2;
> only the "who approves it" changes. A NO-GO the PM can't self-resolve at level 2 escalates via
> G10, not by falling through to this file.

## Inputs

- All declared artifacts (verified at G7)
- The **Phase 3.3 preflight decision** (GO/NO-GO + Blockers + Recommended fixes + Rollback plan)
- Chunk log, drift notes, retuning notes from SUPERHUMAN.md
- Git history (commits + (if remote) push status)

## Steps

1. **Compose acceptance summary.**
   - Project name + slug.
   - Vision one-liner (from SUPERHUMAN.md).
   - Chunks completed (count, with link to chunk log).
   - Drift events (count and severity breakdown).
   - Retuning notes (count; offered as a separate read-out if the user wants).
   - **Preflight decision (Phase 3.3):** GO/NO-GO, plus any Blockers resolved and the rollback plan.
   - Declared artifacts (path list).
   - Git refs (initial commit SHA, final commit SHA, branch, remote URL if applicable).

2. **Pre-G8 sanity:** confirm the Phase 3.3 preflight verdict is **GO** (or every Blocker is
   closed / explicitly risk-accepted), confirm declared-artifacts ✓ from Phase 3.2 still holds, and run the
   **standing Definition of Done** (`references/definition-of-done.md`) across the whole delivery
   — the Integration, Documentation, and Ship-readiness sections in particular (PHI/sensitive-data
   respected, security lens cleared, rollback path exists, and the rung's declared **`promote_into`
   approver** has signed off on any promotion). Any unmet DoD item is surfaced in the acceptance summary as a
   residual concern, never silently passed.

3. **Worktree cleanup.** Run `git worktree prune` to clean any remaining stale worktree refs before closing out the project.

4. **G8: Acceptance sign-off.**
   - Type A gate.
   - Present: the acceptance summary.
   - Recommendation: "sign off; mark project complete".
   - Alternative: "request additional changes" → loop back to Phase 3.

5. **On sign-off:**
   - Append `[<timestamp>] G8: signed off` to SUPERHUMAN.md decisions log.
   - Optionally invoke `references/finishing-a-development-branch/SKILL.md` for merge/PR/cleanup options.
   - **Emit the PROJECT COMPLETE terminator** as the very last thing PM says, on its own line, after the summary and any merge/PR output — so the user cannot mistake it for another routine update:

     ```
     ✅ PROJECT COMPLETE — superhuman is done; reply '/new' to start another
     ```

     This is the only place superhuman declares the whole project finished. Emit it exactly once, only after G8 sign-off is logged. Do not emit it at any earlier gate.

## Outputs

- Project marked complete in SUPERHUMAN.md.

## Exit criteria

- G8 signed off.
