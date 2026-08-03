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
#                                      [--slug <project>] [--kickoff]
#           (level defaults to 1 if omitted, preserving pre-v0.5.0 callers;
#            the letter spellings H|M|L are also accepted)
#   Exit:   0 = allowed
#           2 = usage error, or no profile found while SUPERHUMAN_REQUIRE_PROFILE=1
#           3 = blocked by ladder policy, no git remote, missing GOAL.md, or a
#               missing/undeclared rollback plan at HITL-L
#           4 = policy declared but unresolved — halt and escalate (new in 0.7.0;
#               safe because every caller aborts on any non-zero exit). Also
#               returned when a project-state precondition cannot be scoped
#               because no --slug was given.
#
# `--slug` names the project under docs/superhuman/ whose state is checked. It
# is not optional in effect: without it the project-state preconditions cannot
# be scoped, and the gate exits 4 rather than guessing across sibling projects
# (roadmap #143 — the unscoped check answered about the wrong project, and
# passed vacuously when no sibling tripped it).
#
# `--kickoff` is for phases/0-kickoff.md Step 3 only, where the project's own
# state is still being written: it checks the ladder and git+remote and defers
# GOAL.md and the rollback plan to the unflagged re-run at the end of kickoff.
# Never pass it to authorize a loop.
#
# Do not edit policy here — edit the profile.

set -euo pipefail

ROOT="."
LEVEL="1"
SLUG=""
KICKOFF=""

while [ $# -gt 0 ]; do
  case "$1" in
    --level)     LEVEL="${2:-}"; shift 2 ;;
    --level=*)   LEVEL="${1#--level=}"; shift ;;
    --slug|--project)   SLUG="${2:-}"; shift 2 ;;
    --slug=*)    SLUG="${1#--slug=}"; shift ;;
    --project=*) SLUG="${1#--project=}"; shift ;;
    --kickoff)   KICKOFF="1"; shift ;;
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

# Built with `if`, not `[ -n "$X" ] && ARGS+=(…)`: under `set -e` a trailing
# `&&` list whose test fails takes the whole script's exit status with it.
ARGS=(check "$ROOT" --action act_unattended --level "$LEVEL")
if [ -n "$SLUG" ]; then
  ARGS+=(--slug "$SLUG")
fi
if [ -n "$KICKOFF" ]; then
  ARGS+=(--kickoff)
fi

exec "$PY" "$SCRIPT_DIR/superhuman_profile.py" "${ARGS[@]}"
