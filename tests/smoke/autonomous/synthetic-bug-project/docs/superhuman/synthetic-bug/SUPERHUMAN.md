# Superhuman: synthetic-bug-project

**Slug:** synthetic-bug
**Started:** 2026-06-25
**Superhuman-version:** 0.2.0
**Vision (one-liner):** Fix the two calculator bugs so all tests pass.
**Cadence:** on-divergence
**Value-vs-foundation:** value-first
**Parallelism preference:** PM-decides
**Git:** remote
**Remote:** n/a
**Branch strategy:** autonomous/<slug>/<run-id>
**Value definition:** all tests passing (fitness == 1.0)
**Conventions in effect:** python.md, testing.md, git.md, autonomous.md
**HITL-level:** 1_medium
**Modifies-existing-code:** yes

## Environment: lab
<!-- MUST be 'lab' or 'test' for HITL-M or 2; 'uat'/'prod' hard-blocks both. HITL-H ignores this. -->

## Declared artifacts
- GOAL.md (PM)
- src/calculator.py (Developer)
- tests/test_calculator.py (QA)
- SUPERHUMAN.md (PM, automatic)

## Decisions log
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <one-line summary>; user decision: <decision> -->
[2026-06-25T10:00:00] G0: vision approved (fix the two calculator bugs); user decision: approve
[2026-06-25T10:05:00] G1: kickoff; cadence=on-divergence, value-first, git=remote, HITL-level=1 (Medium); user decision: approve

## Chunk log
<!-- Append-only table. -->
| # | Title | Files | Dev model | Status | Started | Ended |
|---|---|---|---|---|---|---|

## Drift notes
<!-- Append-only. Format: [<ISO timestamp>] Chunk <n>: <severity> — <one-line trigger>; action: <taken> -->

## Archive log
<!-- Append-only. Format: [<ISO timestamp>] archived <chunk> to archive/<dir>/; reason: <reason> -->

## Recommendation overrides
<!-- Append-only. Format: [<ISO timestamp>] G<n>: PM recommended <X>; user chose <Y>; reason: <if given> -->

## Retuning notes
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <observation about user pattern>; bias adjustment: <going-forward note> -->

## Autonomous run config
<!-- Present only for autonomous runs. -->
<!-- run-id: <to be filled at loop start> | branch: autonomous/synthetic-bug/<run-id> | goal: GOAL.md
     fitness: pytest pass rate = passed / collected | min_delta: 0.01 | bounds: iters<=5, per-iter<=15min, plateau<=3 -->

## Autonomous iterations log
<!-- Append-only. One row per iteration. -->
| iter | fitness before | fitness after | delta | KEEP/ROLLBACK | tag | archive ref |
|---|---|---|---|---|---|---|
