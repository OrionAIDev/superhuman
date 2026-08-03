# Autonomous mode conventions

Rules governing the try→measure→keep/rollback loop, used by both HITL-M (Medium) and
HITL-L (Low) — see SKILL.md "HITL levels" for what distinguishes the two. These decisions
are locked; they may not be loosened by project configuration.

## Where autonomous mode may run

HITL-M (Medium) and HITL-L (Low) are allowed wherever the deployment profile's `act_unattended`
policy permits them, and nowhere else. This is never a judgment call — it is declared data,
evaluated deterministically by
`scripts/autonomous-precondition.sh <project-root> --level <M|L> --slug <slug>`.

`--slug` scopes the preconditions that are questions about one project — its `GOAL.md` and, at
HITL-L, its rollback plan. A repo may hold several projects under `docs/superhuman/`; without a
slug the gate exits 4 instead of answering about whichever one it finds first.

The gate fails **closed** for any rung whose policy is `never`, and for any rung that declares no
policy at all (exit 4 — an undeclared policy must be settled by a human before an unattended run;
it is never inferred, and never precedent-mined). It fails **open** only where the profile says so
— typically an authoring workstation, or any neutral checkout when no ladder has been declared.

With no profile installed, the built-in ref-space ladder applies: permitted on feature branches,
undeclared on trunk, forbidden on a checked-out release tag.

This convention governs only *where the try→measure→keep/rollback loop runs*. It does **not** touch
the separate `promote_into` policy: landing the loop's result at a protected rung still requires
that rung's declared approver, every time. Passing this gate is not consent to promote.

## What counts as an improvement

An iteration is **kept** iff `fitness_after > fitness_before + min_delta`. The rule is **strictly** improving — ties and any non-improvement trigger a **rollback** to the previous state. A rollback still counts toward plateau detection; it does not reset the counter.

`min_delta` defaults to `0.01` (1 percentage point). A project may set a tighter value in `GOAL.md`; it may never relax it below the default. Rollback is automatic — the loop never prompts the user before rolling back.

## Iteration model

v0.2.0 runs iterations **strictly sequentially**: one iteration runs to completion (or rollback) before the next begins. No parallel iterations within a single run.

Concurrent *runs* of **different projects** are allowed — each run lives on its own `autonomous/<slug>/<run-id>` branch and operates independently. Within a single project run, sequential order is enforced.

## Fitness-function design rules

- Fitness is a single scalar in `[0, 1]` (higher is better).
- The measurement command must be deterministic and runnable headlessly (no GUI, no user input).
- The command must produce a stable exit code and machine-readable output.
- No LLM-judged fitness in v0.2.0. Single objective only; multi-objective fitness is deferred.
- The fitness function is declared in `GOAL.md` and must not change mid-run.

## Loop bounds

Defaults and hard ceilings. `GOAL.md` may **tighten** any bound but must never exceed the hard ceiling.

| Bound | Default | Hard ceiling |
|---|---|---|
| Max iterations | 10 | 25 |
| Max wall-clock | 2 h | 6 h |
| Max tokens | 500 K | 2 M |
| Per-iteration time cap | 15 min | 30 min |
| Plateau | 3 consecutive iters with fitness delta < 1% → stop | same |

**Plateau detection**: when 3 consecutive iterations each produce a fitness delta below 1% (whether kept or rolled back), the loop halts and escalates to a human. The plateau threshold is not configurable — no drift-widening.

## Escalation triggers

### HITL-M (Medium)

The surrogate user must escalate to a human on all of the following:

- **Always escalate:** G0 (vision), G1 (workflow preferences), G8 (acceptance), G9 (high-stakes parallelism), G10 (BLOCKED).
- **Moderate+ G6:** any drift escalation that reaches moderate or higher must go to a human; the surrogate may not absorb it.
- **REVISIT-REQUIREMENTS or ABORT signals** in any role output.
- **RE-CHUNK > 2×** the original chunk estimate.

**No drift-widening:** the loop halts when 3 consecutive iterations pass without a gate. The surrogate must NOT absorb cumulative minor drift beyond this limit. Three minor-no-gate is the ceiling — not a starting point for negotiation.

### HITL-L (Low)

Only **G10 (BLOCKED)** always escalates. Everything else the surrogate/PM would escalate at level 1 —
G6 at any severity, G8, G9, RE-CHUNK > 2× — is instead resolved via the precedent-mining policy in
`roles/surrogate-user.md` and logged to SUPERHUMAN.md's Decisions log with its basis, rather than
paused on. The two exceptions that still always go to a human at level 2:

- **ABORT** — ending the project outright is never a surrogate/PM call, at any level.
- **A NO-GO from the Phase 3.3 preflight that survives the PM's own fix attempt** — this is the
  level-2 acceptance path (`phases/3-autonomous-loop.md` Step 4); it escalates via **G10**, not a
  new gate, because at that point the PM genuinely cannot self-resolve it.

**No drift-widening still applies at level 2**: 3 consecutive iterations without a gate still halts
the loop — level 2 changes *who* resolves the resulting gate (PM via precedent, not a human), not
whether the halt-and-check itself fires.

## Tag retention

All pre-release tags are kept forever: `-pre`, `-alpha-…iter-N`, `-beta-…`, and any other pre-release tag applied during an autonomous run.

These tags form an audit trail of each iteration's state. They match the archive-never-delete principle. No tag pruning is permitted, automated or manual.
