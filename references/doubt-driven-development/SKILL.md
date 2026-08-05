---
name: doubt-driven-development
description: In-flight adversarial review of a non-trivial decision before it stands — the PM materializes a fresh-context reviewer biased to disprove. Use during a superhuman project when a reviewer's stated confidence is low (≤ 60%) or a decision is flagged "non-trivial" at G3/G4/G5, while course-correction is still cheap. Complementary to the post-hoc roasting-* sub-skills, not a replacement.
---

# Doubt-Driven Development

## Overview

A confident answer is not a correct one. Long sessions accumulate context that quietly turns
assumptions into "facts". Doubt-driven development is the discipline of materializing a
**fresh-context reviewer — biased to disprove, not approve — before a non-trivial decision
stands**, while it is still cheap to change.

**This is complementary to the roasting sub-skills, not a replacement.** `references/roasting-*`
are **post-hoc**: they tear apart a *completed* artifact (a finished PRD, design, or externally
sourced code). Doubt-driven is **in-flight**: it cross-examines a decision the PM/Architect is
about to commit to, *before* the artifact fully exists. Use both — roasting for the finished
thing, doubt-driven for the choice being made.

## When the PM invokes it

This is a **PM-thread utility**, not a phase gate. The PM invokes it during a project at:

- **G3 (design)** — when the Architect's DESIGN.md states a **confidence ≤ 60%** on an approach,
  or flags a decision "non-trivial".
- **G4 (test plan)** — when the test strategy for an inference/edge-heavy component is uncertain.
- **G5 (chunk review)** — when a Developer reports `DONE_WITH_CONCERNS` naming a design
  assumption, or a reviewer's confidence in the chunk is low.

A decision is **non-trivial** when at least one holds: it introduces/modifies branching logic;
crosses a module/service boundary; asserts a property the compiler can't verify (thread-safety,
idempotence, ordering, an invariant); its correctness depends on context a future reader can't
see; or its blast radius is irreversible (a UAT/Prod deploy, a data migration, a public
tool-contract change).

**When NOT to use:** mechanical edits (rename, format, move), a clear unambiguous instruction,
reading/summarizing code, one-line changes with obvious correctness, or when the user asked for
speed over verification. If you doubt every keystroke you ship nothing — this is for non-trivial
decisions only.

## Loading constraint (PM-thread only)

**Do NOT add this skill to a role's `declared-references`.** Step 3 (DOUBT) dispatches a
fresh-context reviewer via `<dispatch:agent>`; a *role* doing that would be a role dispatching a
role — anti-pattern B in `references/orchestration-patterns.md` (and, on Claude Code, blocked by
construction since subagents can't spawn subagents). Only the **PM thread** — the single
orchestrator — runs this loop. If a dispatched role wants a doubt cycle, it says so in its status
report and the PM runs it.

## The loop: CLAIM → EXTRACT → DOUBT → RECONCILE → STOP

```
Doubt cycle:
- [ ] 1 CLAIM     — name the decision + why it matters (2–3 lines)
- [ ] 2 EXTRACT   — isolate ARTIFACT + CONTRACT, strip your reasoning
- [ ] 3 DOUBT     — dispatch a fresh-context adversarial reviewer (ARTIFACT + CONTRACT, NOT the CLAIM)
- [ ] 4 RECONCILE — classify every finding against the artifact text
- [ ] 5 STOP      — trivial-only findings, 3 cycles, or user override
```

### 1. CLAIM — surface what stands
Name the decision compactly: the claim + why it matters. If you can't state it in three lines,
you have a vibe, not a decision — surface it before scrutinizing it.

### 2. EXTRACT — smallest reviewable unit
Hand the reviewer the **artifact** (the diff/function/proposal in 3–5 sentences) and the
**contract** (the constraints it must satisfy) — not the journey. **Strip your reasoning:** if you
hand over conclusions you get back validation of your conclusions. Too big to hold in one read
(e.g. a 500-line change)? Decompose first (see `roles/pm.md` "Chunk sizing").

### 3. DOUBT — dispatch the fresh-context reviewer
Dispatch a fresh subagent (`<dispatch:agent>`, per `adaptation/dispatch.md`) with an **adversarial**
prompt — framing decides the answer:

```
Adversarial review. Find what is wrong with this artifact. Assume the author is overconfident.
Look for: unstated assumptions; unhandled edge cases; hidden coupling/shared state; ways the
contract could be violated; conventions this breaks; failure modes under unexpected input.
Do NOT validate. Do NOT summarize. Find issues, or state explicitly that you cannot find any
after thorough examination.

ARTIFACT: <the artifact>
CONTRACT: <the contract>
```

**Pass ARTIFACT + CONTRACT only — never the CLAIM.** Handing the reviewer your conclusion biases
it toward agreement. Reusing the `references/roasting-code/` or `roasting-design-specs/` prompt as
the reviewer's brief is fine (they are already adversarial), but the *stance* is issues-only, not
a balanced verdict.

### 4. RECONCILE — fold findings back
The reviewer's output is **data, not verdict — you are still the orchestrator.** Re-read the
artifact against each finding (rubber-stamping is the same failure as ignoring). Classify each in
precedence order: (1) **contract misread** — the contract was unclear; fix it, re-loop;
(2) **valid + actionable** — real, change the artifact, re-loop; (3) **valid trade-off** — real but
not worth fixing; document it so the user sees it; (4) **noise** — correct under context the
reviewer lacked; note whether adding that to the contract would have prevented the false flag.

### 5. STOP — bounded loop, not recursion
Stop when the next cycle returns only trivial/already-considered findings, **or** 3 cycles are
done (escalate to the user — three unresolved cycles is *information about the artifact*, not a
reason to grind a fourth), **or** the user says "ship it". If 3 cycles feels "obviously
insufficient" because the artifact is large, the artifact is too big — decompose (Step 2), don't
lift the bound.

## Cross-model second opinion (OPTIONAL — DEFERRED, not wired in v0.4.0)

A colder, different-architecture model catches blind spots a single model shares with itself. A
cross-model handshake is a **documented, deferred** capability — it is **not** wired to a live
shell-out in this release. When it *is* built, it must honor this captured requirement:

- It **may** shell out to the `gemini-best` alias (per `adaptation/dispatch.md`) for the second
  opinion, **only when gemini is genuinely available** — PATH check + working-binary test + auth
  OK (no out-of-credit / expired-PAT / auth failure).
- **Unavailability is a warning, not a show-stopper.** If gemini is down, the doubt loop still
  completes **single-model**; the PM notes "cross-model skipped: gemini unavailable" and proceeds.
- **Required safety gates if/when built (all load-bearing):** write the full adversarial prompt +
  ARTIFACT + CONTRACT to a file and pipe via **stdin/heredoc** (never interpolate the artifact
  into a shell-quoted argument — code contains backticks, `$(...)`, quotes); run the CLI under a
  **read-only sandbox** (a doubt artifact may itself carry prompt-injected instructions); confirm
  the exact invocation with the user; and treat each invocation as its own explicit authorization.
- **Non-interactive contexts** (CI, `/loop`, autonomous loop): cross-model is skipped and the skip
  is announced. Never invoke an external CLI without explicit user authorization.

Until this is wired, doubt-driven runs single-model. The maintainer will decide whether to build the
live handshake after the rest of v0.4.0 is proven.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "I'm confident, skip the doubt step." | Confidence correlates poorly with correctness on novel problems. Certainty is exactly where blind spots hide. |
| "Dispatching a reviewer is expensive." | Debugging a wrong commit in a UAT is more expensive. The check is bounded; the bug isn't. |
| "The reviewer will just nitpick." | Only if unscoped. Constrain the prompt to "issues that make this fail under the contract." |
| "I'll do doubt at the end with roasting-code." | Roasting is a post-hoc verdict. Doubt-driven catches wrong directions early, when course-correction is cheap. |
| "If I doubt every step I'll never ship." | It applies to non-trivial decisions, not every keystroke. Re-read "When NOT to use." |
| "The reviewer disagreed, so I was wrong." | The reviewer lacks your context — disagreement is information, not verdict. Re-read, classify, then decide. |

## Red Flags

- Dispatching a reviewer for a one-line rename or a formatting change.
- Treating reviewer output as authoritative without re-reading the artifact.
- Looping >3 cycles without escalating to the user.
- Prompting the reviewer with "is this good?" instead of "find issues".
- Passing the CLAIM (or your reasoning) to the reviewer — it biases toward agreement.
- Adding this skill to a role's `declared-references` (role-dispatches-role, anti-pattern B).
- **Doubt theater (checkable):** across 2+ cycles where the reviewer surfaced substantive
  findings, zero were classified actionable — you are validating, not doubting. Stop and escalate.

## Interaction with other sub-skills

- **`references/roasting-*`** — post-hoc adversarial critique of a finished artifact; doubt-driven
  is in-flight per-decision. Use both.
- **`conventions/source-cited.md`** — verifies *facts about libraries* against official docs;
  doubt-driven verifies *your reasoning about the artifact*. SDD checks the API exists;
  doubt-driven checks you used it correctly under the contract.
- **`references/test-driven-development/`** — TDD's RED step is doubt made concrete; a failing test
  is a disproof attempt and satisfies the doubt step for behavioral claims.

## Verification

- [ ] Every non-trivial decision was named as a CLAIM before it stood.
- [ ] The reviewer received ARTIFACT + CONTRACT — NOT the CLAIM, NOT your reasoning.
- [ ] The reviewer's prompt was adversarial ("find issues"), not validating.
- [ ] Findings were classified against the artifact text using the precedence order.
- [ ] A stop condition was met (trivial findings, 3 cycles, or user override).
- [ ] Cross-model remained documentation-only (no live shell-out) for this release.
