---
name: roasting-design-specs
description: Use when user presents a technical design doc, architecture spec, ADR, system design, or API spec and wants adversarial critique of what is wrong before implementation begins. Not for code review; not a replacement for the G3 approval gate.
---

# Roasting Design Specs

Adversarial critique of technical designs, architecture documents, and system specs. Finds what breaks before a line of code is written.

**Do not use for:**
- Implementation code (use roasting-code for that)
- Approving or blocking G3 — this is an on-demand adversarial second opinion, not a gate
- Generating an alternative design — produce findings only

## Attack Dimensions

Before writing findings, read: [attack-dimensions.md](attack-dimensions.md)

**Quick summary by lens:**

- **Assumptions** — what must be true for this design to work that is not stated?
- **Failure modes** — which error paths and edge cases are unhandled?
- **Interfaces** — are all contracts complete? What happens on bad input?
- **Data model** — missing fields, wrong types, unmodeled states?
- **Scalability** — what breaks first under load?
- **Security** — which surfaces are unaddressed?
- **Implementation risk** — is this buildable by a normal team?
- **Test coverage** — what cannot be tested as designed?

## Output

Follow the shared roast framework: [../roasting-shared/roast-framework.md](../roasting-shared/roast-framework.md)

Produce: verdict → findings grouped by severity. Every finding states what fails, why (cite the specific design section), impact, and a concrete fix.

## Calibration

- **Critical** bar: shipped as-is, this design would fail in production — data loss, unavailability, security breach, or total functional failure.
- If a required section is absent (e.g., no error handling section at all), that absence is itself a finding.
- One well-evidenced finding from the document beats five speculative risks.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "The design is elegant, it'll work." | Elegance is not a failure analysis. Walk assumptions / failure-modes / interfaces before concluding. |
| "The happy path is clear." | The happy path is never where designs fail. The roast targets the unstated error paths. |
| "Security is out of scope for a design doc." | Unaddressed security surfaces are a design finding, not a later problem. |
| "It's buildable, so ship the design." | A buildable design still loses data on an unhandled partial-write path — buildability says nothing about failure-mode, interface, or data-model coverage (the other seven lenses). |
| "A missing section is fine — they'll add it." | An absent required section (e.g., no error-handling) is itself a finding. |

## Red Flags

- A verdict issued without walking all 8 lenses.
- Findings not tied to a specific design section.
- Treating the roast as a G3 gate (it is an on-demand second opinion, never a gate).
- Proposing an alternative design instead of findings.
- Speculative load/scale failures the document itself does not create.
