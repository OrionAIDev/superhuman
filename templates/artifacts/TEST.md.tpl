# Test plan: {{project_name}}

**Created:** {{iso_date}}
**Owner:** QA
**Source design:** `DESIGN.md`

## Coverage targets

| Surface | Target | Notes |
|---|---|---|
| Code: core modules | ≥ {{x}}% line coverage | |
| Code: integration paths | All happy paths + key failure modes | |
| Inference components (if any) | Per § Inference coverage below | |

## Test cases

### TC-1: <name>
- **Requirement:** FR-N / NFR-N
- **Setup:** <one paragraph>
- **Steps:** <numbered>
- **Expected:** <observable>
- **Type:** unit | integration | e2e

<!-- Repeat per test case. -->

## Inference coverage
<!-- For projects with inference-driven components (skills, prompts, LLM-integration). Skip section if not applicable. -->

### Eval suites
| Suite | What it measures | Model under test | Pass criteria |
|---|---|---|---|

### Edge-case coverage
<!-- What weird inputs / states / contexts are covered. -->

### Regression tests
<!-- What past failures are now tests. -->

### Output-quality benchmarks
<!-- For generative components, how we measure quality. -->

## Backup strategy
<!-- For chunks that modify existing code, what gets backed up (per the file-backup safety check at G4). -->

| Files to be modified | Backup location | Restore command |
|---|---|---|

## Test execution

```bash
# Full suite
{{full_suite_command}}

# Per-component (used in Phase 3.1)
{{per_component_pattern}}
```
