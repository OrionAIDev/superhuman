# Goal: {{project_name}}

> The autonomous loop optimizes THIS file. Keep it small, measurable, and honest.

## Objective (verbatim)
{{one_paragraph_goal}}

## Fitness function
A single scalar in [0, 1] (higher is better). The loop KEEPs an iteration only
if its fitness strictly exceeds the previous KEEP by `min_delta` (ties -> rollback).

**Definition:** {{e.g. pytest pass rate = passed / total}}
**min_delta:** {{default 0.01}}

## Measurement command
The exact command whose output yields the fitness scalar (must be deterministic
and runnable headlessly):
```
{{e.g. python -m pytest tests/ -q --tb=no}}
```
How the scalar is extracted: {{e.g. passed / collected}}

## Success criterion (loop exit on success)
{{e.g. fitness == 1.0 (all tests pass)}}

## Budget envelope (overrides defaults in conventions/autonomous.md)
- Max iterations: {{default 10, hard ceiling 25}}
- Max wall-clock: {{default 2h, hard ceiling 6h}}
- Max tokens: {{default 500K, hard ceiling 2M}}
- Per-iteration time cap: {{default 15 min, hard ceiling 30 min}}
- Plateau: stop after 3 consecutive iters with fitness delta < 1%

## Environment
lab | test   <!-- MUST be lab or test; uat/prod is hard-blocked -->
