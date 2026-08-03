# Design spec — Tiered entry and the express lane

**Status:** Draft for approval
**Date:** 2026-07-25
**Applies to:** superhuman v1.0.3 → v1.1.0
**Author:** design session, 2026-07-25
**Supersedes:** nothing. Extends the `<HARD-GATE>` in `SKILL.md` and adds one phase recipe.
**Related:** `docs/superhuman/specs/2026-07-24-portable-profile-and-ladder.md` (same "extract the
decision into data, leave the orchestrator alone" strategy)

---

## 1. Problem

Superhuman has one entry point and it is expensive. Every project — a hello-world CLI, a config
change, an eight-chunk service rewrite — enters through Phase 0 and walks G0 → G1 → G2 → G3 → G4
before a line of code is written. G0 alone is capped at 5–7 elicitation exchanges.

That cost is correct for greenfield work with real design decisions. It is not correct for
"add the phase-5 fixture to skill-validator," and the usage record shows developers pricing it
accordingly.

### 1.1 The measured record

Across `~/dev` and `~/.claude/skills` (2026-07-25):

| Path | Repos | Artifacts | Where the recent activity is |
|---|---|---|---|
| **Full orchestrator** — `docs/superhuman/<slug>/` with `SUPERHUMAN.md` | 9 projects | full artifact sets | 2 live, 5 reached G8, 2 stopped after G4 |
| **Light path** — `docs/superhuman/{specs,plans}/` | 17 repos | ~79 docs | 29 and 16 concentrated in two repos, both updated this week |

The full orchestrator is not failing. One full-lane project is eight chunks deep with 167 tests at 97.8%
coverage, and its reviewers caught a Windows log-rotation defect (the service module rebinding
`sys.stdout` to the rotated log) that was undetectable from the off-network test suite. The
framework earns its cost when it is used.

The problem is what happens to everything else. The two projects that stopped, stopped at G4 —
the design/implementation boundary — and the seventeen light-path repos never entered at all.

### 1.2 The load-bearing observation

**The light path is superhuman's own vendored content.** `references/brainstorming`,
`references/writing-plans`, and `references/executing-plans` all ship inside this skill. A developer
producing `docs/superhuman/specs/2026-07-01-foo.md` followed by `plans/2026-07-01-foo.md` is running
superhuman's sub-skills — just without the orchestrator.

And yet:

```
$ grep -nE 'docs/superhuman/(specs|plans)|brainstorm|writing-plans|executing-plans' SKILL.md README.md
(no matches)
```

The lane that carries ~79 of the ~90 planning artifacts in this ecosystem is **invisible to the
skill's front door**. It is not deprecated, not discouraged, not described — it simply is not
acknowledged. There is therefore no way for the orchestrator to route into it, no consistency in
what it produces, and no audit trail attached to it.

### 1.3 Why this is a structural defect, not a discipline problem

`SKILL.md` currently frames every deviation as rationalization:

> "This is just a tiny project, framework is overkill" → "Tiny projects are where unexamined
> assumptions cause the most wasted work."

That table is right about greenfield work and wrong about the incremental case, and it offers no
third option. Faced with a two-hour feature addition, the developer's choice is a ten-gate ladder or
nothing. Nothing wins, correctly, and the framework loses the engagement entirely — including the
parts that are cheap and would have helped.

**A cliff produces avoidance; a ramp produces adoption.** The fix is a graded entry, not a louder
hard gate.

### 1.4 What is lost today

The light path discards the framework's most valuable output. From that same project's
`## Retuning notes`:

> `[2026-07-23] G2: … expect operational/credential details to need explicit elicitation rather than
> assuming VISION-level assumptions were complete; ask about credential/service-account handling
> proactively earlier in future projects with a DB/auth surface.`

> `[2026-07-24] G6: … when a vendoring/config decision surfaces PII or identity-table duplication,
> offer "replace with a config/env mechanism" as an explicit option, not just variants of keeping
> the data.`

This is the system learning its operator across projects. Seventy-nine light-path documents produced
none of it, because the light path has nowhere to write it.

---

## 2. Goals and non-goals

### Goals

- **G-1.** A sanctioned entry tier for incremental work on an existing repo, at roughly 5% of the
  gate cost of the full lane.
- **G-2.** The tier decision is made **deterministically**, by code reading repo state — not by the
  orchestrator reasoning about whether it is allowed to skip discipline.
- **G-3.** Express work leaves an audit trail and contributes to the cross-project retuning record.
- **G-4.** A documented, non-punitive escalation from express to full when the work turns out to be
  bigger than it looked.
- **G-5.** Full backward compatibility: the 9 existing full projects resume unchanged, and the ~79
  existing light-path documents are retroactively valid express artifacts.

### Non-goals

- **NG-1.** Restructuring the full lane. Phases 0–4, the ten gates, the seven roles, drift watch, and
  the artifact catalog are unchanged.
- **NG-2.** Weakening the `<HARD-GATE>` for work that routes to full. Everything it says about
  rationalization stays true *of that tier*.
- **NG-3.** Touching HITL levels (H/M/L) or the deployment-profile ladder. Tier answers *how much
  process*; HITL answers *who approves it*; the profile answers *what is permitted here*. Three
  orthogonal axes; this spec moves one.
- **NG-4.** Any new artifact directory. Express writes to `docs/superhuman/{specs,plans}/` — the
  convention already declared in the operator's `CLAUDE.md` and already populated in 17 repos.

---

## 3. The tier model

Three tiers, one of which is new only in the sense of being written down.

| Tier | Name | For | Gates | Artifacts | Roles |
|---|---|---|---|---|---|
| **T0** | direct | one-file fix, config, docs, a rename | none (declaration only) | none | none |
| **T1** | **express** | **incremental feature/phase on an existing repo with a known shape** | **E1 (scope+plan), E2 (accept)** | `specs/<date>-<slug>.md`, `plans/<date>-<slug>.md`, ledger entry | PM + Developer; reviewer on request |
| **T2** | full | greenfield, multi-role, unresolved design decisions, or anything touching a protected rung | G0–G10 | full declared set + `SUPERHUMAN.md` | all seven |

T0 is not "no framework" — it is *the framework declaring that no framework applies*, logged in one
line. The distinction matters: today an unlogged direct edit is indistinguishable from an
undisciplined one.

### 3.1 Tier is a floor, not a ceiling

Identical in shape to the profile spec's ceiling rule, inverted. The router recommends a **minimum**
tier. The user may always choose more process than the router asks for; the user may **not** choose
less without an explicit logged override. Where the router and the user disagree downward, the
disagreement is recorded in the ledger as a recommendation override — the same mechanism
`SUPERHUMAN.md` already uses at gates.

---

## 4. The express lane (`phases/express.md`)

A new phase recipe, dispatched by the router when it resolves T1.

### 4.1 Recipe

| Step | Actor | Action | Output |
|---|---|---|---|
| E.0 | PM | Read the repo's existing `specs/` + `plans/` and the ledger. Establish what shape this repo's work already takes. | working context |
| E.1 | PM | Elicit scope — **capped at 2 exchanges**, not 5–7. If it takes more than 2, that is a router mis-resolution → escalate to T2 (§6). | scope statement |
| E.2 | PM | Draft `specs/<date>-<slug>.md` via `references/brainstorming` and `plans/<date>-<slug>.md` via `references/writing-plans`. | 2 artifacts |
| **E.3** | **user** | **GATE E1 — approve scope + plan.** Options + recommendation rule applies. Artifacts by path. | approval logged |
| E.4 | Developer | Execute the plan via `references/executing-plans` under `references/test-driven-development`. | code + tests |
| E.5 | PM | `references/verification-before-completion` + `references/definition-of-done`. Fresh evidence required; `DONE_WITH_CONCERNS` permitted. | evidence |
| **E.6** | **user** | **GATE E2 — accept.** Preconditioned on E.5 evidence. | approval logged |
| E.7 | PM | Append ledger entry incl. any retuning observation. | ledger line |

Two human stops, not ten. Both are Type A.

### 4.2 What express deliberately does not do

No `VISION.md` (the repo's existing direction is the vision). No `REQUIREMENTS.md` separate from the
spec. No `DESIGN.md` with three options — if the work needs three options weighed, it is not express
(§6). No chunk log, no drift watch, no Phase 3.3 adversarial fan-out. No surrogate user.

Express borrows the *cross-cutting rules* — options-plus-recommendation, verification before
completion, honest concern surfacing, artifacts by path, append-mostly authoring — because those are
cheap and are most of the value. It skips the *phase machinery*, which is where the cost is.

---

## 5. The express ledger

**Decision: one append-only `docs/superhuman/LEDGER.md` per repo.**

Rejected alternatives:

- *Frontmatter in each spec doc.* Cheapest to write, but the retuning record ends up scattered across
  29 files in the largest light-path repo and unreadable as a series. The value of retuning notes is
  precisely that they
  accumulate.
- *A `SUPERHUMAN.md` per express slug.* Structurally consistent with the full lane, but it recreates
  the ceremony the tier exists to avoid, and 29 near-empty state files per repo is noise.

Format — three lines per express project, appended at E.7:

```markdown
## <date> — <slug>  (T1 express)
- spec: specs/<date>-<slug>.md  |  plan: plans/<date>-<slug>.md
- E1 <ts> approved: <one line>  |  E2 <ts> accepted: <one line, incl. evidence ref>
- retuning: <observation about operator preference, or "none">
```

The `retuning:` line is the point of the whole file. It is the same field the full lane already
maintains, at a cost of one sentence.

### 5.1 Retroactive validity

The ~79 existing light-path documents **are** valid express artifacts under this spec. No backfill
is performed and none is permitted — consistent with the anti-pattern `SKILL.md` already forbids and
with the precedent set by the public-release cutover's legacy-import decision. A repo adopting the
ledger starts it at its next express project; prior work is referenced, not reconstructed.

---

## 6. Escalation and de-escalation

Express must have a visible exit or it becomes a trap — work that should have been T2 grinding
through a two-gate lane because switching feels like failure.

**T1 → T2 triggers (any one, mandatory, non-overridable):**

1. E.1 scope elicitation exceeds 2 exchanges.
2. The spec surfaces a decision needing 2–3 weighed options with real trade-offs.
3. The plan exceeds 3 chunks, or touches more than one architectural seam.
4. Any drift trigger classified **moderate or above** during E.4.
5. The work modifies a component at a rung whose profile requires `promote_into` approval.

On any trigger the PM stops, states the trigger verbatim, and presents a **G6-shaped** escalation:
(a) convert to T2 — the express spec and plan are carried forward as the legacy-import reference set,
not rewritten; (b) reduce scope to fit express; (c) abandon. Recommendation first, per the standard
rule.

**T2 → T1 de-escalation is not offered.** A project that entered full stays full. Downgrading
mid-flight would strand a partially-populated `SUPERHUMAN.md` and is exactly the rationalization the
`<HARD-GATE>` exists to block.

---

## 7. The router (`scripts/route.py`)

Per development principle #1, the tier decision is deterministic and belongs in code. The
orchestrator currently reasons about it, which is why `SKILL.md` must argue with itself about
rationalization in five separate places.

Python, `argparse`, no third-party dependencies — matching `scripts/superhuman_profile.py`, which set
this precedent.

```
$ python scripts/route.py --repo <path> --request "<text>"
tier: T1
basis: existing-repo(specs=29,plans=29) + no-open-superhuman-md + request-verbs(add,extend)
floor: T1   # user may raise, may not lower without logged override
```

### 7.1 Signals

| Signal | Source | Pushes toward |
|---|---|---|
| Open `SUPERHUMAN.md` with an unclosed gate for this scope | filesystem | **resume that project** (short-circuits routing entirely) |
| Repo has no commits / no `src/` / no manifest | filesystem + git | T2 |
| Repo has ≥1 prior `specs/` or `plans/` doc | filesystem | T1 |
| Request verbs: *add, extend, fix, wire, phase N of* | request text | T1 |
| Request verbs: *build, design, from scratch, replace, migrate* | request text | T2 |
| Request names ≥2 components or a new external interface | request text | T2 |
| Diff surface would touch a protected rung (via `superhuman_profile.py`) | profile resolver | T2 |
| Single file, no test change implied | request text | T0 |

Deterministic scoring over these signals, exit code carrying the tier — same contract shape as
`autonomous-precondition.sh`. Ambiguous cases resolve **upward** and say so; the router never
silently picks the cheaper lane.

### 7.2 Router output is advisory to the user, binding on the orchestrator

The orchestrator may not overrule the router downward. The user may, and that override is logged.
This is the same split the profile spec uses: code decides, human may override with a record, model
never decides.

---

## 8. `SKILL.md` amendment

The `<HARD-GATE>`'s first action changes from "check for `SUPERHUMAN.md`, else Phase 0" to:

1. Check for a resumable `SUPERHUMAN.md` (**unchanged**, including the valid/invalid/stale-state
   logic and the pre-existing-code drift check).
2. If none: run `scripts/route.py`. Announce the tier and its basis.
3. Dispatch to the tier's recipe: T0 → declare and log; T1 → `phases/express.md`; T2 → `phases/0-kickoff.md`
   (**unchanged**).
4. Rules 3–5 (never claim completion without gates; the rationalization warning; the unattended
   precondition) apply **within** the resolved tier and are otherwise unchanged.

The anti-pattern table gains a column stating which tier each red flag applies to. "This is just a
tiny project, framework is overkill" remains a red flag *at T2* — and at T1 it becomes the router's
finding rather than the model's excuse.

### 8.1 Discoverability

Two edits outside the phase machinery, both required for the tiering to be reachable:

- **Skill description.** Currently: *"when starting OR resuming a non-trivial project."* It never
  names the case that is most of the volume. Add the incremental trigger: *"adding a feature or
  phase to an existing repo."*
- **Operator `CLAUDE.md`.** The Planning & Roadmap table declares that specs and plans live in
  `docs/superhuman/{specs,plans}/` but never says *superhuman puts them there* — so it reads as a
  filing convention rather than a skill invocation. One clause fixes it.

---

## 9. Compatibility

| Existing state | Effect |
|---|---|
| 9 repos with `docs/superhuman/<slug>/SUPERHUMAN.md` | none — resume path is checked before routing |
| ~79 light-path docs in 17 repos | retroactively valid T1 artifacts; no backfill |
| `public-release-cutover` (in flight, same repo) | none — it holds an open `SUPERHUMAN.md`; step 1 short-circuits |
| Profile / rung ladder | none — orthogonal axis (NG-3) |
| Users with no profile | none — router's rung signal degrades to "unknown", resolves upward |

Ships as **v1.1.0**: additive, no breaking change to any existing project's state file.

---

## 10. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Express becomes the universal escape hatch; T2 is never used again | **High** | The router owns the decision, not the model (§7.2); ambiguity resolves upward; §6 triggers are mandatory and non-overridable. |
| The ledger degenerates into a write-only log nobody reads | Medium | E.0 makes reading it the first step of the next express project. If it is never read it is never written to usefully — and that is a measurable failure, not a silent one. |
| Two-exchange cap produces under-specified specs | Medium | The cap is an *escalation trigger*, not a truncation: exceeding it routes to T2 rather than shipping a thin spec. |
| Router mis-resolves on unusual repos | Low | Advisory to the user, who sees the basis string and can raise the tier in one word. |

---

## 11. Open questions for G-approval

1. **Ledger placement** — `docs/superhuman/LEDGER.md` (recommended, §5) or per-slug state files?
2. **T0 logging** — is a declaration-only tier worth the line it costs, or should T0 mean "superhuman
   was not involved" and write nothing?
3. **Sequencing against the public release** — ship the cutover at v1.0.3 and tier afterwards, or hold
   publication for v1.1.0 so public users do not meet the cliff first? This is a decision for the
   cutover project, not this one; recorded here because the two interact.
4. **Reviewer in express** — E.4/E.5 currently dispatch no independent reviewer. Given that full-lane
   reviewers caught defects their developer did not, should T1 include one cheap-tier code-quality pass
   before E2?

---

## 12. Decision log

<!-- Append-only. Format: [<ISO date>] <decision>; basis: <why> -->
[2026-07-25] Tier is a floor, not a ceiling — user may raise, orchestrator may never lower; basis:
mirrors the profile spec's ceiling rule, keeps the model out of the discipline decision.
[2026-07-25] Express writes to the existing `docs/superhuman/{specs,plans}/` rather than a new
directory; basis: 17 repos and the operator's CLAUDE.md already declare it — NG-4.
[2026-07-25] No retroactive backfill of ledger entries for the ~79 existing documents; basis: same
anti-pattern SKILL.md forbids, same precedent as the public-release-cutover legacy-import decision.
[2026-07-25] T2 → T1 de-escalation not offered; basis: would strand a partially-populated
SUPERHUMAN.md and is the rationalization the HARD-GATE blocks.
[2026-07-25] Router in Python with argparse and no third-party deps; basis: matches
scripts/superhuman_profile.py precedent and development principle #7.
