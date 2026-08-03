# Smoke checklist — Autonomous loop (synthetic-bug-project)

Manual smoke test of the superhuman **autonomous loop**, exercised at **HITL-M (Medium)**
(v0.2.0 naming: "autonomous mode" — same mechanics, renamed in v0.5.0's HITL-level scheme; see
SKILL.md "HITL levels") against the `synthetic-bug-project` fixture. Run on both Claude Code and
a deployed environment before tagging a release final. Tick every box; if any item fails, do NOT mark the
release final — cut a patch fix and re-run.

**Target model:** `google/gemini-2.5-pro` (most-capable available while
Codex/Anthropic credits are down). Driven headlessly per the
openclaw-headless-drive runbook for an OpenClaw deployment; directly in Claude Code session
for the laptop platform.

Both platforms (Claude Code + OpenClaw) must pass.

---

## Step 1 — Restore from pristine

- [ ] From inside `tests/smoke/autonomous/synthetic-bug-project/`, run:

  ```bash
  cp -r pristine/src/ src/
  cp -r pristine/tests/ tests/
  ```

- [ ] Sanity-check: `python -m pytest tests/ -q` shows **1 passed, 2 failed**
      (test_add passes; test_sum_to_inclusive and test_double fail).

---

## Step 2 — Invoke superhuman with autonomous-mode entry

- [ ] Point superhuman at `tests/smoke/autonomous/synthetic-bug-project/`.
- [ ] Use an entry prompt that references the pre-completed `SUPERHUMAN.md` at
      `docs/superhuman/synthetic-bug/SUPERHUMAN.md` and requests resume into
      the autonomous loop. Example prompt:

  > "Resume superhuman on this project. SUPERHUMAN.md is at
  > docs/superhuman/synthetic-bug/SUPERHUMAN.md — G0 and G1 are already
  > approved. Start the autonomous loop using GOAL.md."

- [ ] Confirm the skill reads `SUPERHUMAN.md`, sees G0 and G1 in the Decisions
      log, and enters the autonomous loop **without** re-presenting G0 or G1.

---

## Step 3 — Step-0 precondition check

- [ ] The loop reads `GOAL.md` and confirms `Environment: lab` passes the
      hard-gate (permitted wherever the profile's act_unattended policy allows; refused at any
      rung declaring `never`, and at any rung declaring no policy at all).
- [ ] The loop reads the initial fitness: `python -m pytest tests/ -q` →
      **fitness = 0.333** (1/3 passed).
- [ ] No crash or gate rejection at this step.

---

## Step 4 — Loop iterates to fitness 1.0

- [ ] The loop performs at least one iteration, each within the 15-min
      per-iteration cap.
- [ ] Fitness strictly increases each KEEP iteration (delta ≥ 0.01).
- [ ] Loop exits with **fitness == 1.0** within 5 iterations.
- [ ] Final `python -m pytest tests/ -q` output shows **3 passed, 0 failed**.

---

## Step 5 — Verify per-iteration artifacts

- [ ] Each iteration has a pre-snapshot tag `v<X.Y.Z>-alpha-<run-id>.iter-<N>-pre` created
      before the Developer runs.
- [ ] Each KEEP iteration has a tag `v<X.Y.Z>-alpha-<run-id>.iter-<N>` (no `-pre` suffix)
      created after the commit.
- [ ] At end-of-run, a candidate tag `v<X.Y.Z>-beta-<run-id>` marks the final result.
- [ ] An autonomous run summary report has been generated (file or assistant
      output) with columns matching `templates/autonomous-run-summary.md.tpl`.
- [ ] No commits were made on `main` during the run.
- [ ] An `autonomous/synthetic-bug/<run-id>` branch exists containing all
      iteration commits.

---

## Step 6 — Reject path (rollback)

- [ ] Run the rollback script:

  ```bash
  scripts/autonomous-rollback.sh synthetic-bug
  ```

- [ ] Confirm the script creates an annotated git tag
      `archive/autonomous/synthetic-bug/<run-id>` at the tip of the autonomous
      branch (the branch itself is NOT deleted — history is preserved).
- [ ] Confirm `main` (or `master`) is reset to the last human-approved ref
      (from `Approved-Ref:` in SUPERHUMAN.md, or the merge-base fallback).
- [ ] `git log --oneline main` is unchanged from before the loop started.
- [ ] The script does NOT push — the operator pushes manually afterward.

---

## After the run

- [ ] Restore from `pristine/` (Step 1) to leave the fixture clean for the
      next run.
- [ ] Delete the `autonomous/synthetic-bug/<run-id>` branch locally if
      desired (the rollback script does not delete it — it only tags the tip
      with `archive/…`). The archive tag is permanent per the
      archive-never-delete policy.
