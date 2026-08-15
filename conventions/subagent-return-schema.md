# Subagent return-schema convention

Applies to every role subagent dispatched by the PM, on any harness. This file is the **single
authoritative definition** of what a subagent hands back. Roles reference it by pointer
(`conventions/subagent-return-schema.md`) — they do not redefine the shape themselves.

## The six ordered fields

Every subagent report carries these fields, in this order:

1. **conclusion** — the headline result or verdict. Specialized role verdicts ride here (QA
   `approved|issues_found`, Surrogate `ACCEPT|ESCALATE`, an Architect option-table's
   recommendation) — see "Specialized verdicts" below.
2. **evidence** — file paths, artifacts, or observations that ground the conclusion. Cite by
   path, not pasted bulk.
3. **commands** — commands or tests actually run, so the PM can reproduce the result.
4. **assumptions** — what was taken as true to reach the conclusion; flag any that are shaky.
5. **risks** — open risks, debts, or concerns, each ideally paired with a mitigation.
6. **next-action** — the recommended next step for the PM.

## Specialized verdicts ride in `conclusion`

QA/Tester pass-fail, Surrogate accept/escalate, Architect option recommendations, and any other
role-specific verdict are **specializations of `conclusion`**, not a replacement schema. A role
still emits all six fields; it just puts its own verdict vocabulary in the first one. Do not drop
the other five fields to make room for a specialized verdict, and do not invent a parallel
"role schema" alongside this one.

## Provider- and role-neutral

This schema names no harness, vendor, or model. Every role, on every supported harness, emits the
same six fields in the same order. Role docs may add role-specific *content* inside a field (e.g.
what "evidence" typically looks like for QA vs. Developer) but must not add, remove, reorder, or
rename the six fields themselves.

## Enforcement is at the PM boundary

This is an advisory convention, not a machine parser. Enforcement happens where the PM reads a
subagent's report: the PM accepts reports that carry this shape and rejects free-form prose that
omits it, asking the subagent to resubmit in schema. No component here validates the report
programmatically — the PM's read is the gate.
