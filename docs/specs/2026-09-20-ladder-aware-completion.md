# Spec — ladder-aware completion: "all gates fired" means accepted, not complete

**Created:** 2026-09-20
**Status:** spec. Step 1 is normative and implemented in this PR. Steps 2 and 3 are scoped here
but deliberately NOT built here — see §6 sequencing.
**Ruling:** roadmap #272 (the boss ruling, 2026-09-20) — priority p1, step 1 "now, strictly after
roadmap #271 merges".
**Predecessor:** `docs/specs/2026-07-24-portable-profile-and-ladder.md` (the ladder is data; rungs,
`promote_into`, `act_unattended`).

---

## 1. The defect

Superhuman reports a project "complete" when the last gate fires. A project is not complete then.
It is complete when CI is green **and** every environment in its deployment ladder has been
reached, or when the human PM says it is.

The claim traces to one sentence — HARD-GATE rule 3 in `SKILL.md`:

> "NEVER claim a project is complete unless all 8 phase gates (G0, G1, G2, G3, G4, G5, G7, G8)
> have fired and their HITL approvals are logged in SUPERHUMAN.md `## Decisions log`."

That sentence *defines* complete as "every gate fired". This is not a missing gate. It is a wrong
definition, and every downstream surface inherits it.

`phases/4-acceptance.md:41` already requires that "the rung's declared `promote_into` approver has
signed off on **any promotion**", but line 42 downgrades an unmet item to "a residual concern,
never silently passed" — surfaced, not blocking. And it asks only about promotions that *happened*.
Nothing anywhere asks which rungs **remain**.

## 2. Why this is not an inference problem

The proposal that reached the boss suggested an LLM-inferred completion gate as a quick interim,
with a deterministic version later. The ruling rejected that, and this spec keeps the rejection,
for two reasons that constrain the design:

1. **There is nothing to infer.** Once the ladder is declared and the sign-offs are recorded,
   "which rungs remain" is a set difference over records the framework already holds.
2. **The failure being fixed is a confident wrong claim.** Fixing it with a second claim-generator
   reproduces the mechanism. The motivating incident was exactly this: a session reported an
   integration project as having "nothing outstanding" and the rollup repeated it, while the
   project's own runbook showed its first release stuck mid-ladder with three later versions never
   promoted. That was an inference failure; another inference layer makes it again.

**On the irreversibility principle.** Declaring a project complete is not itself irreversible — it
is a label on a record, wrong-able in both directions, and a human approves it at HITL-M/H anyway.
So the "irreversible paths are always code" rule does not strictly forbid an inferred gate. But the
acts *underneath* the label — promoting into an acceptance or production rung — are irreversible,
and those are already code plus an explicit human yes. The binding rule is therefore: **the
completion gate reads those records and never re-derives them.** An inferred gate re-derives by
definition. That is why the inference window here is zero rather than "acceptable for a while".

## 3. Finding that changes step 1's shape — the profile holds no promotion order

The ruling says "which rungs remain is a set difference over data the resolver already holds".
That is **almost** right, and the gap matters.

Verified on `main` at `46f58ae`:

- `scripts/superhuman_profile.py:246` — `kind` is documented as *"Optional, semantics-free label
  for human readability."* It carries no ordering.
- `_RUNG_KEYS = {"name", "kind", "labels", "detect", "approvals", "tests", "promote"}`
  (`scripts/superhuman_profile.py:339`) — there is no `next`, `after`, `rank` or `order` key.
  `promote` is a command/manual marker, not an edge.
- Ladder list order is **detection precedence, narrower-first**, and is explicitly documented as
  such in `profiles/presets/classic-3tier.yaml`: *"Narrower rungs first — deny rungs precede allow
  rungs."* In that shipped preset `production` is the **first** entry and `workstation` the last.

So the resolver holds the rung **set** and each rung's approver policy, but not the **sequence**.
Reading list order as promotion order would, on the shipped preset, report the ladder backwards.

**Consequence.** Step 1 cannot merely promote an existing marker and call the ladder declared. The
project's ordered ladder has to become a recorded decision in its own right, or steps 2 and 3 can
never answer "the next rung is X".

## 4. Deviation from the ruling — a new field, not an overload of `## Environment`

The ruling proposes promoting `## Environment` in `templates/SUPERHUMAN.md.tpl` from an optional
comment to a decision captured at alignment, with "no ladder" as an explicit allowed value. This
spec **declines the overload** and adds a separate field instead. Two reasons:

1. **`## Environment` answers a different question.** It answers *"which rung am I standing on?"*
   Its value is matched against the profile's `env_marker` detector and **outranks path
   detection** — it is a detection input. Completion needs *"which rungs must this work reach?"*
   Those are different questions with different lifetimes: the first changes when a checkout moves,
   the second is fixed at alignment.
2. **`none` would collide.** `## Environment: none` is fed to the same `env_marker` matcher. With no
   rung declaring the marker `none` it falls through to path detection — i.e. it behaves exactly
   like the omission it was meant to disambiguate, reintroducing the ambiguity the ruling wants
   removed. Worse, an operator who *does* declare a rung named `none` gets a silent
   mis-resolution.

`## Environment:` therefore keeps its current semantics and its heading (also pinned by
`tests/test_content.py`, which the precondition guard's marker check depends on). The new decision
is a distinct header field:

```
**Deployment ladder:** dev > staging > production   |   none
```

- **Ordered, left to right**, lowest rung first. This is the promotion sequence the profile does
  not encode (§3).
- **`none` is a real answer,** and a required one. A blank is not an answer; today the absence of a
  ladder is ambiguous between "there is no ladder" and "nobody asked", and that ambiguity is the
  largest bucket in the fleet rollup.
- **Rung names should resolve** against the operator's profile where one exists; they are recorded
  as written either way, because a project may name a rung the local profile has not declared yet.
  Validating the names against the profile is step 2's job, not step 1's — step 1 records.

## 5. Step 1 — declare and define (this PR)

**5.1 Definition.** `SKILL.md` HARD-GATE rule 3 is amended so that all 8 gates firing establishes
**accepted**, not **complete**. Complete additionally requires either:

- every rung in the declared ladder carries a recorded `promote_into` sign-off, **or**
- the human PM has explicitly declared the project complete over an incomplete ladder.

**The PM override is mandatory and must stay available** — it is half of the originating
requirement, not a convenience. It is recorded as a decision in `## Decisions log`, naming the
rungs left uncleared, never assumed. A project with `**Deployment ladder:** none` is complete at
acceptance because its declared ladder is empty and the set difference is empty — not because the
question was skipped.

**5.2 Declaration.** `templates/SUPERHUMAN.md.tpl` gains the `**Deployment ladder:**` field, and
`phases/0-kickoff.md` captures it at G1 alongside the other locked preferences. Where the ladder is
already knowable — the operator's profile declares one, or the harness context states one — the PM
**confirms** it rather than asking cold.

**5.3 Out of scope for this PR, on purpose.** `phases/4-acceptance.md` is not changed to refuse.
Refusing on a signal nothing has validated yet produces false refusals, and a gate that refuses
wrongly gets disabled. The refusal is step 3, after step 2 has run for real.

## 6. Steps 2 and 3 — scoped, not built

**Step 2 — see it, continuously. In `superhuman-cto`.** Report, per project, whether the **latest**
version has cleared its declared ladder: done / incomplete at rung N / no ladder declared. This is
the half a gate structurally cannot do: **a gate fires once, and projects rot afterwards.** The
motivating project was genuinely fine at its G8 and stale three versions later; no completion gate,
however strict, catches that. Only a standing report re-read against current state does. Sequenced
after FR-24 and the approved P1 item already in that repo's queue.

**Step 3 — refuse on it. In `superhuman`.** G8 presentation reads step 2's answer and cannot print
"complete" while rungs remain; it prints "accepted; ladder incomplete, next rung is X". The step-1
PM override stays, and stays logged.

**Backfill** of the ladders of existing projects that never declared one: deferred, and folded into
the existing fleet-wide cleanup item (roadmap #260), reusing `superhuman-cto`'s FR-23
propose-and-confirm rather than growing a second confirm flow. That backfill is the one place
inference is warranted — proposing what a ladder *should* have been, for a human to confirm.

## 7. Compatibility

Additive. Existing `SUPERHUMAN.md` files have no `**Deployment ladder:**` line; a project that
predates this field resumes unchanged and is treated as **undeclared**, which is reported, never
guessed and never silently read as `none`. `## Environment:` semantics, the `env_marker`
precedence, and the precondition guard are untouched.

## 8. What this spec does not verify

How many existing projects would resolve to a usable rung today. That number is step 2's first
output, not an input here. The file citations in §1 and §3 are from `main` at `46f58ae` and are
re-runnable.
