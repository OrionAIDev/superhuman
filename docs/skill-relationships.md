# Session-tracking skill relationships (FR-9)

The session-tracking manifest (the event log + fragment projection under `scripts/fleet/`) is a
capability of the `superhuman` skill, but it is not the only skill that touches a superhuman
project's sessions. This document records the three relationships the manifest core is designed
against, and the packaging decision that follows from them.

## The three relationships

**session-relay = the Claude adapter / companion.** session-relay is the harness-specific
origination and handoff surface — it is where a session actually gets spawned, relayed, or
kicked off inside the Claude Code harness. The manifest's `adapter/claude.py` implementation
calls into it; the manifest core never does. This keeps the origination mechanics (native
session tools, harness-specific process spawning) entirely on session-relay's side of the
adapter boundary, so the manifest core stays usable in a harness that has no session-relay at
all — the `PortableAdapter` degradation path is exactly what makes that true in practice, not
just in principle.

**The governing dev-flow discipline skill is a governing peer.** It governs *where work lives*,
naming, and promotion between environments — the same discipline that motivates the manifest's
own D3-uat/D4-prod human-approver gate on the done-ladder (a promotion decision always needs an
explicit human approval, every time). It is not a dependency the manifest's core imports or calls
into; it governs the process the manifest's data describes, from outside that data model. A
project can run session-tracking without ever having that discipline skill installed, and the
discipline skill's rules apply to a project's promotions whether or not session-tracking is
tracking them.

**A skill-audit tool is a conditional QA companion.** It is invoked, on demand, to audit a skill
(including this one) against a conformance standard — linting, git hygiene, and judgment-based
checks. It is not part of the runtime manifest path: nothing in `create → update → validate →
query` calls out to it, and no manifest write depends on it having run. It is a companion a
maintainer reaches for periodically, not a component the manifest core depends on to function.

## Packaging: schema contract now, bundle later

The three relationships above raise an obvious question: if these skills are this closely
related, why aren't they shipped as one bundle?

**Decision: the trio shares a schema contract now, rather than being bundled into one package.**
The manifest's event/fragment schema (the record shape every writer emits and every reader
parses) is the contract the three skills coordinate through. Each skill is installed and
versioned independently against that shared contract, rather than being packaged as a single
unit with one install/version lifecycle.

**Rationale.** Bundling is the heavier, more coupled option: it forces one release cadence across
skills whose actual coupling is looser than that — the origination adapter, the process
discipline, and the QA audit tool each have their own reasons to change on their own schedule,
and none of those changes should force a version bump in the other two. A shared schema contract
gets the coordination benefit (every skill agrees on what a valid event/fragment looks like)
without the packaging cost (one artifact, one version, one release train) that bundling would
impose. It also keeps the manifest core's own harness-neutrality intact: a schema contract is
just a data shape, so a skill that only ever produces or consumes that shape has no reason to
import another skill's code to do so.

**Bundling is not ruled out — it is deferred.** If and when this trio ships together as a single
distributable unit (a genuine "these always travel together" case), bundling becomes the more
appropriate choice at that point, and the schema contract these skills already share is exactly
the interface a bundle would formalize rather than replace. Nothing about today's schema-contract
decision needs to be undone to get there later; it is the natural starting point for that path,
not a design that would need to be abandoned first.

## Future directions

Two adjacent directions are worth naming here conceptually, without committing to either as part
of this phase of work:

- **Live cross-session handoff.** The current handoff mechanism is a durable, asynchronous
  intent row — one session records that a handoff is happening, and a later session picks it up.
  A live, synchronous handoff (two sessions coordinating in real time through the adapter/handoff
  seam) is a natural extension of that same seam, but it intersects harness-specific capabilities
  this phase deliberately keeps out of scope, so it is deferred rather than designed here.
- **Model-tier-aware plan/task tracking.** The manifest tracks session lifecycle, dependency
  edges, and a deploy-readiness ladder — it does not currently reason about which model tier
  produced a given piece of work, or use that as a tracking dimension. That is adjacent platform
  work in its own right, not a natural extension of this manifest's data model, and belongs to a
  separate effort rather than to this schema.

Neither direction is a commitment; they are recorded here so a future design pass has a starting
point instead of rediscovering the same two questions from scratch.
