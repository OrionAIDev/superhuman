# Superhuman: {{project_name}}

**Slug:** {{project_slug}}
**Started:** {{iso_date}}
**Superhuman-version:** {{superhuman_version}}
**Vision (one-liner):** {{vision_oneline}}
**Cadence:** per-chunk | on-divergence
**Value-vs-foundation:** value-first | foundation-first | hybrid
**Parallelism preference:** PM-decides | gate-each | serial-only
**Git:** none | local | remote
**Remote:** {{remote_url_or_na}}
**Branch strategy:** {{branch_strategy_or_na}}
**Value definition:** {{value_definition}}
**Conventions in effect:** {{conventions_list}}
**HITL-level:** {{H | M | L}}
<!-- H (High)   = full HITL; every gate is answered by a human.
     M (Medium) = surrogate answers G2-G5/G7; G0, G1, G6(moderate+), G8, G9, G10 stay human.
     L (Low)    = surrogate/PM answers everything via precedent-mining; only G10 stays human.
     Locked at G1 for this project's lifetime — do not re-elicit on resume. See SKILL.md
     "HITL levels". Legacy 0/1/2 still parses and maps to H/M/L.

     This is a CEILING request, not a guarantee: the resolved rung's act_unattended policy can
     forbid M and L regardless of what is recorded here. A project may always be more cautious
     than its location requires; never less. -->
**Modifies-existing-code:** {{yes_or_no}}
<!-- Set at Phase 0 kickoff (same detection as the pre-existing-code drift check). Gates whether
     HITL-L requires ROLLBACK.md — see scripts/autonomous-precondition.sh. -->

## Environment: {{environment_marker_or_omit}}
<!-- OPTIONAL. Declares which rung of your deployment profile this project belongs to, for cases
     where the filesystem path alone does not say (e.g. a container whose in-image path drops the
     environment name). The value is matched against your profile's `env_marker` detector and
     outranks path detection, so it is authoritative.

     Omit this line entirely if your path already identifies the location, or if you have no
     deployment ladder at all — most projects need neither.

     Check what a given location resolves to:
       superhuman_profile.py explain <project-root> -->
<!-- **Resolved rung:** {{rung_name}} (matched by {{matched_by}}; profile {{profile_hash}}) -->
<!-- PM records the resolved snapshot at G1. It is an AUDIT RECORD, not an authority — the
     resolver re-resolves on every run and raises a G6 drift event on mismatch. -->

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
<!-- Present only for HITL-M or 2 runs. -->
<!-- run-id: <ISO-compact+nonce> | branch: autonomous/<slug>/<run-id> | goal: GOAL.md
     fitness: <defn> | min_delta: <x> | bounds: iters<=N, wall<=Hh, tokens<=K, per-iter<=Mmin -->

## Autonomous iterations log
<!-- Append-only. One row per iteration. -->
| iter | fitness before | fitness after | delta | KEEP/ROLLBACK | tag | archive ref |
|---|---|---|---|---|---|---|
