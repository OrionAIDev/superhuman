# Superhuman: example-fidelity-hardening

**Slug:** example-fidelity-hardening
**Started:** 2026-08-15
**Superhuman-version:** 1.0.3
**HITL-level:** H

## Decisions locked -- do not relitigate
<!-- Dogfooding the resume-packet construct on this very project. Distinct from the append-only
     Decisions log below (which records WHAT happened); this records WHAT MAY NOT BE REOPENED. -->
- **LD-1 (immutable, from invocation):** Provider- and harness-AGNOSTIC throughout.
- **LD-2 (G0):** Elicitation depth = primary + fallback per tier.
- **LD-3 (G1):** HITL-H, on-divergence cadence, foundation-first -- locked for project lifetime.

## Decisions log
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <one-line summary>; user decision: <decision> -->
[2026-08-15] G0: VISION approved; user decision: approve and proceed.
[2026-08-15] G1: Workflow prefs set; user decision: approve.
[2026-08-15] G2: REQUIREMENTS approved; user decision: approve and proceed.
[2026-08-15] G3: DESIGN approved; user decision: approve all.
