# Fleet observation

Superhuman can write to its own session-fleet manifest as a side effect of normal operation — a
session starting, a role dispatch, a relayed session, a launched checkpoint — without anyone
running the fleet CLI by hand. This doc is the operator-facing guide to turning that on,
understanding what it guarantees (and does not), and reading its output correctly.

It covers: enablement, the fail-soft/fail-closed boundary, the project locator, the granularity
rule for spawned dispatches, the role-first discipline gate, installing the deterministic hook
ceiling, the portable/harness boundary rule, the diagnostic surfaces, and the caveats every
operator needs before acting on a stale report — plus the honest list of what this project does
not (yet) cover.

## Invoking the CLI

Everything below is a subcommand of one CLI, invoked as a Python module **from the superhuman skill
root** — the checkout holding `SKILL.md` and `scripts/`:

```bash
python -m scripts.fleet.cli observe status --workspace <project-root> --slug <slug>
```

Two things about that form are load-bearing:

- **The module form, not a bare `fleet`.** Superhuman is a skill loaded by path, not an installed
  Python distribution — there is no `pyproject.toml` and no `console_scripts` entry point, so
  nothing ever puts a `fleet` executable on `PATH`. Running the file directly
  (`python scripts/fleet/cli.py`) does not work either: `cli.py` imports its siblings
  package-relatively and must be run as a module.
- **The working directory is the *skill* root, not the project being observed.** `-m` resolves
  `scripts.fleet` against the current directory, so the command must run from wherever superhuman
  itself is checked out. The project under observation is named separately, by `--workspace`, and
  is normally a different directory. Getting these two confused is the single most likely reason a
  call that looks right silently does nothing.

For readability the rest of this doc names subcommands in short form — `fleet observe status`,
`fleet handoff stale` — as shorthand for `python -m scripts.fleet.cli observe status` and so on.
Each one needs the full form above to actually run.

**Every row this project writes carries `"origination": "observed"`.** A manifest event written
by `fleet observe *` — whether the call came from the hook ceiling described below or from the
portable prose floor calling the identical verb by hand — is tagged this way in the event's JSON,
distinguishing it from a row an operator or the PM wrote directly through the pre-existing
`fleet register`/`fleet handoff emit` machinery. Nothing about how a row was validated changes; the
tag only records which path produced it.

## Enablement

Fleet observation is opt-in and off by default. It turns on by setting a `fleet:` block in the
project's profile YAML (the same profile file the deployment ladder already reads):

```yaml
fleet:
  enabled: true
  # All of the following are optional.
  # manifest_dir: <workspace>/docs/superhuman/<slug>/fleet
  # observe_deadline_seconds: 5.0
  # git_timeout_seconds: <unset by default -- the git-facts adapters use their own 30s timeout
  #                        unless you deliberately override it here>
  # lock_timeout_seconds: 0.8
```

`enabled` must be literally `true` — anything else (absent, `false`, a string, a number) resolves
to disabled. With no `fleet:` block at all, or no profile at all, observation is disabled and
**inert by construction**: every `fleet observe *` call returns immediately with zero writes and
zero output. A workspace that has never heard of fleet observation is not perturbed by it — a
normal `superhuman kickoff` behaves exactly as it did before this project existed.

Enabling it does not change anything superhuman actually does. It only adds a parallel, best-effort
record of what already happened.

**The manifest directory ignores itself.** `events.jsonl`, the per-session fragments under
`sessions/`, and `observe-failures.log` all carry absolute local paths from the machine that wrote
them (workspace roots, git toplevels) — local runtime state, not something to commit. Rather than
relying on every consuming repo to remember to add `docs/superhuman/<slug>/fleet/` (or a configured
`manifest_dir`) to its own `.gitignore`, `fleet observe` does it for you: the first time a manifest
directory is about to be written and git (asked from the *workspace* root) does not already report
it as ignored, `observe.py` drops a self-ignoring `.gitignore` (content `*`, which also ignores
itself) inside that directory. Nothing outside the manifest directory is ever touched — your repo's
own tracked `.gitignore` is never edited.

This is best-effort, matching this module's fail-soft posture: if `git` is unavailable, the
workspace is not a git repository, the check times out, or the directory turns out to be
unwritable, nothing is written and nothing raises — silence, same as any other fault this façade
absorbs. It is also deliberately skipped when the directory is *already* covered by an ignore rule
further up the tree, which matters for one specific case: a project whose manifest directory is
mounted inside a private git carrier repo that deliberately tracks fleet manifests as a permanent
record (see `docs/superhuman/fleet-deterministic-seams/DECISIONS.md`, ruling R-b) is never given a
self-ignoring marker, because the *outer* workspace repo (not the carrier) already ignores the mount
point that contains it.

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
session a launch flip got bound to*. `fleet observe session-start` (the verb both the hook ceiling
and the portable floor call — see below) only ever attempts an **id-anchored** flip: it resolves a
target row by an explicit `--handoff-id` or by grepping the invoking prompt for the first
`FLEET-HANDOFF-ID:` line found anywhere (`handoff.extract_handoff_id`), and it **never** falls back
to a fuzzy `(cwd, branch)` match. That fuzzy fallback still exists on `fleet observe launch` — a
separate, lower-level verb `observe session-start` calls internally only for the id-anchored case,
and which other callers may still invoke directly — and it is there that a prompt which quotes or
re-pastes an earlier handoff prompt (a user copies a kickoff prompt forward, or a prompt embeds an
issue body that itself contains an old `FLEET-HANDOFF-ID:` line) can bind a launch flip to the
*wrong* row: a real commission, a row asserting a launch that did not happen for that session, not
merely a missing one. This is narrow (it requires a foreign id line literally present in the
prompt, or an ambiguous fuzzy match on a direct `observe launch` call) but real, and it is the one
place this doc's "never wrong" framing needs qualifying — see the caveat in "Reading `fleet handoff
stale` output" below.

## The project locator and disambiguation ladder

Both the `SessionStart` hook and the portable prose floor need to answer the same question before
they can register anything: given a session's working directory, which superhuman project — as a
`(workspace, slug)` **pair**, never a bare slug — is this session working in? `fleet locate` (and
the library function behind it) is that resolver, and both the hook ceiling and the floor call it
identically; they never each maintain their own notion of "the current project."

**Root discovery** tries up to three `git rev-parse` steps, in order, stopping at the first that
yields at least one candidate:

- **H0 — own root.** `git rev-parse --show-toplevel` from the working directory. If the working
  directory is not inside a git repository at all, the locator refuses immediately — there is
  nothing to hop from.
- **H0′ — main-worktree sidestep.** `git rev-parse --git-common-dir`; when it names a `.git`
  outside H0's root, that directory's parent is the repository's *main* working tree. This is not
  an ancestry hop — it names the *same* repository's main working tree, an identity relation git
  itself asserts — so it keeps the full disambiguation ladder below. Tried only when H0 finds
  nothing.
- **H1 — one bounded outward hop.** `git rev-parse --show-toplevel` executed in H0's root's
  *parent* directory. Bounded by a named constant (`MAX_OUTWARD_HOPS = 1`), a self-loop guard, and
  a hard ceiling at `Path.home()` — a workspace exactly at your home directory is refused rather
  than accepted. Because this genuinely crosses a repository boundary, only the strongest
  disambiguation rung (cwd-containment, below) is permitted to fire here. Tried only when H0 and
  H0′ both find nothing.
- Otherwise: refuse, with a reason that names every candidate considered.

A *candidate* is a directory `<root>/docs/superhuman/<slug>/` containing a readable
`SUPERHUMAN.md` whose `**Slug:**` line equals `<slug>` exactly — the shallower
`<root>/<slug>/SUPERHUMAN.md` shape is never a candidate, deliberately: broadening the pattern would
let the same physical directory resolve to two different workspace strings depending on where a
session happened to start, which a design whose whole premise is deterministic resolution cannot
allow. A record with no `**Project-id:**` line is still a candidate, so an estate-wide id backfill
can never change what this resolver returns.

**The disambiguation ladder**, tried in order against whichever root root discovery accepted, each
rung firing only when it picks out exactly one candidate (never on a first match):

1. **cwd-containment** — the working directory is at or under a candidate's own directory.
2. **declared** — the profile resolved for the working directory sets `fleet.slug` to a
   candidate's slug.
3. **branch** — the working directory's current git branch equals a candidate's slug.
4. **singleton** — exactly one candidate exists, full stop.

At the outward hop (H1) only the first rung may fire; the full four-rung ladder applies at H0 and
H0′. If no permitted rung produces a unique answer, the locator refuses rather than guess — a
session working *inside* superhuman's own nested project-docs mount, for example, resolves outward
to the OUTER repository via the outward hop and cwd-containment, while a session sitting
exactly at that mount's own root (no slug segment in its path) correctly refuses: several
candidates, no positional evidence for any one of them.

**Provenance guarantee.** Every workspace path this resolver ever returns comes directly out of a
`git rev-parse` call — never a string join, `os.path.join`, or f-string. A profile value, an
environment variable, or a branch name may only ever *select among* directories already found this
way; none of them can ever contribute a path segment to a returned workspace.

## The granularity rule (which dispatches register)

Not every subagent dispatch is a fleet-worthy session. The rule, stated once (`roles/pm.md`,
"Fleet dispatch observation"), is:

> A dispatch registers **iff** the dispatched prompt leads with a `roles/*.md` block.

That covers PM, Architect, Developer, QA, Tester, Business Expert, surrogate-user, and reviewer
role dispatches — anything with its own role contract and its own deliverable. It excludes
research or read-only fan-outs the PM makes for its own reading (an `Explore` dispatch, for
example) — those are not sessions with a deliverable in any meaningful sense, and registering them
would just add noise to the manifest without adding a real thread to track.

The deterministic ceiling for this rule is the `SubagentStart` hook
(`templates/hooks/claude-code/subagent-start`): it registers on every subagent start (matcher `*`)
and applies this exact predicate itself, by reading the dispatching prompt out of the parent
transcript named in the hook payload — it never trusts the dispatch tool's own `agent_type`, which
is the same generic value for a role dispatch and a research fan-out alike. If you are writing your
own hook or automation on top of `fleet observe dispatch` for a different harness, apply this same
predicate before calling through; it depends on your harness's own dispatch-tool payload shape and
cannot be decided generically.

## The role-first discipline gate

A second, separate mechanism sits on top of the granularity rule above: a `PreToolUse` hook
(`templates/hooks/claude-code/pre-tool-use-role-gate`) that **denies** a role-shaped dispatch tool
call (`Agent`/`Task`) outright unless its prompt is either a verbatim, unedited `roles/<name>.md`
file's content, or opens with the exact literal line `superhuman-dispatch: non-role` marking it as
deliberately not a role dispatch. This exists because a role prompt that only *resembles* a role
brief — paraphrased, missing a reference, hand-edited — silently breaks the granularity rule above:
the subagent never received its actual role contract, so nothing in this project would have caught
it. The gate is checked before that failure mode occurs, not after.

**This is a discipline aid, not a security control (G6, 2026-09-20).** It catches a forgotten or
edited role block — the accidental case the paragraph above describes. It is not a barrier against
a caller deliberately trying to defeat it: a Phase 3.3 preflight round found four distinct,
PM-reproduced ways to bypass it outright (a spoofed `.git` file, a poisoned locator-cache entry, a
directory planted inside the hook's own repository, and inherited git environment variables — see
`templates/hooks/claude-code/pre_tool_use_role_gate.py`'s module docstring for the full list), none
of which this project closes. Every fault in the gate's own machinery already lets the dispatch
through by design (see below); a deliberate bypass gets the identical outcome.

The gate produces one of four verdicts per candidate dispatch: `ROLE` (verbatim match — allowed,
silently, and never itself journaled, since chunk 7's own dispatch-registration row already counts
it), `NON_ROLE` (the literal marker line — allowed), `MISMATCH` (frontmatter names a real role but
the body was edited — denied), or `UNMARKED` (anything else — a prose brief, a prompt that only
mentions `roles/` mid-body, an unrecognised role name — denied). A denial's deny reason names the
literal first differing line and the role file's path; the gate never emits `allow`, `ask`, or
`defer` — it only ever stays silent (empty stdout, exit 0) or denies. `NON_ROLE`, `MISMATCH`, and
`UNMARKED` decisions are appended to `<fleet-dir>/role-gate.jsonl` as one JSON line each, carrying
exactly six fields (a timestamp, the session id, the verdict, the claimed role name where
applicable, the dispatch tool's `subagent_type`, and the mismatch's first differing line NUMBER
where applicable — an integer locating the divergence, never the line's own content) and never the
prompt text itself or any free-form description (this project's own sensitive-data discipline). A
compliant `ROLE`/`NON_ROLE` dispatch is never logged here at all.

Like every other check in this project, a fault in the gate's own machinery (an unreadable
`roles/` directory, an unrecognised locator result, a corrupted payload) degrades to "let the
dispatch through, log nothing" rather than becoming a basis for denial — a missing directory must
never deny every dispatch on the machine.

**Which `roles/` a verdict is judged against.** The primary comparison above always runs against
the checkout the hook itself was installed from — fixed once, at install time. That is not
necessarily the checkout the session is actually working in: a worktree on another branch, or a
branch that edits `roles/pm.md`, is this estate's normal working mode, so a dispatch that is
verbatim against the session's *own* copy could otherwise still earn a would-deny verdict here. To
close that gap, a `MISMATCH`/`UNMARKED` verdict gets one more chance — but a **narrow** one, because
the first shape of this seam was a bypass (a preflight re-run showed any repository carrying a
`SKILL.md` naming `superhuman` could decide the verdict, and an EMPTY `roles/` turned every deny into
a pass). As amended on 2026-09-20 and as shipped:

- The second `roles/` is consulted **only when it sits in the same git repository as the hook's own** —
  `git rev-parse --git-common-dir` must resolve on both sides and be equal. A linked worktree on
  another branch shares that value, which is the case this seam exists for; a third-party repository
  never does. If git cannot answer for either side, the second check does not run at all.
- **A fault in the second check no longer lets the dispatch through.** The pending deny stands: the
  primary check completed successfully, so refusing is not "denying on a check the gate could not
  complete". (A fault in the PRIMARY check still passes the dispatch through, as everywhere else.)
- The locator cache the workspace can come from is validated before use — a cached workspace that is
  not an absolute existing directory, or an unsafe slug, is treated as a cache miss.

The rule is still **widen-only**, but only within one repository: a second `roles/` can turn a pending
deny into a pass, and can never turn a pass into a deny. When both checks deny, the deny reason and the
logged decision name the session's own role file, not the hook's, since that is the copy the session can
actually open and fix.

### The role-tier check (roadmap#275)

A `ROLE`/`NON_ROLE` verdict from the mechanism above is necessary but no longer sufficient for a
pass: the same `PreToolUse` hook also checks the dispatch's `subagent_type` (and rejects a `model`
parameter on a role dispatch) against `adaptation/role-tiers.json` — the harness-neutral table of
which dispatch class (`most_capable`, `most_capable+raised`, `standard`, `cheap`) each role, and
each non-role duty, runs at — and this harness's own dispatch-class -> agent-definition map
(`templates/agents/claude-code/tier-agents.json`). Both files are always read from the hook's own
skill checkout, independent of which `roles/` directory the ROLE-BLOCK comparison above used — the
tier policy is a property of the installed hook, not of whichever workspace a dispatch happens to
target. This closes the gap `adaptation/dispatch.md` names directly: without it, a subagent
dispatch that passes no `model` silently inherits the parent session's model and effort (often the
most-capable tier), an unforced overspend measured at roughly 40% of superhuman's own subagent
dispatches before this check existed.

- **A role dispatch** must use one of its role's tier agents (`adaptation/role-tiers.json`'s
  `default`, or one of its `opt_in` classes) and must pass no `model=` at all — the tier agent
  definition pins the model, and an explicit `model=` would silently override that pin. Using an
  opt-in class additionally requires a `superhuman-tier-opt-in: <reason>` line in the task brief
  (checked as plain text, anywhere in the prompt) — logged to `SUPERHUMAN.md`'s decisions log by
  convention, not enforced by this hook.
- **A non-role dispatch** (`superhuman-dispatch: non-role`) is not tied to one role's allowed
  classes: any tier agent is fine, or an explicit `model=` — the documented way to use a built-in
  `subagent_type` (`Explore`, `general-purpose`) for a non-role duty.
- **Fail-soft, twice over.** A role with no row in `adaptation/role-tiers.json` (a new role file
  added before the policy is updated for it) is not enforced against at all. An unreadable or
  malformed policy or class-map file disables the tier check entirely for that invocation — NFR-9
  applies here exactly as it does to the ROLE-BLOCK check above: a fault in this gate's own
  machinery must never become the reason a dispatch is wrongly blocked.
- **Latency.** The tier check runs BEFORE the locator for a `ROLE` verdict, so a compliant `ROLE`
  dispatch still never pays for the locator — the D7.9 latency guarantee above is unaffected. A
  tier violation, like an ordinary `NON_ROLE`/`MISMATCH`/`UNMARKED` deny, needs the locator for
  scope (D7.4) before it can print anything.
- **The widen path is covered too.** When a `MISMATCH`/`UNMARKED` primary verdict is widened into a
  `ROLE`/`NON_ROLE` pass against the session's own `roles/` (the mechanism described above), the
  identical tier check applies to that WIDENED verdict before it is treated as a pass — otherwise
  the widen path would be a second, unenforced way to dispatch outside the tier policy.
- **Logging.** A tier-policy denial is appended to `role-gate.jsonl` exactly like a `NON_ROLE`/
  `MISMATCH`/`UNMARKED` denial, under its own verdict value, `TIER_DENY` — distinct from the four
  verdicts the ROLE-BLOCK predicate itself produces, so a reader of the log can always tell which
  mechanism denied a given dispatch.
- **Deny reason.** Names the role (or "this non-role dispatch"), the `subagent_type` actually used,
  and the allowed tier agent(s) (default first), then says: drop `model=`; for an opt-in, add the
  `superhuman-tier-opt-in:` line and log it in `SUPERHUMAN.md`; if the tier agents are not
  installed at all, run `python scripts/superhuman_profile.py models install-agents --harness
  claude-code`.

## The D4 boundary: the portable floor and the harness ceiling

Superhuman ships harness-agnostic; hooks are inherently harness-specific. This project drew the
line once, in a form written to be adopted by name rather than reinvented per harness:

> **The portable/harness boundary is drawn at the artifact, not at the module.**
>
> 1. Everything under `scripts/` is harness-agnostic — no harness name appears anywhere in it.
>    Where a harness-shaped input is unavoidable (the hook payload reader), it is documented
>    generically, by field name, so any harness emitting the same fields works unchanged.
> 2. Harness knowledge lives in exactly two kinds of artifact: the hook templates under
>    `templates/hooks/<harness>/`, and the installer, which requires an explicit `--harness <name>`
>    and performs no auto-detection.
> 3. The installer is never invoked automatically — not at kickoff, not by a phase recipe, not by a
>    test. It is an operator command, run by hand.
> 4. The ceiling and the floor call the **identical verb**, differing only in where their arguments
>    come from — enforced by a content test, not maintained by discipline, so drift between them
>    is structurally impossible rather than merely discouraged.
> 5. With the ceiling uninstalled, the floor behaves byte-identically to the pre-ceiling baseline —
>    regression-tested, not merely assumed.

Concretely: `templates/hooks/claude-code/session-start` and `SKILL.md`'s session-start-floor step
both call `observe session-start`; `templates/hooks/claude-code/subagent-start` and
`roles/pm.md`/`phases/3-implementation.md`'s dispatch-observation steps both call
`observe dispatch`. Neither pair is ever allowed to name two different verbs, and a test in this
project's own suite fails the build if they ever do. A workspace with none of the hooks installed
still gets every write the floor alone can produce; installing the ceiling only adds a deterministic
path that fires whether or not the orchestrating model reads or acts on the prose instruction.

## Installing the hook ceiling

The hook templates under `templates/hooks/claude-code/` are never installed automatically — nothing
in this skill registers a live hook on its own. Installation is an explicit, operator-run step:

```bash
python -m scripts.fleet.cli hooks install   --harness claude-code
python -m scripts.fleet.cli hooks status    --harness claude-code
python -m scripts.fleet.cli hooks uninstall  --harness claude-code
```

`--harness` is required and has exactly one accepted value today (`claude-code`); per the D4
boundary above, the installer never guesses which harness it is running under. Useful flags on
`install`:

- `--settings-path <path>` — override the settings file to edit. Defaults to the operator's real
  `~/.claude/settings.json`.
- `--skill-root <path>` — override where the registered commands point. Only needed if you are
  running the installer somewhere other than the checkout you want hooks to run from.
- `--dry-run` — compute and print the change without writing anything.

**What `install` registers.** All five documented `SessionStart` matchers (`startup`, `resume`,
`clear`, `compact`, `fork`), the `SubagentStart` match-all matcher (`*`), and the `PreToolUse`
matcher `Agent|Task` (the role-first discipline gate above) — seven entries in total. Every entry it writes
carries `"timeout": 10` (seconds); a Claude Code command hook's own undeclared default is 600
seconds, so this is a deliberate, much tighter backstop, not an oversight.

**It refuses to touch entries it does not own.** Any pre-existing `SessionStart`, `SubagentStart`,
or `PreToolUse` hook not shipped by this project — including the several that are typically already
present on a real machine — survives byte-identical, both after `install` and after `uninstall`. An
entry is considered superhuman's own iff its command string resolves to one of this project's three
wrapper scripts, whatever root precedes it in the path; that is also what lets `install` **replace**
a stale, previously hand-registered superhuman entry rather than adding a duplicate beside it.
`uninstall` restores the file to exactly its prior state, including migrated entries.

**Where the registered commands point.** With no `--skill-root` override, `install` resolves the
checkout to register against from its *own* file location, and that resolution always lands on the
**main git checkout**, never a linked worktree, even when the installer itself is being run from
inside one. If it cannot establish that (a degenerate git state), it refuses outright rather than
silently writing a worktree-rooted path. This matters for two independent reasons: a hook command
pointing into a linked worktree silently changes behavior — or breaks — the moment that worktree is
switched to a different branch or deleted, and a machine-wide config file is exactly the kind of
dependency a worktree-reaping tool has no way to see, since nothing about the worktree itself
records that something outside it points back in.

**The write itself never leaves a half-written file.** Every write goes through a temporary file in
the same directory, then an atomic rename — a crash midway leaves the real `settings.json` exactly
as it was.

`fleet hooks status` reports, per entry, whether it is present, whether the harness's own
`Edit`/`Write`/`Bash` guard (or any other pre-tool-use control already on the machine) would matter
here, and two things worth checking after any manual edit to the file:

- **Whether the registered command's path still exists on disk.** A registered entry whose command
  no longer resolves to a real file is a **silent-disable case** — the hook is "installed" in the
  sense that `settings.json` names it, but the harness will simply fail to find it and move on.
  `status` calls this out explicitly rather than reporting such an entry as healthy.
- **Whether a registered command is pinned to a linked worktree** rather than the main checkout —
  the same hazard `install`'s own refusal above exists to prevent, surfaced here for an entry that
  was registered before that refusal existed, or via an explicit `--skill-root` override.

## Diagnostics: `fleet doctor` and `fleet observe status`

Two read-only surfaces answer two different questions.

**`fleet doctor --scan <root> [<root> ...]`** answers, for every project record found under the
given roots, "could a hook write to this project right now, and if not, why?" Each record lands in
exactly one of four states: `unresolvable` (the locator, run from inside the record's own
directory, cannot uniquely resolve it — a malformed record or a genuine ambiguity), `fleet_disabled`
(resolves fine, but no profile enables `fleet.enabled: true` for its workspace), `no_project_id`
(enabled, but the record carries no `**Project-id:**` line — the exact silent blocker this project
exists to close), or `ok`. `fleet doctor` also prints the operator's measured git version and flags
anything older than 2.31 — the version the locator's root discovery depends on
(`git rev-parse --path-format=absolute`); on an older git, the locator's calls fail, resolution goes
silently dead, and this is the one surface that would tell you why. For every `ok` record it
additionally reports the role-first discipline gate's activity for that project (how many `NON_ROLE` and
`MISMATCH`/`UNMARKED` decisions were logged) — and reports that section as `UNKNOWN`, never as zero,
when `role-gate.jsonl` is absent or empty: an absent log is genuinely ambiguous between "the gate is
installed and every dispatch complied" and "the gate never ran at all," and collapsing that
ambiguity into a confident-looking zero would hide the second case.

**`fleet observe status --workspace <path> --slug <slug>`** answers a narrower, per-project
question — "is fleet observation active for *this* project, and why (not)?" — in one of four
shapes:

- `not configured: <reason>` — disabled, with the specific cause (no profile found, no `fleet:`
  block, `fleet.enabled` not `true`, an unreadable or malformed profile, etc.).
- `configured and enabled, zero writes recorded for this project` — enabled, but nothing has
  registered yet.
- `configured and enabled, last write for this project succeeded` — enabled and working.
- `configured and enabled, last write for this project failed: <detail>` — enabled, but the most
  recent observation hit a fault (surfaced from the failure journal).

Both always exit `0` — a read-only report has nothing to reject.

## Reading `fleet handoff stale` output: candidates, not a verdict

`fleet handoff stale` lists `awaiting-launch` rows past their expiry — handoffs that were emitted
but, as far as the manifest can tell, never got launched. **Treat that list as a set of candidates
to confirm, not as a verdict.**

The reason is structural, not a hedge: the launch flip depends on the launched session actually
invoking `fleet observe session-start` (via its embedded `FLEET-HANDOFF-ID:` prompt instruction, the
`SKILL.md` first-action step, or the installed `SessionStart` hook). If none of those fire for a
session that *did* in fact launch and continue — a hook was never installed, the model skipped the
prose step, the prompt was edited beyond recognition — that session's row stays `awaiting-launch`
and will eventually show up as "stale" even though real work is happening in it. A stale report is
strong evidence a handoff was dropped; it is not proof, and the right response is to go check the
candidate, not to assume it is abandoned.

**The converse caveat: a row that has already flipped to `active` is not automatically trustworthy
either.** The list above is about rows that stayed `awaiting-launch` when they shouldn't have
(omission). The opposite failure is narrower but real: the id-matching described in "The fail-soft
/ fail-closed boundary" above means a session whose prompt happens to contain a *foreign*
`FLEET-HANDOFF-ID:` line (a re-pasted or quoted earlier prompt), or a fuzzy match on a direct
`observe launch` call, can flip the wrong row to `active` — which then reads as launched-and-fine
and drops out of the stale list entirely, even though the row it displaced is the one still
actually unlaunched. If a handoff you know was emitted is missing from `fleet handoff stale` and you
did not expect it to have launched yet, that is worth checking too, not just the rows the command
actually lists.

## Declaring ownership (`fleet owner`)

Unlike every `observe` verb, `fleet owner claim|stand-down|show` is a **deliberate act**: it fails
loudly with a stated, non-zero exit rather than the fail-soft façade's always-exits-0 contract, and
a refused or failed call leaves the manifest log byte-unchanged.

**The two events and the read rule.** A claim writes an `ownership_declared` event; a stand-down
writes an `ownership_stood_down` event. Ownership is read by *folding* every one of a project's
events, in log order (`core.project_owner.fold_owner`) — never by counting lines or assuming any
pairing between the two event types. The fold's actual rules:

- A declaration always makes its node the current owner — even displacing a prior owner with no
  stand-down in between, so a forged or legacy sequence still has exactly one answer.
- A stand-down clears the owner only when it names that owner's own declaring `event_id` as its
  `stood_down_from` — or, on a project with no `ownership_declared` event at all, the OQ-1 legacy
  owner's `session_registered` event id. Any other valid stand-down still moves the fold's anchor
  (so `fleet owner show` reflects it happened) but leaves the current owner unchanged.
- A stand-down written `written_by="claimant"` (the paired takeover stand-down a claim writes for
  the prior owner) is skipped entirely — as if it never happened, an *orphan* — unless its
  `claim_key` matches the `idempotency_key` of some real `ownership_declared` event by that exact
  `claimant`. This is what makes a torn write (an interrupted batch that persisted only the
  stand-down half of a claim) invisible to every reader, rather than a phantom vacated ownership.
  A `written_by="self"` or `written_by="on_behalf"` stand-down (the CLI's own-identity and
  `--node-id` paths, respectively — the latter honestly recording that the acting identity differs
  from the standing-down node, R1) is never subject to this orphan check; only the `stood_down_from`
  rule above governs whether either one clears the owner.

`fleet owner show` performs this fold for you and also prints the basis and attestation of the
claim that made the current owner current — read it (or call `fold_owner` directly), never derive
ownership by scanning the raw event log yourself.

**Exit codes.**
- **Exit 0** — the claim or stand-down was written (or was already a no-op, e.g. re-claiming your
  own existing ownership).
- **Exit 1** — could not write: a lock timeout after bounded retry, a validation/ownership error,
  the bounded re-evaluation budget exhausted against a changing ownership state, an
  adapter-construction/identity failure, an unusable `--sessions-json` (missing, unreadable, not
  JSON, or not a list of objects), or an I/O error reading/writing the manifest.
- **Exit 2** — a usage error: `--prior-owner-notified` and `--notified-via` were not given
  together, `--node-id` was combined with a self-identity flag, or a required self-identity flag is
  missing (`--harness claude` with no `--session-id`/`CLAUDE_CODE_SESSION_ID`, or a non-claude
  harness with no `--local-id` — see "Identity" below); these hand-checked cases are printed and
  returned the same way every other refusal is, never routed through argparse's own usage path. A
  plain argparse-level usage error (e.g. `--lock-retry-attempts` given a value below 1) still exits
  2 as well, via argparse's own path, before any of this project's own checks run.
- **Exit 3** — coordination required: an active prior owner exists with no or mismatched
  attestation. The message names the current owner's node id; re-run adding
  `--prior-owner-notified <that node id> --notified-via <how you reached it>` once you have told
  that session.
- **Exit 4** — refused: state is not what was assumed (`--node-id` given without
  `--writer-role cto`, the claimant/node is not registered in this project when no session facts
  could be supplied to register it — see "Claim registers the session if needed" below —, or a
  stand-down was attempted by a node that is neither the project's current declared owner nor its
  legacy owner).
- **Exit 5** — not applicable: fleet is disabled, the slug is invalid, or the project identity
  could not be resolved. Skip it and proceed.

**The coordination rule.** A claim over an *active* prior owner is refused (exit 3) unless the
claimant attests it notified that owner — `--prior-owner-notified`/`--notified-via` together. This
is what makes takeover a deliberate, coordinated act rather than a silent race.

**The legacy-owner coordination rule.** A project with no `ownership_declared` event yet is not
automatically unowned for coordination purposes: if it has a `relayed`/`manual` registration
written by `pm` for a node other than the claimant, that registration counts as a *legacy* prior
owner, and claiming over it needs the same coordination (or the same liveness-based exception) a
real declared owner would. Only the NEWEST such qualifying registration counts — if more than one
node was ever registered this way for the project, every earlier one is irrelevant to this rule,
never coordinated with. `fleet owner show` says so — a project with no ownership events at all
still names its legacy owner (and notes it is not a real declared owner yet) when one exists,
instead of silently reporting "no declared owner" as if the project had never been touched.

**Claim registers the session if needed.** A claimant with no prior `session_registered` event in
the project is no longer refused outright: `fleet owner claim`'s self path (not the `--node-id`
on-behalf path, which has no session facts to offer) registers the claimant in the SAME write as
the claim itself, using the identity it already resolved. This closes the gap where a session
started with the plain `observe session-start` line (no `--harness`, so it registered under a
different, process-lifetime-only identity) could never satisfy a later `owner claim` under its real
harness identity — see "Identity" just below for why that gap existed.

**Identity.** An owner verb needs an identity that is stable ACROSS separate process invocations,
not just within one — a claim and its later stand-down are always two different processes.
`--harness claude` reads `--session-id` if given, else the `CLAUDE_CODE_SESSION_ID` environment
variable; if neither is present, the verb refuses with exit 2 rather than guessing. Every other
harness (`portable`, `subagent`) requires an explicit `--local-id` — a bare process id is a new
value on every invocation and is therefore useless as a repeatable identity. The on-behalf
`--node-id` path (paired with `--writer-role cto`) names its target directly and has none of this
to resolve.

**Legacy stand-down.** A node that IS the legacy prior owner described above (the newest
`relayed`/`manual` `pm` registration, with no `ownership_declared` event yet for the project) can
stand itself down the same way a real declared owner would, even though no one has formally claimed
the project yet. That stand-down is recorded as a real `ownership_stood_down` event, so the project
now shows its ownership as vacated, and a successor's claim lands uncontested rather than needing to
coordinate with a registration that was never a real claim to begin with.

**The O-NFR-2 limit, stated plainly: the fleet records the attestation and does not and cannot
verify that the message was delivered.** `--prior-owner-notified`/`--notified-via` are taken at
face value and stored with the claim; nothing in this project confirms the named prior owner ever
actually saw the notification.

**The `unknown`-counts-as-active rule.** Liveness is read only from records supplied via
`--sessions-json` (DESIGN O.4); when no record matches a node — or no `--sessions-json` was given
at all — its liveness is `unknown`, and `unknown` counts as **active** for the coordination rule
above. This is the conservative default: it is always safer to ask for coordination when liveness
can't be confirmed than to let an unconfirmed takeover through.

**The `deleted`-reachability note.** Liveness resolution never infers `deleted` from a session's
absence in `--sessions-json` — an absent or ambiguous match always resolves to `unknown`, never
`deleted`. `deleted` (were it ever produced) would mean positive evidence the harness reports the
session gone, not merely "not found in this snapshot."

## Stated limitations

This project is deliberately honest about where it does not reach, rather than presenting a
confident-looking surface that quietly covers less than it appears to.

1. **Nested dispatches are not gated and write no row.** Neither the granularity rule's
   `SubagentStart` hook nor the role-first `PreToolUse` discipline gate has any way to see a dispatch issued
   from *inside* an already-running subagent — the parent-transcript lookup both rely on finds no
   matching candidate for it, which produces no row and no denial, silently.
2. **Dispatches through other tools are not gated at all.** A `Workflow` script, a `SendMessage`
   continuation, or an agent-team mechanism that starts a subagent without going through the
   `Agent`/`Task` tool call this project watches is invisible to both hooks — no registration, no
   role enforcement.
3. **A session the locator cannot resolve gets neither hook's coverage.** It falls back entirely to
   the portable prose floor, and if the floor's own step is skipped or the model does not act on it,
   that session writes nothing at all — silently, by design (fail-soft, not fail-loud).
4. **A message that mixes a role dispatch with a non-role dispatch of the same `subagent_type`
   yields no row for either.** The registration filter requires every dispatch tool call in the
   triggering batch to agree on the verdict for that `subagent_type`; a mixed batch is ambiguous and
   is treated as no candidate at all. Send role dispatches in their own message.
5. **Choosing the `superhuman-dispatch: non-role` line when a dispatch should really have been a
   role dispatch is possible, and the gate cannot tell the difference.** The literal marker line is
   *counted* (logged as `NON_ROLE`), never *prevented* — the gate can verify a claimed role prompt
   is unedited, but it has no way to know whether a dispatch that opted out of the role block should
   have opted in.
6. **Whether a session `fork` mints a new `session_id` is still an open question**, so idempotency
   across a `fork` is untested in either direction. Relatedly, four of the five `SessionStart`
   matchers (`resume`, `clear`, `compact`, `fork`) are exercised in this project's test suite only
   via synthetic fixtures, never through a harness-observed live event — only `startup` has ever
   been seen firing for real.
7. **A malformed `git_timeout_seconds` in a profile is silently ignored, not reported.** If the
   value given is not a positive number (a string, a negative number, `true`/`false`), it is treated
   identically to the key being absent at all — the adapters fall back to their own built-in
   timeout. This matches this module's fail-soft posture generally, but it means a typo in this one
   setting produces no warning anywhere.
8. **Every latency figure this project measured was measured on Windows, on one machine.** No
   Linux or CI figure exists for any of the hook paths' timing budgets.
9. **After `fleet hooks install`, the registered commands point at the main checkout — so the hooks
   are inert until that checkout actually carries `templates/hooks/claude-code/` on disk.** Installing
   the ceiling on a checkout that predates this project's hook templates (for example, a `main`
   branch this work has not yet merged into) registers commands that resolve to files that do not
   yet exist there; `fleet hooks status` will report exactly that as its silent-disable case above,
   but only if someone runs it.
10. **The role-first discipline gate can be deliberately bypassed; it was never designed to resist
    that.** It is a discipline aid, catching a forgotten or edited role block — not a security
    control. A caller willing to try can get past it outright (a spoofed `.git` file, a poisoned
    locator-cache entry, a directory planted inside the hook's own repository, or inherited git
    environment variables all work — see the section above and the hook's own module docstring for
    the full list), and every fault in its own machinery already lets the dispatch through by
    design. Treat a passing gate as evidence of an honest mistake avoided, never as proof against a
    determined attempt.
11. **An `owner claim`'s attestation is recorded, not verified.** `--prior-owner-notified`/
    `--notified-via` are taken at face value — the fleet has no way to confirm the named prior
    owner ever actually received the notification (O-NFR-2). Both the successor's launch-instruction
    claim step and PM's own stand-down-at-handoff step are, in addition, reachable only through
    prose a launched session or PM must act on — see "Declaring ownership" above. F5: the SAME
    unverified-self-report caveat applies to `--sessions-json`-sourced liveness (DESIGN O.4) — an
    `isArchived`/`isDeleted` flag is taken from whatever the caller supplied, not independently
    confirmed against the harness; a caller could supply a stale or fabricated snapshot and a claim
    would take it at face value the same way it takes an attestation at face value.
12. **An `owner stand-down --node-id` records ITS OWN honest `written_by`/`basis` value,
    `"on_behalf"` — distinct from `"self"` (R1).** Before this, every stand-down through the CLI,
    including the `--node-id` on-behalf path, was recorded identically as `written_by="self"`,
    misreporting a third party's action as the standing-down node's own voluntary choice.
    `fold_owner` treats `"on_behalf"` exactly like `"self"` for clearing ownership (never subject to
    the orphan check that only applies to `written_by="claimant"`); only the recorded `written_by`/
    `basis`/`reason` differ. `--node-id` requires `--reason` (exit 2 if missing) precisely because
    this is now a distinguishable, audited act.
13. **`fleet owner show` names the OQ-1 legacy owner when the project has no `ownership_declared`
    event at all**, the same as a claim's own coordination check would — read its output (or the
    `legacy_owner` field in `--json` mode), not just `owner`, before assuming "no declared owner"
    means no coordination is needed.
14. **`--harness claude`'s explicit `--session-id` always takes precedence over
    `CLAUDE_CODE_SESSION_ID`** when both are present — the environment variable is read only as a
    fallback when `--session-id` was not given (or was blank).

**Known limitation: a torn batch that persists only a claim's registration half can leave that
claimant looking like a legacy owner.** O14-a's register-if-absent writes the claimant's
`session_registered` event in the SAME locked `append_batch` call as the claim (and any on-behalf
stand-down) — but if the process is killed between that batch partially landing and completing (the
same class of interruption "Torn batches" above documents for a claim's paired stand-down), the
registration alone can persist while the `ownership_declared` half does not. If that registration
happens to be `relayed`/`manual` and written by `pm`, the claimant then resolves as the OQ-1 legacy
owner on the next read — not a real claim, but indistinguishable from one until a re-run. Re-running
the SAME claim completes it: the registration half dedupes as a no-op (its idempotency key already
exists) and only the still-missing `ownership_declared` half is written.

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
