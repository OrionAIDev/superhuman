#!/usr/bin/env bash
# install-hooks.sh — wire the superhuman pre-commit hook into this repo (F15).
#
# Installs scripts/git-hooks/pre-commit as .git/hooks/pre-commit for the git repo
# that contains THIS script. Prefers a symlink; falls back to a copy where
# symlinking is unavailable (e.g. Windows without the required privilege). Either
# way the result is an executable .git/hooks/pre-commit.
#
# Idempotent: re-running refreshes the installed hook without error. An unrelated
# pre-existing pre-commit is backed up to .git/hooks/pre-commit.bak-<timestamp>
# before being overwritten (archive, never delete).
#
# Usage:
#   scripts/install-hooks.sh        # install / refresh the hook
#   scripts/install-hooks.sh --help

set -euo pipefail

usage() {
    cat <<'EOF'
install-hooks.sh — install the superhuman pre-commit hook into this repo.

Usage:
  scripts/install-hooks.sh        Install or refresh .git/hooks/pre-commit.
  scripts/install-hooks.sh --help Show this help.

The hook runs the fast test suite before each commit and refuses the commit on
failure. Bypass in an emergency with: git commit --no-verify
EOF
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    "")
        ;;
    *)
        echo "install-hooks.sh: unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
esac

# Resolve paths relative to THIS script so cwd does not matter.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_HOOK="$SCRIPT_DIR/git-hooks/pre-commit"

if [ ! -f "$SOURCE_HOOK" ]; then
    echo "install-hooks.sh: source hook not found: $SOURCE_HOOK" >&2
    exit 1
fi

# Install into the git repo that contains this script.
REPO_TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_TOP/.git/hooks"
DEST="$HOOKS_DIR/pre-commit"

mkdir -p "$HOOKS_DIR"

# Back up an unrelated pre-existing hook (not one of ours) before overwriting.
if [ -e "$DEST" ] && [ ! -L "$DEST" ]; then
    if ! grep -q "fast-test gate for the superhuman skill" "$DEST" 2>/dev/null \
        && ! cmp -s "$DEST" "$SOURCE_HOOK"; then
        BACKUP="$DEST.bak-$(date -u +%Y%m%d-%H%M%S)"
        cp -p "$DEST" "$BACKUP"
        echo "install-hooks.sh: backed up existing pre-commit -> $BACKUP"
    fi
fi

# Refresh: remove any current hook (symlink or file) before reinstalling.
rm -f "$DEST"

# Prefer a symlink; fall back to a copy if symlinking fails.
if ln -s "$SOURCE_HOOK" "$DEST" 2>/dev/null; then
    echo "install-hooks.sh: linked .git/hooks/pre-commit -> $SOURCE_HOOK"
else
    cp "$SOURCE_HOOK" "$DEST"
    echo "install-hooks.sh: copied pre-commit hook into .git/hooks/ (symlink unavailable)"
fi

chmod +x "$DEST" 2>/dev/null || true

echo "install-hooks.sh: pre-commit hook installed."
exit 0
