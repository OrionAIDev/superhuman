# Superhuman: Fidelity + first-run provider setup

**Slug:** fidelity-provider-setup
**Started:** 2026-08-15
**Superhuman-version:** 1.0.3
**Vision (one-liner):** Harden superhuman's own cross-session fidelity (#165) + make first-run setup elicit each operator's provider stack (#139), provider/harness-agnostic throughout.
**Cadence:** on-divergence
**Value-vs-foundation:** foundation-first
**Parallelism preference:** PM-decides
**Git:** remote
**Remote:** https://github.com/OrionAIDev/superhuman.git
**Branch strategy:** feature branch `feat/superhuman-fidelity-provider-setup` off main; PR into main; ceiling OrionTest
**Value definition:** Each landed chunk leaves superhuman's substrate correct and self-consistent — a template/schema/elicitation change that CI validates and that a fresh resuming session could rely on without relitigation.
**Conventions in effect:** git
**HITL-level:** H
**Modifies-existing-code:** yes

## Declared artifacts
<!-- PM appends one line per declared artifact at G3 -->
- VISION.md (PM)
- REQUIREMENTS.md (PM)
- DESIGN.md (Architect)
- PLAN.md (PM)
- TEST.md (QA)
- README.md (PM)
- SUPERHUMAN.md (PM, automatic)

## Decisions log
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <one-line summary>; user decision: <decision> -->
[2026-08-15] G0: VISION approved (fidelity #165 + provider-setup #139 as one agnostic project); user decision: approve & proceed. #139 elicitation depth = primary + fallback per tier.
[2026-08-15] G1: Workflow prefs set — HITL-H, on-divergence cadence, foundation-first, git=remote, parallelism=PM-decides; user decision: approve.

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
