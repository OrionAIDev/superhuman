# Orchestration patterns

Reference catalog of the agent-orchestration patterns superhuman endorses, plus the anti-patterns
to avoid. Read this before adding a phase step that coordinates multiple roles, or before
proposing a new role that "wraps" existing ones.

**The governing rule: the PM is the only orchestrator. Roles do not dispatch other roles.**
The PM thread (`roles/pm.md`) is the single persistent orchestrator; every other role
(Architect, Developer, Tester, QA, Business Expert, Surrogate-User) is a fresh, non-persistent
subagent that does its work and reports back. Sub-skills (roasting-*, doubt-driven-development,
verification-before-completion) are mandatory hops *inside* a role's workflow, not orchestrators.

Any time the PM parallelizes across an **architecture seam** or a risky boundary, that is not an
autonomous decision — it fires **G9** (high-stakes parallelism). See `roles/pm.md`
"Parallelism decisions" for the 4-step checklist the PM runs before every parallel dispatch.

## The cross-harness dispatch layer

Superhuman runs on **both Claude Code and OpenClaw**. Every pattern below is expressed in the
symbolic `<dispatch:*>` names (`<dispatch:agent>`, `<dispatch:ask>`, …); `adaptation/dispatch.md`
is the single file that maps those symbols to concrete platform tools (`Agent` /
`sessions_spawn`, `AskUserQuestion` / degraded chat prompt, etc.) and to per-tier model aliases.
When you add or change an orchestration pattern, express it symbolically and let dispatch.md
resolve it — never hard-code `Agent`/`sessions_spawn` in a role or phase recipe. This symbolic
layer *is* the organisation's orchestration-portability pattern; it replaces the per-tool setup docs that
single-harness packs ship.

---

## Endorsed patterns

### 1. Direct dispatch (no fan-out)
One role, one perspective, one artifact — the PM dispatches a single subagent. The default and
cheapest option.

```
PM → <dispatch:agent> Developer (one chunk) → status report → PM
```
**Use when:** the work is one role's perspective on one unit of work. **Cost:** one dispatch.
The baseline to compare every fan-out against.

### 2. Sequential per-chunk pipeline (the Phase-3 spine)
The PM drives chunks in order, dispatching a fresh Developer per chunk, then Phase 3.1 review,
then the next chunk. There is one persistent orchestrator (the PM) and human checkpoints at the
declared cadence (G5).

```
PM: chunk 1 (Dev → 3.1 review → G5) → chunk 2 (…) → … → Phase 3.2
```
**Use when:** chunks depend on each other and per-chunk human judgment (or drift watch) adds
value. **Cost:** one subagent context per role per chunk. **Why not collapse it:** the
checkpoints catch wrong-direction work early; that is the whole point of the gated flow.

### 3. Parallel fan-out with merge
Multiple **read-only** roles operate on the same input concurrently, each producing an
independent report; the PM merges them into one decision. Superhuman uses this in two places:
`phases/3.1-test-review.md` (spec-compliance + code-quality reviewers on the same chunk) and the
new `phases/3.3-preflight-review.md` (roasting-code + security lens + roasting-design-specs
before acceptance).

```
                 ┌─→ roasting-code (correctness/arch) ─┐
PM → fan out ────┼─→ security lens                    ─┤→ merge → GO/NO-GO + rollback
                 └─→ roasting-design-specs re-run      ─┘
```
**Use when:** the sub-tasks are genuinely independent (read-only, no shared mutable state), each
role produces a *different kind* of finding, and the merge fits in the PM's context.
**The single-assistant-turn rule:** issue **all** the parallel `<dispatch:agent>` calls in **one**
assistant turn — sequential turns serialize execution and defeat the pattern (`phases/3.3` calls
this out; it mirrors the PM's own G9 parallel discipline). **Cost:** N subagent contexts + one
merge turn; higher than direct dispatch but better reports and lower wall-clock.

Validation checklist (from `roles/pm.md`): all sub-agents runnable at once? each a distinct
finding *kind*? merge fits remaining context? wait long enough that parallelism is noticeable? If
any "no" → fall back to direct dispatch.

### 4. Research isolation (context preservation)
When a step must read a lot of material that shouldn't pollute the PM context, dispatch a
read-only research subagent that returns only a digest.

```
PM → <dispatch:agent> research (reads many files) → digest → PM continues
```
**Use when:** the investigation result is much smaller than its input and the PM needs room to
think after. **On Claude Code**, prefer the built-in `Explore` subagent (read-only, cheap-tier).
**On OpenClaw**, dispatch a read-only subagent per `adaptation/dispatch.md`. **Cost:** one
isolated context — worth it any time the alternative is loading hundreds of files into the PM.

### 5. Worktree-isolated parallel Developers
When the PM parallelizes *writing* Developers across genuinely disjoint files, each runs in its
own git worktree (`roles/pm.md` "Per-chunk worktree") to prevent merge contention; the PM merges
back after each `DONE`. This is the only endorsed pattern with concurrent writers, and crossing an
architecture seam with it triggers **G9**.

---

## Anti-patterns

### A. Router role ("meta-orchestrator")
A role whose only job is to decide which other role to dispatch.
**Why it fails:** pure routing with no domain value; two extra paraphrasing hops (~2× tokens +
information loss); the PM already owns routing. **Instead:** the PM routes directly per the phase
recipes; there is no router role.

### B. Role that dispatches another role
E.g. a Developer that internally dispatches a Tester when it sees untested code.
**Why it fails:** roles produce a single perspective; chaining defeats that, the hand-off summary
loses context the called role needs, output-format/ownership conflicts multiply, and cost is
hidden from the PM. **Instead:** the role *recommends* the follow-up in its status report; the PM
dispatches the second pass. **Platform-enforced:** on Claude Code, "subagents cannot spawn other
subagents" — this anti-pattern (and D) simply fail to load. Superhuman's PM-only-orchestrator
rule leans on exactly this guarantee.

### C. Sequential orchestrator that paraphrases the human out
An agent that runs G0→G1→…→G8 on the user's behalf, summarizing between gates.
**Why it fails:** loses the human checkpoints that catch wrong-direction work, accumulates
hand-off drift, doubles token cost, and removes user agency where judgment matters most. This is
exactly what the HARD-GATE + Type-A gates forbid. **Instead:** at HITL-level 0, the PM pauses at
every Type A gate. At level 1, a *surrogate* substitutes for the human on a conservative gate
subset, never G0/G1/G8/G9/G10. At level 2, the PM/surrogate substitutes for the human on nearly
everything (via precedent-mining, logged, not paraphrased-and-hidden) — but G0/G1 still get one
combined human confirmation, and G10 is never substituted at any level. See SKILL.md "HITL levels".

### D. Deep role trees
PM → coordinator → sub-coordinator → Developer.
**Why it fails:** each layer adds latency/tokens with no decision value; leaf roles lose context to
repeated summarization; debugging becomes multi-level. **Instead:** keep orchestration depth at
**1** — PM → roles. The merge happens in the PM thread.

> **Not part of the superhuman model:** Claude Code "Agent Teams" (teammates messaging each other).
> Superhuman's model is PM-orchestrated report-back subagents, not peer debate. If a task genuinely
> needs adversarial peer investigation, that is a manual, out-of-framework choice — do not build it
> into a phase recipe.
>
> Read that as a statement about the **orchestration shape** (peer debate vs. report-back), not as a
> rule that a capability belonging to one harness is off-limits. A feature only one harness offers
> may still be adopted: through the `adaptation/` seam, with a declared degradation path for
> harnesses that lack it — never by naming a platform tool inside a phase recipe. Portability means
> every harness keeps a working floor, not that every harness gets the identical ceiling.

---

## Decision flow

```
Is the work one role's perspective on one unit of work?
├── Yes → Direct dispatch. Stop.
└── No  → Do the sub-tasks depend on each other?
         ├── Yes → Sequential per-chunk pipeline (Pattern 2), human checkpoints at cadence.
         └── No  → Are they read-only and independent?
                  ├── Yes → Parallel fan-out with merge (Pattern 3); single assistant turn.
                  │         Crossing an architecture seam → G9 first.
                  └── No (concurrent writers) → Worktree-isolated Developers (Pattern 5); seam → G9.
```

## When to add a new pattern here

Add an entry only after you've (1) used it twice in real superhuman work, (2) can name a phase
recipe or role section that demonstrates it, (3) can explain why an existing pattern wouldn't have
worked, and (4) can describe its anti-pattern shadow. Premature entries become aspirational docs
no one follows.
