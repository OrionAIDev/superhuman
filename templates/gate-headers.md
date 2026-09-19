# Gate presentation templates

The orchestrator constructs every gate using one of the headers below. Keep these stable — they are cached across many invocations per project.

## Type A — Synchronous gate

```
**[G<n>] <Gate name>** — <one-line summary>

<Briefing — for a stakeholder who hasn't seen this project recently, in plain words with no
requirement IDs, file paths or code names: what the project is and what is being decided; why it
matters now (what the decision unblocks, or what waiting costs).>

<3-5 bullet summary of what was produced/decided>

**Artifact:** `<path>`

**Recommendation:** <PM's pick>, because <reason>.
- <option 1> — <what it leads to>
- <option 2> — <what it leads to>
- <option 3> — <what it leads to>

<AskUserQuestion or open prompt>
```

The briefing is presentation only: it is never copied into the SUPERHUMAN.md decisions log. The
log entry keeps the one-line form in `roles/pm.md` format rule 5.

## Type B — Notification (no pause)

```
[G<n>] <one-liner: what happened>
```

(One line in chat; one line appended to SUPERHUMAN.md.)

## Type C — Switchable

Same as A or B depending on the project's re-eval cadence.

## Drift Type A (G6)

Use the delta-report schema at `templates/delta-report.md.tpl`.
