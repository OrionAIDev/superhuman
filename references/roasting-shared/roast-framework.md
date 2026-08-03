# Shared Roast Framework

All three Superhuman roast skills share this spine. Read this before producing any roast output.

## Tone

- Elite. Concise. Skeptical. Diagnostic. Pragmatic.
- Harsh on artifacts, never personal toward the user.
- No theatrical mockery. No sycophancy. No hedging.
- **One strong finding beats five weak ones.**

## Severity Model

| Level | Definition |
|---|---|
| **critical** | Causes failure, data loss, security breach, or blocks the project entirely |
| **major** | Significant risk or rework required; should not proceed without addressing |
| **minor** | Worth fixing before production; not blocking |
| **nitpick** | Advisory only; caller decides |

## Per-Finding Structure

Every finding must answer all four:

1. **What fails** — concrete scenario, not hypothetical
2. **Why** — cite specific section, line, or claim in the artifact
3. **Impact** — what breaks and how badly
4. **Fix** — specific change (not "improve this" — name the actual change)

A finding missing any of these four is incomplete.

## Output Structure

```
## Verdict
[1-2 sentences. Direct overall quality assessment. No hedging.]

## Findings

### Critical
- **[Title]**: [What fails]. [Why — cite artifact]. [Impact]. **Fix:** [Specific action.]

### Major
[same structure]

### Minor
[same structure]

### Nitpick
[same structure — omit section entirely if none]

## What's Not Worth Worrying About
[Optional. Only include when the caller asked about something that isn't a real issue.]
```

## Categorize findings (prefix rule)

Label every finding with a severity prefix so the caller knows what is required vs. optional. The
prefixes map onto the severity model above; use them in the review output and in any inline review
the PM dispatches (e.g. `phases/3.1-test-review.md`, `phases/3.3-preflight-review.md`).

| Prefix | Meaning | Caller action | Maps to |
|---|---|---|---|
| **Critical:** | Blocks the work — security breach, data loss, broken/again-unshippable behavior | Must fix before proceeding | critical |
| *(no prefix)* | Required change | Must address | major |
| **Optional:** / **Consider:** | Worth doing, not required | Caller decides | minor |
| **Nit:** | Cosmetic / style preference | May ignore | nitpick |
| **FYI** | Informational only | No action | — |

**Lead with what matters.** Order findings by leverage: correctness and security first, then
structural regressions and missed simplifications, then everything else. A few high-conviction
findings beat a long list. **If you have one structural problem and ten nits, the structural
problem *is* the review** — never bury it under cosmetics.

## Calibration Rules

- Every finding must be defensible from the artifact. No invented scenarios.
- Exclude style, naming, and formatting unless they directly cause misunderstanding.
- Speculative "what if someone does X" is only valid if the artifact itself creates that risk.
- When in doubt about severity: lower is better. A real minor beats a speculative major.
- "Every finding must include concrete fix language." Vague critique ("this is unclear") is not a finding.

## Clarifying Question Gate

If the artifact is too incomplete to critique honestly (e.g., a 3-line PRD, a sketch with no context):
- Do NOT fabricate findings.
- State exactly what is missing.
- Ask the specific questions that must be answered before a useful roast is possible.
