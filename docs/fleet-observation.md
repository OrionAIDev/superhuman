# Fleet observation

Superhuman can write to its own session-fleet manifest as a side effect of normal operation — a
handoff prompt, a role dispatch, a relayed session, a launched checkpoint — without anyone running
`fleet` by hand. This doc is the operator-facing guide to turning that on, understanding what it
guarantees (and does not), and reading its output correctly.

It covers: enablement, the fail-soft/fail-closed boundary, the granularity rule for spawned
dispatches, installing the optional hook templates, `fleet observe status`, and the caveat every
operator needs before acting on a stale report.

## Enablement

Fleet observation is opt-in and off by default. It turns on by setting a `fleet:` block in the
project's profile YAML (the same profile file the deployment ladder already reads):

```yaml
fleet:
  enabled: true
  # All of the following are optional; shown with their built-in defaults.
  # manifest_dir: <workspace>/docs/superhuman/<slug>/fleet
  # observe_deadline_seconds: 5.0
  # git_timeout_seconds: 0.25
  # lock_timeout_seconds: 0.8
```

`enabled` must be literally `true` — anything else (absent, `false`, a string, a number) resolves
to disabled. With no `fleet:` block at all, or no profile at all, observation is disabled and
**inert by construction**: every `fleet observe *` call returns immediately with zero writes and
zero output. A workspace that has never heard of fleet observation is not perturbed by it — a
normal `superhuman kickoff` behaves exactly as it did before this project existed.

Enabling it does not change anything superhuman actually does. It only adds a parallel, best-effort
record of what already happened.

**Gitignore your own manifest directory.** The default `manifest_dir` (`<workspace>/docs/superhuman/
<slug>/fleet`) sits inside your tracked tree. `events.jsonl`, the per-session fragments under
`sessions/`, and `observe-failures.log` all carry absolute local paths from the machine that wrote
them (workspace roots, git toplevels). Add `docs/superhuman/<slug>/fleet/` (or your configured
`manifest_dir`, if you overrode it) to your own repo's `.gitignore` before enabling — otherwise those
host-specific paths get committed. (Superhuman's own repo already ignores this pattern for its own
use; a newly-enabling project does not inherit that for free.)

**Scope: short-lived process invocation only.** `fleet observe` is designed and tested as a
short-lived CLI process — invoked, does its bounded work, exits. That exit is load-bearing: on an
internal timeout (`observe.py`'s `_run_bounded`), the abandoned worker thread is not cancelled, and
relies on the process itself exiting soon after to release any manifest lock it might still be
holding. A long-lived in-process/library caller that imports `observe.py` and keeps running past a
timeout would not get that release for free, and could see a wedged lock persist for the rest of its
lifetime. This is explicitly unsupported/undocumented usage; treat `fleet observe` as a CLI, not a
library to embed in a long-running process.

**The profile that enables this can be repo-carried.** `fleet:` is read from whichever profile
`superhuman_profile.find_profile` resolves for the workspace, which can be a project-local
`.superhuman/profile.yaml` — a file that travels inside a cloned repo, not just an operator's own
`~/.superhuman/profile.yaml`. In other words, a repo you check out can itself switch fleet
observation on for you. `manifest_dir` is confined to stay inside `workspace` (an override that
would resolve outside it disables observation instead of writing there), but the enablement switch
itself is still something a project-local profile controls — know this before trusting an unfamiliar
repo's profile blindly.

## The fail-soft / fail-closed boundary

This is the single most important thing to understand before relying on fleet observation for
anything.

**`scripts/fleet/observe.py` is fail-soft. Everything beneath it is unchanged and stays
fail-closed.** The manifest's write path — `cli.register_session`, `handoff.py`,
`scripts/fleet/core/*` — is Phase 1's existing machinery, untouched by this project
(`scripts/fleet/core/*` is never modified, per this project's own non-negotiable boundary). When
that machinery correctly *rejects* a write — a validation error, an ownership conflict, a
policy-refused write — that rejection is real and is never silently swallowed.

`observe.py` sits one layer above that boundary and catches broadly on purpose: a lock timeout, a
missing directory, an unwritable path, a malformed profile, an adapter error, or any other fault in
the *observation* itself is caught, journaled to `<fleet-dir>/observe-failures.log`, and never
allowed to change the result of the operation being observed. `fleet observe <event>` always exits
`0`. The deliverable superhuman was already producing — a dispatch, a handoff prompt, a launched
session — is never held hostage to whether the manifest write behind it succeeded.

Put differently: **the manifest may be incomplete, rarely wrong about whether a row was validly
written.** A missed observation means a row that should exist does not (an omission) — that part
holds unconditionally, because every row that exists flowed through Phase 1's unchanged, validating
write path. If fleet observation is disabled, misconfigured, or hits a fault short of a write, you
get silence, not a fabricated row.

That guarantee is about *whether a row's write was valid* — it is not a guarantee about *which
session a launch flip got bound to*. `fleet observe launch` resolves its target id by grepping the
launched session's own prompt text for the first `FLEET-HANDOFF-ID:` line found anywhere
(`handoff.extract_handoff_id`), or by a fuzzy `(cwd, branch)` match when no id line is present. A
prompt that quotes or re-pastes an earlier handoff prompt — a user copies a kickoff prompt forward,
or a prompt embeds an issue body that itself contains an old `FLEET-HANDOFF-ID:` line — can bind the
launch flip to the *wrong* row: a real commission, a row asserting a launch that did not happen for
that session, not merely a missing one. This is narrow (it requires a foreign id line literally
present in the prompt, or an ambiguous fuzzy match) but real, and it is the one place this doc's
"never wrong" framing needs qualifying — see the caveat in the next section.

## The granularity rule (which dispatches register)

Not every subagent dispatch is a fleet-worthy session. The rule, stated once (`roles/pm.md`,
"Fleet dispatch observation"), is:

> A dispatch registers **iff** the dispatched prompt leads with a `roles/*.md` block.

That covers PM, Architect, Developer, QA, Tester, Business Expert, surrogate-user, and reviewer
role dispatches — anything with its own role contract and its own deliverable. It excludes
research or read-only fan-outs the PM makes for its own reading (an `Explore` dispatch, for
example) — those are not sessions with a deliverable in any meaningful sense, and registering them
would just add noise to the manifest without adding a real thread to track.

If you are writing your own hook or automation on top of `fleet observe dispatch`, apply this same
predicate before calling through — the shipped `templates/hooks/PreToolUse` template says so
explicitly and cannot decide it for you generically, since the check depends on your harness's own
dispatch-tool payload shape.

## Installing the hook templates

`templates/hooks/SessionStart` and `templates/hooks/PreToolUse` are operator-neutral templates,
not live hooks — nothing in this skill installs them automatically. Each is a deterministic ceiling
layered *on top of* the portable prose floor (the `roles/pm.md` / `SKILL.md` call-outs an
orchestrating model follows on its own): where installed, the hook fires regardless of whether the
model reads or acts on the prose instruction.

Both templates call the exact same `fleet observe <verb>` entry point the prose floor calls — never
a parallel or divergent invocation, so a hook can never drift out of sync with what the portable
path does.

To install:

1. Copy the template for your harness's session-start / pre-tool-use hook mechanism (for Claude
   Code, `.claude/settings.json`'s `SessionStart` / `PreToolUse` hooks).
2. Fill in the placeholders each template marks with `REPLACE_WITH_...` — the project slug
   (`SessionStart`, `PreToolUse`) and, for `PreToolUse`, a dispatch id your harness can supply per
   call. Neither is auto-derived, because more than one project slug can exist under one workspace.
3. Point `FLEET_CLI` at your `fleet` install if it is not on `PATH`.
4. Leave the best-effort guarding (`|| true`, unconditional `exit 0`) as shipped — the hook must
   never block session start or the tool call it observes, matching `observe.py`'s own contract.

Both templates contain no operator-, host-, or vendor-specific tokens as shipped; the placeholders
above are exactly what you fill in for your own environment.

## `fleet observe status`

`fleet observe status --workspace <path> --slug <slug>` is the one `observe` subcommand whose
entire purpose is its printed report. It answers "is fleet observation active for this project,
and why (not)?" in one of four shapes:

- `not configured: <reason>` — disabled, with the specific cause (no profile found, no `fleet:`
  block, `fleet.enabled` not `true`, an unreadable or malformed profile, etc.).
- `configured and enabled, zero writes recorded for this project` — enabled, but nothing has
  registered yet.
- `configured and enabled, last write for this project succeeded` — enabled and working.
- `configured and enabled, last write for this project failed: <detail>` — enabled, but the most
  recent observation hit a fault (surfaced from the failure journal).

Always exits `0` — a read-only report has nothing to reject.

## Reading `fleet handoff stale` output: candidates, not a verdict

`fleet handoff stale` lists `awaiting-launch` rows past their expiry — handoffs that were emitted
but, as far as the manifest can tell, never got launched. **Treat that list as a set of candidates
to confirm, not as a verdict.**

The reason is structural, not a hedge: the launch flip depends on the launched session actually
invoking `fleet observe launch` (via its embedded prompt instruction, the `SKILL.md` first-action
step, or an installed `SessionStart` hook). If none of those fire for a session that *did* in fact
launch and continue — a hook was never installed, the model skipped the prose step, the prompt was
edited beyond fuzzy-match recognition — that session's row stays `awaiting-launch` and will
eventually show up as "stale" even though real work is happening in it. A stale report is strong
evidence a handoff was dropped; it is not proof, and the right response is to go check the
candidate, not to assume it is abandoned.

This is also why the launch flip's fuzzy `(cwd, branch)` fallback exists: any later
`fleet observe launch` call from the right checkout reconciles an `awaiting-launch` row even if the
original id line was lost, so a false stale report is usually recoverable rather than permanent —
but only once someone acts on the candidate.

**The converse caveat: a row that has already flipped to `active` is not automatically trustworthy
either.** The list above is about rows that stayed `awaiting-launch` when they shouldn't have
(omission). The opposite failure is narrower but real: the id-matching described in "The fail-soft /
fail-closed boundary" above means a session whose prompt happens to contain a *foreign*
`FLEET-HANDOFF-ID:` line (a re-pasted or quoted earlier prompt), or an ambiguous fuzzy match, can
flip the wrong row to `active` — which then reads as launched-and-fine and drops out of the stale
list entirely, even though the row it displaced is the one still actually unlaunched. If a handoff
you know was emitted is missing from `fleet handoff stale` and you did not expect it to have
launched yet, that is worth checking too, not just the rows the command actually lists.

## Manual-smoke log

Two acceptance criteria in this project are only partially automatable — the live-dispatch half of
`W-FR-1` (does a real spawned dispatch actually produce a validated `spawned` entry) and the
live-execution half of `W-NFR-4` (does a resumed pre-existing project fire identical gates in
identical order). Both are documented manual procedures (`docs/superhuman/fleet-wiring/TEST.md`'s
`MV-1` and `MV-2`), not gaps and not silently downgraded to "trust the design." Record each run's
date, commit hash, and pass/fail below.

| Date | Procedure | Commit | Result | Notes |
|---|---|---|---|---|
| _(none recorded yet)_ | | | | |
