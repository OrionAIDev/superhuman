# Dispatch adaptation layer

This file is the ONLY place that knows about platform-specific tool names. When porting superhuman to a different harness (e.g., Claude Code → OpenClaw), edit only this file.

> **Every `<dispatch:agent>` call carries `conventions/subagent-constraints.md` verbatim**, in the
> `declared conventions` position. A subagent inherits its parent's TOOLS but not its parent's
> ENVIRONMENT — the harness preamble about worktrees, shared stash stacks and forbidden operations
> reaches the orchestrator and stops there. Restating it per-brief from memory failed on
> 2026-09-09 and lost nothing only by luck; that file records the incident and the block to paste.

> For the *patterns* these symbols compose into — direct dispatch, sequential pipeline, parallel
> fan-out with merge, research isolation — and the anti-patterns to avoid, see
> `references/orchestration-patterns.md`. This file resolves the symbols; that catalog governs how
> they're wired together (PM is the only orchestrator; seam-crossing parallelism → G9).

## Symbolic names used throughout superhuman

| Symbol | What it does | Claude Code | OpenClaw (TBD; port-time fill-in) |
|---|---|---|---|
| `<dispatch:agent>` | Run a subagent with a focused prompt and isolated context | `Agent` (subagent_type: the dispatch class's tier agent — see "Model-tier selection" below; no `model=`). When `templates/hooks/claude-code/pre-tool-use-role-gate` is installed (chunk 7a, optional), a dispatch that opens with neither a role file's full, unedited content nor the literal `superhuman-dispatch: non-role` line is refused before the subagent starts -- a discipline aid catching a forgotten or edited role block, not a security control; a caller deliberately trying to defeat it can (see `docs/fleet-observation.md`'s role-first discipline gate section). | `sessions_spawn(runtime="subagent", model=..., thinking=..., task=...)` — agentId must be one the instance permits (often only `main`); convey the role via the prompt, never a role-named agentId; always pass an explicit working `model`. See "OpenClaw `sessions_spawn` constraints" below. |
| `<dispatch:ask>` | Present a multiple-choice gate to the user | `AskUserQuestion` | (no direct equivalent — degrade to assistant chat message with numbered options; user replies via chat reply) |
| `<dispatch:read>` | Read a file by path | `Read` | `read(path="...")` |
| `<dispatch:write>` | Write a file (full content) | `Write` | `apply_patch` with `*** Add File: <path>` (multi-file patch format) |
| `<dispatch:edit>` | Edit a file (diff) | `Edit` | `apply_patch` with `*** Update File: <path>` |
| `<dispatch:bash>` | Run a shell command | `Bash` (POSIX) / `PowerShell` (Windows) | `exec(command="...", workdir="...")` |
| `<dispatch:grep>` | Search file contents | `Grep` | `exec(command="rg ...")` — no dedicated grep tool in OpenClaw; use ripgrep via exec |
| `<dispatch:glob>` | Find files by pattern | `Glob` | `exec(command="find ...")` — no dedicated glob; use find via exec |
| `<dispatch:task_create>` | Create a task in the task tracker | `TaskCreate` | (no direct equivalent — OpenClaw lacks a discrete task-tracker tool comparable to Claude Code's TaskCreate. Degrade to appending a checklist item to SUPERHUMAN.md `## Chunk log` table directly via apply_patch.) |
| `<dispatch:task_update>` | Update a task | `TaskUpdate` | (no direct equivalent — same degradation as task_create: apply_patch the SUPERHUMAN.md chunk log row to update status.) |

## OpenClaw `sessions_spawn` constraints

Two constraints surfaced by a live OpenClaw smoke that the orchestrator must honor when `<dispatch:agent>` maps to `sessions_spawn`:

1. **agentId allowlist — never use a role-named agentId.** An OpenClaw instance may restrict which agent ids can be spawned; a typical deployment permits only `agentId="main"`, so `sessions_spawn(agentId="architect", …)` is **rejected**. Pass an agent id the instance permits (often just `main`) and **convey the role through the dispatched prompt** — the role prompt (`roles/<role>.md`) goes in as the leading block per the cache-stable ordering (`role prompt → declared references → declared conventions → cached artifact slice → task brief`). The role is a property of the prompt, never of the agent id.

2. **Always pass an explicit `model`.** A spawned subagent that inherits default/routing model selection can land on a provider the box can't currently use — in the smoke, subagents defaulted to Anthropic `claude-opus-4-7`, which was out of credits, so every dispatch failed until an explicit `model=` was supplied. On OpenClaw, set `model=` on every `sessions_spawn` per the tier table below. If the tier's preferred provider is unavailable (auth expired, out of credits), **fall back to another acceptable most-capable model** (e.g. a Gemini Pro id) rather than letting the dispatch fail — the model must satisfy the role's tier, not a specific vendor.

## Surrogate-user dispatch pattern (v0.2.0)

The surrogate-user is a **role**, not a new dispatch verb — dispatch it through the existing `<dispatch:agent>` symbol, passing `roles/surrogate-user.md` as the leading block of the prompt (per the cache-stable ordering `role prompt → declared references → declared conventions → cached artifact slice → task brief`). In autonomous mode it answers a Type A gate in place of `<dispatch:ask>`.

- **Claude Code:** `Agent(subagent_type:"superhuman-tier-standard-subagent", prompt:<role + gate context>)` — no `model=` (the tier agent pins model + effort).
- **OpenClaw:** `sessions_spawn(agentId="main", model=<explicit standard-tier id>, thinking=<standard-tier effort>, task=<role + gate context>)` — honor BOTH `sessions_spawn` constraints above: never a role-named agentId (use `main`), and always an explicit working `model` (fall back to another acceptable standard-tier model if the preferred provider is down).

The surrogate returns the structured verdict defined in `roles/surrogate-user.md` "Output contract"; the PM parses `ACCEPT`/`ESCALATE` and either proceeds autonomously or surfaces the real gate to the human.

## Rules

- Role prompts and phase recipes MUST use the symbolic names, not platform-specific names.
- At session start, the orchestrator reads this file and substitutes symbols in its working memory.
- If a symbol has no equivalent on the current harness, the orchestrator surfaces a G10-style escalation: "This feature requires `<dispatch:X>` which is not available; consider degrading to <fallback>?"

## Adding a new dispatch symbol

1. Add a row to the table above with description and Claude Code equivalent.
2. Add the OpenClaw equivalent (or `_not available_` if there isn't one).
3. Update role prompts / phase recipes to use the new symbol.
4. Bump VERSION; update CHANGELOG.md.

## Model-tier selection

Independent of platform mapping; uses the cheapest model that can handle a role per §9 rule 7 of DESIGN.md. **Every dispatch resolves BOTH a model and a reasoning effort** (roadmap#275). A dispatch that names neither inherits the parent session's model *and* effort — measured on 2026-09-21 as 801 of 802 subagents running at exactly the parent's effort, 62% of them at high. Never rely on default/inherited routing for either.

Three layers, each in one place:

| Layer | Lives in | Harness-specific? |
|---|---|---|
| Role → dispatch class | `adaptation/role-tiers.json` (authoritative; rendered below) | No |
| Tier → model + effort | the operator's `~/.superhuman/profile.yaml` `models:` block | No |
| Dispatch class → harness call | this file, per-harness sections below | Yes |

A **dispatch class** is a tier, optionally at that tier's raised effort: `most_capable`, `most_capable+raised`, `standard`, `cheap`.

### Role → dispatch class

Rendered from `adaptation/role-tiers.json`; `tests/test_role_tiers.py` fails if this table and the JSON disagree.

| Role / duty | Default | Opt-in (reason required) |
|---|---|---|
| PM | `most_capable` | `most_capable+raised` |
| Architect | `most_capable` | `most_capable+raised` |
| code-quality review | `most_capable` | `most_capable+raised` |
| security review | `most_capable+raised` | — |
| Developer | `standard` | `most_capable` |
| QA | `standard` | `most_capable` |
| Business Expert | `standard` | `most_capable` |
| surrogate-user | `standard` | `most_capable` |
| Tester | `cheap` | `standard` |
| docs-sync | `cheap` | `standard` |
| convention checks | `cheap` | `standard` |

**Opting in.** The PM may dispatch a role at an opt-in class when the chunk justifies it — data-model or auth-boundary decisions, security or adversarial review, a Developer chunk Sonnet-tier has visibly failed. It (1) puts one line `superhuman-tier-opt-in: <reason>` in the **task brief** (never inside the role block), and (2) appends `<UTC> — tier opt-in: role=<role> class=<class> reason=<reason>` to `SUPERHUMAN.md` `## Decisions log`. Anything outside a role's default + opt-in set is not allowed.

### Tier → model + effort (harness-neutral, operator profile)

Configured once, at first-run setup (`phases/0-kickoff.md`, operator model-tier elicitation), in `~/.superhuman/profile.yaml`:

```yaml
models:
  most_capable: {primary: <alias>, fallback: <alias>, effort: medium, raised_effort: high}
  standard:     {primary: <alias>, fallback: <alias>, effort: medium}
  cheap:        {primary: <alias>, fallback: <alias>, effort: n/a}
```

`effort` is one of `low | medium | high | xhigh | max | n/a`. `n/a` is for a model that ignores effort (e.g. a small fast model); it is a real answer, not a gap. `raised_effort` is optional and only meaningful on a tier some role can opt into at `+raised`.

### Claude Code — tier agent definitions

The `Agent` tool takes `model` but **no effort parameter**; effort comes only from an agent definition's frontmatter, and an omitted `effort:` inherits the session's. So each dispatch class is its own agent definition, generated from the profile by first-run setup (`superhuman_profile.py models install-agents --harness claude-code`) into `~/.claude/agents/`:

| Dispatch class | `subagent_type` |
|---|---|
| `most_capable` | `superhuman-tier-most-capable-subagent` |
| `most_capable+raised` | `superhuman-tier-most-capable-high-effort-subagent` |
| `standard` | `superhuman-tier-standard-subagent` |
| `cheap` | `superhuman-tier-cheap-subagent` |

- Dispatch as `Agent(subagent_type=<tier agent>, prompt=<role block + … + task brief>)` and **pass no `model=`** — the agent pins it, and a `model=` would silently override the pin.
- The role is still conveyed by the prompt (role block first), exactly as before; the tier agent carries only model + effort.
- A non-role dispatch (`superhuman-dispatch: non-role`) uses a tier agent too, by its duty's class above; a built-in type (`Explore`, `general-purpose`) is allowed only with an explicit `model=`.
- If the tier agents are missing or stale against the profile, `superhuman_profile.py doctor` says so; re-run `models install-agents`.
- **Enforced** when `templates/hooks/claude-code/pre-tool-use-role-gate` is installed: a role dispatch whose `subagent_type` is not its role's default/opt-in tier agent, or that passes `model=`, is refused; an opt-in class without the `superhuman-tier-opt-in:` line is refused; a non-role dispatch with neither a tier agent nor an explicit `model=` is refused. Faults still let the dispatch through (fail-soft).

Claude Code is single-provider — no fallback path. An Anthropic auth/credit failure surfaces to the user as a G10-style escalation.

### OpenClaw — per-spawn model + thinking

`sessions_spawn` accepts both `model` and `thinking` per call (verified in installed OpenClaw v2026.6.10: `createSessionsSpawnToolSchema`; `thinking` accepts `off | minimal | low | medium | high | adaptive | xhigh | max`, translated per provider — Anthropic thinking budget, OpenAI `reasoning_effort`). Pass both on every spawn:

`sessions_spawn(agentId="main", runtime="subagent", model=<tier primary>, thinking=<tier effort>, task=<role block + … + task brief>)`

For `+raised`, pass the tier's `raised_effort`. For `effort: n/a`, omit `thinking`. Without a per-call `thinking`, OpenClaw falls back to `agents.<id>.subagents.thinking` → `agents.defaults.subagents.thinking` config — not a substitute for passing it. No enforcement hook exists on OpenClaw; `scripts/superhuman_dispatch_tier_audit.py` is the backstop once it reads OpenClaw transcripts (Claude Code only today).

### Hermes — config-level fallback

Hermes's `delegate_task` takes **no model and no effort per call** (verified in installed Hermes v0.21.0, `tools/delegate_tool.py` `DELEGATE_TASK_SCHEMA`). Every delegated child gets the one `delegation:` block in the instance's `config.yaml` (`reasoning_effort`, `provider`/model), else inherits the parent. Fallback, written by first-run setup (`superhuman_profile.py models install-agents --harness hermes`):

- Set `delegation:` to the **`standard`** tier (its primary model + effort).
- Run `most_capable` roles (PM, Architect, code-quality review, security review) **in the orchestrator session itself**, which runs on the most-capable tier — never delegate them.
- `cheap` roles delegate at `standard` — a known overspend. Log each such dispatch once to `SUPERHUMAN.md` `## Decisions log`: `<UTC> — tier degraded (hermes): role=<role> wanted=cheap got=standard`.
- `delegate_task` then gets the role through the task `goal`/`context` as usual.

### Tier → model (OpenClaw — alias-based, with fallback)

Aliases (not concrete model names) so the mapping survives provider model updates without doc churn:

| Tier | Primary | Fallback |
|---|---|---|
| Most-capable | `claude-best` | `gemini-best` |
| Standard | `claude-better` | `gemini-better` |
| Cheap/fast | `claude-fast` | `gemini-good` |

### Fallback rule (OpenClaw only)

- Dispatch `<dispatch:agent>` with `model=<primary>` from the matching tier row.
- On auth, credits, or rate-limit failure from the primary → **immediately retry** with `model=<fallback>` from the same tier row.
- Log the event to `SUPERHUMAN.md` `## Decisions log` as one line: `<UTC> — fallback: tier=<tier> primary=<alias> → fallback=<alias> reason=<short>`.
- Within a single dispatch, do **not** retry back to primary once fallen back; stay on the fallback for that dispatch.
- Each *new* dispatch starts fresh at primary (primary-recovery between dispatches is welcome and desired).

Worked example (OpenClaw, Tester role, cheap/fast tier): orchestrator calls `sessions_spawn(model="claude-fast", …)`; if that returns an auth-failure error, it retries `sessions_spawn(model="gemini-good", …)` and appends the fallback line to `SUPERHUMAN.md`.

## Dispatch-time placeholder warning (C-DISP)

When the tier resolved for a dispatch is still an unfilled placeholder (`PROMPT_ME`, written by
`write_models_block` in `scripts/superhuman_profile.py` — see C-PROF) in the operator's
`~/.superhuman/profile.yaml` `models:` block, the orchestrator emits a **one-line, Type-B
(notification, non-blocking) warning** naming the tier, then PROCEEDS with the dispatch — e.g.
`warning: tier 'most_capable' is unconfigured (PROMPT_ME) — run first-run provider setup`. This
warning does not pause or gate autonomous progression; it is not a gate, only a fail-safe
reminder that a deferred tier is still unset (FR-10, OQ-5).

**Roadmap#275: the same warning fires for an unfilled `effort`.** A tier's `effort` can be
unconfigured in two indistinguishable-in-effect ways — an explicit `PROMPT_ME` placeholder, or the
key being absent entirely (a profile written before roadmap#275, or a `models set` answer that
named only `primary`/`fallback`; `Profile.models[tier].get("effort")` returns `None` for both, per
`scripts/superhuman_profile.py`'s `Profile.models` docstring). Either shape is "unconfigured" for
this warning's purposes: `warning: tier 'standard' effort is unconfigured (PROMPT_ME) — run first-run
provider setup`. A dispatch class's own `+raised` variant with no `raised_effort` configured warns
the same way, naming `raised_effort` instead of `effort`. As with the model-alias placeholder above,
this never blocks — the dispatch proceeds either without an `effort:` line (Claude Code tier agent)
or without a `reasoning_effort` (Hermes `delegation:`), which means the dispatch simply inherits
whatever effort the session/parent was already running at.
