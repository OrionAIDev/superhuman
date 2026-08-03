# Rollback plan: {{project_name}}

> Required precondition for HITL-L (Low) whenever `Modifies-existing-code: yes` in
> SUPERHUMAN.md. Checked deterministically by `scripts/autonomous-precondition.sh --level 2`
> before the PM is allowed to run unattended. Not required for net-new/greenfield projects —
> there is nothing pre-existing to revert to, and archive-never-delete already covers undoing
> new work.

## Revert target
The exact commit/tag the project must be restored to if the delivered change needs to be undone:
```
{{git tag or commit SHA of the last known-good state before this project started}}
```

## Revert procedure
The exact, runnable steps to restore that state — not a description, the actual commands:
```
{{e.g. git checkout main && git reset --hard <sha> && <any migration-down / service-restart steps>}}
```

## What this does NOT cover
- Superhuman's own project artifacts (`docs/superhuman/<slug>/`) — those are archive-never-delete
  regardless of this plan.
- Any state at a rung that forbids unattended operation — HITL-L never runs there, so this plan
  only ever applies where `act_unattended` permits it.

## Verification after rollback
{{e.g. the command that confirms the revert succeeded — test suite passes, service responds, etc.}}
