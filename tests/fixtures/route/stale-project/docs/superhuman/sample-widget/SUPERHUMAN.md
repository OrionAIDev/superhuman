# Superhuman: Sample widget export

**Slug:** sample-widget
**Started:** 2026-01-05
**Vision (one-liner):** Let operators export their widget list as CSV.

<!--
  This fixture models a legacy/malformed SUPERHUMAN.md: it has no structured
  "## Decisions log" section at all (an older format, or a file someone hand-
  edited and broke), so route.py's resume check must classify it as INVALID
  ("stale") per DESIGN.md's resume table, and short-circuit to
  `resume: stale(<path>)` rather than treating it as open or falling through
  to scoring.
-->

## Notes
Some free-form notes about the export idea were left here instead of using
the Decisions log at all.
