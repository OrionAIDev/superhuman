---
name: {{NAME}}
description: {{DESCRIPTION}}
model: {{MODEL}}
{{EFFORT_LINE}}{{GENERATED_MARKER}}
---

This agent definition carries only a model and (when configured) a reasoning
effort. It does not define a role — the role for this dispatch is the leading
block of the prompt (`roles/<name>.md` or the `superhuman-dispatch: non-role`
marker, per `adaptation/dispatch.md`). Follow that block as your instructions
for this dispatch, exactly as if it were your system prompt.

Do not use this agent directly outside a superhuman dispatch. It is
regenerated from `~/.superhuman/profile.yaml` by
`scripts/superhuman_profile.py models install-agents --harness claude-code`
every time the operator's tier configuration changes — a hand edit here will
be silently overwritten on the next run, and (per the generated-by marker
above) is exactly the class of file `models install-agents` is allowed to
replace without asking.
