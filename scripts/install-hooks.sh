#!/usr/bin/env bash
# install-hooks.sh — wire the superhuman git hooks into this repo (F15).
#
# Installs scripts/git-hooks/<name> as .git/hooks/<name>, for both `pre-commit`
# (the fast-test gate) and `commit-msg` (the message guard, roadmap#242), for the
# git repo that contains THIS script.
# Prefers a symlink; falls back to a copy where
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
install-hooks.sh — install the superhuman git hooks into this repo.

Usage:
  scripts/install-hooks.sh        Install or refresh pre-commit and commit-msg.
  scripts/install-hooks.sh --help Show this help.

  pre-commit  runs the fast test suite and refuses the commit on failure.
  commit-msg  refuses a commit whose MESSAGE carries operator vocabulary or an
              infrastructure leak — a separate control, not a step inside the
              file guard.

Bypass either in an emergency with: git commit --no-verify
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

# Install into the git repo that contains this script.
REPO_TOP="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_TOP/.git/hooks"

mkdir -p "$HOOKS_DIR"

# Marker text identifying a hook as ours, so a user's unrelated pre-existing hook
# of the same name is archived rather than silently overwritten. One entry per
# installed hook; the marker must appear in that hook's own header comment.
hook_marker() {
    case "$1" in
        pre-commit) echo "fast-test gate for the superhuman skill" ;;
        commit-msg) echo "keep private vocabulary out of commit MESSAGES" ;;
        *) echo "" ;;
    esac
}

install_hook() {
    local name="$1"
    local source_hook="$SCRIPT_DIR/git-hooks/$name"
    local dest="$HOOKS_DIR/$name"
    local marker
    marker="$(hook_marker "$name")"

    if [ ! -f "$source_hook" ]; then
        echo "install-hooks.sh: source hook not found: $source_hook" >&2
        return 1
    fi

    # Back up an unrelated pre-existing hook (not one of ours) before overwriting.
    if [ -e "$dest" ] && [ ! -L "$dest" ]; then
        if ! grep -q "$marker" "$dest" 2>/dev/null && ! cmp -s "$dest" "$source_hook"; then
            local backup="$dest.bak-$(date -u +%Y%m%d-%H%M%S)"
            cp -p "$dest" "$backup"
            echo "install-hooks.sh: backed up existing $name -> $backup"
        fi
    fi

    # Refresh: remove any current hook (symlink or file) before reinstalling.
    rm -f "$dest"

    # Prefer a symlink; fall back to a copy if symlinking fails.
    if ln -s "$source_hook" "$dest" 2>/dev/null; then
        echo "install-hooks.sh: linked .git/hooks/$name -> $source_hook"
    else
        cp "$source_hook" "$dest"
        echo "install-hooks.sh: copied $name hook into .git/hooks/ (symlink unavailable)"
    fi

    chmod +x "$dest" 2>/dev/null || true
}

# Both hooks install together on purpose. They are independent controls -- the
# message guard is NOT a step inside the file guard (see git-hooks/commit-msg) --
# but installing only one leaves a published surface unwatched, and "remember to
# also install the other one" is the class of instruction that produced
# roadmap#242 in the first place.
for hook in pre-commit commit-msg; do
    install_hook "$hook"
done

echo "install-hooks.sh: pre-commit and commit-msg hooks installed."
exit 0
