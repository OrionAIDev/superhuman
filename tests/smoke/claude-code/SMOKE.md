# Smoke checklist — Claude Code

Manual smoke test of the superhuman skill on the **Claude Code** harness, run
against the canonical `hello-cli` fixture before tagging a release final. Tick
every box; if any item fails, do NOT mark the release final — cut a patch fix and
re-run.

**Reset before each run** (archives, never deletes — restores a clean fixture):

```bash
scripts/cleanup-project.sh tests/smoke/fixtures/hello-cli --include-code
```

## Preconditions

- [ ] PM orchestrator thread is on a **most-capable** tier (e.g. `claude-best` /
      `claude-better`). A `-mini` / `-flash` / `haiku-*` tier is forbidden — it
      will skip gates regardless of the HARD-GATE.
- [ ] State reset has been run (see command above) so no stale `SUPERHUMAN.md`
      from a previous run is present.

## Walkthrough

- [ ] Invoke superhuman on the `hello-cli` fixture (point it at
      `tests/smoke/fixtures/hello-cli/`).
- [ ] **G0 (vision)** is presented.
- [ ] **G1 (workflow preferences)** is presented — even though the prefs are
      obvious for a tiny CLI, G0 and G1 BOTH fire (never auto-skipped).
- [ ] **G2 (requirements)** fires.
- [ ] **G3 (design + chunking + declared artifacts)** fires.
- [ ] **G4 (test plan)** fires.
- [ ] At least one chunk is implemented (Developer dispatched, tests written
      first per TDD).
- [ ] **G5 (per-chunk result)** fires after Phase 3.1 for that chunk.
- [ ] **Autonomous progression:** after a **Type B** G5 (on-divergence cadence),
      the PM continues to the next chunk / next phase on its own — NO "continue"
      or "status?" prompt is needed from the user.
- [ ] **Phase 3 heartbeat** appears during the implementation/review stretch — a
      one-line `[HH:MM] Phase 3 chunk n/N — <subagent> in flight (<elapsed>)`
      notification while a subagent is in flight.
- [ ] **G7 (docs sync)** fires with a docs-sync diff for review.
- [ ] **G8 (acceptance sign-off)** fires.
- [ ] The **G8 PROJECT COMPLETE terminator** appears on its **own line**, after
      the acceptance summary and any merge/PR output:

      ```
      ✅ PROJECT COMPLETE — superhuman is done; reply '/new' to start another
      ```

- [ ] The terminator appears **exactly once** and only after G8 (never at an
      earlier gate).

## After the run

- [ ] Re-run the reset command above to leave the fixture clean for the next run.
