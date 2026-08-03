#!/usr/bin/env bash
# autonomous-rollback.sh — Operator rollback for a v0.2.0 autonomous run.
#
# Given a project <slug>, this script:
#   1. Finds the autonomous/<slug>/<run-id> branch for that slug.
#   2. Archives it by creating an annotated tag archive/<branch> at its tip
#      (ARCHIVE-NEVER-DELETE: we tag, we do NOT delete the branch — history
#      is preserved for audit).
#   3. Resets main to the last human-approved ref for that project.
#
# CONCURRENT-RUN SAFETY: <slug> is REQUIRED. Without it the script refuses to
# run, preventing accidental rollback of the wrong project when multiple
# autonomous runs are in flight.
#
# Usage:
#   scripts/autonomous-rollback.sh <slug> [--dry-run]
#   scripts/autonomous-rollback.sh --help | -h
#
# --dry-run prints the autonomous branch found, the computed target ref, and
# the planned archive tag name, then exits 0 without creating tags or
# resetting anything.
#
# Exit codes: 0 ok; 2 usage error; non-zero on other errors (inherited from
# shell set -euo pipefail).

set -euo pipefail

usage() {
  cat <<'EOF'
autonomous-rollback.sh — operator rollback for a v0.2.0 autonomous run.

Usage:
  scripts/autonomous-rollback.sh <slug> [--dry-run]
  scripts/autonomous-rollback.sh --help | -h

Arguments:
  <slug>        Project slug (REQUIRED — concurrent-run safety, Q3). Must
                match the slug used in autonomous/<slug>/<run-id> branch names.

Options:
  --dry-run     Print the plan (branch found, target ref, archive tag name)
                and exit 0 without creating tags, branches, or resetting.
  --help, -h    Show this help and exit.

Archive policy (ARCHIVE-NEVER-DELETE):
  The autonomous branch is preserved via an annotated git tag at its tip:
    archive/autonomous/<slug>/<run-id>
  The branch itself is NOT deleted. Tags are not pushed by this script;
  the operator pushes manually.
EOF
}

die() {
  echo "autonomous-rollback.sh: $*" >&2
  exit 1
}

# ---------------------------------------------------------------------------
# Arg parsing — slug is positional and required.
# ---------------------------------------------------------------------------

SLUG=""
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $arg" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [ -z "$SLUG" ]; then
        SLUG="$arg"
      else
        echo "Unexpected argument: $arg" >&2
        usage >&2
        exit 2
      fi
      ;;
  esac
done

# Slug is required (concurrent-run safety, Q3).
if [ -z "$SLUG" ]; then
  echo "autonomous-rollback.sh: missing required argument <slug>" >&2
  echo "  A slug is required for concurrent-run safety — you must name the" >&2
  echo "  specific project slug you want to roll back." >&2
  usage >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Guards.
# ---------------------------------------------------------------------------

# Must be inside a git repo.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "not inside a git repository (cwd: $(pwd))"

# ---------------------------------------------------------------------------
# Find the autonomous branch for this slug.
# ---------------------------------------------------------------------------

# List branches matching autonomous/<slug>/* (short names only).
BRANCHES="$(git for-each-ref --format='%(refname:short)' "refs/heads/autonomous/${SLUG}/" 2>/dev/null || true)"

if [ -z "$BRANCHES" ]; then
  die "no autonomous branch found for slug '${SLUG}' (looked in refs/heads/autonomous/${SLUG}/*)"
fi

# Count the matches.
BRANCH_COUNT="$(printf '%s\n' "$BRANCHES" | grep -c '.')"

if [ "$BRANCH_COUNT" -gt 1 ]; then
  echo "autonomous-rollback.sh: multiple autonomous branches found for slug '${SLUG}':" >&2
  printf '%s\n' "$BRANCHES" | sed 's/^/  /' >&2
  echo "" >&2
  echo "  Please disambiguate: delete or archive all but the target run, then" >&2
  echo "  re-run this script. (v0.2.0 handles exactly one run per invocation.)" >&2
  exit 1
fi

AUTO_BRANCH="$BRANCHES"

# ---------------------------------------------------------------------------
# Determine the target ref to reset main to.
#
# Heuristic (in priority order):
#   1. Read the last human-approved git ref recorded in
#      docs/superhuman/<slug>/SUPERHUMAN.md — look for a line of the form
#        Approved-Ref: <sha-or-ref>
#      This is the most precise signal when the PM agent writes it.
#   2. Fall back to git merge-base <auto-branch> main — i.e., the common
#      ancestor between the autonomous branch and main at branch time.
#      This is safe even if the auto-branch diverged far from main.
# ---------------------------------------------------------------------------

SUPERHUMAN_MD="docs/superhuman/${SLUG}/SUPERHUMAN.md"
TARGET_REF=""

# Detect trunk branch once, early — used in merge-base fallback, dry-run echo,
# and the real checkout.  Prefer 'main'; fall back to 'master' for repos that
# haven't renamed the default branch yet.
TRUNK=""
if git rev-parse --verify main >/dev/null 2>&1; then
  TRUNK="main"
elif git rev-parse --verify master >/dev/null 2>&1; then
  TRUNK="master"
else
  die "could not find a 'main' or 'master' branch; set Approved-Ref in ${SUPERHUMAN_MD}"
fi

if [ -f "$SUPERHUMAN_MD" ]; then
  # Extract the first "Approved-Ref: <ref>" line, strip the prefix.
  TARGET_REF="$(grep -m1 '^Approved-Ref:' "$SUPERHUMAN_MD" | sed 's/^Approved-Ref:[[:space:]]*//' | tr -d '[:space:]' || true)"
fi

if [ -z "$TARGET_REF" ]; then
  # Fallback: merge-base between the autonomous branch and the trunk branch.
  TARGET_REF="$(git merge-base "$AUTO_BRANCH" "$TRUNK" 2>/dev/null || true)"
  if [ -z "$TARGET_REF" ]; then
    die "could not determine target ref: SUPERHUMAN.md has no Approved-Ref line and git merge-base failed for '${AUTO_BRANCH}' vs '${TRUNK}'"
  fi
fi

ARCHIVE_TAG="archive/${AUTO_BRANCH}"

# ---------------------------------------------------------------------------
# --dry-run: print the plan and exit, fully side-effect-free.
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> autonomous-rollback.sh --dry-run (no changes will be made)"
  echo "    slug:               ${SLUG}"
  echo "    autonomous branch:  ${AUTO_BRANCH}"
  echo "    target ref:         ${TARGET_REF}"
  echo "    planned archive tag: ${ARCHIVE_TAG}"
  echo "    planned actions (skipped in dry-run):"
  echo "      git tag -a '${ARCHIVE_TAG}' ${AUTO_BRANCH} -m 'archive: ${AUTO_BRANCH}'"
  echo "      git checkout ${TRUNK}"
  echo "      git reset --hard ${TARGET_REF}"
  exit 0
fi

# ---------------------------------------------------------------------------
# Real rollback path.
# ---------------------------------------------------------------------------

echo "==> Archiving autonomous branch '${AUTO_BRANCH}' (ARCHIVE-NEVER-DELETE)"
git tag -a "${ARCHIVE_TAG}" "${AUTO_BRANCH}" -m "archive: ${AUTO_BRANCH}"
echo "    Created tag: ${ARCHIVE_TAG}"

echo "==> Resetting ${TRUNK} to ${TARGET_REF}"
git checkout "${TRUNK}"
git reset --hard "${TARGET_REF}"

echo ""
echo "==> Rollback complete for slug '${SLUG}'"
echo "    archived branch: ${AUTO_BRANCH}  →  tag ${ARCHIVE_TAG}"
echo "    ${TRUNK} is now at:  $(git rev-parse --short HEAD)"
echo ""
echo "NOTE: No changes have been pushed. Run 'git push && git push --tags' to"
echo "      publish the archive tag and updated ${TRUNK} to origin."
