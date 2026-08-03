# Design spec — Portable deployment profile and rung ladder

**Status:** Draft for approval
**Date:** 2026-07-24
**Applies to:** superhuman v0.6.0 → v1.0.0
**Author:** design session, 2026-07-24
**Supersedes:** the hardcoded environment taxonomy in `scripts/autonomous-precondition.sh`

---

## 1. Problem

Superhuman is coupled to one organisation's deployment ladder. The coupling is narrow but
load-bearing, and it blocks two goals at once:

1. **Publishing.** The skill cannot be shared publicly without either leaking internal environment
   names, server paths and policy, or being stripped in a way that has to be re-applied on every
   release.
2. **Evolving.** The owning organisation's ladder will change — more test environments (one per
   agent harness), more products, possibly more acceptance tiers. Today each change means editing
   shell scripts, phase recipes, role prompts and tests.

The requirement is a single artefact that captures "what my deployment ladder is and who may
approve what," consumed by a generic framework, so that:

- the framework can be published with **no fork** for the owning organisation;
- a developer with **no ladder at all** (one machine, one repo, no promotion) is unaffected and
  never sees the feature;
- a developer with a **different** ladder describes it once, in data, without touching the skill.

### 1.1 Scope of the coupling (as measured)

Grep over all tracked non-`references/` files:

| Kind | Location | Severity |
|---|---|---|
| Env taxonomy hardcoded in code | `scripts/autonomous-precondition.sh` — literal environment-name list, `*prod*` glob, `uat\|prod` marker | Blocker |
| Deployment mechanics | `scripts/update-*.sh` — ssh + absolute server paths | Must not publish |
| Policy prose | `SKILL.md` HARD-GATE §5, `conventions/autonomous.md`, `phases/3-autonomous-loop.md` Step 0, `phases/3.3-preflight-review.md` Lens 2, `phases/4-acceptance.md`, `roles/pm.md`, `roles/surrogate-user.md`, `templates/SUPERHUMAN.md.tpl`, `templates/artifacts/ROLLBACK.md.tpl` | Mechanical |
| Stack conventions | `conventions/source-cited.md` (inlined preferred-library table), `conventions/git.md`, `conventions/python.md` | Mechanical |
| Model aliases | `adaptation/dispatch.md`, `SKILL.md`, `README.md` — harness-account-specific alias names | Mechanical |
| Tests asserting org strings | `tests/test_content.py`, `test_autonomous_mode.py`, `test_update_*.py` | Mechanical |
| History / doc leakage | `CHANGELOG.md`, `MIGRATION.md`, `TROUBLESHOOTING.md` (names a PAT path) | See §12 |

### 1.2 The load-bearing observation

The ladder is consulted for exactly **three** decisions:

- (a) may an unattended (HITL 1/2) loop run here?
- (b) does landing work *into* here require approval, and from whom?
- (c) which classes of test belong at this rung?

Everything else in superhuman — the ten gates, the seven roles, drift watch, the artifact catalog,
TDD discipline — is already environment-agnostic. This spec therefore does **not** restructure the
orchestrator. It extracts three decisions into data and leaves the rest alone.

### 1.3 Precedent inside the skill

`adaptation/dispatch.md` already declares itself "the ONLY place that knows about platform-specific
tool names," and role prompts use `<dispatch:agent>` rather than `Agent` / `sessions_spawn`. That
indirection works today across two harnesses. This spec adds a **second seam of the same shape** —
one for deployment topology instead of tool names.

---

## 2. Goals and non-goals

### Goals

- G-1 Superhuman publishes as-is; the owning organisation maintains **zero patches** against it.
- G-2 A user with no ladder gets a safe, silent default and never configures anything.
- G-3 A user with any ladder describes it in one declarative file.
- G-4 All policy decisions are **deterministic code over data**; no LLM inference in the safety path.
- G-5 Every decision is **auditable** — the resolver reports which rule matched and why.
- G-6 The ladder can change without changing the skill.
- G-7 Ref-space (branch / worktree / tag) is a first-class ladder binding, not a special case.

### Non-goals

- N-1 Not a general-purpose policy engine. No Rego, no expression language, no plugins.
- N-2 Not a deployment tool. Superhuman never performs a promotion; it gates one and may invoke a
  user-supplied command.
- N-3 Not a secrets store. The profile holds policy, never credentials.
- N-4 Not multi-tenant. One profile describes one developer's or one team's machine view.

---

## 3. Core model

### 3.1 The rung

A **rung** is a named position in the developer's world with a *detector* and a *policy*. That is
the entire vocabulary. There is no environment class hierarchy.

```yaml
- name: product-test
  detect:   { path_segments: [product-test], env_marker: [test] }
  approvals:
    promote_into:   [human]
    act_unattended: [self]
```

> **Decision D-2 — no `class` / `kind` enum.**
> The first draft defined a fixed vocabulary (`authoring | integration | verification | acceptance |
> production`). It was cut in adversarial review: the framework never consults a class, only the
> three capabilities in §1.2, so an enum is an indirection that buys nothing and constrains the
> model when an unanticipated stage type appears. `kind:` survives as *optional, semantics-free
> sugar* that the onboarding wizard writes for human readability. Simplicity wins where results are
> near-identical.

### 3.2 Two axes, conjunctively matched

A rung is **not** identified by a filesystem path *or* a git ref. It is identified by a conjunction
of coordinates, any subset of which may be specified. All specified coordinates must match.

> **Decision D-13 — unify deployment-space and ref-space.**
> These were originally two alternative designs: path rungs for multi-environment users, ref rungs
> as a "surrogate" for single-environment users. They are in fact simultaneously active for
> multi-environment users — work is authored on a workstation (path) *on a branch* (ref), and
> superhuman's own autonomous loop already cuts `autonomous/<slug>/<run-id>` branches while sitting
> in a workstation checkout. Modelling one rung space with two coordinate families:
> - lets a multi-environment user distinguish rungs that share a path but differ by branch;
> - makes the single-environment case the **degenerate form** (deployment axis has one value and
>   disappears) rather than a second code path;
> - turns the loop's hardcoded "never commit to `main`" invariant into derived policy.

Worked example — same machine, different branch, different policy:

```yaml
- name: workstation-feature
  detect:   { default: true, branch: ["feature/*", "autonomous/*"] }
  approvals: { act_unattended: [self], promote_into: none }

- name: workstation-trunk
  detect:   { default: true, branch: [main] }
  approvals: { act_unattended: never, promote_into: [human] }
```

Both are the workstation in path-space. They differ in ref-space and carry different policy,
encoding "the unattended loop may run on a feature branch but never directly on `main`."

### 3.3 Detector families

| Family | Key | Matches against | Available offline |
|---|---|---|---|
| Explicit | `marker_file` | a named file in the project root | ✅ |
| Explicit | `env_marker` | `SUPERHUMAN.md` `## Environment:` value | ✅ |
| Path | `path_segments` | any single segment of the absolute cwd; globs allowed | ✅ |
| Ref | `branch` | current branch name; globs allowed | ✅ |
| Ref | `tag_channel` | `stable` (semver, no prerelease) \| `prerelease` \| glob | ✅ |
| Ref | `worktree` | `linked` \| `main` \| `any` | ✅ |
| Fallback | `default: true` | matches when nothing more specific does | ✅ |

Every detector is evaluable **offline and deterministically**. Nothing here requires a network call
or an LLM.

### 3.4 Detection precedence

> **Decision D-3 — fixed, auditable precedence; explicit beats inferred.**

Resolution order, highest authority first:

1. `SUPERHUMAN_PROFILE_STAGE` environment variable (explicit override, escape hatch)
2. `marker_file`
3. `env_marker` (`SUPERHUMAN.md` `## Environment:`)
4. `path_segments`
5. `branch` / `tag_channel` / `worktree`
6. `default: true`

Within a single tier, **most specific wins**: the rung matching the greatest number of detector keys
is chosen. Ties break by declaration order. `validate` raises an error on a genuine ambiguity
(two rungs, equal specificity, overlapping detectors) rather than picking silently.

**Rationale for putting explicit above inferred.** The current implementation blocks any path
segment containing `prod`, which also matches `products`, `reproduce`, `prod-team`. That is
acceptable when one person is affected and unacceptable in a published tool. Under this precedence
the false positive still blocks (fail-closed preserved) but is now (a) traceable via `explain` and
(b) overridable by declaring a marker. A user who deliberately marks a production directory as
`dev` has made their own declaration; the framework cannot do better than deterministic and
auditable.

### 3.5 Flat rungs plus labels, not a matrix

> **Decision D-5.**
> A ladder that grows along several dimensions (product × harness × stage) invites matrix modelling.
> Rejected: the framework never queries the matrix — it asks only "which rung am I standing on?"
> A flat list with free-form `labels:` represents the matrix without matrix machinery.

```yaml
- name: product-test-openclaw
  labels: { harness: openclaw, tier: test }
  detect: { path_segments: [product-test-openclaw] }
  approvals: { promote_into: [human], act_unattended: [self] }

- name: product-test-cli
  labels: { harness: cli, tier: test }
  detect: { path_segments: [product-test-cli] }
  approvals: { promote_into: [human], act_unattended: [self] }
```

Adding an environment is one rung. Adding a product is a label, or one rung if it needs distinct
policy. Labels are carried into the resolver output and into `SUPERHUMAN.md`, so they are available
for reporting, but the framework attaches no semantics to them.

A `pipelines:` key (named branching ladders) was considered and **deferred** — the flat + labels
form covers every case raised, and branching adds a resolution problem for no demonstrated benefit.

---

## 4. Approvals

### 4.1 One concept, not two

The first draft carried two fields: `autonomy: allow|deny` and `promotion_approval: auto|human`.

> **Decision D-8 — collapse into a single `approvals` map keyed by action class.**
> Both fields answer the same question — *who may authorise an action of class X at this rung?* —
> differing only in X. Two differently-shaped fields for one concept is redundancy. They remain
> weakly independent (a shared sandbox may accept pushes from anyone yet forbid an unattended loop),
> so both keys are retained; only the shape is unified.

**Action classes (v1, closed set):**

| Class | Question |
|---|---|
| `promote_into` | may work *land* at this rung, and who signs off? |
| `act_unattended` | may an unattended (HITL 1/2) loop *operate* while standing here? |

The key namespace is reserved for future classes; `validate` rejects unknown keys loudly rather
than ignoring them.

### 4.2 Approver vocabulary

> **Decision D-9.**

| Token | Meaning |
|---|---|
| `none` | no approval required |
| `never` | no approver may authorise — the hard block |
| `null` / key omitted | **unresolved** — see §5 |
| `human` | any human, explicit in-context yes |
| `human:<name>` | a named person (`human:alice`) |
| `agent:<name>` | a named subagent (`agent:security-reviewer`, `agent:surrogate-user`) |
| `self` | the PM orchestrator thread itself |

**List semantics.** A bare list is **any-of** — the cheapest sufficient approver satisfies it. For
co-signing, use `all_of`:

```yaml
approvals:
  promote_into:   { all_of: [agent:security-reviewer, human] }   # both must sign
  act_unattended: [self, agent:surrogate-user]                   # either suffices
```

`all_of` is implemented in v1. It is roughly ten lines in the resolver and it is the difference
between a demonstration and a credible policy engine; "security review *and* a human before
production" is a mainstream requirement.

### 4.3 HITL levels dissolve into approvals

> **Decision D-25 — SUPERSEDED at implementation time (v0.8.0). See D-25a below.**
> ~~HITL-H/M/L stop being an independent mechanism. Level 2 *is* "`self` appears in the
> approver list for these gate classes."~~ This contradicted §10, which defines action classes as a
> **closed set** of `promote_into` and `act_unattended`.

> **Decision D-25a — HITL levels and ladder approvals are different scopes; keep them separate.**
>
> The two governance mechanisms answer different questions, at different scopes, with different
> lifetimes:
>
> | | Ladder approvals | HITL level |
> |---|---|---|
> | Question | may work run/land at this **location**? | who answers this project's **gates**? |
> | Scope | machine — one profile per developer | project — one setting per `SUPERHUMAN.md` |
> | Lifetime | changes when the topology changes | fixed at G1 for one project's lifetime |
> | Home | `profile.yaml` | `SUPERHUMAN.md` |
>
> Collapsing them would force a single oversight level across every project on a machine, which is
> plainly wrong: a throwaway spike and a production-bound refactor in the same checkout want
> different HITL, on the same ladder.
>
> **They interact in exactly one direction, and it is already enforced.** The resolved rung's
> `act_unattended` policy is a *ceiling* on the project's HITL level: a rung of `never` forbids
> HITL-M and HITL-L no matter what the project asked for at G1. A project can always be more
> cautious than its location requires; it can never be less. That is the whole coupling — no
> further unification is warranted.
>
> `HITL-level:` therefore stays a first-class field in `SUPERHUMAN.md` rather than becoming sugar
> over an approvals map. A *project-level* approvals map (e.g. "`agent:security-reviewer` may answer
> G5 for this project") is a plausible future extension, deferred: no demonstrated need, and it
> would need its own file, not the machine profile.

> **Decision D-26 — rename the levels `0/1/2` → `H/M/L`.**
> The numeric scale is backwards: level `0` means *High* human involvement and level `2` means
> *Low*, so a larger number denotes less oversight. Since D-25 keeps the label as human-facing
> sugar, the confusion persists in the updated design and is worth removing.
>
> | Was | Becomes | Meaning |
> |---|---|---|
> | `0` | `HITL-H` | High — every gate is human |
> | `1` | `HITL-M` | Medium — a surrogate answers routine implementation gates |
> | `2` | `HITL-L` | Low — the orchestrator resolves nearly everything itself |
>
> **Lands in phase 0.8.0, not as separate work.** That phase already rewrites every file carrying
> the label (`SKILL.md`, `phases/0-kickoff.md`, `phases/3-autonomous-loop.md`,
> `roles/surrogate-user.md`, `conventions/autonomous.md`, `templates/SUPERHUMAN.md.tpl`) for the
> approvals expansion; a separate pass would duplicate the edit and conflict.
>
> **Back-compatibility:** `--level 1|2` and `HITL-level: 0|1|2` continue to parse, mapping to
> `H/M/L` with a deprecation note, so projects started under ≤0.7.0 resume without edits.

### 4.4 Safety warning, not a safety block

> **Decision D-10.**
> The schema *permits* `agent:*` as the sole approver for `promote_into` on a production rung.
> Publishing a framework in which "let an LLM approve your production deploy" is a one-line config
> is irresponsible. It is equally wrong for a tool to refuse a configuration its owner deliberately
> chose.
>
> Resolution: the resolver and `doctor` emit a **loud warning** — never silent, never a block —
> whenever a rung with no downstream rung has a `promote_into` policy containing no `human`.
> Warning text names the rung and the exact key. Exit code is unaffected.

---

## 5. Unresolved policy: `null`

> **Decision D-11 — `null` means "not yet decided", resolved on first encounter, failing closed.**

A key that is `null` or omitted (and not supplied by `defaults:`) is **unresolved**. This makes the
profile *grow by use* rather than demanding a complete document up front — a materially better
onboarding path for both audiences.

**Interactive behaviour.** On first encounter the resolver returns `UNRESOLVED` with the question to
ask. Superhuman surfaces it as a Type A gate:

```
GATE — approval policy undeclared

About to promote into rung `product-test`.
This rung has no declared policy for `promote_into`.

Who may approve landing work at this rung?
  1. none                     — no approval needed
  2. self                     — the orchestrator may decide
  3. agent:surrogate-user     — a subagent may decide
  4. human                    — always ask me            (recommended)

Your answer is written back to ~/.superhuman/profile.yaml.
```

The answer is written back with a timestamp and the resolving context.

**Three hard constraints:**

1. **Unresolved fails closed.** Mid-run, with no human available, `UNRESOLVED` **halts and
   escalates (G10)**. It is never treated as `none`. A 3 a.m. unattended run must stop, not guess.
2. **Level 2 may not precedent-mine it away.** Everywhere else at HITL-L the PM resolves gates
   by precedent-mining. An unresolved approval cell is an explicit human decision *by construction*
   and is exempt. This is a deliberate carve-out; state it in `roles/surrogate-user.md`.
3. **`validate` and `doctor` enumerate every unresolved cell**, so a user can pre-fill deliberately
   rather than discover one at a bad moment.

**Interaction with drift detection.**

> **Decision D-12.**
> `SUPERHUMAN.md` records a profile hash at G1 so a changed ladder surfaces as drift (§8.3). A
> naive whole-file hash would make every first-encounter fill trip a spurious G6 on every active
> project. The hash therefore covers **declared cells only**; a `null → value` transition is a
> non-drift *fill* event, recorded as a Decisions-log append with no G6.

---

## 6. File location and discovery

### 6.1 Location

```
~/.superhuman/profile.yaml          the ladder
~/.superhuman/conventions/*.md      convention overlays referenced by the profile
```

Deliberately **not** under `~/.claude/` — superhuman targets more than one harness, and this is
machine-level configuration, sibling to `~/.gitconfig` and `~/.ssh/`, not a skill asset.

> **Decision D-15 — the profile lives at the standard user path, not inside any skill.**
> Housing an organisation's instance inside its private governance skill was considered and
> rejected for two reasons:
> 1. **Fail-open in the worst place.** On any machine where that skill is not checked out — a
>    server, a fresh laptop, a container — no profile is found, the permissive default applies, and
>    the production block silently disappears.
> 2. **Dogfooding.** If the maintainer's layout differs from every published user's layout, the
>    maintainer's path is the tested one. For a project whose value proposition is "no fork," that
>    is the wrong asymmetry to build in.
>
> The de-duplication benefit that motivated the original suggestion is preserved by §11 instead.

### 6.2 Discovery is a lookup, not a registration

> **Decision D-16 — search path, no handshake.**
> No skill registers a profile with another. No manifest, no install hook. Every consumer runs the
> same lookup at call time against a fixed well-known path, exactly as `git` finds `~/.gitconfig`
> and `dbt` finds `~/.dbt/profiles.yml`.

```python
def find_profile(cwd: Path) -> Path | None:
    if p := os.environ.get("SUPERHUMAN_PROFILE"):          # 1. explicit override
        return Path(p)
    home = Path.home()
    ceiling = git_toplevel(cwd)                            # bound the walk
    for d in [cwd, *cwd.parents]:                          # 2. project-local, walking up
        if d == home:
            break
        if (c := d / ".superhuman" / "profile.yaml").is_file():
            return c
        if ceiling is not None and d == ceiling:
            break
    if (c := home / ".superhuman" / "profile.yaml").is_file():          # 3. user-level
        return c
    return None                                            # 4. zero-config
```

> **Implementation finding (v0.7.0) — the project-local walk must be bounded.**
> As first drafted the walk was unbounded, which meant it escaped the project and matched the
> **user-level** profile as though it were project-local: `~` is an ancestor of nearly every
> checkout, so `~/.superhuman/profile.yaml` satisfies the tier-2 pattern for any project under the
> home directory. The file found is the same either way, so no verdict changed — but tiers 2 and 3
> collapsed into one, `explain` misreported which rule fired, and no caller could construct a
> genuine "no profile anywhere" state (which broke the fail-closed tests for
> `SUPERHUMAN_REQUIRE_PROFILE`). The walk now stops at the enclosing git repository root, or at the
> home directory when the location is not a repository. A project-local profile must live inside
> the project.

`Path.home()` resolves correctly on Windows — unlike `~` inside a harness `settings.json`, this
expansion is performed by Python.

Consequences, which answer the install-order question directly:

| Scenario | Result |
|---|---|
| Published user, superhuman only, no profile written | `None` → built-in ladder (§7). The words *environment*, *ladder*, *promotion* never appear. |
| Governance skill installed **first**, superhuman **second** | Both find the same file. Order is irrelevant — it is a filesystem check at call time, not a handshake at install time. |
| superhuman installed first, governance skill never installed | superhuman is fully functional. |
| Either skill uninstalled | The file is untouched; the other consumer keeps working. |

The file outlives both skills because neither owns it.

### 6.3 `SUPERHUMAN_REQUIRE_PROFILE`

> **Decision D-17.**
> §6.2 scenario "profile absent" falls through to the permissive built-in ladder. On a machine that
> *does* have protected environments, that is fail-open in the worst possible place.

```
SUPERHUMAN_REQUIRE_PROFILE=1
```

When set, a missing profile is a hard error (exit 2) with the message *"no profile found and
SUPERHUMAN_REQUIRE_PROFILE is set"* rather than a fall-through. Published users never set it and
never see it. An organisation with real environments sets it once per machine, closing the hole.
`doctor` always reports which mode is active.

The same effect is available in-file as `require_profile: true`, for the project-local case.

---

## 7. Zero configuration

> **Decision D-18 — zero-config means a *built-in default ladder*, not the absence of policy.**
> "No profile → everything allowed" was the first formulation and was too crude: it silently
> permits unattended operation against `main` or a checked-out release tag.

Two things are separated: whether a rung **exists** (discovery) and what its **default policy** is.

### 7.1 The built-in ladder

Active whenever `find_profile` returns `None`:

```yaml
# built-in; not written to disk. Order is load-bearing — see below.
- name: stable
  detect:    { tag_channel: stable }           # detached HEAD on a non-prerelease semver tag
  approvals: { act_unattended: never, promote_into: [human] }

- name: trunk
  detect:    { branch: [main, master, trunk] }
  approvals: { act_unattended: null, promote_into: null }      # unresolved → ask on first encounter

- name: work
  detect:    { branch: ["*"] }
  approvals: { act_unattended: [self], promote_into: none }

- name: local                                  # catch-all for non-git locations
  detect:    { default: true }
  approvals: { act_unattended: [self], promote_into: none }
```

Stable defaults to human and never to unattended. Trunk defaults to `null` rather than a guess,
using §5 first-encounter resolution.

> **Implementation finding (v0.7.0) — two corrections to the ladder as first drafted.**
>
> 1. **A `local` catch-all is required.** The original three rungs were all ref-bound, so a plain
>    directory or a repository with no commits matched *nothing*. Because `stable` declares a hard
>    block, "nothing matched" then denied under §7's fail-closed rule — turning zero-config from
>    permissive into unusable outside a git repo. `local` restores the intended default.
> 2. **`trunk` must be declared before `work`.** On `main`, both match exactly one `branch` key, so
>    they tie on authority *and* specificity and the tie falls to declaration order. With `work`
>    first, `main` resolved to `work` and the trunk policy never applied.
>
> Generalised rule, which the ladder authoring guidance should state: **declare narrower rungs
> before broader ones.** The same rule governs deny-before-allow in a path-bound ladder.

### 7.2 Ref-space only

> **Deliberate omission.** The built-in ladder performs **no path-space detection.** Path heuristics
> are where the false-positive problem lives (`~/code/my-products/` tripping a `*prod*` glob on a
> stranger's machine). Path rungs exist only when a user declares them or `init` proposes them.
> This makes the published default both safe and quiet.

### 7.3 When a zero-config user is actually asked

| Situation | Prompted? |
|---|---|
| HITL-H (the default) | **Never.** The approvals map is inert — every gate is human already. Behaviour is identical to today. |
| HITL-M/L, on a work branch | No. The loop already runs on `autonomous/<slug>/<run-id>`, which lands in the `work` rung. |
| HITL-M/L, merging the run branch back to `main` | **Once.** Trunk's `promote_into` is unresolved; one question at the moment that genuinely deserves a human. |
| Detached HEAD at a release tag, level 1/2 | Blocked (`never`), with the rung and rule named. |

> **Decision D-21 — the profile materialises on first decision.**
> The built-in ladder is *not* written to disk at install time; writing files unasked is rude and
> would falsify the zero-config property. `~/.superhuman/profile.yaml` is created the first time a
> user answers a first-encounter question, seeded with the built-in ladder plus their answer.

### 7.4 Honest gap

Production is **not detectable in ref-space.** A stable tag is a proxy, not the real thing. `doctor`
must say so rather than let the built-in ladder imply coverage it does not have:

```
No deployment rungs declared — only branch/tag rungs are active.
If you deploy to a protected environment, run `superhuman init` to declare it.
```

---

## 8. The resolver

### 8.1 Deterministic code over data

> **Decision D-6.**
> All logic lives in one Python module; the profile is pure data; the orchestrator reads only a JSON
> verdict. This is *stronger* than today: currently the `SKILL.md` HARD-GATE prose and the shell
> script are belt-and-suspenders. Afterwards the prose can simply cite the resolver.

> **Decision — Python, not bash.** The current gate is bash. Python is chosen because: the primary
> development machine is Windows (the repo already carries `.cmd` shims for this reason); a
> published repo cannot assume bash; JSON output is native; and the logic becomes unit-testable
> without a shell.

### 8.2 CLI surface

`scripts/superhuman_profile.py`, exposed as `superhuman profile <cmd>`:

| Command | Purpose |
|---|---|
| `resolve` | print the resolved rung as JSON |
| `explain [<path>]` | human-readable trace of the precedence chain and why a rung won |
| `check --action <class> [--level N]` | exit-code verdict for one action class |
| `validate` | schema check; lists unresolved cells; errors on ambiguity |
| `init` | onboarding wizard (§9) |
| `doctor` | profile + harness + git + resolved rung + warnings, one screen |

`resolve` output:

```json
{
  "stage": "product-test",
  "labels": { "tier": "test" },
  "matched_by": ["path_segments", "branch"],
  "specificity": 2,
  "profile": "/home/dev/.superhuman/profile.yaml",
  "profile_hash": "sha256:9f2a…",
  "approvals": {
    "promote_into":   { "any_of": ["human"] },
    "act_unattended": { "any_of": ["self"] }
  },
  "warnings": []
}
```

`explain` output:

```
cwd     /srv/product-test/skills/widget
branch  feature/parser-rewrite
profile /home/dev/.superhuman/profile.yaml  (sha256:9f2a…)

  tier 1  SUPERHUMAN_PROFILE_STAGE   unset
  tier 2  marker_file                no match
  tier 3  env_marker                 no match (no SUPERHUMAN.md ## Environment:)
  tier 4  path_segments              MATCH  'product-test'  → rung `product-test`
  tier 5  branch                     MATCH  'feature/*'     → rung `product-test`
  tier 6  default                    (not reached)

resolved  product-test   (specificity 2, no ties)
  promote_into    any_of [human]
  act_unattended  any_of [self]
```

### 8.3 Exit codes

| Code | Meaning |
|---|---|
| 0 | allowed |
| 2 | usage or validation error, or missing profile with `require_profile` |
| 3 | denied by policy |
| 4 | unresolved policy — halt and escalate |

Codes 0/2/3 preserve the existing `autonomous-precondition.sh` contract. Code 4 is new and safe:
`phases/3-autonomous-loop.md` Step 0 already aborts on *any* non-zero exit.

`autonomous-precondition.sh` becomes a thin shim over `check --action act_unattended`, so no phase
recipe changes in the extraction step.

> **Implementation finding (v0.7.0) — the shim is not purely a ladder query.**
> The v0.6.0 gate carried a third guard that is *not* ladder policy: at the lowest HITL level, a
> project whose `SUPERHUMAN.md` declares `Modifies-existing-code: yes` must have a `ROLLBACK.md`.
> That is a **project-state precondition**, orthogonal to where the project sits — a rung cannot
> express it, and modelling it as one would be a category error.
>
> It therefore stays a distinct check inside `check`, evaluated only at HITL-L and only after the
> ladder verdict allows. It is *not* organisation-specific ("declare how to revert before running
> unattended against existing code" is generic), so it ships in the published resolver rather than
> moving to the profile. Documented here because §8.2's command table implies `check` asks exactly
> one question, and it asks two.

### 8.4 Compatibility guarantee

> **Decision D-24 — golden-verdict test is mandatory and blocking.**
> The existing hard block is battle-tested; a refactor can silently loosen it. A fixture of ~20
> representative absolute paths must produce byte-identical allow/deny verdicts under the new
> resolver and the current script, with the maintainer's real ladder as input. Extraction does not
> merge until this passes. `autonomy: deny` → `act_unattended: never` must be provably lossless.

---

## 9. Onboarding

### 9.1 Two discovery budgets

> **Decision D-19.**

| | Runtime resolver | `init` / `doctor` |
|---|---|---|
| Constraint | offline, deterministic, fast — runs at every gate | may probe, may be slow, runs once |
| Discovers | branch, tag channel, worktree linkage, path segments, markers | the above **plus** hosted-CI environments and their required reviewers, branch-protection rules, `environment:` keys in CI workflow files, `docker-compose-*.yml` env suffixes, `.env.<name>` files, terraform workspaces |
| Produces | a verdict | a *proposed* ladder for approval or editing |

Branch protection on `main` is a strong signal that `promote_into: [human]` is right, but
discovering it costs a network call — so it belongs in onboarding, never in the hot path.

### 9.2 Presets

`init` offers four starting points, then lets the user edit:

| Preset | Shape |
|---|---|
| `solo` | one implicit rung; nothing written; equivalent to zero-config |
| `solo-git` | ref-space rungs: `work` / `trunk` / `stable` |
| `classic-3tier` | `dev` → `product-test` → `production`, path + ref |
| `custom` | wizard from discovery output |

### 9.3 Wizard flow

At most five questions:

1. How many deployment stages do you promote through? `1 / 2–3 / more`
2. What are they called? (pre-filled from discovery)
3. Which require a human to approve work landing there? (pre-filled from branch protection /
   required reviewers where discoverable)
4. Where may an unattended loop run?
5. Which convention packs apply?

Then: write, validate, and print the resolved ladder as a table.

### 9.4 Imported-ladder example

```
$ superhuman profile init

Scanning…
  git                3 branches, protection on `main` (1 required review)
  CI workflows       environments referenced: dev, product-test, production
  hosted env config  production → required reviewers: 2

Proposed ladder:

  rung          detect                          promote_into     act_unattended
  ────────────────────────────────────────────────────────────────────────────
  work          branch feature/*, autonomous/*  none             self
  dev           env-key dev                     none             self
  product-test  env-key product-test            human            self
  production    env-key production              all_of[human×2]  never

Accept, edit, or start over? [A/e/s]
```

---

## 10. Full schema (v1)

`validate` **rejects unknown keys** at every level rather than ignoring them.

```yaml
version: 1                          # required, integer, currently 1
citation: "<string>"                # optional; quoted verbatim in block messages
require_profile: false              # optional; in-file equivalent of the env var

defaults:                           # optional; merged into rungs that omit a key
  approvals:
    promote_into: null
    act_unattended: null

ladder:                             # optional; omitted → built-in ladder (§7.1)
  - name: <string>                  # required, unique across the ladder
    kind: <string>                  # optional, semantics-free sugar
    labels: { <key>: <value> }      # optional, free-form; carried into output
    detect:                         # required
      default:       true|false
      marker_file:   <filename>
      env_marker:    [<value>, …]
      path_segments: [<glob>, …]
      branch:        [<glob>, …]
      tag_channel:   stable | prerelease | <glob>
      worktree:      linked | main | any
    approvals:
      promote_into:   <approval-spec>
      act_unattended: <approval-spec>
    tests: [<label>, …]             # optional, inert by default (§10.2)
    promote:                        # optional
      command: "<shell command>"
      manual:  true

conventions:                        # optional; names ship with the skill, paths are overlays
  - python
  - testing
  - git
  - "~/.superhuman/conventions/preferred-libraries.md"

models:                             # optional; tier → model/alias for this account
  most_capable: <id-or-alias>
  standard:     <id-or-alias>
  cheap:        <id-or-alias>
```

**`<approval-spec>` grammar:**

```
none | never | null
[<approver>, …]                  # any-of
{ any_of: [<approver>, …] }
{ all_of: [<approver>, …] }

<approver> ::= human | human:<name> | agent:<name> | self
```

### 10.1 Model tiers move into the profile

> **Decision.** `adaptation/dispatch.md` currently carries both the harness symbol mapping *and* an
> account-specific tier→alias table. These are different concerns: the symbol mapping is a property
> of the **harness**, the model mapping is a property of the **account/plan**. `dispatch.md` keeps
> symbols; the profile takes `models:`. This removes the last account-specific alias names from
> `SKILL.md` and `README.md`.

### 10.2 `tests:` is optional and inert

The per-rung test-class list (which test types belong at which stage) is the least generalisable
part of the model — most projects simply run their whole suite everywhere. It is included because it
is cheap to express and lets an organisation encode a real test ladder, but the framework attaches
**no default behaviour** to it. Published users omit it and notice nothing.

---

## 11. Relationship to a separate org-governance skill

> **Decision D-7 — do not merge repositories; share the profile.**

Where an organisation also maintains a runtime governance skill (dev-flow rules, promotion approval,
commit guards), that skill and superhuman have **different kinds of gate**:

| | superhuman G0–G10 | runtime governance skill |
|---|---|---|
| Fires on | phase boundaries inside an orchestrated project | *actions*, whether or not a project is running |
| Examples | design approval, acceptance sign-off | promotion approval, sensitive-data commit guard, post-push CI verification |
| Enforcement | prose + orchestrator discipline | hooks for hard blocks; advisory prose elsewhere |

They are orthogonal in *when* they fire but overlap in *what they protect* — both encode the
environment taxonomy. Today that means the taxonomy is written twice, in two languages, and the two
skills additionally ship **duplicate `conventions/git.md` and `conventions/testing.md`**.

Merging is rejected: the lifecycles differ (governance is always-on across all work, most of which
never touches superhuman), and merging forces the published repo either to carry organisation-
specific rules or to genericise them — and org rules are not generalisable; they *are* the org's
policy.

**Resolution.** The profile becomes the shared artefact:

- superhuman ships the **schema**, the **resolver**, and the **presets** — all public.
- The governance skill's rules document stops enumerating environments inline and instead names
  `~/.superhuman/profile.yaml` as authoritative, with a consistency test asserting its prose and
  the profile agree.
- The governance skill reads the YAML directly (no code dependency) and *optionally* shells out to
  `superhuman profile check` for a deterministic verdict when superhuman is installed.
- The duplicated convention files collapse to one copy, referenced from the profile's
  `conventions:` list.

Neither repository depends on the other's code. The dependency, such as it is, is a **document**
(the schema) and a **file path** (the search path).

---

## 12. Publication

> **Decision D-23 — publish from a fresh repository, not a scrubbed history.**

Sanitising the working tree does not sanitise `git log`. The existing history contains absolute
server paths, container names, deployment topology, and at least one credential *location*.

Recommendation: **invert the repositories.** Create the public repo with a squashed initial commit
from the sanitised tree, make that the canonical development repo going forward, and archive the
current private one. This is less ongoing friction than maintaining a private→public scrub pipeline
indefinitely, and it removes the possibility of a leak by `git push`.

Pre-publication checklist:

- [ ] `CHANGELOG.md` / `MIGRATION.md` / `TROUBLESHOOTING.md` rewritten without internal names or paths
- [ ] `scripts/update-*.sh` removed; `examples/` gains one generic promotion script
- [ ] `tests/` assertions on org strings replaced with profile-driven fixtures
- [ ] `NOTICE.md` attribution reviewed for the vendored upstream skills
- [ ] `LICENSE` present and correct
- [ ] `README.md` leads with the **orchestrator**; the ladder is a section, not the headline
- [ ] a `profiles/presets/` directory with `solo`, `solo-git`, `classic-3tier`
- [ ] no absolute paths from any real machine anywhere in the tree

Also flagged: the repo vendors fourteen upstream skills under `references/`. That is licensed and
attributed, but it doubles repo size for users who may already have them installed. Worth reviewing
before v1.0; not a blocker.

---

## 13. Worked examples

### 13.1 Single developer, no ladder (zero-config)

No file. No configuration. HITL-H. The user never encounters the concepts of environment,
rung, or promotion. Behaviour is identical to superhuman v0.6.0 minus the org-specific prose.

### 13.2 Single developer, ref-bound rungs

```yaml
version: 1

ladder:
  - name: work
    detect:    { branch: ["feature/*", "fix/*", "autonomous/*"] }
    approvals: { act_unattended: [self], promote_into: none }

  - name: trunk
    detect:    { branch: [main] }
    approvals: { act_unattended: never, promote_into: [human] }

  - name: release
    detect:    { tag_channel: stable }
    approvals: { act_unattended: never, promote_into: [human] }
```

"Promotion" for this user means merging to `main` or cutting a tag — gated by the same machinery
that gates a multi-environment user's deployment, with no special case in the framework.

### 13.3 Classic three-tier

```yaml
version: 1
citation: "Release policy §4"

ladder:
  - name: workstation
    detect:    { default: true, branch: ["feature/*", "autonomous/*"] }
    approvals: { act_unattended: [self], promote_into: none }
    tests:     [unit]

  - name: workstation-trunk
    detect:    { default: true, branch: [main] }
    approvals: { act_unattended: never, promote_into: [human] }

  - name: dev
    kind:      integration
    detect:    { path_segments: [dev], env_marker: [dev, lab] }
    approvals: { act_unattended: [self], promote_into: none }
    tests:     [unit, e2e-portable]
    promote:   { command: "./deploy/to-dev.sh" }

  - name: product-test
    kind:      verification
    detect:    { path_segments: [product-test, qa], env_marker: [test] }
    approvals: { act_unattended: [self], promote_into: [human] }
    tests:     [unit, e2e-portable, e2e-integration]

  - name: uat
    kind:      acceptance
    detect:    { path_segments: [uat, user-acceptance], env_marker: [uat] }
    approvals: { act_unattended: never, promote_into: [human] }
    tests:     [acceptance]

  - name: production
    kind:      production
    detect:    { path_segments: ["prod", "production"], env_marker: [prod, production] }
    approvals:
      act_unattended: never
      promote_into:   { all_of: [agent:security-reviewer, human] }
    promote:   { manual: true }

conventions: [python, testing, git]
models: { most_capable: opus, standard: sonnet, cheap: haiku }
```

### 13.4 Multi-product, multi-harness

Two products and two agent harnesses, without a matrix:

```yaml
version: 1

ladder:
  - name: workstation
    detect:    { default: true, branch: ["feature/*", "autonomous/*"] }
    approvals: { act_unattended: [self], promote_into: none }

  - name: dev
    labels:    { tier: integration }
    detect:    { path_segments: [dev], env_marker: [dev] }
    approvals: { act_unattended: [self], promote_into: none }

  - name: product-test-harness-a
    labels:    { tier: verification, harness: harness-a }
    detect:    { path_segments: [product-test-a] }
    approvals: { act_unattended: [self], promote_into: [human] }
    tests:     [unit, e2e-integration]

  - name: product-test-harness-b
    labels:    { tier: verification, harness: harness-b }
    detect:    { path_segments: [product-test-b] }
    approvals: { act_unattended: [self], promote_into: [human] }
    tests:     [unit, e2e-integration]

  - name: uat-billing
    labels:    { tier: acceptance, product: billing }
    detect:    { path_segments: [uat-billing], env_marker: [uat] }
    approvals: { act_unattended: never, promote_into: [human] }

  - name: uat-analytics
    labels:    { tier: acceptance, product: analytics }
    detect:    { path_segments: [uat-analytics], env_marker: [uat] }
    approvals: { act_unattended: never, promote_into: [human] }

  - name: production
    labels:    { tier: production }
    detect:    { path_segments: ["*prod*"], env_marker: [prod, production] }
    approvals: { act_unattended: never, promote_into: { all_of: [human, human] } }
```

Adding a third harness is one rung. Adding a third product is one rung. No skill change, no schema
change.

### 13.5 A denial, end to end

```
$ cd /srv/uat-billing/skills/widget
$ superhuman profile check --action act_unattended

DENIED — rung `uat-billing`, act_unattended: never

  matched by  path_segments 'uat-billing'  (tier 4)
              env_marker    'uat'          (tier 3, higher authority)
  policy      Release policy §4
  profile     /home/dev/.superhuman/profile.yaml (sha256:9f2a…)

Unattended operation is forbidden at this rung. Run at HITL-H,
or move the work to a rung where act_unattended is permitted.

$ echo $?
3
```

---

## 14. Phasing

Four steps, each independently shippable, each leaving an existing installation working. The
governance-skill step runs from a **separate session with that repository as the working
directory**.

| Phase | Repo | Scope | Risk |
|---|---|---|---|
| **0.7.0** | superhuman | Schema + Python resolver + shim. Existing ladder moves to `~/.superhuman/profile.yaml` (a new private repo). Golden-verdict test. **No behaviour change, no prose change.** | Low — provably identical |
| **0.8.0** | superhuman | Strip org strings from `SKILL.md` / phases / roles / conventions. Approvals map. Convention overlays. `models:` moves out of `dispatch.md`. HITL levels expand to approvals. | Medium — broad but mechanical |
| **0.8.x** | **governance skill** *(new session, that cwd)* | Rules document points at the profile instead of enumerating environments; de-duplicate the two shared convention files; consistency test | Low |
| **0.9.0** | superhuman | `init` wizard, presets, `explain`, `doctor`, ref-bound rungs, hosted-CI environment importer, `SUPERHUMAN_REQUIRE_PROFILE` | Low — additive |
| **1.0.0** | superhuman | Fresh public repository, docs, examples, licence audit (§12) | The history decision |

Phase 0.7.0 creates the private profile repository, since that is the step that first needs
somewhere to put a real ladder.

---

## 15. Risks

| # | Risk | Mitigation |
|---|---|---|
| R-1 | **Zero-config is not genuinely zero.** If a published user must write YAML before superhuman runs, adoption fails. | §7 built-in ladder; explicit test asserting a no-profile level-0 session asks zero environment questions and behaves identically to v0.6.0. This is the single most important acceptance criterion. |
| R-2 | **Safety regression during extraction.** | §8.4 golden-verdict test, blocking. |
| R-3 | **Two sources of truth.** `SUPERHUMAN.md` already carries `## Environment:`. | `SUPERHUMAN.md` records the *resolved snapshot* (rung + matching rule + profile hash) at G1 and is an audit record, never an authority. The precondition re-resolves each run and raises G6 on mismatch. §5 constraint 3 prevents fill-events from tripping it. |
| R-4 | **Scope creep into a config framework.** | Schema frozen at §10 for v1. `validate` rejects unknown keys. `extends:`, `pipelines:`, and expression-language detectors are explicitly deferred. |
| R-5 | **Publication leaks history.** | §12 fresh-repo decision. |
| R-6 | **Detection false positives** (`my-products` blocked by a `*prod*` glob). | §3.4 precedence — explicit beats inferred; `explain` makes every block traceable; published default does no path detection at all (§7.2). |
| R-7 | **`agent:*` approving production.** | §4.4 loud warning on any terminal rung lacking `human`, surfaced by both `resolve` and `doctor`. |
| R-8 | **Profile absent on a machine that needs one.** | §6.3 `SUPERHUMAN_REQUIRE_PROFILE=1`. |
| R-9 | **Vendored upstream skills bloat the published repo.** | Reviewed at 1.0.0; optional-vendoring deferred. |

---

## 16. Deferred

- `extends:` — profile composition (base ladder + per-machine override, in the style of shareable
  lint configs). A natural fit and likely eventually wanted for workstation-vs-server differences,
  but it adds a resolution-order problem on top of two-axis matching. Add when real duplication
  appears.
- `pipelines:` — named branching ladders. Flat + labels covers every case raised.
- Expression-language detectors. Fixed detector families only.
- Optional vendoring of the upstream `references/` tree.
- Machine-readable promotion *execution* (superhuman running a deploy). v1 gates promotions and may
  invoke a user-supplied command; it never implements one.

---

## 17. Decision log

| ID | Decision | Section |
|---|---|---|
| D-1 | Profile is a second adaptation seam, mirroring `adaptation/dispatch.md` | §1.3 |
| D-2 | No environment-class enum; capability keys only. `kind:` is inert sugar | §3.1 |
| D-3 | Fixed, auditable detection precedence; explicit beats inferred | §3.4 |
| D-4 | Most-specific-wins; ties by declaration order; `validate` errors on ambiguity | §3.4 |
| D-5 | Flat rungs plus labels, never a matrix | §3.5 |
| D-6 | Deterministic Python resolver; prose consumes only a verdict | §8.1 |
| D-7 | Do not merge with a governance skill; share the profile instead | §11 |
| D-8 | Collapse `autonomy` + `promotion_approval` into one `approvals` map | §4.1 |
| D-9 | Approver vocabulary; bare list = any-of; `all_of` implemented in v1 | §4.2 |
| D-10 | Warn — never block — on a terminal rung with no `human` approver | §4.4 |
| D-11 | `null` = unresolved; fail closed; resolve on first encounter | §5 |
| D-12 | Profile hash covers declared cells only; fills are not drift | §5 |
| D-13 | One rung space, two coordinate families (path × ref), conjunctively matched | §3.2 |
| D-15 | Profile lives at `~/.superhuman/`, owned by no skill | §6.1 |
| D-16 | Discovery by search path; no registration; install order irrelevant | §6.2 |
| D-17 | `SUPERHUMAN_REQUIRE_PROFILE=1` closes the fail-open hole | §6.3 |
| D-18 | Zero-config is a built-in default ladder, not an absence of policy | §7 |
| D-19 | Runtime resolver offline; `init`/`doctor` may probe the network | §9.1 |
| D-20 | Approvals map is inert at HITL-H | §7.3 |
| D-21 | Profile materialises on first decision, not at install | §7.3 |
| D-23 | Publish from a fresh repository, not a scrubbed history | §12 |
| D-24 | Golden-verdict compatibility test is blocking | §8.4 |
| D-25 | HITL levels dissolve into the approvals map; the field survives as sugar | §4.3 |

---
