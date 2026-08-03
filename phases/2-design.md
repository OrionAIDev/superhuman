---
phase: 2
title: Design
gates: [G3]
driver: architect
consulted: [pm, business-expert]
---

# Phase 2: Design

## Inputs

- `VISION.md`, `REQUIREMENTS.md`
- Codebase context if existing project.

## Steps

1. **Dispatch Architect** with the inputs above.
   - Architect drafts `DESIGN.md` from `templates/artifacts/DESIGN.md.tpl`.
   - Proposes 2-3 design approaches with a recommendation.
   - If the project meets the ARCHITECTURE.md trigger (2+ deployable units OR external-API integration OR cross-process IPC), also drafts `ARCHITECTURE.md` from its template.
   - Drafts skeleton `README.md` from its template.
   - Drafts the declared artifact set (selecting from §12.1 catalog).
   - Drafts the chunking strategy (value-first / foundation-first / hybrid) + chunk list (5-10 chunks).
   - Drafts a one-line `value definition` for SUPERHUMAN.md.

2. **PM scope-check** the Architect's output. If scope drift is implied, surface it before the gate.

3. **G3: DESIGN + chunking + artifact set approval.**
   - Type A gate.
   - Present: DESIGN.md path, chunking-strategy recommendation with 2 alternatives, proposed artifact set (user can add/remove), value definition.
   - User picks the strategy + chunk list + artifact set.

4. **Record decisions** in SUPERHUMAN.md: artifact set under `## Declared artifacts`, chunking strategy, value definition.

## Outputs

- `DESIGN.md`
- `ARCHITECTURE.md` (if triggered)
- Skeleton `README.md`
- `PLAN.md` skeleton (from `templates/artifacts/PLAN.md.tpl`) with the approved chunk list filled in
- SUPERHUMAN.md updated

## Exit criteria

- G3 approved.

## Retuning hook

After G3 resolution: append a retuning entry per DESIGN.md §7 rule 9. Watch for: artifact-set deltas, chunking-strategy override, value-definition edits.

Next phase: 2.1-test-plan.
