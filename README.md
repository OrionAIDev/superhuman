# Superhuman

Orchestrates complete software-development projects: vision → requirements → design → test plan → implementation → review → acceptance. Dispatches role-specific subagents (PM, Business Expert, Architect, Developer, QA, Tester) with structured HITL gates, drift detection, and project-conventions support.

Superhuman is a one-time fork of [superpowers](https://github.com/anthropics/claude-code-plugins) v5.1.0 (MIT), evolved into a phased orchestrator. It does not depend on superpowers at runtime. See `NOTICE.md` for attribution and `CHANGELOG.md` for the delta.

## Quick start

In a Claude Code session: `Use superhuman to build/design/implement <your task>`.

The skill will:
1. Elicit your vision (G0) — what and why
2. Collect workflow preferences (G1) — cadence, value-vs-foundation, git/remote, parallelism
3. Drive phases 1 → 4 with HITL gates at each transition
4. Watch for drift; pause you on moderate+ severity (G6)
5. Retune its recommendations after each milestone

Artifacts land at `<your-project-root>/docs/superhuman/<slug>/`.

## Files of interest for developers of this skill

- `SKILL.md` — the orchestrator entry
- `roles/` — one prompt per role
- `phases/` — orchestration recipes per phase
- `conventions/` — user-set defaults (Python, testing, git)
- `templates/artifacts/` — skeletons for canonical project artifacts
- `references/` — evolved content forked from superpowers
- `adaptation/dispatch.md` — single port-time edit point for harness differences (e.g. OpenClaw)
- `hooks/session-start` — cache priming
- `tests/` — structure validators + integration smoke test
- `CHANGELOG.md` — version history

## Versioning

`VERSION` (semver) is read at G1 and recorded in each project's SUPERHUMAN.md as `Superhuman-version:`. Bump on any change to role prompts, phase recipes, gate semantics, or default conventions.

## Install / register

The skill bundle lives at `~/.claude/skills/superhuman/`. To register the SessionStart hook with Claude Code (recommended for cache priming):

Edit your Claude Code `settings.json` (typically `~/.claude/settings.json` or per-project `.claude/settings.json`).

> **Windows note:** the POSIX `~` does not expand inside Claude Code's `settings.json` reliably on Windows. Use the `.cmd` shim form shown below instead, OR substitute the full Windows path (e.g. `C:\\Users\\Chris\\.claude\\skills\\superhuman\\hooks\\session-start`).

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/skills/superhuman/hooks/session-start",
            "async": false
          }
        ]
      }
    ]
  }
}
```

On Windows, replace the command path with the `.cmd` shim:

```json
"command": "%USERPROFILE%\\.claude\\skills\\superhuman\\hooks\\session-start.cmd"
```

The skill itself is auto-discovered by Claude Code from the `~/.claude/skills/` directory — no settings entry needed for skill registration.

## Install / register on OpenClaw

**The SessionStart hook is NOT required on OpenClaw.** OpenClaw uses a different idiom: skills are auto-discovered from `<workspace>/skills/` when the orchestrator loads; role and phase files are loaded on-demand via `read(path=...)` dispatch calls as each subagent is invoked. There is no per-session hook equivalent — the platform handles caching differently.

**What this means in practice:**
- The skill works on OpenClaw without any hook registration. Deploy the skill bundle into that instance's `<workspace>/skills/superhuman/` and it is available immediately.
- **Cost:** the first PM dispatch in a new session pays a one-time cache-warm cost (loading `SKILL.md` + the PM role prompt + any declared references). Subsequent dispatches in the same session benefit from the platform's own prompt caching.

**Optional: one-time-per-gateway-startup priming (advanced)**

OpenClaw has a `gateway:startup` event and a `boot-md` bundled hook that fires once at gateway start (not per-session). If you want to pre-warm the cache at gateway startup, you can add a workspace-level `BOOT.md` that includes a `read(path="<workspace>/skills/superhuman/SKILL.md")` invocation. The `boot-md` hook, where enabled in `openclaw.json`, will execute this at startup.

Note: `BOOT.md` is a workspace-wide file shared across all skills. Adding skill-specific `read()` calls to it increases gateway startup token cost. Only do this if cache priming on first PM dispatch is measurably disruptive in your workflow.

**Resolution (v0.1.2):** This is intentionally no-op. No hook registration is required; OpenClaw's lazy skill discovery + on-demand loading is the correct pattern for this platform. The v0.1.1 tracking issue is closed.

## Set up a deployment profile

Superhuman works with no profile at all: it falls back to a built-in ref-space
ladder — unattended work permitted on feature branches, undeclared on trunk,
forbidden on a checked-out release tag. If you have no deployment environments,
you can stop reading here.

Otherwise, let it propose one from what it can find:

```bash
scripts/superhuman_profile.py init . --dry-run     # look before you leap
scripts/superhuman_profile.py init .               # writes ~/.superhuman/profile.yaml
```

`init` inspects `.env.<name>` files, `docker-compose-<env>.yml` files,
`environment:` keys in CI workflows, and — unless you pass `--offline` — hosted
deployment environments and branch-protection rules via `gh`. Names that look
protected (`prod`, `staging`, `uat`, …) are proposed as deny rungs; the rest are
proposed as permissive. **It is a proposal, not a policy** — read it before you
keep it. Anything it cannot infer is left `null`, which halts an unattended run
rather than guessing.

Prefer to start from a known shape:

```bash
scripts/superhuman_profile.py init . --preset solo-git        # branches as the ladder
scripts/superhuman_profile.py init . --preset classic-3tier   # dev -> staging -> production
```

Then check what any location resolves to:

```bash
scripts/superhuman_profile.py doctor .
scripts/superhuman_profile.py explain /path/to/some/project
```

`doctor` prints the resolved rung and the rule that matched it, lists every
undeclared policy, warns when a protected rung could be promoted into without a
human, and tells you plainly when a ref-only ladder cannot see your deployment
targets.

## Verify install

```bash
cd ~/.claude/skills/superhuman
# POSIX:
source .venv/bin/activate
# Windows (PowerShell or cmd):
# .venv\Scripts\activate
pytest tests/ -v
```

Expected: all tests pass.

## Required orchestrator model

The PM thread (the model you, the user, are talking to) must be a most-capable tier model. Smaller models will skip HITL gates regardless of the SKILL.md HARD-GATE.

**Reference by alias, not by version.** Concrete model names (gpt-5.5, claude-opus-4-7) go stale as providers ship newer versions. Reference your environment's stable **alias** for "current best of family X" so this skill's recommendations keep working without doc churn when underlying models are upgraded.

### Where the tier -> model mapping lives

Tier → model is account-specific, so it belongs in your profile rather than in this skill:

```yaml
# ~/.superhuman/profile.yaml
models:
  most_capable: opus      # or your harness's alias for "current best"
  standard:     sonnet
  cheap:        haiku
```

`adaptation/dispatch.md` supplies a per-harness default when the profile declares none.

**Acceptable** for the PM thread: any alias resolving to a current top-tier model, any provider.

**Forbidden** for the PM thread: anything resolving to the fast/cheap tier — names containing
`-mini`, `-flash`, `-fast`, `haiku`, or a local small model. These do not reliably honor the
HARD-GATE.

### OpenClaw config

Set `agents.defaults.model` in that instance's `openclaw.json`, using its alias names:

```json
{
  "agents": {
    "defaults": {
      "model": { "primary": "<your most-capable alias>", "fallbacks": ["<fallback alias>"] }
    }
  }
}
```

Some deployments make `openclaw.json` immutable (`chattr +i`); if so, clear the flag, edit, restore
it, and restart or hot-reload the gateway.

### Claude Code config

Claude Code does not have a configurable alias system, but Anthropic provides stable shortnames (`opus`, `sonnet`, `haiku`) that automatically map to the current model in each family — these serve the same staleness-resistant purpose as an alias system.

**Recommended:** primary `opus`, fallback `sonnet`.

Set via `/model` (interactive) or in `~/.claude/settings.json`:

```json
{
  "model": "opus"
}
```

Fallback handling: Claude Code's `settings.json` `model` field is a single value, not a primary/fallback pair. If Opus is unavailable or rate-limited, switch interactively with `/model sonnet` (or set a project-local `.claude/settings.json` with `"model": "sonnet"` to override for specific projects). At the time of v0.1.3 the shortnames resolve to `claude-opus-4-7` and `claude-sonnet-4-6`; Anthropic updates the mapping as new versions ship.

### Subagents

Subagent dispatches use their own model-tier selection per `adaptation/dispatch.md` model-tier table (PM/Architect/code-quality reviewer → most-capable; Developer/QA/Business Expert → standard; Tester/mechanical chunks → cheap-fast). The config above only governs the user-facing PM orchestrator thread.

## HITL levels (v0.5.0)

Superhuman offers three levels of human-in-the-loop involvement, chosen once at G1 and locked for
the project's lifetime:

| Level | Name | Human stops at |
|---|---|---|
| **0** | High HITL (default) | Every Type A gate: G0, G1, G2, G3, G4, G6, G7, G8, G9, G10 |
| **1** | Medium HITL (the old "autonomous mode") | G0, G1, G6 (moderate+), G8, G9, G10 |
| **2** | Low HITL | One combined G0+G1 confirmation, then only G10 |

### What HITL-M and HITL-L do

A surrogate-user role answers a subset of the Type A gates using the declared `GOAL.md` as the decision authority, while the PM runs a bounded, sequential try → measure → keep/rollback loop: each iteration proposes a change, runs tests, keeps the result if it strictly improves (ties roll back), and continues until the goal is met or the iteration cap is reached. The surrogate runs at `tier=standard`.

- **Level 1:** the surrogate answers G2, G3, G4, G5, G7 conservatively — when in doubt, it escalates. G6 (moderate+), G8, G9, and G10 always go to you.
- **Level 2:** the surrogate/PM also resolves G6 (any severity), G8, and G9 itself, by **precedent-mining** first — checking this project's own decisions log, sibling repos/ADRs via codebase-memory-mcp, and declared conventions — before deciding, then logging the decision and its basis to SUPERHUMAN.md instead of asking. Only G10 (a genuinely blocked PM) and an `ABORT` recommendation still reach you. The Phase 3.3 preflight GO/NO-GO stays a hard, non-overridable blocker at every level — precedent-mining can't wave through a NO-GO.

### Preconditions

1. **Git with a remote must be configured** — the loop operates on a dedicated `autonomous/<slug>/<run-id>` branch; no remote = precondition fails.
2. **Only where your profile permits it** — `scripts/autonomous-precondition.sh --level <M|L>` resolves the current location to a rung and refuses unless that rung's `act_unattended` policy allows an unattended run. It also refuses when the rung declares *no* policy (exit 4): an undeclared policy must be settled by a human, never inferred. Enforced deterministically in code, not prose.
3. **GOAL.md must be provided** — file-first (you drop a `GOAL.md` at the project root before invocation) or elicited at G1 during kickoff. The loop measures every iteration against it.
4. **Level 2 only:** if the project modifies existing code, a `ROLLBACK.md` (revert target + procedure) must exist — `scripts/autonomous-precondition.sh --level 2` checks for it. Net-new/greenfield projects are exempt.
5. **Level 0 by default** — HITL-M/L are opt-in. The PM asks at G1 which level you want.

### The hard block

`scripts/autonomous-precondition.sh --level <M|L>` resolves the project to a rung and exits
non-zero — blocking the loop — whenever that rung forbids unattended operation, or declares no
policy for it. There is no override path in the skill: the way to change the answer is to change
the profile, deliberately and in version control.

This governs where the *authoring loop* runs. Landing its result at a protected rung is a separate
`promote_into` policy that still requires whatever approver that rung names, every time. Passing
this gate is never consent to promote.

### Branch and tag scheme

Each run operates on its own branch and leaves a trail of immutable tags:

```
autonomous/<slug>/<run-id>          ← working branch; never merged to main by the loop
v<X.Y.Z>-alpha-<run-id>.iter-N-pre  ← snapshot before iteration N starts
v<X.Y.Z>-alpha-<run-id>.iter-N      ← snapshot after iteration N passes
v<X.Y.Z>-beta-<run-id>              ← candidate tag when loop exits (goal met or cap hit)
```

Tags are kept forever (archive-never-delete). The loop never touches `main`, never moves a stable tag, and never creates a release tag — only alpha/beta markers.

### Rolling back

```bash
scripts/autonomous-rollback.sh <slug>
```

The rollback script is slug-scoped: it locates the most recent `autonomous/<slug>/*` branch and restores the project to the last good state using git tag archaeology. Prior iterations are archived, never deleted.

### Acceptance: human at level 1, PM self-accept at level 2

At **level 1**, the surrogate never answers G8. When the loop exits (goal met, cap hit, or stalled), control returns to you for G8 acceptance sign-off before any result is considered final.

At **level 2**, once the Phase 3.3 preflight reaches GO (or all Blockers are closed), the PM self-accepts — it composes the acceptance summary, logs the G8 decision and its basis to SUPERHUMAN.md, and emits the PROJECT COMPLETE terminator itself. If the PM's own fix attempt can't clear a NO-GO, that's the one point a level-2 run pauses for you — via G10, not a separate acceptance gate.

## Known limitations

- **PM thread requires a most-capable-tier model.** Smaller or faster models (anything labeled `-mini`, `-flash`, or equivalent cheap-fast tier) will silently skip HITL gates even with the HARD-GATE block in place. The framework's discipline is only reliable when the PM orchestrator runs on a model capable of holding the full role contract in context. See the "Required orchestrator model" section above.

- **Full subagent-dispatch smoke is manual-only.** The automated test suite (`pytest tests/`) validates structure, content, and load-time integrity, but does not exercise live subagent dispatch (the `<dispatch:agent>` path). End-to-end smoke tests (all 8 gates firing, autonomous progression, drift escalation) must be run manually against an active Claude Code or OpenClaw session. See `tests/fixtures/tiny-project-brief.md` for the canonical manual smoke brief.

- **No explicit "PROJECT COMPLETE" signal at G8 (tracked for v0.1.4).** The G8 acceptance summary does not include a visually distinct terminator. Users may not be certain when superhuman has fully finished — the acceptance summary can look like another progress update. A dedicated "PROJECT COMPLETE" announcement is tracked for v0.1.4.

- **No progress heartbeat during long phases (tracked for v0.1.4).** During Phase 3 implementation chunks and Phase 3.1 parallel reviews, long periods of silence can make the PM thread appear stuck. A periodic heartbeat notification (e.g., "Chunk 2 — developer subagent in flight (4 min elapsed)…") is tracked for v0.1.4 as a Type B append-only notification that does not pause the flow.

## License

MIT, inherited from superpowers. See `NOTICE.md`.
