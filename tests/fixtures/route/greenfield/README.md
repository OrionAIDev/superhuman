# route.py fixture: greenfield

A synthetic, essentially-empty repository: no `docs/superhuman/` tree, no
manifest, no prior specs or plans. Used by `tests/test_route_cli.py` to
exercise the case where `check_resume` finds nothing to short-circuit on and
routing proceeds to scoring -- which, in this chunk, raises
`NotImplementedError` and the CLI exits 3 (EXIT_INTERNAL).
