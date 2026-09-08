# Golden hook payload fixtures

Populated by Chunk 1 (`PLAN.md` "Hook payload probe"). Each file is a captured
`SessionStart` or `SubagentStart` stdin JSON payload, replayed by
`test_hook_payload.py` and `test_hooks.py` as the regression coverage for the
harness's stdin contract (`REQUIREMENTS.md` A1).

**Redaction rule (NFR-8), non-negotiable before commit:**

- Every `session_id` value replaced with a fixed placeholder
  (`"redacted-session-id"`).
- Every absolute path containing the operator's username or a
  machine-specific segment (`C:\Users\Chris\...`) replaced with a neutral
  placeholder root (`C:\example\workspace\...`), preserving path *shape*
  (worktree vs. plain repo, depth) since the shape is what the tests
  exercise.
- Verified by extending `publication_patterns.find_tokens`'s existing scan
  (already used by `tests/test_content.py`'s operator-token guard) to cover
  this directory, rather than inventing a parallel redaction check.

No fixture files exist yet — this directory is empty scaffolding until
Chunk 1 runs and commits its (redacted) captures.
