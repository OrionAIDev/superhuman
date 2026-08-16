#!/usr/bin/env bash
# release.sh — laptop release driver for the superhuman skill (F13).
#
# Operates on the CURRENT WORKING DIRECTORY's git repo: it reads ./VERSION and
# runs git in the cwd. It does NOT hardcode the skill path, so it can be driven
# against a throwaway repo in tests.
#
# Order of operations (non-dry-run):
#   1. Parse args.
#   2. Guards (BEFORE any test run, tag, or mutation):
#        - refuse a dirty working tree
#        - refuse an autonomous/* branch (v0.2.0 autonomous-mode safety rail:
#          no stable release may be cut from an autonomous branch)
#   3. Determine version from ./VERSION; with --bump, compute + write + commit.
#   4. Run pytest tests/ -x (abort on failure).
#   5. Hook smoke: ./hooks/session-start must exit 0.
#   6. Create a SIGNED tag: git tag -s vX.Y.Z -m "superhuman vX.Y.Z".
#   7. git push && git push --tags.
#   8. Print the release URL, built from the cwd repo's origin remote (NOT a
#      hardcoded slug — a fork must report its own release page). Override with
#      SUPERHUMAN_REPO_SLUG=<owner>/<repo>; with no origin the link is omitted.
#
# --dry-run: run the guards, print the planned actions, exit 0. Side-effect-free
# and network-free — no pytest, no hook smoke, no VERSION write, no tag, no push.
#
# SIGNING PREREQUISITE (project policy — tags MUST be signed):
#   The tag step uses `git tag -s`, which requires a configured signing key, e.g.
#     git config user.signingkey <KEYID>          # GPG
#   or an SSH signing key:
#     git config gpg.format ssh
#     git config user.signingkey ~/.ssh/id_ed25519.pub
#   If signing fails this script surfaces the error and aborts — it NEVER falls
#   back to an unsigned tag.
#
# Usage:
#   scripts/release.sh [--bump patch|minor|major] [--dry-run]
#   scripts/release.sh --help

set -euo pipefail

# Repository slug ("<owner>/<repo>"), used only to print the release URL at the
# end of a run. DERIVED from the cwd repo's origin remote rather than hardcoded:
# a hardcoded slug makes every fork of this skill print the ORIGINAL author's
# release page, which contradicts the skill's claim to be organisation-neutral.
# Override with SUPERHUMAN_REPO_SLUG when origin is not the publishing remote
# (a mirror, or a clone whose origin points at an upstream you do not own).
#
# Empty is a valid result: throwaway test repos have no origin, and a missing
# URL is better than a wrong one. Callers must handle the empty case.
repo_slug() {
  if [ -n "${SUPERHUMAN_REPO_SLUG:-}" ]; then
    printf '%s' "$SUPERHUMAN_REPO_SLUG"
    return 0
  fi

  local url
  url="$(git remote get-url origin 2>/dev/null || true)"
  [ -n "$url" ] || return 0

  # Reduce every remote form to its trailing "<owner>/<repo>". Hosts are shown
  # as <host> placeholders so this comment does not itself trip the repo's
  # publication guard, which reads a literal user@host as an ssh target:
  #   https://<host>/owner/repo.git   ssh://git@<host>/owner/repo.git
  #   git@<host>:owner/repo.git       https://user@<host>/owner/repo
  # The greedy ".*[/:]" backtracks to whichever separator leaves exactly two
  # segments, so it handles the scp-style colon and the URL slash alike.
  #
  # Only the slug is derived; the URL is built against github.com below. A fork
  # hosted elsewhere gets the right owner/repo but a GitHub link — deriving the
  # host too would need per-forge release paths (GitLab uses /-/releases/).
  local slug
  slug="$(printf '%s' "$url" \
    | sed -E 's#\.git$##; s#/$##; s#^.*[/:]([^/:]+)/([^/]+)$#\1/\2#')"

  # A remote that does not reduce to exactly "<owner>/<repo>" (a bare local
  # path, say) yields nothing rather than a plausible-looking wrong slug.
  case "$slug" in
    */*/*|/*|*/) return 0 ;;
    ?*/?*) printf '%s' "$slug" ;;
  esac
}

REPO_SLUG="$(repo_slug)"

usage() {
  cat <<'EOF'
release.sh — laptop release driver for the superhuman skill.

Usage:
  scripts/release.sh [--bump patch|minor|major] [--dry-run]
  scripts/release.sh --help | -h

Options:
  --bump <part>   Bump the semver in ./VERSION by patch|minor|major before
                  releasing (writes VERSION and commits "release: v<new>").
                  Omit to release the version currently in ./VERSION.
  --dry-run       Run the guards, print the planned actions, and exit 0
                  without running tests, writing VERSION, tagging, or pushing.
  --help, -h      Show this help and exit.

Environment:
  SUPERHUMAN_REPO_SLUG   <owner>/<repo> for the printed release URL. Defaults
                         to the slug derived from this repo's origin remote;
                         set it when origin is not the publishing remote.

Guards (always run first):
  * Refuses a dirty working tree.
  * Refuses any autonomous/* branch (v0.2.0 safety rail).

Tags are SIGNED (git tag -s); a signing key must be configured
(user.signingkey). Signing failure aborts; it never falls back to unsigned.
EOF
}

die() {
  echo "release.sh: $*" >&2
  exit 1
}

BUMP=""
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --bump)
      [ $# -ge 2 ] || die "--bump requires an argument (patch|minor|major)"
      BUMP="$2"
      case "$BUMP" in
        patch|minor|major) ;;
        *) die "--bump must be one of patch|minor|major (got: $BUMP)" ;;
      esac
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Guards — BEFORE any test run, tag, or mutation (including --bump writes).
# ---------------------------------------------------------------------------

# Must be inside a git repo.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "not inside a git repository (cwd: $(pwd))"

# Guard 1: dirty working tree.
if [ -n "$(git status --porcelain)" ]; then
  die "working tree is dirty — commit or stash changes before releasing."
fi

# Guard 2: autonomous/* branch (v0.2.0 autonomous-mode safety rail).
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
case "$BRANCH" in
  autonomous/*)
    die "refusing to release from autonomous branch '$BRANCH' — the v0.2.0 autonomous-mode safety rail forbids cutting a stable release from an autonomous branch."
    ;;
esac

# ---------------------------------------------------------------------------
# Determine version.
# ---------------------------------------------------------------------------

[ -f ./VERSION ] || die "./VERSION not found in $(pwd)"
CURRENT="$(tr -d '[:space:]' < ./VERSION)"
[ -n "$CURRENT" ] || die "./VERSION is empty"

# Validate semver shape.
case "$CURRENT" in
  *.*.*) ;;
  *) die "./VERSION is not semver X.Y.Z (got: $CURRENT)" ;;
esac

NEW="$CURRENT"
if [ -n "$BUMP" ]; then
  IFS='.' read -r MAJOR MINOR PATCH <<EOF
$CURRENT
EOF
  case "$BUMP" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch) PATCH=$((PATCH + 1)) ;;
  esac
  NEW="${MAJOR}.${MINOR}.${PATCH}"
fi

TAG="v${NEW}"
# No derivable slug (no origin, or a remote that is not a hosting URL) means no
# link to print. The release itself does not depend on this — it is display only.
if [ -n "$REPO_SLUG" ]; then
  RELEASE_URL="https://github.com/${REPO_SLUG}/releases/tag/${TAG}"
else
  RELEASE_URL="(no origin remote — set SUPERHUMAN_REPO_SLUG to print a release URL)"
fi

# ---------------------------------------------------------------------------
# --dry-run: print the plan and exit, fully side-effect-free.
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> release.sh --dry-run (no changes will be made)"
  echo "    branch:        $BRANCH"
  echo "    current:       $CURRENT"
  if [ -n "$BUMP" ]; then
    echo "    bump:          $BUMP"
    echo "    planned write: VERSION -> $NEW (+ commit 'release: v${NEW}')"
  fi
  echo "    planned version: $NEW"
  echo "    planned tag:     $TAG (signed)"
  echo "    planned push:    git push && git push --tags"
  echo "    release URL:     $RELEASE_URL"
  exit 0
fi

# ---------------------------------------------------------------------------
# Real release path.
# ---------------------------------------------------------------------------

if [ -n "$BUMP" ]; then
  printf '%s\n' "$NEW" > ./VERSION
  git commit -am "release: v${NEW}"
fi

echo "==> Running test suite (pytest tests/ -x)"
# Prefer `python` (laptop/Windows), fall back to `python3` (Linux authoring fallback).
PY="$(command -v python || command -v python3 || true)"
[ -n "$PY" ] || die "no python interpreter found (need python or python3)."
"$PY" -m pytest tests/ -x || die "test suite failed — aborting release."

echo "==> Hook smoke: ./hooks/session-start"
[ -x ./hooks/session-start ] || die "./hooks/session-start not found or not executable"
./hooks/session-start >/dev/null || die "session-start hook exited non-zero — aborting release."

echo "==> Creating signed tag $TAG"
# Signed per project policy; on failure surface the error and abort (no unsigned fallback).
git tag -s "$TAG" -m "superhuman ${TAG}" \
  || die "signed tag creation failed for $TAG — ensure a signing key is configured (git config user.signingkey ...). Not falling back to an unsigned tag."

echo "==> Pushing"
git push
git push --tags

echo
echo "==> Released $TAG"
echo "    $RELEASE_URL"
