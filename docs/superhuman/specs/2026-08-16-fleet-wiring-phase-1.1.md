# Scoping brief — Phase 1.1: wire fleet session registration into superhuman's own flow

**Created:** 2026-08-16
**Status:** scoping brief, feeds a new superhuman project's G0 (VISION). Not a spec, not a plan.
**Parent program:** `docs/superhuman/specs/2026-08-13-superhuman-session-fleet.md` §16 (phased delivery).
**Predecessor:** Phase 1 `session-tracking` — COMPLETE, G8 accepted 2026-08-16.

---

## Why this project exists (the finding)

Phase 1 built and proved the session-manifest **mechanism**: schema, append-only event log,
fragments, adapters, handoff emit/self-register, dependency-edge graph, evidence-backed
`done_level` ladder, and the read-only `fleet status` / `FLEET.md` viewer. All 7 chunks accepted;
fleet suite 456/2, full repo 688/2.

**But nothing calls it.** Verified 2026-08-16 by grep across the skill:

- No references to `fleet register` / `fleet handoff` / `scripts.fleet` in `SKILL.md`, `phases/`,
  `roles/`, `adaptation/`, or `conventions/`.
- No wiring in the `session-relay` skill.
- No hook in `~/.claude/settings.json`.

So FR-1 ("superhuman records **every session it causes to exist**") holds today as a *capability*,
not as a *behavior*. The manifest stays empty unless a human runs `fleet` commands by hand.

This is consistent with Phase 1's declared scope — VISION says Phase 1 "only makes state
**recorded and readable**, not acted upon", and no wiring chunk was ever in `PLAN.md`. It is a
**gap between built and useful**, not a defect in what was accepted.

## Scope (proposed — the new project's G0 decides finally)

Wire the three FR-1 origination paths so superhuman writes to the manifest as a side effect of
its normal operation:

1. **Spawned** — superhuman dispatches a role subagent → `fleet register` (origination=spawned).
2. **Relayed** — a `session-relay` handoff → `fleet register` (origination=relayed).
3. **Manual** — superhuman emits a next-session prompt → `fleet handoff emit` writes the
   `awaiting-launch` row with the durable `handoff_id`; the launched session self-registers via
   `fleet handoff self-register` (FR-2 launch-flip). This is the path that answers *"what did I
   hand off and never launch?"* via `fleet handoff stale` (FR-3).

**First real user-visible benefit** lands here: dropped-thread detection. Everything before this
is capability without observation.

## Hard constraints (carried from Phase 1)

- **NFR-4 additive / non-breaking** is the binding constraint. This project touches `phases/` and
  `roles/` — files Phase 1 deliberately never modified. Existing gate/role/phase-recipe
  **semantics** must not change; existing projects must resume unchanged.
- **NFR-2 harness portability** — wiring must go through the adapter seam, never harness-specific
  calls in the core. Must degrade gracefully where `session-relay` is absent (NFR-3).
- **NFR-5 operator-neutral** — everything committed must pass
  `tests/test_content.py::test_operator_tokens_are_absent`.
- **Registration must never break the thing it observes.** A manifest write failure (lock timeout,
  missing dir, bad profile) must not abort a dispatch or a handoff. Fail-soft on the *observation*
  path, unlike the fail-closed discipline that governs the manifest's own safety gates.
  Expect this to be the central design question at G3.

## Base-branch decision (verified, do not re-derive)

`origin/main` contains **zero** files under `scripts/fleet` — the fleet code lives only on
`superhuman/session-tracking`, which is **29 commits ahead of main and unmerged**.

→ **Phase 1.1 must branch off `superhuman/session-tracking`**, not `main`. A branch off `main`
would have nothing to wire.

**Open question for G1:** whether to merge `superhuman/session-tracking` → `main` first (via PR)
and rebase, or to stack Phase 1.1 on the unmerged branch and merge both later. Stacking is
faster; merging first keeps history linear and gets Phase 1 durably onto the default branch.
No PR exists for Phase 1 yet.

## Accepted residuals inherited from Phase 1 (context, not scope)

Forge-only in-process-attacker case (direct `append()` of a forged adjacent `done_level_advanced`
chain); F2a declared-overrides-derived edge upgrade; `feeds-into`/`serves` registration-order
proxy derivation; the `fleet:` profile-schema block never formally extended. None blocks wiring.

## Not in scope

The Phase-2 CEO overseer (active loop, dashboard, stall detection, escalation) — that is a
separate project at its own G0. This project only makes superhuman *write* what it already
promised to write.
