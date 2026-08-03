---
name: roasting-requirements
description: Use when user presents a PRD, requirements doc, product spec, or feature brief and wants adversarial critique of whether it is good product thinking. Not for checking completeness (use brainstorming spec-reviewer for that); this challenges problem definition, assumptions, success criteria, scope, and feasibility.
---

# Roasting Requirements

Adversarial critique of PRDs, requirements documents, and product specs. The question is not "is this complete?" — that is the spec reviewer's job. The question is: **is this actually good product thinking?**

**Do not use for:**
- Specs produced inside the current Superhuman session (those go through G2 review with the PM)
- Generating a better PRD — produce findings only; do not drift into rewriting

## Attack Dimensions

Before writing findings, read: [attack-dimensions.md](attack-dimensions.md)

**Quick summary:**

1. **Problem definition** — is the problem real, specific, and worth solving?
2. **Success criteria** — measurable? With a baseline and a target? Guardrails defined?
3. **Personas** — real users with real pain, or generic archetypes?
4. **Assumptions** — what must be true for this to work? Are they stated?
5. **Scope** — is non-scope explicit? Is scope creep embedded in the requirements?
6. **Contradictions** — do goals, non-goals, and constraints conflict?
7. **Feasibility** — can this actually be built as described?

## Output

Follow the shared roast framework: [../roasting-shared/roast-framework.md](../roasting-shared/roast-framework.md)

Produce: verdict → findings grouped by severity (critical / major / minor / nitpick). Every finding states what fails, why (cite the specific PRD section), impact, and a concrete fix.

## Calibration

- **Critical** bar: shipped as-is, this would cause real-world harm or project failure (e.g., undefined success means no one knows if it worked; undefined personas means the product gets built for the wrong user).
- **Falsifiability test**: if a claim in the spec cannot be verified at launch, flag it as at least major.
- Concrete fix language is required. "This needs improvement" is not a finding.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "The PRD is detailed, so it's good." | Detail is not good product thinking. A long PRD can still lack a real problem or falsifiable success criteria. |
| "Success criteria are implied." | Implied success means no one can tell at launch whether it worked. Flag as at least major. |
| "The personas are obvious." | Generic archetypes hide that the product may be built for the wrong user. Demand real users with real pain. |
| "It's not my place to challenge product scope." | Roasting requirements is exactly that place. Unchallenged scope creep ships as-is. |
| "I'll just draft a better PRD." | The skill produces findings, not rewrites. Drifting into authoring loses the adversarial lens. |

## Red Flags

- A finding with no cited PRD section.
- Passing a spec whose success criteria cannot be verified at launch.
- Softening a "critical" because the document is polished.
- Rewriting the PRD instead of listing findings.
- Skipping the falsifiability test on stated claims.
