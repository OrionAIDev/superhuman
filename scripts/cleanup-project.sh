#!/usr/bin/env bash
# Cleanup script — archives a superhuman project's docs/superhuman/<slug>/ state
# (and optionally source code) so a fresh run starts cleanly.
#
# Usage:
#   scripts/cleanup-project.sh <project-root>
#   scripts/cleanup-project.sh <project-root> --slug <slug>
#   scripts/cleanup-project.sh <project-root> [--slug <slug>] --include-code
#
# Archive-never-delete: nothing is removed, only moved to
#   <project-root>/docs/superhuman/archive/<slug>-pre-cleanup-<timestamp>/

set -euo pipefail

PROJECT=""
SLUG=""
INCLUDE_CODE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --slug) SLUG="$2"; shift 2 ;;
    --include-code) INCLUDE_CODE=1; shift ;;
    --help|-h)
      head -16 "$0" | tail -14
      exit 0
      ;;
    *)
      if [ -z "$PROJECT" ]; then
        PROJECT="$1"
        shift
      else
        echo "Unknown argument: $1" >&2
        exit 2
      fi
      ;;
  esac
done

if [ -z "$PROJECT" ]; then
  echo "Usage: $0 <project-root> [--slug <slug>] [--include-code]" >&2
  exit 2
fi
if [ ! -d "$PROJECT" ]; then
  echo "Project root not found: $PROJECT" >&2
  exit 2
fi
if [ ! -d "$PROJECT/docs/superhuman" ]; then
  echo "No docs/superhuman/ in $PROJECT — nothing to clean up." >&2
  exit 1
fi

cd "$PROJECT"

# Auto-detect slug if not provided
if [ -z "$SLUG" ]; then
  SLUGS=()
  for d in docs/superhuman/*/; do
    name=$(basename "$d")
    [ "$name" = "archive" ] && continue
    SLUGS+=("$name")
  done
  if [ "${#SLUGS[@]}" -eq 0 ]; then
    echo "No project slug found under docs/superhuman/ — already cleaned up?"
    exit 0
  elif [ "${#SLUGS[@]}" -gt 1 ]; then
    echo "Multiple slugs found; specify --slug <name>:" >&2
    for s in "${SLUGS[@]}"; do echo "  $s"; done
    exit 2
  fi
  SLUG="${SLUGS[0]}"
fi

if [ ! -d "docs/superhuman/$SLUG" ]; then
  echo "Slug not found: docs/superhuman/$SLUG" >&2
  exit 2
fi

TS=$(date -u +%Y%m%d-%H%M%S)
ARCHIVE_DIR="docs/superhuman/archive/${SLUG}-pre-cleanup-${TS}"

echo "==> Cleanup plan"
echo "    project:    $PROJECT"
echo "    slug:       $SLUG"
echo "    archive to: $ARCHIVE_DIR"
echo "    include code: $([ $INCLUDE_CODE -eq 1 ] && echo yes || echo no)"
echo

mkdir -p "$ARCHIVE_DIR"

# Move the slug's contents into the archive directory
mv "docs/superhuman/$SLUG"/* "$ARCHIVE_DIR/" 2>/dev/null || true
# Move dotfiles too if present
shopt -s dotglob 2>/dev/null || true
mv "docs/superhuman/$SLUG"/.* "$ARCHIVE_DIR/" 2>/dev/null || true
shopt -u dotglob 2>/dev/null || true
# Remove the now-empty directory
rmdir "docs/superhuman/$SLUG" 2>/dev/null || true

# Code archival (opt-in)
CODE_ARCHIVED=()
if [ $INCLUDE_CODE -eq 1 ]; then
  for candidate in src tests pyproject.toml setup.py setup.cfg requirements.txt requirements-dev.txt requirements-test.txt README.md CHANGELOG.md LICENSE .gitignore .env.example; do
    if [ -e "$candidate" ]; then
      mv "$candidate" "$ARCHIVE_DIR/"
      CODE_ARCHIVED+=("$candidate")
    fi
  done
  # Top-level Python files
  for f in *.py; do
    [ -e "$f" ] || continue
    mv "$f" "$ARCHIVE_DIR/"
    CODE_ARCHIVED+=("$f")
  done
  # SAFETY: never archive a real .env (secrets) — note it and leave it in place.
  if [ -e ".env" ]; then
    echo "    note: .env left in place (secrets never archived)"
  fi
fi

# WHY.md
cat > "$ARCHIVE_DIR/WHY.md" <<EOF
# Why this was archived

**Archived:** ${TS}
**Trigger:** cleanup-project script run
**Action:** pre-cleanup snapshot

## What was archived

- \`docs/superhuman/${SLUG}/\` (all contents)
$([ $INCLUDE_CODE -eq 1 ] && echo "- Source code files at project root:" && printf '  - \`%s\`\n' "${CODE_ARCHIVED[@]}")

## Why

User invoked \`scripts/cleanup-project.sh\` to start a fresh superhuman run on this project. Existing state was moved here rather than deleted, per the archive-never-delete principle.
EOF

# RESTORE.md
cat > "$ARCHIVE_DIR/RESTORE.md" <<EOF
# How to restore this archive

To put everything back where it was:

\`\`\`bash
cd "$PROJECT"
mkdir -p "docs/superhuman/${SLUG}"
mv "${ARCHIVE_DIR}"/* "docs/superhuman/${SLUG}/" 2>/dev/null || true
EOF

if [ $INCLUDE_CODE -eq 1 ] && [ "${#CODE_ARCHIVED[@]}" -gt 0 ]; then
  for c in "${CODE_ARCHIVED[@]}"; do
    echo "mv \"docs/superhuman/${SLUG}/${c}\" \"./${c}\"" >> "$ARCHIVE_DIR/RESTORE.md"
  done
fi

cat >> "$ARCHIVE_DIR/RESTORE.md" <<EOF
rmdir "${ARCHIVE_DIR}" 2>/dev/null || true
\`\`\`

Note that the slug directory inside the archive will need to be unwound carefully if it was non-trivial; this restore script does a best-effort move.
EOF

echo
echo "==> Done. Archived to $ARCHIVE_DIR"
echo "    Restore instructions: $ARCHIVE_DIR/RESTORE.md"
