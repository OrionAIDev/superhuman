# Definition of Done

A standing, project-wide bar that every chunk must clear before it counts as done. Unlike
**acceptance criteria**, which vary per chunk and answer "did we build the right thing?", the
Definition of Done (DoD) is the same every time and answers "is this finished to our standard?".

In superhuman the DoD is a **checklist reference**, not a gate of its own. It is applied:

- by the **Developer** during self-review (`roles/developer.md`) before reporting `DONE`;
- by **QA** at **G5** (`phases/3.1-test-review.md`) as the coverage-review floor;
- by the **PM** at the pre-G8 sanity step (`phases/4-acceptance.md`) across the whole delivery.

## Definition of Done vs. Acceptance Criteria

| | Acceptance criteria | Definition of Done |
|---|---|---|
| Scope | Specific to one chunk (PLAN.md) | Applies to every chunk |
| Changes | Different for each chunk | Fixed and reused |
| Answers | "Did we build *this thing*?" | "Is it *ready*?" |
| Owner | Set when planning the chunk (G3) | Defined once for the project (here) |
| Example | "the importer writes a validated record via the service" | "Tests pass, no regressions, docstrings present, promotion policy respected" |

The two are complementary. A chunk is done only when **its** acceptance criteria are met **and**
this standing bar is satisfied. Skipping either leaves work that looks finished but is not.

## The standing checklist

Apply this to every chunk before declaring it done.

### Correctness
- [ ] All acceptance criteria for the chunk (PLAN.md) are met.
- [ ] Code runs and behaves as intended, verified at runtime — not just imported or typechecked.
- [ ] New behavior is covered by a test that fails without the change and passes with it (TDD red-first).
- [ ] Existing tests still pass; no regressions introduced.
- [ ] Edge cases and error paths are handled, not just the happy path.

### Quality
- [ ] Code reveals intent through naming and structure; comments explain *why*, not *what*.
- [ ] No duplicated business logic; no dead code, debug output, or commented-out blocks left behind.
- [ ] Changes are scoped to the chunk; no unrelated refactors snuck in (scope growth → `DONE_WITH_CONCERNS`, not silent).
- [ ] **`conventions/python.md` satisfied** where Python is in effect: Google-style docstrings on every module/class/method/function, CLI via `argparse`, no bare `except:`, stdlib→third-party→local import order.
- [ ] **Library-first (`preferred-libraries.md`)**: no hand-rolled implementation of a capability a preferred/established library already covers, absent a stated reason.
- [ ] Linting/formatting pass (`ruff` where configured).

Depth behind Quality lives in `references/roasting-code/` (adversarial correctness/architecture
review) and `references/roasting-shared/roast-framework.md` (severity + finding structure).

### Integration
- [ ] Change works with the rest of the system, not just in isolation.
- [ ] Migrations, config changes, and feature flags are accounted for.
- [ ] Backward compatibility considered for any public interface / API / tool-contract change
      (stable exit codes, JSON stdout shape, CLI flags).

### Documentation
- [ ] Public interfaces, tool surfaces, and user-facing behavior are documented.
- [ ] Decisions worth preserving are recorded in the project's `DECISIONS.md` artifact.
- [ ] Docs describe the **current state** in timeless language, not the change history.

### Ship-readiness
- [ ] **Secrets and sensitive data**: no secrets, credentials, tokens, or regulated personal data
      committed to a repo. Where the organisation provides a sensitive-data commit guard, it is
      respected and never bypassed.
      Clinical user data is read/written only through the supported daemon path, never improvised
      file writes.
- [ ] **Security** of any untrusted input, auth, or data handling is reviewed (the pre-acceptance
      security lens in `phases/3.3-preflight-review.md`).
- [ ] Observability is in place for new critical paths (structured logs / stable error codes).
- [ ] A rollback path exists for anything risky (archive-never-delete; git tags/branch).
- [ ] **`promote_into` policy**: any promotion into a rung whose policy names an approver has that
      approver's explicit,
      in-context human "yes". A green Test smoke is NOT consent. Autonomous mode never stands in
      for this approval.

## How to apply

- **Per chunk (Developer + QA/G5):** confirm Correctness and Quality before the chunk is checked off.
- **Per delivery (PM pre-G8):** confirm Integration, Documentation, and Ship-readiness across the
  whole declared artifact set before acceptance.

Tailor this list to the project **once**, then reuse it unchanged. A DoD renegotiated every chunk
is not a Definition of Done.

## Red Flags

- "It's done, I just haven't run it yet." Unverified work is not done.
- "Tests pass" used as a synonym for done while docstrings, regressions, or runtime verification are skipped.
- A different bar applied under deadline pressure.
- Acceptance criteria treated as the whole bar, with no standing quality floor.
- "Done" declared before the human promotion approval on a change that needs it.
