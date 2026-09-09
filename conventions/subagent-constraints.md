# Convention: subagent environment constraints

**A subagent inherits its parent's tools. It does not inherit its parent's
environment.**

Session-level constraints — the ones a harness injects into the orchestrator's
own preamble about worktrees, shared state, scratchpad locations, and which
operations are forbidden on this machine — reach the PM thread and stop there.
The dispatched subagent starts with the role prompt, the conventions, the
artifact slice and the task brief, and nothing else. Whatever the PM was told
about its environment, the subagent was not.

That makes every such constraint dependent on the PM remembering to restate it,
from memory, in every brief. This convention exists because that failed:

> 2026-09-09, `fleet-deterministic-seams` chunk 6a. The PM's brief carried the
> don't-commit, don't-touch-`settings.json` and don't-touch-the-docs-mount
> constraints, and omitted the prohibition on bare `git stash` / `git stash pop`
> in a repo whose stash stack is shared with every other worktree and session.
> The Developer used exactly that, to produce a before/after comparison. No work
> was lost — but only because the stack happened to be empty at that moment,
> which is luck, not a control.

## The rule

**Every `<dispatch:agent>` brief includes the constraint block below, verbatim,
in the `declared conventions` position of the cache-stable ordering**
(`role prompt → declared references → declared conventions → cached artifact
slice → task brief`). Do not paraphrase it and do not trim it to the items that
feel relevant to this chunk — the 2026-09-09 omission was exactly such a trim,
made in good faith, of the one item that mattered.

Project-specific constraints go in the task brief, at the end, as before. This
block is the floor, not the ceiling.

## The block

```
ENVIRONMENT CONSTRAINTS (inherited from the dispatching session; not optional)

- Work only in the worktree named in this brief. Do not `cd` to the main
  checkout or to a sibling worktree.
- The git stash stack is shared with the main checkout, every other worktree,
  and any other session working in this repository. Never use bare `git stash`
  or `git stash pop` — you may silently restore and destroy another session's
  work. Prefer a temporary WIP commit. If you must stash: `git stash push -u -m
  "<unique-tag>"`, capture the SHA from `git stash list --format='%H %gs'`, and
  restore with `git stash apply <sha>`, never `pop`.
- Never `git add -A` and never a bare `git commit` in a repository that carries
  a mounted or nested clone; stage by explicit pathspec so you cannot sweep up
  another session's in-flight work.
- Do not commit unless this brief explicitly tells you to. Leave the tree dirty
  and report; the dispatching thread reviews and commits.
- Do not modify harness or machine configuration — `settings.json`, hook
  registrations, permission settings, git config — under any circumstances.
  Those are the dispatching thread's to change, with the user's approval.
- Use the scratchpad directory for temporary files, never the repository and
  never a path built by walking upward out of the worktree.
- Report honestly. `DONE_WITH_CONCERNS` with a real flag is worth more than a
  clean-looking `DONE`. Claim success only with fresh evidence — actual command
  output, actual counts.
```

## Why prose here and a hook elsewhere

Where a constraint can be enforced deterministically, it should be, and this
convention is not a substitute for that. The shared-stash prohibition above, for
instance, is better served by a `PreToolUse` hook that inspects the proposed
command, because a hook reaches a subagent's tool calls whether or not anyone
remembered to write it down; prose depends on the dispatcher's memory, and that
is the thing that already failed. Prefer the hook wherever a constraint can be
decided from the command text alone.

This block exists for the constraints that have no enforcement point yet, and as
the thing a Developer can read to understand *why* a hook stopped them — a
refusal without an explanation gets worked around rather than learned from.

**A worked example of the same class, from this file's own history.** The first
draft of this section named the specific hook implementation by its
operator-specific project name. This repository publishes, and the guard that
keeps operator vocabulary out of it refused the commit. Two things are worth
taking from that. Generic guidance should name the *mechanism* ("a `PreToolUse`
hook that inspects the command"), never an operator's private artifact — the
mechanism is what a reader of this public skill can act on. And a control only
catches you if it is actually armed: that guard had been silently skipping in
every linked worktree until the fix that shipped alongside this file.
