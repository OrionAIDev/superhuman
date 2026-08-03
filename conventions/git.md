# Git and remote-sync conventions

## PM git decision rubric (G1)

The PM proposes git use at G1 based on project shape:

| Project shape | PM recommendation |
|---|---|
| Single-file script, <100 lines, throwaway | Optional — propose "no git", override available |
| Multi-file, any complexity | Git required |
| Anything declared for archive/long-term use | Git required |
| Anything intended for deployment to another environment | Git required + remote |

PM logs the chosen value in SUPERHUMAN.md `Git:` field (`none | local | remote`).

## Remote-sync flow (when user opts in at G1)

1. **Determine target.** PM asks: existing repo URL OR create new?
2. **If existing:** collect URL + access (SSH key reachable, HTTPS + token); test reachability with `git ls-remote`.
3. **If new:** collect provider (GitHub / GitLab / other), org/user, repo name, visibility (public/private); create via API if credentials present, otherwise instruct user to create manually; capture URL.
4. **Branch strategy.** Collect: trunk-based (commit to main), feature-branches (branch per chunk), other. Default: feature-branches when parallelism is enabled; trunk-based otherwise.
5. **Initialize.** Initialize local repo if not present; configure remote; first push of scaffolding.
6. **Per-chunk push policy.** Developer pushes after each successful chunk completion (Phase 3.1 ✓). PM verifies push succeeded; push failure = G10 BLOCKED escalation.

## Notification policy

Routine pushes are Type B (notification, no pause). Failures escalate to G10 (BLOCKED).

## Commit message convention (default)

`<type>: <chunk-N>: <short description>` where `<type>` ∈ `feat|chore|docs|test|fix|refactor`. PM may propose a different convention at G3 if the project's existing codebase uses one; user approves.
