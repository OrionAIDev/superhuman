# Code Attack Surfaces

Work through all eight surfaces before writing findings. For each surface: read the relevant code sections, then apply the questions below.

---

## 1. Auth / Permissions

- Can callers access data or operations they shouldn't?
- Is authorization checked at every layer, not just the API boundary?
- Is there privilege escalation — a low-privilege caller reaching a high-privilege operation?
- Are resource IDs validated against the caller's entitlements? (IDOR: can caller A access caller B's resource by guessing an ID?)
- Are auth checks consistent across all code paths, or bypassed in some branches?
- Are admin operations protected separately from user operations?

**Red flags:** bare ID lookups with no ownership check; auth checked only at the controller but not in service/data layers; admin endpoints reachable without admin role

---

## 2. Data Integrity

- Are all external inputs validated before use — type, range, length, format?
- Can state become inconsistent if one write succeeds and a related write fails?
- Are numeric operations safe from overflow, underflow, or precision loss?
- Is string concatenation used where parameterization is required — SQL, shell commands, HTML output?
- Are type coercions safe? (implicit truthy/falsy, string/number ambiguity, language-specific gotchas)
- Are there assumptions about input encoding that could be violated?

**Red flags:** string formatting into SQL or shell commands; missing bounds checks; writes to multiple tables without a transaction; implicit type coercions in conditional logic

---

## 3. Race Conditions / Concurrency

- Can two concurrent callers produce an inconsistent state?
- Is there check-then-act (TOCTOU) on shared resources — read a value, decide, act, but the value could change between read and act?
- Are locks held for the minimum necessary scope?
- Is there a scenario where a lock is never released — exception between acquire and release?
- Are shared mutable data structures accessed without synchronization?
- Are there assumptions about operation ordering that concurrent execution could violate?

**Red flags:** read-modify-write sequences without atomic guarantees; try blocks that could throw before releasing a lock; global mutable state accessed from multiple threads/goroutines/workers

---

## 4. Rollback Safety

- What happens if an operation succeeds partially — step 1 of 3 succeeds, step 2 fails?
- Is the partial state safe to observe or is it corruption?
- Is rollback implemented? Does it handle the same failure modes as the forward path?
- Are external side effects reversible — emails sent, webhooks fired, charges initiated?
- Are compensating actions (undo operations) defined and tested?
- Does the rollback path itself have error handling?

**Red flags:** multi-step operations with no compensation strategy; external notifications fired before the primary operation is confirmed; rollback paths that are untested or unimplemented

---

## 5. Error Handling

- Are all error cases handled or explicitly propagated? No silent swallows.
- Does the code distinguish between expected errors (not found, user error) and unexpected errors (bugs, infrastructure failure)?
- Are error messages safe to expose to callers — no stack traces, secrets, or internal paths in user-facing errors?
- Does the error handling itself have error handling? (handlers that can throw)
- Are errors logged with enough context to debug — not just "error occurred"?
- Are all error branches tested, not just the happy path?

**Red flags:** empty catch blocks; generic exception handling that hides the error type; error messages containing stack traces or file paths; errors that surface internal state to the caller

---

## 6. Null / Zero / Empty / Boundary State

- Are zero-length collections handled where they would cause division or index errors?
- Are null, undefined, or None values handled at every dereference?
- Are integer boundaries handled — minimum, maximum, signed/unsigned wraparound?
- What happens when the database returns no rows when the code expects one?
- Are date and time edge cases handled — timezone, DST, epoch, far-future dates, leap seconds?
- Are empty string and whitespace-only string treated as equivalent where they should be?

**Red flags:** division by collection length without a zero check; direct array index access without bounds check; `first()` or `get()` calls that panic or throw on empty results; unguarded nullable dereferences

---

## 7. Schema Compatibility

- What happens to existing data on a schema change?
- Are there assumptions that older records have fields that did not exist when they were created?
- Is there forward compatibility — can new code read old records — where it is needed?
- Is there backward compatibility — can old code read new records — where it is needed?
- Are migrations reversible?
- Are there hard-coded field references that would break on a rename?

**Red flags:** migrations that drop columns with no data migration; code that assumes all records have a field added in a migration; no migration plan in the diff

---

## 8. Observability Gaps

- Can you tell in production when this code is failing?
- Are there operations with no logging, metrics, or tracing?
- Are errors logged with enough context to debug without a debugger attached?
- Are slow paths — timeouts, retries, circuit breaks — observable?
- Can you distinguish between "no traffic" and "silently failing"?
- Are there background operations or async tasks that could fail silently?

**Red flags:** operations that only log on success; async tasks with no error reporting; operations that produce no output when they fail; health checks that pass even when the system is degraded
