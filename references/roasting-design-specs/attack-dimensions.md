# Design Spec Attack Dimensions

Work through all eight lenses before writing findings. Each lens targets a different class of failure.

---

## Assumptions Lens

- What must be true about the environment, dependencies, or team for this design to work?
- Are those assumptions stated? Are they reasonable?
- What breaks if a single key assumption is wrong?
- Are latency, throughput, and availability assumptions made explicit?
- Does the design depend on team knowledge or institutional context that is not documented?

**Look for:** design choices that only make sense given unstated constraints; architectural decisions that would be wrong on a different stack or scale

---

## Failure Modes Lens

- What happens when the primary happy path fails?
- Are error states modeled, or only success states?
- What happens on partial failure — some steps succeed, others fail?
- Is there a defined behavior for every external system being unavailable?
- What is the behavior on timeout, retry exhaustion, or circuit break?
- Are there failure modes that would leave the system in an inconsistent state?

**Look for:** designs that describe only the success path; absence of error state diagrams or error handling sections; no retry or timeout policy

---

## Interface Completeness Lens

- Are all API and interface contracts fully specified — inputs, outputs, and errors?
- What happens when an interface receives invalid input?
- Are there interfaces that accept `*` or `any` where specificity is needed?
- Are side effects of each operation documented?
- Are implicit contracts stated — ordering assumptions, idempotency assumptions, caller obligations?
- Are versioning and backward compatibility requirements defined for each interface?

**Look for:** interfaces described by example only; absence of error codes or error contract; implicit caller obligations not in the spec

---

## Data Model Lens

- Are there missing fields — things required to implement the described behavior that are not in the model?
- Are field types correct — strings vs. enums, IDs vs. objects, nullable vs. required?
- Are there unmodeled states — combinations of field values that are valid in the model but invalid in the domain?
- Is the data model versioned? What happens to existing data on schema change?
- Are soft-delete, audit trail, and timestamp requirements reflected?
- Are relationships and cardinality constraints defined?

**Look for:** models that cannot represent all the states the spec describes; missing null/optional distinctions; no migration strategy for schema changes

---

## Scalability Lens

- What is the expected read/write volume? Is the design appropriate for it?
- Where are the bottlenecks? What breaks first under 10× load?
- Is there unmodeled state that grows with user count or data volume?
- Are caching strategies defined where needed?
- Are there N+1 query patterns or unbounded list fetches in the described data flows?
- Is pagination defined for all list operations?

**Look for:** designs with no stated capacity targets; synchronous calls that will block under load; global locks or single points of coordination

---

## Security Lens

- Where are the trust boundaries in this design?
- Is authorization checked at the right layer — not just at the API boundary?
- Is sensitive data identified and is its storage and transit protection specified?
- Are inputs validated at all system entry points?
- Are there injection risks in the described data flows — SQL, command, SSRF, template injection?
- Are secrets and credentials described in a way that implies unsafe storage or transmission?

**Look for:** designs that assume the API caller is trusted; sensitive data flowing through components without protection; no threat model or trust boundary diagram

---

## Implementation Risk Lens

- Can a normal engineering team build this as described?
- Are there novel technical choices where simpler alternatives exist and no justification is given?
- Is the estimated complexity realistic given the design scope?
- Are there dependencies on external teams, APIs, or services that could block implementation?
- Is the proposed technology stack appropriate and supported by the team's expertise?
- Are there coordination dependencies between components that are not sequenced in the design?

**Look for:** designs that require capabilities the team does not have; critical path dependencies that are not acknowledged; exotic tech choices without rationale

---

## Test Coverage Lens

- What is the test strategy for the designed behavior?
- Are there behaviors that cannot be tested as designed — e.g., distributed race conditions with no test hook?
- Are integration points testable in isolation?
- Is there a plan for testing error paths, not just the happy path?
- Are acceptance criteria for each major component testable from outside the component?
- Is there a strategy for testing performance characteristics that the design claims?

**Look for:** designs with behaviors that are only observable in production; no seam for injecting failures; performance claims with no defined test approach
