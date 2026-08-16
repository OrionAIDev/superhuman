#!/usr/bin/env bash
# autonomous-summary.sh — emit a human-readable run summary for a superhuman project.
#
# Usage:
#   scripts/autonomous-summary.sh <slug> [<project-root>]
#
# Reads <project-root>/docs/superhuman/<slug>/SUPERHUMAN.md (default root = cwd).
# Prints a report to STDOUT:
#   - Header naming the slug
#   - Extracted ## Autonomous iterations log table
#   - Extracted ## Autonomous run config section (if present)
#   - Rollback command line
#
# Pure read/format script — NO git mutation.
#
# Exit codes:
#   0  success
#   1  usage error or missing file

set -euo pipefail

usage() {
  cat <<'EOF'
autonomous-summary.sh — emit a run summary for a superhuman project.

Usage:
  scripts/autonomous-summary.sh <slug> [<project-root>]

Arguments:
  <slug>           Project slug (required). Matches docs/superhuman/<slug>/.
  <project-root>   Path to the project root (default: current working directory).

Output goes to STDOUT.
EOF
}

die() {
  echo "autonomous-summary.sh: $*" >&2
  exit 1
}

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

[ $# -ge 1 ] || { usage >&2; die "slug is required"; }

SLUG="$1"
PROJECT_ROOT="${2:-$(pwd)}"

SUPERHUMAN_MD="${PROJECT_ROOT}/docs/superhuman/${SLUG}/SUPERHUMAN.md"

[ -f "$SUPERHUMAN_MD" ] || die "SUPERHUMAN.md not found: ${SUPERHUMAN_MD}"

# ---------------------------------------------------------------------------
# Report header
# ---------------------------------------------------------------------------

echo "=== Autonomous Run Summary: ${SLUG} ==="
echo "Source: ${SUPERHUMAN_MD}"
echo ""

# ---------------------------------------------------------------------------
# Extract ## Autonomous iterations log
# ---------------------------------------------------------------------------

echo "## Autonomous iterations log"
echo ""

# Extract from the heading line until the next ## heading or EOF.
# Uses awk: start printing after the heading, stop at next ## heading.
awk '
  /^## Autonomous iterations log/ { printing=1; next }
  printing && /^## / { exit }
  printing { print }
' "$SUPERHUMAN_MD"

echo ""

# ---------------------------------------------------------------------------
# Extract ## Autonomous run config (if present)
# ---------------------------------------------------------------------------

if grep -q "^## Autonomous run config" "$SUPERHUMAN_MD" 2>/dev/null; then
  echo "## Autonomous run config"
  echo ""
  awk '
    /^## Autonomous run config/ { printing=1; next }
    printing && /^## / { exit }
    printing { print }
  ' "$SUPERHUMAN_MD"
  echo ""
fi

# ---------------------------------------------------------------------------
# Rollback command
# ---------------------------------------------------------------------------

echo "## Rollback command"
echo ""
echo "  scripts/autonomous-rollback.sh ${SLUG}"
echo ""
