# route.py fixture: stale-project

A synthetic repository whose `SUPERHUMAN.md` (slug `sample-widget`) has no
structured `## Decisions log` section at all -- modelling a legacy or
hand-broken file. Used by `tests/test_route_cli.py` to exercise the FR-4
resume short-circuit's "stale" (INVALID) outcome -- `resolve` must exit 0
with `resume: stale(<path>)` on stdout and no `tier:` line, and must never
reach signal evaluation.
