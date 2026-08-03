---
name: deprecating-a-system
description: Guides the safe removal and migration of a PRODUCT system, API, feature, or integration. Conditional sub-skill — the PM invokes it at G0 when a project's VISION declares "remove / retire / sunset / consolidate X". Not part of the standard 8-gate flow. Applies to product code, NOT to superhuman's own project artifacts (those are archive-never-delete).
---

# Deprecating a system

## Scope — read this first

This skill is about removing **product / infrastructure code** the organisation is sunsetting — a service,
an API, an integration, a feature, a duplicate implementation. Recent real examples: the
**a UAT environment wealth-only pivot** after a product moved to its production environment, and the **RO-bind-mount removal**
that forced daemon-only writes to user data. Both were compulsory-with-tooling migrations; nothing
in the framework guided them at the time — this skill fills that gap.

**It does NOT apply to superhuman's own project artifacts.** VISION/REQUIREMENTS/DESIGN/PLAN/chunks
and any superseded work are governed by **archive-never-delete** (move to `archive/…/` with
`WHY.md` + `RESTORE.md`), not by this skill. Do not use this skill to justify deleting a project's
own documents.

## When the PM invokes it (conditional, at G0)

This is a **conditional reference**, like the roasting sub-skills — it is not one of the eight
gates. The PM invokes it when the **VISION** for a project declares an intent to *remove, retire,
sunset, decommission, or consolidate* an existing system. It then shapes REQUIREMENTS (migration
scope), DESIGN (which migration pattern), and PLAN (incremental cutover chunks). If a project has no
"remove X" in its vision, skip this skill entirely.

## Core principles

- **Code is a liability, not an asset.** Every line has ongoing cost — tests, docs, patches,
  dependency updates, onboarding. The value is the *functionality*, not the code. When the same
  functionality costs less elsewhere, the old code should go.
- **Hyrum's Law makes removal hard.** With enough consumers, every observable behavior — including
  bugs, timing quirks, exit codes, and JSON-shape side effects — becomes depended on. This is why
  deprecation needs **active migration**, not just an announcement. Consumers can't "just switch"
  when they depend on behaviors the replacement doesn't replicate.
- **Plan removal at design time.** When building something new, ask "how would we remove this in
  three years?" Clean interfaces, feature flags, and a small surface area make later deprecation
  cheap.

## The deprecation decision

Before deprecating anything, answer:

1. Does this system still provide **unique value**? If yes → maintain. If no → proceed.
2. How many consumers depend on it? → quantify the migration scope.
3. Does a **replacement** exist? If no → build it first. Never deprecate without an alternative.
4. What is the migration cost per consumer? → trivially automatable → do it; manual/high-effort →
   weigh against maintenance cost.
5. What is the ongoing cost of **not** deprecating? → security risk, engineer time, complexity drag.

## Advisory vs compulsory

| Type | When | Mechanism |
|---|---|---|
| **Advisory** | Migration optional, old system stable | Warnings, docs, nudges; consumers migrate on their own timeline. |
| **Compulsory** | Old system has a security issue, blocks progress, or is unsustainable to maintain | Hard cutover with **migration tooling + docs + support** provided — never a bare deadline. |

**Default to advisory.** Use compulsory only when risk/maintenance cost justifies forcing it — and
then you owe the consumers tooling, not just a date. In the organisation, any compulsory cutover that
touches a **UAT/Prod** environment is a **the promotion-approval policy** action: it needs explicit human approval, every
time (a green Test smoke is not consent).

## The migration process

1. **Build the replacement** — covers all critical use cases, has a migration guide, is
   production-proven (not just "theoretically better").
2. **Announce and document** — a deprecation notice: status, replacement, removal date (or
   "advisory, no deadline"), reason, and a concrete migration guide.
3. **Migrate incrementally** — one consumer at a time: identify touchpoints → switch to the
   replacement → verify behavior matches (tests/integration) → remove old references → confirm no
   regressions.
4. **Remove the old system** — only after verifying **zero active usage** (metrics/logs/dependency
   analysis): remove code, its tests, docs, and config, then remove the deprecation notices.

**The Churn Rule:** if you own the infrastructure being deprecated, **you** migrate the consumers
(or ship backward-compatible updates that need no migration). Don't announce and walk away.

## Migration patterns

- **Strangler** — run old and new in parallel; route traffic incrementally (0% → canary → 50% →
  100% → remove). Best when you can split traffic and want a reversible, observable cutover.
- **Adapter** — wrap the new implementation behind the *old* interface so consumers keep calling
  the old surface while the backend moves. Best when the interface is stable but the implementation
  must change.

  ```python
  # Adapter: old surface, new implementation
  class LegacyDiaryClient:
      def __init__(self, daemon: SalusDaemonClient) -> None:
          self._daemon = daemon

      def record(self, uuid: str, text: str) -> dict:
          # old signature, delegates to the supported daemon path
          return self._to_old_shape(self._daemon.diary_record(uuid, text))
  ```

- **Feature flag** — switch consumers old→new one at a time behind a flag; flip back instantly if a
  regression appears. Best for a controlled, per-consumer rollout.

## Zombie code

Code nobody owns but everybody depends on — unmaintained, no owner, accreting vulnerabilities.
Signs: no commits in 6+ months but active consumers; no assigned maintainer; failing tests nobody
fixes; vulnerable pinned dependencies; docs referencing systems that no longer exist. **Response:**
either assign an owner and maintain it, or deprecate it with a concrete migration plan. Zombie code
cannot stay in limbo — investment or removal.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "It still works, why remove it?" | Unmaintained working code accumulates security debt and complexity. The cost grows silently. |
| "Someone might need it later." | If it's needed later it can be rebuilt. Keeping it "just in case" costs more than rebuilding. |
| "The migration is too expensive." | Compare migration cost to 2–3 years of maintenance. Migration is usually cheaper long-term. |
| "We'll deprecate after the new system ships." | Removal planning starts at design time. By ship-day you'll have new priorities and it never happens. |
| "Consumers will migrate on their own." | They won't. Provide tooling and docs, or do it yourself (the Churn Rule). |
| "We can run both systems indefinitely." | Two systems doing one job is double the maintenance, testing, docs, and onboarding. |

## Red Flags

- A deprecated system with no replacement available.
- A deprecation announcement with no migration tooling or docs.
- "Soft" deprecation that has been advisory for years with no progress.
- Zombie code with no owner and active consumers.
- New features added to a deprecated system (invest in the replacement instead).
- Deprecating without measuring current usage; removing code without verifying zero consumers.
- A compulsory UAT/Prod cutover taken without the explicit promotion approval.

## Verification

- [ ] Replacement is production-proven and covers all critical use cases.
- [ ] Migration guide exists with concrete steps.
- [ ] All active consumers migrated (verified by metrics/logs).
- [ ] Old code, tests, docs, and config fully removed; no references remain.
- [ ] Deprecation notices removed (they served their purpose).
- [ ] Any UAT/Prod cutover had explicit promotion approval.
