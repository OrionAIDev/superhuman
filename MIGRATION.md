# MIGRATION.md — superhuman release & deploy workflow

## 1. Overview

This is the end-to-end workflow doc for the **superhuman skill maintainer**: how to
get a change from your editor onto a deployed environment, verified, and tagged.

`superhuman` is a single-author skill repo. Its "CI/CD" is not GitHub Actions — it
is **three scripts** under `scripts/`, driven by hand from the laptop:

| Script | Role |
|---|---|
| `scripts/release.sh` | Reads `VERSION`, optionally bumps it, runs the suite + hook smoke, cuts a **signed** tag, and pushes. |
| your own promote script | Idempotent deploy/verify of a released tag onto a target rung. Not shipped — see `examples/promote.sh.example` and the rung's `promote:` key. |
| `scripts/git-hooks/pre-commit` | Fast-test commit gate (installed via `install-hooks.sh`). |

The pipeline is authoring → first deployed rung, as declared in your profile:

```
Workstation (authoring + pytest)
   │  scripts/release.sh   → signed tag pushed to your remote
   ▼
first deployed rung  (your promote script → git checkout <tag> + pytest + hook smoke)
   │
   ▼
(promotion onward is a SEPARATE action, gated by each rung's `promote_into`
 policy — never performed implicitly by a release)
```

Deployment mechanics are organisation-specific — ssh targets, absolute paths,
container names — so superhuman ships an example rather than a script. Copy
`examples/promote.sh.example` into your own profile repo, adapt it, and point
the rung's `promote: { command: ... }` at it.

There is no GitHub Actions workflow; the three scripts above are the whole
automation surface.

## 2. One-time setup

**Wire the pre-commit gate** (run once per clone):

```bash
scripts/install-hooks.sh
```

This installs `scripts/git-hooks/pre-commit` as `.git/hooks/pre-commit` (symlink,
or a copy where symlinking is unavailable). The hook runs the fast suite before
every commit and refuses the commit on failure. Emergency bypass:
`git commit --no-verify`.

**Configure a tag signing key** (prerequisite for `release.sh`). The release
script uses `git tag -s` and **never** falls back to an unsigned tag, so a signing
key must be configured:

```bash
# GPG:
git config user.signingkey <KEYID>

# or an SSH signing key:
git config gpg.format ssh
git config user.signingkey ~/.ssh/id_ed25519.pub
```

If signing is not configured, `release.sh` surfaces the error and aborts.

## 3. The release loop

The happy path, in order:

1. **Edit** the role/phase/convention/template files or scripts.

2. **Run the suite locally** (the fast inner loop):

   ```bash
   python -m pytest tests/
   ```

   When you commit, the **pre-commit gate fires automatically** and re-runs the
   fast tests — a failing commit is refused.

3. **Bump `VERSION` and write the `CHANGELOG.md` entry** for the new version.
   (You can either bump `VERSION` by hand here, or let `release.sh --bump` do it
   in the next step — but the CHANGELOG entry is always hand-written.)

4. **Cut the release.** Either bump in-place:

   ```bash
   scripts/release.sh --bump patch
   ```

   …or, if you already bumped `VERSION` by hand in step 3, run without `--bump`:

   ```bash
   scripts/release.sh
   ```

   This guards against a dirty tree / autonomous branch, runs `pytest tests/ -x`,
   smokes `hooks/session-start`, creates a **signed** tag `vX.Y.Z`, and pushes
   (`git push && git push --tags`). Preview first with:

   ```bash
   scripts/release.sh --bump patch --dry-run   # side-effect-free: prints the plan
   ```

5. **Promote to your first deployed rung:**

   ```bash
   ~/.superhuman/deploy/promote.sh <rung> v0.1.4 --dry-run   # preview: no ssh, no network
   ~/.superhuman/deploy/promote.sh <rung> v0.1.4
   ```

   Whatever script you adapt should check out the **tag** (never a branch), run
   `pytest tests/ -x -q` and the hook smoke *at the destination*, and be
   idempotent so it is safe to re-run. Verifying at the destination is the point:
   a green local build is not evidence the deployed environment works.

6. **Manual smoke per platform.** Run both checklists against the `hello-cli`
   fixture:
   - Claude Code: `tests/smoke/claude-code/SMOKE.md`
   - OpenClaw: `tests/smoke/openclaw/SMOKE.md`

7. **Both green = final.** If a smoke checklist surfaces a defect, fix it and cut
   another **patch** release (back to step 1) — never mark a release final with a
   red smoke run.

## 4. Rules for adding a new feature

A checklist for any new feature or gate-semantics change:

- [ ] **Write the test first** (TDD) — add or extend a test before the
      implementation.
- [ ] **If you add a new `<dispatch:*>` symbol**, fill **BOTH** platform cells
      (Claude Code **and** OpenClaw) in `adaptation/dispatch.md`. A structure test
      enforces that every symbol has both cells populated.
- [ ] **Bump `VERSION`.**
- [ ] **Add a `CHANGELOG.md` entry** describing the change.
- [ ] **Update `docs/superhuman/DESIGN.md`** if design or gate semantics change.
- [ ] **Keep role/phase files using `<dispatch:*>` symbols** — never raw platform
      tool names (e.g. write `<dispatch:agent>`, not `Agent` or `sessions_spawn`).
- [ ] **Run the full suite** (`python -m pytest tests/`).
- [ ] **Smoke both platforms** (Claude Code + OpenClaw) before tagging the release
      final.

## 4a. Version notes

### v0.4.0 — addyosmani harvest (no breaking changes)

v0.4.0 is a purely **additive** harvest of 8 quality-gate/workflow items from
`addyosmani/agent-skills` (see `CHANGELOG.md` and the project record at
`docs/superhuman/addyosmani-harvest/`). **No consumer-facing behavior is removed or renamed** — a
project run started under v0.3.x continues to work identically; the new material (Definition of
Done, anti-rationalization anatomy, orchestration-patterns catalog, source-cited convention,
doubt-driven-development, the pre-acceptance preflight fan-out phase, chunk-sizing/severity norms,
and the deprecation sub-skill) is additional capability the PM opts into.

One **maintainer-facing** note (not a consumer break): a new deterministic invariant
(`tests/test_content.py::test_anatomy_invariant`) now requires every roasting sub-skill SKILL.md,
every `roles/*.md`, and the two new sub-skills to carry both a `## Common Rationalizations` and a
`## Red Flags` H2 section. If you add a new role or roast/critique sub-skill, add those two
sections or the suite will fail.

## 5. Troubleshooting

- See **`TROUBLESHOOTING.md`** at the repo root for known issues (Windows
  worktree `EEXIST`, stale `git worktree` entries, a deployed environment pull auth, etc.).
- **SessionStart-hook size caveat:** on a small / cheap orchestrator model the
  large Claude Code SessionStart prime can be truncated, dropping rules that land
  late in the stream. The fix is to run the PM on a most-capable tier (see
  `TROUBLESHOOTING.md` → "SessionStart hook output is large"). OpenClaw is
  unaffected — it uses lazy skill discovery and needs no SessionStart hook.
- **A deployed checkout pulls with whatever identity that host is authenticated as.** For a
  private repo, that identity needs read access, or `git fetch` reports the repository as
  missing rather than forbidden. Verify with `git ls-remote <url>` before deploying.
  the skill repo for the promote script's `git fetch`/`checkout` to
  succeed (resolved 2026-05-25; see `TROUBLESHOOTING.md`).
