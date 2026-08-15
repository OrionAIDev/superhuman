# Vision: Superhuman fidelity + first-run provider setup

**Created:** 2026-08-15 (G0)
**Last refined:** 2026-08-15

## Purpose

Harden two weaknesses in superhuman's *own* orchestration substrate so that projects survive
context loss and so first-run setup fits whatever LLM stack the operator actually has:

1. **Cross-session fidelity (roadmap #165).** Make a resumed project reconstruct its own state
   losslessly and make every subagent hand back a uniformly-shaped result. Three concrete changes:
   - (a) A `## Resume packet` kept current at the top of the `SUPERHUMAN.md` template — the
     handoff-packet shape: objective / immutable constraints / decisions-locked / ruled-out paths /
     current state / next-3-actions / evidence-pointers.
   - (b) A **standardized subagent return schema** applied across *all* roles:
     conclusion → evidence → commands → assumptions → risks → next-action.
   - (c) **"Decisions locked — do not relitigate"** promoted to a first-class construct, distinct
     from the existing append-only Decisions log (which records *what happened*, not *what may not
     be reopened*).

2. **First-run provider setup (roadmap #139).** Superhuman's G1/init must **elicit each new user's
   provider subscriptions / APIs and populate their tier mapping** (`~/.superhuman/profile.yaml`
   `models:` block) rather than silently assuming Anthropic aliases.

## Reason

Superhuman is stateless between sessions by design — all state lives in `SUPERHUMAN.md`. But today a
resuming orchestrator has to *reconstruct* the live decision context by re-reading the whole file and
inferring what is still open versus settled; there is no single always-current packet and no explicit
"do not reopen this" marker, so resumed sessions relitigate closed decisions and drift. Uniform
subagent returns are the same problem one level down: each role improvises its output shape, so the PM
re-parses ad hoc and evidence/assumptions/risks get dropped on the floor.

Separately, superhuman ships to other users. Its tier→model routing assumes an Anthropic-shaped world
in the Claude Code default path, but a new operator may run Gemini, OpenAI, a local model, or a mix.
First-run setup that assumes Anthropic produces a broken profile for everyone else and quietly
contradicts superhuman's own harness-agnostic design (the adaptation layer already supports
multi-provider aliasing; init just never asks).

Both are fidelity problems: #165 is fidelity across *time* (sessions), #139 is fidelity across
*operators* (whose stack differs from the author's).

## Scope as user envisions it

- Edit `templates/SUPERHUMAN.md.tpl` to add the `## Resume packet` section (kept-current contract
  documented so the PM maintains it at every gate).
- Define one canonical **subagent return schema** and thread it through every `roles/*.md` output
  contract + the dispatch/orchestration guidance that consumes those returns.
- Add a first-class **"Decisions locked"** construct (template section + the SKILL.md / gate-handling
  semantics that make it distinct from the Decisions log and enforce "do not relitigate").
- Extend Phase 0 kickoff (G1/init) to elicit provider subscriptions/APIs and write the
  `~/.superhuman/profile.yaml` `models:` tier mapping from the operator's actual stack.
- Keep everything **provider- and harness-agnostic** (see immutable constraints).

## Scope extensions identified during elicitation

- Pre-answered by the invocation brief; G0 confirms rather than re-elicits. The one PM-flagged
  extension to confirm at G0: whether #139's elicitation should also cover the *fallback* provider
  per tier (the OpenClaw tier table already models primary→fallback), or only the primary tier map.

## Out of scope (explicit)

- No change to superhuman's phase/gate *count* or the HARD-GATE semantics.
- No new provider integrations or live model shell-outs — this is setup/elicitation + doc/template
  substrate, not runtime routing changes.
- Not deploying past OrionTest (superhuman's deployment ceiling; R8's UAT gate never applies to
  superhuman itself).

## Success looks like

A resumed superhuman project reads one always-current Resume packet and a distinct "Decisions locked"
list and continues without relitigating settled decisions; every role subagent returns the same
six-part schema the PM can parse uniformly; and a brand-new operator running first-run setup is asked
what providers/APIs they have and ends up with a correct `profile.yaml` tier map for *their* stack —
with zero Anthropic-first assumptions anywhere in the shipped defaults. Changes land on a branch with
green CI, merged into `OrionAIDev/superhuman`.

## Open questions for Phase 1

- Exact field list / ordering of the Resume packet vs. what already exists in front-matter (avoid
  duplication; the packet should reference, not restate, where sensible).
- Where "Decisions locked" is enforced: template-only, or also a soft check in the PM resume path /
  drift watch.
- Elicitation depth for #139: primary-only vs. primary+fallback per tier; how to handle an operator
  who declines to answer (safe agnostic default with no vendor baked in).
- Whether the subagent return schema is advisory (documented contract) or enforced (a parseable
  block every role must emit).
