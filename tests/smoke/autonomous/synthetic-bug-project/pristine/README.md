# pristine/

Restore the synthetic-bug project to its 2-bug starting state by copying
`src/` and `tests/` from here over the project root before each smoke run:

```bash
cp -r pristine/src/ src/
cp -r pristine/tests/ tests/
```

This ensures the autonomous loop always starts from a known broken state
(1 passing test, 2 failing tests) rather than a previously-fixed state.
