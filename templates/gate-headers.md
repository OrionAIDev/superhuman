# Gate presentation templates

The orchestrator constructs every gate using one of the headers below. Keep these stable — they are cached across many invocations per project.

## Type A — Synchronous gate

```
**[G<n>] <Gate name>** — <one-line summary>

<3-5 bullet summary of what was produced/decided>

**Artifact:** `<path>`

**Recommendation:** <PM's pick>
- <option 1>
- <option 2>
- <option 3>

<AskUserQuestion or open prompt>
```

## Type B — Notification (no pause)

```
[G<n>] <one-liner: what happened>
```

(One line in chat; one line appended to SUPERHUMAN.md.)

## Type C — Switchable

Same as A or B depending on the project's re-eval cadence.

## Drift Type A (G6)

Use the delta-report schema at `templates/delta-report.md.tpl`.
