# Troubleshooting

## `mkdir .claude/worktrees` EEXIST on first subagent dispatch (Windows)

**Symptom:** the first 1-2 subagent dispatches fail with `EEXIST: file already exists, mkdir '.claude/worktrees'`.

**Cause:** Claude Code's Agent harness on Windows attempts to create `.claude/worktrees/` non-idempotently. If a leftover empty directory exists from a previous session, the create fails.

**Workaround:** delete the directory once, then retry the dispatch:
```bash
rmdir .claude/worktrees    # POSIX
# or on cmd.exe:
rmdir /S /Q .claude\worktrees
```

The harness recreates it on the next attempt. The skill itself does not own this directory.

## Stale `.git/worktrees/agent-<hash>` entries

**Symptom:** `git worktree list` shows entries pointing at paths that no longer exist.

**Cause:** A subagent dispatch's worktree was registered but never cleaned up.

**Workaround:** `git worktree prune`. Phase 3.1 and Phase 4 of superhuman run this automatically as of v0.1.1.

## `git pull` on a deployed checkout fails with "Repository not found"

**Symptom:** A server-side `git pull` of the skill repo fails to authenticate, reporting the
repository as missing rather than as forbidden.

**Cause:** GitHub returns "not found" rather than "forbidden" for a private repo the credential
cannot see — so this is almost always an authorization problem wearing a 404. The credential on
that host (a PAT, a deploy key, or a `gh` login) belongs to an identity without read access to the
repo.

**Fix:** Grant that identity read access, or re-authenticate the host as one that already has it
(`gh auth status` to see which account is active). Verify with `git ls-remote <url>` before
retrying the deploy.

## SessionStart hook output is large — small models may truncate it

**Symptom:** On a smaller/cheaper orchestrator model, the PM behaves as if it never read parts of the framework — e.g. it skips the HARD-GATE, forgets the autonomous-progression rule, or loses the cross-cutting rules midway through a session.

**Cause:** The Claude Code SessionStart hook (`hooks/session-start`) primes the cache by emitting the role prompts plus canonical references in a stable order — ~4800 lines as of v0.1.3. A model with a small context window (or one that aggressively truncates injected SessionStart context) can drop the tail of that output, losing whichever rules landed late in the stream. This compounds the model-tier problem the HARD-GATE already warns about: a forbidden fast/cheap tier both reasons worse *and* may not even see the full contract.

**Remediation:**

1. **Run the PM orchestrator on a most-capable tier** (see SKILL.md "Required model tier for the orchestrator"). This is the real fix — the framework assumes a model that can hold the full contract in context. Forbidden aliases (`-mini`, `-flash`, `haiku-*`, `gpt-fast`, `or-fast`, etc.) are forbidden partly for this reason.
2. **OpenClaw needs no SessionStart hook** — it uses lazy skill discovery and loads `roles/`/`phases/` on demand, so the large one-shot prime does not apply there (see README). The hook-size concern is Claude-Code-specific.
3. If you must run on a constrained context, confirm the PM has actually internalized the HARD-GATE by asking it to restate the 4 rules before proceeding; if it cannot, switch tiers rather than continuing.

This is a model/harness limitation, not a bug in the skill — the hook content is intentionally complete. The skill mitigates by keeping the load-bearing rules (HARD-GATE, cross-cutting rules) at the **top** of SKILL.md, which is read first regardless of hook truncation.
