# SUPERHUMAN.md — legacy-format regression fixture

<!-- Backward-compat fixture for NFR-2 (tests/test_content.py::test_backward_compat_fixture_resumes_without_error).
     Represents a pre-existing SUPERHUMAN.md written before the Resume-packet and
     Decisions-locked template sections (added in the fidelity-provider-setup project, #165)
     existed. It has a structurally VALID Decisions-log section (a G<n> entry with a
     'user decision:' field), so the HARD-GATE validity check in SKILL.md passes and the
     project is resumable — the absence of the two new sections must be treated as empty,
     never as corruption. -->

Superhuman-version: 0.3.0
Slug: legacy-fixture-project
HITL-level: H
Cadence: per-chunk
Git: local

## Declared artifacts
- VISION.md (PM)
- REQUIREMENTS.md (PM)
- DESIGN.md (Architect)
- PLAN.md (PM)
- TEST.md (QA)
- README.md (PM)
- SUPERHUMAN.md (PM, automatic)

## Decisions log
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <one-line summary>; user decision: <decision> -->
[2026-01-05T10:00:00Z] G0: VISION approved; user decision: approve
[2026-01-05T10:15:00Z] G1: workflow preferences set (HITL-H, per-chunk, git local); user decision: approve
[2026-01-05T11:00:00Z] G2: REQUIREMENTS approved; user decision: approve

## Chunk log
<!-- Append-only table. -->
| # | Title | Files | Dev model | Status | Started | Ended |
|---|---|---|---|---|---|---|
| 1 | Initial scaffold | src/main.py | standard | done | 2026-01-05T12:00:00Z | 2026-01-05T12:30:00Z |

## Drift notes
<!-- Append-only. Format: [<ISO timestamp>] Chunk <n>: <severity> — <one-line trigger>; action: <taken> -->

## Archive log
<!-- Append-only. Format: [<ISO timestamp>] archived <chunk> to archive/<dir>/; reason: <reason> -->

## Recommendation overrides
<!-- Append-only. Format: [<ISO timestamp>] G<n>: PM recommended <X>; user chose <Y>; reason: <if given> -->

## Retuning notes
<!-- Append-only. Format: [<ISO timestamp>] G<n>: <observation about user pattern>; bias adjustment: <going-forward note> -->
