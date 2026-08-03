# PRD Attack Dimensions

Work through all seven dimensions before writing findings. Skipping a dimension is a gap in the roast.

---

## 1. Problem Definition

- Is the problem statement specific enough to know when it is solved?
- Who has this problem? Not "users" — which users, in what context, with what frequency?
- Is this a real problem backed by evidence, or an assumed problem?
- Is the severity of the problem established (frequency × pain)?
- Is the solution disguised as a problem statement? ("users need a dashboard" is a solution, not a problem)
- Could the problem be solved differently? If so, why this approach and not another?

**Flag:** vague problem statements; solutions posing as problems; problems without evidence of existence

---

## 2. Success Criteria

- Are success metrics measurable at the moment of ship?
- Does each metric have a baseline (where we are now) AND a target (where we want to be)?
- Are guardrail metrics defined — metrics that must not regress?
- Can a reasonable person look at these metrics post-launch and say "this succeeded" or "this failed"?
- Are metrics owned? Who is responsible for each?

**Flag:** metrics without baselines; aspirational phrasing ("improve satisfaction"); missing guardrails; metrics that can only be measured months later

---

## 3. Personas

- Are personas defined with specificity — role, context, goal, frequency of use?
- Is there evidence these users exist and have this problem?
- Are there conflicting personas whose needs pull the spec in different directions without reconciliation?
- Are non-target users (explicitly NOT the audience) identified?
- Is there traceability from personas to specific requirements? (if a requirement doesn't serve a persona, why is it there?)

**Flag:** generic persona definitions ("enterprise users"); missing persona-to-requirement traceability; personas that contradict each other without resolution

---

## 4. Assumptions

- What must be true about users, technology, market, or team for this spec to work?
- Are those assumptions stated explicitly?
- Are any assumptions high-risk — likely wrong, or would break the project if wrong?
- Are there dependencies on external systems, APIs, or third parties that are not acknowledged?
- Are there performance, scale, or availability assumptions baked into requirements without being stated?

**Flag:** unstated assumptions that are load-bearing for the spec's logic; assumptions the team has no plan to validate

---

## 5. Scope

- Is non-scope explicitly stated? (What is NOT in this release?)
- Is the scope achievable with the implied team size and timeline?
- Are there requirements embedded in the spec that are actually future phases?
- Does scope creep appear in "nice to have" sections that read as required?
- Are there requirements that logically require other requirements that are out of scope?

**Flag:** implicit scope boundaries; missing non-scope definition; scope that implies unlimited resources; deferred scope that blocks stated scope

---

## 6. Internal Contradictions

- Do goals and non-goals conflict?
- Are there requirements that cannot both be satisfied simultaneously?
- Does the spec say "X is a priority" in one section and "X is not in scope" in another?
- Do different stakeholder requirements conflict without a stated resolution?
- Do the user stories describe behavior that is inconsistent with the stated system constraints?

**Flag:** any two statements in the spec that cannot both be satisfied; priority conflicts across sections

---

## 7. Feasibility

- Can this be built with the implied team size and timeline?
- Are there technical constraints that make a specific requirement impossible as stated?
- Are there regulatory or compliance constraints that would block specific requirements?
- Does the spec assume capabilities the team, product, or infrastructure does not have?
- Are third-party dependencies accurately represented (availability, cost, API limitations)?

**Flag:** requirements that are physically or practically impossible as specified; implicit dependencies on capabilities that do not exist
