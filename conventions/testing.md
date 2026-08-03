# Testing conventions

Applies to all projects.

## Coverage applies to two surfaces

### Code

| Test type | When required | Pattern |
|---|---|---|
| Unit | Always | One test file per source file; same directory under `tests/` |
| Integration | When components interact non-trivially | `tests/integration/` |
| End-to-end | When user-visible flows exist | `tests/e2e/` |

Default coverage target: ≥ 80% line coverage on core modules. QA sets the actual number per project in TEST.md at G4.

### Inference-driven components

For skills, prompts, LLM-integration code, or any component whose behavior is determined by model inference:

| Test type | Purpose |
|---|---|
| **Eval suite** | A bank of inputs with expected output shape/properties; runs against the model the component will actually use (or a documented proxy). |
| **Edge-case coverage** | Weird inputs, empty contexts, very long contexts, adversarial inputs. |
| **Regression tests** | Past failures preserved as eval cases. Never delete a regression test; if it becomes obsolete, mark and explain. |
| **Output-quality benchmark** | For generative output, a scoring rubric (LLM-as-judge with documented rubric, or human review). |

QA writes inference-test plans in TEST.md under "Inference coverage". Tester executes them.

## TDD discipline

Per the test-driven-development reference: write failing test first, verify it fails, implement minimally, verify it passes, commit. Skipping the red step = bug. See `references/test-driven-development/`.

## Anti-patterns

See `references/test-driven-development/testing-anti-patterns.md`.
