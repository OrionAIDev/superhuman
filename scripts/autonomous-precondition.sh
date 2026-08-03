#!/usr/bin/env bash
# autonomous-precondition.sh — compatibility shim (superhuman v0.7.0+).
#
# The deployment-ladder decision moved out of this script and into the
# deterministic resolver `scripts/superhuman_profile.py`, driven by a
# declarative profile (design spec
# docs/superhuman/specs/2026-07-24-portable-profile-and-ladder.md).
#
# This shim exists so that existing callers — phases/3-autonomous-loop.md
# Step 0, phases/0-kickoff.md Step 3, and the SKILL.md HARD-GATE — keep working
# unchanged. It preserves the pre-0.7.0 command line and exit codes exactly:
#
#   Usage:  autonomous-precondition.sh [<project-root>] [--level 1|2]
#           (level defaults to 1 if omitted, preserving pre-v0.5.0 callers;
#            the letter spellings H|M|L are also accepted)
#   Exit:   0 = allowed
#           2 = usage error, or no profile found while SUPERHUMAN_REQUIRE_PROFILE=1
#           3 = blocked by ladder policy, or missing rollback plan at HITL-L
#           4 = policy declared but unresolved — halt and escalate (new in 0.7.0;
#               safe because every caller aborts on any non-zero exit)
#
# Equivalence with the v0.6.0 implementation is proven by
# tests/test_golden_verdicts.py over a table of representative paths, including
# the legacy `*prod*` glob's known false positives. Do not edit policy here —
# edit the profile.

set -euo pipefail

ROOT="."
LEVEL="1"

while [ $# -gt 0 ]; do
  case "$1" in
    --level)     LEVEL="${2:-}"; shift 2 ;;
    --level=*)   LEVEL="${1#--level=}"; shift ;;
    *)           ROOT="$1"; shift ;;
  esac
done

# Prefer the bundle's venv interpreter when present, then python3, then python.
#
# Each candidate is EXECUTED, not merely tested for the executable bit. On
# Windows, `command -v python` resolves to the Microsoft Store "app execution
# alias" — a stub that exists, is executable, and exits 49 with an advert
# instead of running Python. An `-x` test passes it happily. Anyone who clones
# this repo without creating a venv hits exactly that, so the check has to be
# "does it actually run", not "does it look like an interpreter".
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
for candidate in \
  "$SCRIPT_DIR/../.venv/bin/python" \
  "$SCRIPT_DIR/../.venv/Scripts/python.exe" \
  "$(command -v python3 || true)" \
  "$(command -v python || true)"
do
  [ -n "$candidate" ] || continue
  if "$candidate" -c "import sys; sys.exit(0)" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done

if [ -z "${PY:-}" ]; then
  echo "autonomous-precondition: no working python interpreter found." >&2
  echo "  Tried: <bundle>/.venv, python3, python." >&2
  echo "  Install Python 3, or create the bundle venv and install tests/requirements.txt." >&2
  exit 2
fi

exec "$PY" "$SCRIPT_DIR/superhuman_profile.py" check "$ROOT" \
  --action act_unattended --level "$LEVEL"
