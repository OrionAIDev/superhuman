# route.py fixture: open-project

A synthetic repository with one valid, unclosed `SUPERHUMAN.md` project
(`sample-widget`): a `## Decisions log` with `G0`/`G1`/`G2` entries and no
`G8`. Used by `tests/test_route_cli.py` to exercise the FR-4 resume
short-circuit's "open" outcome -- `resolve` must exit 0 with no `tier:` line,
and must never reach signal evaluation.
