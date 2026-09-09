# Superhuman: Sample widget export

**Slug:** sample-widget
**Project-id:** sample-widget-0001
**Started:** 2026-01-05
**Superhuman-version:** 1.1.0
**Vision (one-liner):** Let operators export their widget list as CSV.
**Cadence:** on-divergence
**Value-vs-foundation:** value-first
**Parallelism preference:** PM-decides
**Git:** local
**Remote:** n/a
**Branch strategy:** n/a
**Value definition:** operators can self-serve a CSV export without a support ticket
**Conventions in effect:** python.md, testing.md
**HITL-level:** M
**Modifies-existing-code:** yes

## Declared artifacts
- VISION.md (PM)
- REQUIREMENTS.md (PM)
- DESIGN.md (Architect)
- PLAN.md (PM)
- TEST.md (QA)
- README.md (PM)
- SUPERHUMAN.md (PM, automatic)

## Resume packet
- **objective:** ship a CSV export endpoint for the widget list
- **immutable constraints:** must reuse the existing pagination cursor
- **decisions-locked:** see `## Decisions log` below
- **ruled-out paths:** a scheduled/emailed export (out of scope per the spec)
- **current state:** see `## Chunk log` (latest row) and the last gate entry below
- **next-3-actions:** implement the endpoint; write the test; update the README
- **evidence-pointers:** docs/superhuman/specs/2026-01-05-sample-widget-export.md

## Decisions locked
[2026-01-05T09:00:00] G2: CSV export reuses the existing pagination cursor.

## Decisions log
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <one-line summary>; user decision: <decision> -->
[2026-01-05T09:00:00] G0: vision approved (CSV export for the widget list); user decision: approve
[2026-01-05T09:05:00] G1: kickoff; cadence=on-divergence, value-first, git=local, HITL-level=M; user decision: approve
[2026-01-05T09:10:00] G2: design approved (reuse the pagination cursor); user decision: approve
[2026-01-06T15:00:00] G8: acceptance -- CSV export endpoint accepted; user decision: approve

## Chunk log
| # | Title | Files | Dev model | Status | Started | Ended |
|---|---|---|---|---|---|---|
| 1 | export.csv endpoint | app/routes/export.py | sonnet | done | 2026-01-05 | 2026-01-06 |

## Drift notes

## Archive log

## Recommendation overrides

## Retuning notes
