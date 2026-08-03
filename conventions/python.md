# Python conventions

Applies to any project where Developer or QA is writing Python.

## Documentation

- **Google-style docstrings** on every module, class, method, and function. Non-negotiable; QA fails the review if any are missing.
- Module docstrings explain the module's purpose in 1-3 sentences.
- Class docstrings explain the class's purpose and list public attributes if non-obvious.
- Method/function docstrings include `Args:`, `Returns:`, and `Raises:` sections per Google style. Type info goes in type hints, not duplicated in docstrings.
- README.md is a declared artifact (canonical artifact catalog: always-required).

## CLI

- Use `argparse` (stdlib) for CLI surface. Use `click` only if the project explicitly opts in at G3 (declared artifact set or design notes).
- Subcommand naming: verb-noun (`add-user`, `list-projects`).
- Every CLI must support `--help` and `--version`.

## Style and packaging

- PEP 8.
- Type hints on public functions/methods (`def foo(x: int) -> str:`).
- Prefer stdlib + well-supported third-party libraries (skill-design principles rule 2 from CLAUDE.md).
- `pyproject.toml` over `setup.py` for new projects.

## Class definition idioms

Reach for the lightest tool that fits. The decision order is **dataclass → attrs → pydantic**; escalate only when the current tier cannot express what you need.

- **`dataclass` — the default for plain data holders.** Default to `frozen=True, slots=True`: frozen gives value-object immutability (hashable, safe to share, no accidental mutation), and slots cuts per-instance memory and blocks typo'd attribute assignment. Drop `frozen` only when a method must mutate state in place; drop `slots` only when you need `__dict__` (e.g. caching via `functools.cached_property`, which is incompatible with slots).

  ```python
  from dataclasses import dataclass

  @dataclass(frozen=True, slots=True)
  class ChunkResult:
      """Outcome of one implementation chunk.

      Attributes:
          chunk_id: 1-based index of the chunk in PLAN.md.
          passed: whether all tests for the chunk passed.
          tokens: tokens spent implementing the chunk.
      """
      chunk_id: int
      passed: bool
      tokens: int
  ```

- **`attrs` — when you need validators or converters.** Use it once construction must enforce invariants (range checks, normalization, cross-field validation) that a dataclass cannot express without a hand-written `__post_init__`. `attrs` keeps the slots/frozen ergonomics while giving declarative `validator=`/`converter=` hooks. Don't pull it in just for features `dataclass` already covers.

  ```python
  import attrs

  @attrs.frozen
  class Budget:
      """A token budget with a validated positive ceiling.

      Attributes:
          max_tokens: hard ceiling; must be > 0.
      """
      max_tokens: int = attrs.field(validator=attrs.validators.gt(0))
  ```

- **`pydantic` — only when (de)serialization drives the model.** Justified when the type is the boundary contract for untrusted input — parsing/validating external JSON, API request/response bodies, config files — and you want coercion + schema generation for free. It is the heaviest option (import cost, runtime coercion, a metaclass): do not use it for purely internal data where a frozen dataclass suffices.

  ```python
  from pydantic import BaseModel, Field

  class GoalConfig(BaseModel):
      """Parsed GOAL.md run configuration (external, validated input).

      Attributes:
          max_iters: iteration ceiling; 1–25.
          min_delta: minimum fitness improvement to KEEP an iteration.
      """
      max_iters: int = Field(ge=1, le=25)
      min_delta: float = Field(gt=0)
  ```

## Anti-patterns (QA fails review on these)

- LLM-generated code in irreversible / safety-critical paths (money, credentials, deletion, external sends, auth checks). Per CLAUDE.md skill-design rule 5.
- Bare `except:`.
- Mutating default arguments.
- Print-debugging left in committed code.
