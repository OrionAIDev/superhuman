# Dispatch adaptation layer

This file is the ONLY place that knows about platform-specific tool names. When porting superhuman to a different harness (e.g., Claude Code → OpenClaw), edit only this file.

> For the *patterns* these symbols compose into — direct dispatch, sequential pipeline, parallel
> fan-out with merge, research isolation — and the anti-patterns to avoid, see
> `references/orchestration-patterns.md`. This file resolves the symbols; that catalog governs how
> they're wired together (PM is the only orchestrator; seam-crossing parallelism → G9).

## Symbolic names used throughout superhuman

| Symbol | What it does | Claude Code | OpenClaw (TBD; port-time fill-in) |
|---|---|---|---|
| `<dispatch:agent>` | Run a subagent with a focused prompt and isolated context | `Agent` (subagent_type: `general-purpose` by default; `claude` for high-capability roles like PM/Architect/code-quality reviewer) | `sessions_spawn(runtime="subagent", model=..., task=...)` — agentId must be one the instance permits (often only `main`); convey the role via the prompt, never a role-named agentId; always pass an explicit working `model`. See "OpenClaw `sessions_spawn` constraints" below. |
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

- **Claude Code:** `Agent(subagent_type:"claude", model:<standard-tier>, prompt:<role + gate context>)`.
- **OpenClaw:** `sessions_spawn(agentId="main", model=<explicit standard-tier id>, task=<role + gate context>)` — honor BOTH `sessions_spawn` constraints above: never a role-named agentId (use `main`), and always an explicit working `model` (fall back to another acceptable standard-tier model if the preferred provider is down).

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

Independent of platform mapping; uses the cheapest model that can handle a role per §9 rule 7 of DESIGN.md. The orchestrator MUST pass `model=` explicitly on every dispatch; never rely on default/inherited model routing.

### Role → tier

| Tier | Roles |
|---|---|
| Cheap/fast | Tester, mechanical Developer chunks, docs-sync, convention checks |
| Standard | Integration Developer, QA substantive review, Business Expert |
| Most capable | PM, Architect, code-quality reviewer |

### Tier → model (Claude Code — Anthropic only)

Concrete model IDs from the `Agent` tool's `model` enum:

| Tier | Model |
|---|---|
| Most-capable | `opus` |
| Standard | `sonnet` |
| Cheap/fast | `haiku` |

Claude Code is single-provider — no fallback path. An Anthropic auth/credit failure surfaces to the user as a G10-style escalation.

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
