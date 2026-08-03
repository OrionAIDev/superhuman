# Autonomous Run Summary: {{project_name}}

## Run metadata
- Slug: {{slug}}
- Run-id: {{run_id}}
- Branch: autonomous/{{slug}}/{{run_id}}
- Final tag: v{{version}}-beta-{{run_id}}
- Started / ended: {{start_ts}} / {{end_ts}}

## Goal (verbatim from GOAL.md)
{{goal_text}}
- Fitness function: {{fitness_defn}}
- Measurement command: {{measure_cmd}}
- Budget envelope: {{bounds}}

## Per-iteration table
{{iterations_table}}

## Final state
- Final fitness: {{final_fitness}}
- Iterations attempted / kept / rolled-back: {{n_attempted}} / {{n_kept}} / {{n_rolled}}
- Spend vs budget: {{spend_vs_budget}}

## Declared artifacts check
{{declared_artifacts_status}}

## Drift events surfaced to human
{{drift_events}}

## PM recommended acceptance action (human decides at G8)
**{{APPROVE | REVIEW-AND-APPROVE | REJECT-AND-ROLLBACK}}** — {{reasoning}}

## Rollback command
```
scripts/autonomous-rollback.sh {{slug}}
```

## Audit trail
- SUPERHUMAN.md: docs/superhuman/{{slug}}/SUPERHUMAN.md
- Tags: {{tag_list}}
