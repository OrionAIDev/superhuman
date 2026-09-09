# route.py fixture: with-priors

A synthetic repository that has prior `docs/superhuman/specs/` and
`docs/superhuman/plans/` content, but no `docs/superhuman/<slug>/SUPERHUMAN.md`
project record anywhere. Used by `tests/test_route_cli.py` to exercise the
case where `check_resume` finds nothing to short-circuit on (the glob for
`SUPERHUMAN.md` files matches zero paths) and routing proceeds to scoring --
which, in this chunk, raises `NotImplementedError` and the CLI exits 3
(EXIT_INTERNAL).

A minimal `pyproject.toml` is included so this fixture is also ready for
Chunk 2's repo-axis signal fixtures (`repo(specs=N,plans=M)`), without needing
rebuilding then.
