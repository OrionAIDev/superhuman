---
name: roasting-code
description: Use when user brings code from outside Superhuman for adversarial critique — legacy code, PRs from colleagues, OSS libraries, or codebases being evaluated. Stance is "break confidence, not validate." Distinct from requesting-code-review, which reviews code just built in the current Superhuman session.
---

# Roasting Code

Adversarial implementation critique for externally-sourced code. The stance: **assume the code can fail until the evidence says otherwise.**

**Trigger distinction:**
- `requesting-code-review` → code just built inside Superhuman; dispatch a constructive quality reviewer
- `roasting-code` → code brought from OUTSIDE Superhuman; prove it should not ship as-is

**Do not use for:**
- Code written in the current Superhuman session — use `requesting-code-review`
- OWASP or supply chain security deep-scan — that belongs in a dedicated security-review skill
- Generating a rewrite — produce findings only

## Attack Surfaces

Before writing findings, read: [attack-surfaces.md](attack-surfaces.md)

**Quick summary:**

1. **Auth/permissions** — can callers reach what they shouldn't?
2. **Data integrity** — are inputs validated? Can state become inconsistent?
3. **Race conditions** — concurrent access, TOCTOU, shared mutable state?
4. **Rollback safety** — what happens on partial failure?
5. **Error handling** — are all paths handled and observable?
6. **Null/zero/empty/boundary state** — are edge values handled?
7. **Schema compatibility** — what breaks on schema change?
8. **Observability gaps** — can you debug failures in production?

## Output

Follow the shared roast framework: [../roasting-shared/roast-framework.md](../roasting-shared/roast-framework.md)

End with a clear ship verdict: **ship** / **do not ship** / **ship with fixes listed above**.

Every finding must state: what can fail (concrete scenario), why the code is vulnerable (cite file/line), likely impact, and a concrete fix.

## Calibration

- Exclude style, naming, and formatting — only flag what actually fails or fails to handle a real scenario.
- Bar: "would a staff engineer approve this for production without changes?"
- All findings must be defensible from the code provided. No invented execution paths.
- One strong finding from the code beats five speculative risks.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "The author is senior — it's probably fine." | Provenance is not evidence. The bar is "would a staff engineer approve *this* for production," not who wrote it. |
| "It's just a small snippet, low risk." | Small snippets hide the worst auth and boundary bugs. Size is not safety. |
| "No obvious bug, so ship it." | Absence of an obvious bug is not presence of safety. Walk all 8 attack surfaces before concluding. |
| "I'll flag everything to be safe." | Ten nitpicks bury the one critical. One strong finding beats five speculative ones. |
| "The tests pass, so it's correct." | Passing tests prove the tested paths; roasting targets the untested race / rollback / boundary paths. |

## Red Flags

- A "ship" verdict with zero findings and no documented walk of the 8 attack surfaces.
- Findings that cite no file/line ("this looks risky") — every finding must be defensible from the code.
- Invented execution paths the provided code cannot actually reach.
- Style/naming complaints promoted to critical findings.
- Producing a rewrite instead of findings.
