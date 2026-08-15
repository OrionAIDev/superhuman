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
[2026-08-15] Pre-flight: fixed pre-existing pre-commit hook GIT_DIR-leak defect (scripts/git-hooks/pre-commit) that blocked all commits and had corrupted shared core.bare=true; repaired shared config; committed c6933b8. Orthogonal to #165/#139; user decision: fix hook source now.
[2026-08-15] G2: REQUIREMENTS approved (10 FR / 6 NFR; 5 OQ deferred to Design); user decision: approve & proceed.

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
[2026-08-15] G0/G1/G2: user approved PM recommendation as-is three times running (incl. the one flagged scope choice, taking the recommended option); bias adjustment: user trusts crisp recommend-first framing — keep gates tight, lead with a clear recommendation, avoid padding options the user is unlikely to want. Do not infer they want fewer gates (HITL-H is locked) — only fewer/tighter questions per gate.
