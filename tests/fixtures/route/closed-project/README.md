# route.py fixture: closed-project

Same shape as `open-project`, but its `SUPERHUMAN.md` additionally logs a
`G8` (acceptance) entry, marking the project closed. Used by
`tests/test_route_cli.py` to exercise the FR-4 requirement that a closed
project does **not** short-circuit resume -- routing must proceed to signal
evaluation, which in this chunk raises `NotImplementedError` and the CLI
exits 3 (EXIT_INTERNAL).
