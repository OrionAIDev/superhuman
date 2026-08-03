# Source-cited conventions

Applies whenever the Developer writes **framework- or library-specific** code — API calls,
config, decorators, client setup — for any project in effect. Ground the code in the library's
**official documentation for the version actually installed**, not in training-data recall.

This is the layer that sits *on top of* `preferred-libraries.md`: that file names **which**
library to reach for; this convention says **then verify the API surface against the current
version before you type**, and cite it. It directly reduces the framework-hallucination class
that TDD only catches after the fact.

## When it applies (and when it doesn't)

Apply when writing code whose correctness depends on a specific library/version: FastAPI routes
and dependencies, `O365`/`google-*` client calls, `httpx` request patterns, `pydantic` models,
`FastMCP` tool definitions, `argparse` idioms, DB/migration APIs.

Skip for version-independent work: pure logic (loops, conditionals, data structures), renames,
typo fixes, moving files, or when the user explicitly wants speed over verification.

## The loop: DETECT → FETCH → IMPLEMENT → CITE

### 1. Detect stack + version
Read the dependency file before writing framework code and state what you found:

| Dependency file | What to read off it |
|---|---|
| `pyproject.toml` / `requirements.txt` / `uv.lock` | language + each framework and its **pinned version** |
| `package.json` / lockfile | same, for JS/TS |
| `go.mod`, `Cargo.toml`, `Gemfile.lock`, … | same, per ecosystem |

**Which library to reach for in the first place** is a separate question, answered by your
project's declared conventions — not by this file and not from memory. If the profile declares a
preferred-libraries overlay:

```yaml
# ~/.superhuman/profile.yaml
conventions:
  - python
  - testing
  - "~/.superhuman/conventions/preferred-libraries.md"
```

…then that overlay is the authoritative pick list: consult it before proposing any library, and
default to a listed pick rather than hand-rolling. With no overlay declared, prefer the standard
library, then a well-maintained package with an active release history — and say which you chose
and why.

If a version is missing or ambiguous, **ask**. The version determines which pattern is correct, and
guessing it is exactly the failure this convention exists to prevent.

### 2. Fetch the specific official doc
Fetch the exact page for the feature (via `<dispatch:*>` web fetch where available), not the
homepage. Source hierarchy, most authoritative first:

1. Official documentation for the library, at the installed version.
2. Official changelog / release notes for the installed version.
3. Language/stdlib reference (`docs.python.org`).

**Never authoritative** (never cite as the primary source): Stack Overflow, blog posts/tutorials,
AI-generated summaries, or your own training data — verifying *that* is the whole point.

Your organisation's own integration runbooks are authoritative for **local** wiring (ports,
environment variables, per-environment promotion) and may be cited alongside the upstream doc —
never instead of it.

### 3. Implement the documented pattern
Use the API signatures from the docs, not from memory. If the docs deprecate a pattern, don't use
it. If existing project code conflicts with the current docs, **surface the conflict** with both
options and a recommendation — don't silently pick one.

### 4. Cite the source
Every framework-specific decision gets a citation the reviewer can check:

- In a code comment above the call: a full deep URL (prefer an anchored link).
- In the PR / chunk status report for non-obvious choices, quoting the passage that justifies it.
- If **no** official doc can be found, flag it explicitly rather than hedge:

  ```
  UNVERIFIED: no official doc found for this pattern; based on training data, may be outdated.
  Verify before this reaches a rung that requires human approval.
  ```

Honesty about what you could not verify beats false confidence.

## Verification (Developer self-review add-on)

- [ ] Versions identified from the dependency file.
- [ ] Official docs fetched for each framework-specific pattern used.
- [ ] All primary sources are official docs (not blog/SO/training recall).
- [ ] Non-trivial framework decisions carry a full-URL citation.
- [ ] No deprecated APIs (checked against the version's migration notes).
- [ ] Doc-vs-existing-code conflicts surfaced to the PM, not silently resolved.
- [ ] Anything unverifiable is flagged `UNVERIFIED`.
