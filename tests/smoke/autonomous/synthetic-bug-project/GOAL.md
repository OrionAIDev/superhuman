# Goal: synthetic-bug-project

## Objective (verbatim)
All tests in tests/ pass.

## Fitness function
**Definition:** pytest pass rate = passed / collected (scalar in [0,1]).
**min_delta:** 0.01

## Measurement command
```
python -m pytest tests/ -q
```
How the scalar is extracted: passed / collected.

## Success criterion (loop exit on success)
fitness == 1.0 (all tests pass)

## Budget envelope
- Max iterations: 5
- Per-iteration time cap: 15 min
- Plateau: stop after 3 consecutive iters with fitness delta < 1%

## Environment
lab
