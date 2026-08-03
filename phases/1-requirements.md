---
phase: 1
title: Requirements
gates: [G2]
driver: pm
consulted: [business-expert]
---

# Phase 1: Requirements

## Inputs

- `VISION.md`
- `SUPERHUMAN.md` (for project context)

## Steps

1. **Draft REQUIREMENTS.md** from `templates/artifacts/REQUIREMENTS.md.tpl`.
   - PM drafts; dispatch Business Expert (parallel ok per scope) for domain validation if applicable.
   - Resolve open questions from VISION.md.
   - Use the FR-N / NFR-N ID scheme.

2. **Self-review** (mirrors brainstorming spec self-review): placeholders, internal consistency, scope, ambiguity. Fix inline.

3. **G2: REQUIREMENTS approval.**
   - Type A gate using gate-headers template.
   - Path + 3-5 bullet summary + ambiguity-resolution options (if any).
   - Recommendation: "approve and proceed to design".

## Outputs

- `REQUIREMENTS.md` (approved at G2)

## Exit criteria

- G2 approved.
- All open questions resolved or explicitly deferred.

## Retuning hook

After G2 resolution: append a retuning entry per DESIGN.md §7 rule 9.

Next phase: 2-design.
