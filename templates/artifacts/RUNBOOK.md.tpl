# Runbook: {{project_name}}

**For:** on-call engineers and operators.

## Service summary
{{one_paragraph_what_runs_where}}

## Health checks

| Check | Where | What "healthy" looks like |
|---|---|---|

## Common alerts

### Alert: {{alert_name}}
- **Trigger:** {{condition}}
- **Severity:** {{P1_P2_etc}}
- **First steps:** {{numbered}}
- **Escalation:** {{who_when}}

<!-- Repeat per alert. -->

## Rollback procedure

```bash
{{commands}}
```

## Restart / restore

```bash
{{commands}}
```

## Known issues
{{list_with_jira_links_or_similar}}
