"""Idempotently installs/removes superhuman's own hook entries in a Claude
Code `settings.json` (PLAN.md Chunk 8, DESIGN.md component table line 515,
FR-9, A3).

**Harness-specific by design (D4's one deliberate exception).** Every other
module under `scripts/fleet/` names no harness. This one must: `settings.json`
IS Claude Code's own configuration file, and D4's boundary rule (restated at
`SUPERHUMAN.md` "Decisions locked") draws the line at exactly this module plus
the installer's own `--harness claude-code` flag.

**Ownership rule (R5, chunk-8 PM ruling, 2026-09-16).** A hook entry is
superhuman-owned iff its `command` string resolves to one of the three
wrapper basenames under a `templates/hooks/claude-code/` path -- WHATEVER
root it points at. That is what lets `install()` REPLACE a stale,
worktree-rooted hand install (see the 2026-09-09 hand registration recorded
in SUPERHUMAN.md) instead of adding a duplicate beside it, and it is why
`uninstall()` removes a migrated entry too: it is ours, wherever it points.

**Where registered commands point (R1).** Auto-resolution (no `--skill-root`)
derives the root from THIS MODULE'S OWN file location via
``git rev-parse --path-format=absolute --git-common-dir`` -- the identical
resolution shape ``tests/repo_artifacts.py``'s ``main_checkout_root`` already
uses for publication tokens (chunk 6a), so this repo gains no second pattern.
That always lands on the MAIN checkout, never a linked worktree (a worktree's
own git-common-dir is always the main checkout's), so a defensive check
still runs and refuses (non-zero exit, naming the resolved root) on the
degenerate case where it somehow does not. ``--skill-root`` is an explicit
operator override: never refused, but the root it names is reported as
worktree-pinned by `status()` when applicable.

**Harness facts (R3), verified against the docs, not recalled from training
data** -- https://code.claude.com/docs/en/hooks.md (checked 2026-09-16):
`SessionStart` matchers are `startup`, `resume`, `clear`, `compact`, `fork`
(all five documented; FR-19 registers all five). `SubagentStart` takes
`"*"` (match-all). A matcher containing only letters, digits, `_`, `-`,
spaces, `,` and `|` is exact alternation, not a regex, so `PreToolUse`
matcher `"Agent|Task"` matches exactly those two tool names. `timeout` is in
SECONDS and a `command` hook's default is 600 -- which is what makes the
`"timeout": 10` this module writes on every entry a real backstop (NFR-2)
rather than a formality.

**Never edited in place.** Every write goes through a temp file in the same
directory, then `os.replace()` (TC-98) -- a crash between the two leaves the
real file exactly as it was, never observed half-written (see PLAN.md's
Backup strategy section).

**Tests never touch the real file.** `settings_path` is an explicit
parameter on every public function; the default is for real operator use
only (FR-9). `tests/fleet/test_hooks_install.py` enforces this three ways
of its own (a static self-check, a fixture, and a module-scoped tripwire) --
see that module's docstring.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

#: Real-operator default (FR-9). No test in `test_hooks_install.py` may rely
#: on this -- every call there passes `settings_path=` explicitly.
DEFAULT_SETTINGS_PATH: Final[Path] = Path.home() / ".claude" / "settings.json"

#: The three wrapper basenames this installer manages, all under
#: `templates/hooks/claude-code/` (R2: the extensionless bash wrapper, never
#: the `.cmd` shim -- the shim stays shipped only as a non-executable-bit
#: fallback).
_SESSION_START_BASENAME: Final[str] = "session-start"
_SUBAGENT_START_BASENAME: Final[str] = "subagent-start"
_PRE_TOOL_USE_BASENAME: Final[str] = "pre-tool-use-role-gate"

#: FR-19: the full documented `SessionStart` matcher set (R3).
_SESSION_START_MATCHERS: Final[tuple[str, ...]] = ("startup", "resume", "clear", "compact", "fork")
_SUBAGENT_START_MATCHER: Final[str] = "*"
_PRE_TOOL_USE_MATCHER: Final[str] = "Agent|Task"

#: G4-locked backstop (SUPERHUMAN.md "Decisions locked", TC-60).
_TIMEOUT_SECONDS: Final[int] = 10

#: R5's ownership test, TIGHTENED at the Phase 3.3 preflight (recommended
#: fix, not one of B1-B8): a command resolves to one of the three basenames
#: under a `templates/hooks/claude-code/` path segment, AND the path also
#: names a `superhuman` directory segment somewhere before it. Matched
#: after normalising `\` to `/` so a Windows-style command string still
#: matches.
#:
#: Two independent defects, fixed together because the fix for one shapes
#: the fix for the other:
#:
#: **False negative** -- the original pattern anchored on the wrapper
#: basename alone (`session-start` etc.), so a `.cmd`-suffixed command --
#: R2's own documented Windows fallback, and the most likely shape of a
#: hand install on a machine where the extensionless form is not
#: executable -- was never recognised as ours, producing a DUPLICATE entry
#: on install() instead of a migration (TC-113). Fixed by the trailing
#: `(?:\.cmd)?` alternative.
#:
#: **False positive** -- the original pattern matched ANY command ending in
#: `/templates/hooks/claude-code/<basename>`, whatever preceded it, so an
#: unrelated tool that happened to use the identical conventional layout
#: would be silently claimed as ours and removed/replaced (TC-114). The
#: minimal SOUND criterion considered was requiring the resolved root to
#: exist on disk as a real superhuman checkout (e.g. `scripts/fleet/
#: hooks_install.py` present under it) -- rejected: R1's own chunk-8 ruling
#: is that a real install today points at the MAIN checkout, which does
#: NOT yet carry `scripts/fleet/` at all until this branch merges (see
#: SUPERHUMAN.md, chunk 8 R1's "a real install today would leave the fleet
#: hooks inert until this branch merges"), so an on-disk check would make
#: every pre-merge install a false NEGATIVE against its own intended
#: target -- reintroducing the very defect class this fix exists to close.
#: Adopted instead: require a `superhuman` path segment. Every real
#: deployment shape carries one -- the main checkout (`.../skills/
#: superhuman/`), a linked worktree (`.../skills/superhuman/.claude/
#: worktrees/<slug>/`), and the real 2026-09-09 hand install all do -- and
#: an unrelated tool would need to coincidentally use BOTH the exact
#: `templates/hooks/claude-code/<basename>` layout AND a `superhuman`
#: ancestor directory name to still collide, which this module's docstring
#: judges acceptably unlikely for a "should fix, not blocking" item.
#: Known residual limitation, stated rather than silently accepted: an
#: operator who passes an explicit `--skill-root` NOT containing
#: `superhuman` (a deliberate override to a differently-named checkout)
#: writes entries a later call will no longer recognise as owned -- a
#: pre-existing risk of that override, now made slightly more visible
#: rather than newly introduced by this tightening.
#:
#: Phase 3.3 preflight RE-RUN item C (Major): the `$`-anchor above required
#: the basename to be the LAST characters of the whole command string, so
#: any spelling that put anything after it -- a closing quote (mandatory
#: once the checkout path contains a space -- this is a public repo, not a
#: hypothetical), trailing whitespace, or a `bash "..."` wrapper prefix --
#: was classified FOREIGN. Consequence: `install()` duplicated instead of
#: replacing, and `uninstall()` orphaned the entry -- the rollback path
#: this project's own rollback plan names.
#:
#: Fixed by replacing `$` with a negative lookahead for a continuing
#: path/word character, `(?![\w./-])`: the basename (plus optional `.cmd`)
#: may now be followed by a quote, whitespace, another shell token, or
#: end-of-string, but NOT by more path characters -- so the item-1
#: false-positive guard still holds: an unrelated tool merely PREFIXED by
#: one of our basenames (e.g. `.../session-start-legacy`) is still
#: rejected, because the `-` immediately after `session-start` IS a
#: member of the excluded class and blocks the lookahead. A lookahead was
#: chosen over hand-stripping surrounding quotes because a `bash "..."`
#: wrapper is only PARTIALLY quoted (the quote does not wrap the whole
#: command string), so whole-string quote-stripping would not have
#: covered it; matching the path token's own end this way generalises to
#: that shape (and any future wrapper prefix) without enumerating every
#: possible quoting/wrapping style by hand.
_OWNED_COMMAND_RE: Final[re.Pattern[str]] = re.compile(
    r"/superhuman/.*templates/hooks/claude-code/"
    r"(session-start|subagent-start|pre-tool-use-role-gate)(?:\.cmd)?(?![\w./-])"
)


class HooksInstallError(Exception):
    """Base class for every error this module raises."""


class SkillRootRefusedError(HooksInstallError):
    """The resolved skill root is inside a linked git worktree.

    Attributes:
        root: the refused root.
    """

    def __init__(self, root: Path) -> None:
        """Initialize with the refused root, building the operator-facing message.

        Args:
            root: the resolved root that was refused.
        """
        super().__init__(
            f"refusing to install: resolved skill root {root} is a linked git "
            "worktree, not the main checkout (roadmap#217 -- a worktree can be "
            "reaped, silently breaking a machine-wide hook). Pass an explicit "
            "--skill-root to override deliberately."
        )
        self.root = root


class SkillRootInsideGitDirError(HooksInstallError):
    """The resolved skill root is inside a `.git` directory (Phase 3.3
    preflight recommended fix, item 2): a submodule's `git rev-parse
    --git-common-dir` names `<superproject>/.git/modules/<name>`, and this
    module's `_default_skill_root` takes that value's `.parent` unchanged
    -- landing on `<superproject>/.git/modules`, a directory *inside*
    `.git` that is never a working tree at all. `_is_linked_worktree` does
    not catch this (its own `(root / ".git").is_file()` test is False
    there -- `<superproject>/.git/modules/.git` does not exist), so
    without this check `resolve_skill_root` would silently accept a
    nonsense root and register hook commands nobody could ever run.

    Attributes:
        root: the refused root.
    """

    def __init__(self, root: Path) -> None:
        """Initialize with the refused root, building the operator-facing message.

        Args:
            root: the resolved root that was refused.
        """
        super().__init__(
            f"refusing to install: resolved skill root {root} is inside a "
            "`.git` directory, not a working tree -- this is the shape "
            "`git rev-parse --git-common-dir` produces when this module's "
            "own file is running from inside a git submodule "
            "(`<superproject>/.git/modules/<name>`). Pass an explicit "
            "--skill-root to override deliberately."
        )
        self.root = root


@dataclass(frozen=True, slots=True)
class InstallResult:
    """Outcome of one `install()` call.

    Attributes:
        root: the skill-checkout root the written commands point at.
        worktree_pinned: True when `root` is a linked worktree -- only
            possible via an explicit `skill_root` override; auto-resolution
            raises `SkillRootRefusedError` instead of ever returning this
            as True.
        changed: whether the file's content differs from before this call.
        diff: a unified diff (old vs. new); empty when `changed` is False.
    """

    root: Path
    worktree_pinned: bool
    changed: bool
    diff: str


@dataclass(frozen=True, slots=True)
class UninstallResult:
    """Outcome of one `uninstall()` call.

    Attributes:
        changed: whether the file's content differs from before this call.
        diff: a unified diff (old vs. new); empty when `changed` is False.
    """

    changed: bool
    diff: str


@dataclass(frozen=True, slots=True)
class EntryStatus:
    """Status of one expected hook entry.

    Attributes:
        hook_event: e.g. `"SessionStart"`.
        matcher: the matcher string this entry is registered under.
        basename: the wrapper basename expected for this entry.
        present: whether a superhuman-owned command was found here.
        command: the actual registered command string, or None.
        command_path_exists: whether `command` names a path that exists on
            disk (D7.8's silent-disable case) -- False when absent.
        worktree_pinned: whether `command`'s root is a linked git worktree.
    """

    hook_event: str
    matcher: str
    basename: str
    present: bool
    command: str | None
    command_path_exists: bool
    worktree_pinned: bool


@dataclass(frozen=True, slots=True)
class InstallStatus:
    """Aggregate status across every expected entry.

    Attributes:
        installed: True iff every expected entry is present.
        entries: one `EntryStatus` per expected (hook_event, matcher, basename).
    """

    installed: bool
    entries: tuple[EntryStatus, ...]


def _expected_entries() -> tuple[tuple[str, str, str], ...]:
    """Return every (hook_event, matcher, basename) this installer manages.

    Returns:
        A tuple of 7 triples: 5 `SessionStart` matchers (FR-19), 1
        `SubagentStart` match-all, 1 `PreToolUse` `Agent|Task` (chunk 7a's
        role gate, installed here per DESIGN.md D7.8).
    """
    entries = [("SessionStart", matcher, _SESSION_START_BASENAME) for matcher in _SESSION_START_MATCHERS]
    entries.append(("SubagentStart", _SUBAGENT_START_MATCHER, _SUBAGENT_START_BASENAME))
    entries.append(("PreToolUse", _PRE_TOOL_USE_MATCHER, _PRE_TOOL_USE_BASENAME))
    return tuple(entries)


def _owned_basename(command: str) -> str | None:
    """Apply R5's ownership test to one command string.

    Args:
        command: a hook entry's `command` field.

    Returns:
        The matched wrapper basename, or None if `command` is not
        superhuman-owned.
    """
    match = _OWNED_COMMAND_RE.search(command.replace("\\", "/"))
    return match.group(1) if match else None


def _command_for(root: Path, basename: str) -> str:
    """Build the command string this installer writes for `basename`.

    Args:
        root: the resolved skill-checkout root.
        basename: one of the three wrapper basenames.

    Returns:
        A forward-slash path string, matching the live hand-installed
        entry's own format (`C:/Users/.../templates/hooks/claude-code/...`)
        regardless of platform.
    """
    return f"{root.as_posix()}/templates/hooks/claude-code/{basename}"


def _root_from_command(command: str) -> Path:
    """Recover the root a superhuman-owned command was written against.

    Args:
        command: a command string that `_owned_basename` matched.

    Returns:
        The path preceding `/templates/hooks/claude-code/<basename>`.
    """
    normalized = command.replace("\\", "/")
    marker = "/templates/hooks/claude-code/"
    return Path(normalized[: normalized.rindex(marker)])


def _is_linked_worktree(root: Path) -> bool:
    """Structurally test whether `root` is a linked git worktree.

    A linked worktree's own `.git` is a FILE holding a `gitdir:` pointer;
    a main checkout's `.git` is a directory. This is the exact mechanism
    git itself uses, so it needs no subprocess call and is directly
    testable against a bare fixture directory.

    Args:
        root: a candidate working-tree root.

    Returns:
        True iff `root / ".git"` exists and is a regular file.
    """
    return (root / ".git").is_file()


def _is_inside_dot_git(root: Path) -> bool:
    """Structurally test whether `root` sits inside a `.git` directory.

    A submodule's `git rev-parse --git-common-dir` names
    `<superproject>/.git/modules/<name>`; `_default_skill_root` takes that
    value's `.parent`, landing on `<superproject>/.git/modules` -- a
    directory whose own path carries `.git` as an ancestor SEGMENT, never
    as its own final component (a normal main checkout's `--git-common-dir`
    is exactly `<root>/.git`, whose parent is the working tree root
    itself, with no `.git` segment anywhere in it).

    Args:
        root: a candidate working-tree root.

    Returns:
        True iff `.git` appears anywhere among `root`'s own path
        components -- the sound, structural signature of "this path is
        inside git's own private directory," not a real working tree.
    """
    return ".git" in root.parts


def _default_skill_root() -> Path:
    """Resolve the main checkout root from this module's OWN location (R1).

    Runs `git rev-parse --path-format=absolute --git-common-dir` with `-C`
    set to this file's own directory -- never to `settings_path` or any
    caller-supplied path, since the root registered commands point at must
    be independent of which settings file happens to be getting edited.
    stdout is decoded explicitly as UTF-8 (never `subprocess.run(...,
    text=True)`, which decodes with the ambient code page and reproduces
    the exact defect class fixed at 5894617 for hook stdin on Windows).

    Returns:
        The main checkout's working-tree root -- `--git-common-dir`'s
        parent, which is the main checkout's own working tree whether this
        module is running from the main checkout or a linked worktree
        (mirrors `tests/repo_artifacts.py::main_checkout_root`'s reasoning).

    Raises:
        HooksInstallError: git is unavailable, this file is not inside a
            git working tree, or the invocation fails or times out.
    """
    module_dir = Path(__file__).resolve().parent
    try:
        completed = subprocess.run(
            ["git", "-C", str(module_dir), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HooksInstallError(
            f"could not resolve the main checkout root via git from {module_dir}: {exc}"
        ) from exc
    common_dir_text = completed.stdout.decode("utf-8").strip()
    if not common_dir_text:
        raise HooksInstallError(f"`git rev-parse --git-common-dir` returned nothing for {module_dir}")
    return Path(common_dir_text).parent


def resolve_skill_root(skill_root: Path | None = None) -> tuple[Path, bool]:
    """Resolve the root that registered commands should point at (R1).

    Args:
        skill_root: an explicit operator override. When given, used as-is
            and never refused -- the caller has taken responsibility.

    Returns:
        A `(root, pinned)` pair: `root` is the resolved skill-checkout
        root; `pinned` is True iff `skill_root` was explicitly supplied.

    Raises:
        SkillRootRefusedError: auto-resolution (`skill_root` is None)
            landed inside a linked worktree.
        SkillRootInsideGitDirError: auto-resolution landed inside a `.git`
            directory (the submodule shape -- Phase 3.3 preflight item 2).
        HooksInstallError: git resolution failed outright.
    """
    if skill_root is not None:
        return skill_root, True
    root = _default_skill_root()
    if _is_inside_dot_git(root):
        raise SkillRootInsideGitDirError(root)
    if _is_linked_worktree(root):
        raise SkillRootRefusedError(root)
    return root, False


def _dump_settings(data: dict[str, Any]) -> str:
    """Serialize `data` matching the real file's observed shape.

    Args:
        data: the full settings document.

    Returns:
        JSON text: 2-space indent, LF line endings, one trailing newline,
        no BOM -- matching `~/.claude/settings.json` as measured on this
        machine (2026-09-16).
    """
    return json.dumps(data, indent=2) + "\n"


def _atomic_write(settings_path: Path, text: str) -> None:
    """Write `text` to `settings_path` atomically (TC-98).

    Writes to a temp file in the SAME directory, then `os.replace()` --
    never edited in place, so a crash between the two leaves the real file
    exactly as it was, never observed half-written. On any failure --
    including one this function cannot otherwise observe, e.g. a
    non-`OSError` exception -- the temp file is removed rather than left
    orphaned (Phase 3.3 preflight item 6: the original code only cleaned
    up on `OSError`, via `except OSError: tmp_path.unlink(...); raise`, so
    any other exception type leaked the temp file; the `try/finally`
    below cleans up on ANY exception, not just that one family).

    Also preserves `settings_path`'s existing file mode across the replace
    (item 6's other half): `tempfile.mkstemp` creates its file mode
    `0600` (owner read/write only) per the stdlib docs --
    https://docs.python.org/3/library/tempfile.html#tempfile.mkstemp
    ("the file is readable and writable only by the creating user ID") --
    and `os.replace` carries the REPLACING file's own mode over the
    replaced file's, so writing through an unmodified temp file would
    silently tighten a pre-existing, more permissive settings.json (e.g.
    `0644`) down to `0600` on every install/uninstall. When `settings_path`
    already exists, this function reads its mode first and `os.chmod`s the
    temp file to match before the replace; `os.chmod` is a no-op beyond
    the read-only attribute bit on Windows, so this is harmless there.
    When `settings_path` does not exist yet (a brand-new file), there is
    no prior mode to preserve and the temp file's own default stands.

    Args:
        settings_path: the real destination path.
        text: the full new file content.

    Raises:
        OSError: the write, chmod, or replace failed; `settings_path`
            itself is unmodified either way -- `os.replace` is atomic on
            both POSIX and Windows for a same-volume rename.
    """
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    original_mode: int | None = None
    if settings_path.exists():
        original_mode = stat.S_IMODE(settings_path.stat().st_mode)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{settings_path.name}.", suffix=".tmp", dir=str(settings_path.parent)
    )
    tmp_path = Path(tmp_name)
    replaced = False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if original_mode is not None:
            os.chmod(tmp_path, original_mode)
        os.replace(tmp_path, settings_path)
        replaced = True
    finally:
        if not replaced:
            tmp_path.unlink(missing_ok=True)


def _remove_owned(hooks: dict[str, Any]) -> None:
    """Strip every superhuman-owned entry from `hooks`, in place (R5).

    Only a group that actually CONTAINED a superhuman-owned command is
    ever modified or dropped; a foreign group survives byte-identical no
    matter its shape -- including an empty `"hooks": []` array or a
    missing `"hooks"` key entirely (preflight B2). A group that DID carry
    one or more owned commands is dropped entirely once they are removed
    if nothing else remains (this is what makes a migrated worktree-rooted
    entry, TC-95, disappear rather than leave behind an empty group
    nothing else ever created). An event is popped only if THIS call
    removed every group it had; an event that never had any superhuman
    entries to begin with -- including one whose only group(s) already had
    an empty or absent `"hooks"` array -- is left exactly as found, per
    `uninstall()`'s exact-round-trip guarantee (TC-57).

    Args:
        hooks: the settings document's `"hooks"` sub-object.
    """
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        owned_removed_from_event = False
        kept_groups = []
        for group in groups:
            commands = group.get("hooks")
            if not isinstance(commands, list):
                # No "hooks" key, or a malformed non-list value: nothing of
                # ours could be in here. Foreign -- keep byte-identical.
                kept_groups.append(group)
                continue
            owned_in_group = [
                command for command in commands if _owned_basename(command.get("command", "")) is not None
            ]
            if not owned_in_group:
                # Nothing of ours in this group (commands may be empty or
                # entirely foreign) -- keep byte-identical, never drop it.
                kept_groups.append(group)
                continue
            owned_removed_from_event = True
            remaining = [command for command in commands if _owned_basename(command.get("command", "")) is None]
            if remaining:
                kept_groups.append({**group, "hooks": remaining})
            # else: this group held ONLY owned commands and none remain --
            # drop it entirely rather than leaving an empty "hooks": [].
        if owned_removed_from_event and not kept_groups:
            hooks.pop(event, None)
        else:
            hooks[event] = kept_groups


def _add_owned(hooks: dict[str, Any], root: Path) -> None:
    """Add this installer's own entries to `hooks`, in place.

    Assumes `_remove_owned` has already run against the same `hooks`
    object, so no duplicate can result. Reuses an existing matcher group
    when one already exists for the target event+matcher; creates a new
    group (appended at the end of that event's list) otherwise -- this is
    what creates the previously-absent `fork` `SessionStart` group and the
    `Agent|Task` `PreToolUse` group (TC-92, TC-96).

    Args:
        hooks: the settings document's `"hooks"` sub-object.
        root: the resolved skill-checkout root to point commands at.
    """
    for hook_event, matcher, basename in _expected_entries():
        groups = hooks.setdefault(hook_event, [])
        group = next((candidate for candidate in groups if candidate.get("matcher") == matcher), None)
        entry = {
            "type": "command",
            "command": _command_for(root, basename),
            "timeout": _TIMEOUT_SECONDS,
        }
        if group is None:
            groups.append({"matcher": matcher, "hooks": [entry]})
        else:
            group.setdefault("hooks", []).append(entry)


def _diff(before_text: str, after_text: str, settings_path: Path) -> str:
    """Build a unified diff between the file's before/after text.

    Args:
        before_text: content before the change (may be empty).
        after_text: content after the change.
        settings_path: used only to label the diff's file headers.

    Returns:
        The unified diff text, or `""` when the two are identical.
    """
    if before_text == after_text:
        return ""
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=str(settings_path),
            tofile=str(settings_path),
        )
    )


def _read_settings_text(settings_path: Path) -> str:
    """Read `settings_path` as text, tolerating and discarding a leading BOM.

    Phase 3.3 preflight item 4: the original code read with plain
    `encoding="utf-8"`, which does NOT strip a BOM -- a BOM-prefixed but
    otherwise valid settings.json then failed `json.loads` with a raw,
    uncaught `json.JSONDecodeError` (`\\ufeff` is not valid at the start of
    a JSON document). `"utf-8-sig"` strips a leading BOM if present and is
    byte-identical to plain `"utf-8"` decoding when one is absent, so this
    is a strict improvement with no behavior change for the common
    no-BOM case. https://docs.python.org/3/library/codecs.html#encodings-and-unicode
    ("utf-8-sig": "On encoding the utf-8-sig codec will write 0xef, 0xbb,
    0xbf as the first three bytes... On decoding, an optional UTF-8
    encoded BOM at the start of the data will be skipped").

    Note on the BOM's fate: this installer's own writes (`_dump_settings`)
    have never emitted a BOM, and still don't -- so a BOM-prefixed file
    that this installer WRITES to comes back out with the BOM gone, not
    preserved. This is safe: JSON content is BOM-agnostic, and every
    consumer of this file (this module, the Claude Code harness) reads
    UTF-8 either way.

    Args:
        settings_path: the settings file to read.

    Returns:
        str: the file's text content with any leading BOM stripped, or
        `""` if the file does not exist.
    """
    if not settings_path.is_file():
        return ""
    return settings_path.read_text(encoding="utf-8-sig")


def _parse_settings(settings_path: Path, text: str) -> dict[str, Any]:
    """Parse `text` (already BOM-stripped) as the settings JSON document.

    Phase 3.3 preflight item 4: malformed JSON used to reach a bare
    `json.loads` call with nothing catching `json.JSONDecodeError`, so an
    operator with a hand-edited, syntactically broken settings.json got a
    raw Python traceback instead of a one-line, file-naming error -- and
    every caller here runs this BEFORE any write, so a malformed file
    still causes nothing to be written (install()/uninstall()/status() all
    call this before touching `_atomic_write`).

    Args:
        settings_path: used only to name the file in an error message.
        text: the file's text content (already BOM-stripped via
            `_read_settings_text`), or `""`.

    Returns:
        dict[str, Any]: the parsed document, or `{}` for empty/absent
        content.

    Raises:
        HooksInstallError: `text` is non-empty and not valid JSON, or its
            top level is not a JSON object -- never a raw
            `json.JSONDecodeError` traceback.
    """
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HooksInstallError(f"{settings_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HooksInstallError(f"{settings_path} does not contain a JSON object at its top level")
    return data


def install(
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    *,
    skill_root: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    """Idempotently register every superhuman hook entry (FR-9).

    Replaces any pre-existing superhuman-owned entry (wherever it points)
    rather than adding a duplicate beside it (R5) -- this is what migrates
    the 2026-09-09 worktree-rooted hand install to the resolved root
    (TC-95). Every foreign (non-superhuman) entry survives untouched
    (TC-56). Idempotent: a second call with nothing changed writes nothing
    and reports `changed=False` (TC-55).

    Args:
        settings_path: the settings file to modify. Defaults to the real
            operator location; every test passes this explicitly.
        skill_root: an explicit operator override for the root registered
            commands point at. Omit to auto-resolve via git (R1).
        dry_run: compute and return the diff without writing anything
            (TC-97).

    Returns:
        The `InstallResult` describing what would be (or was) written.

    Raises:
        SkillRootRefusedError: auto-resolution landed inside a linked
            worktree.
        SkillRootInsideGitDirError: auto-resolution landed inside a `.git`
            directory.
        HooksInstallError: git resolution failed outright, or
            `settings_path` exists and is not valid JSON (item 4: named in
            the error, nothing written).
        OSError: the write failed.
    """
    root, pinned = resolve_skill_root(skill_root)

    before_text = _read_settings_text(settings_path)
    data = _parse_settings(settings_path, before_text)
    hooks = data.setdefault("hooks", {})
    _remove_owned(hooks)
    _add_owned(hooks, root)
    after_text = _dump_settings(data)

    changed = after_text != before_text
    diff = _diff(before_text, after_text, settings_path)
    if changed and not dry_run:
        _atomic_write(settings_path, after_text)

    return InstallResult(root=root, worktree_pinned=pinned, changed=changed, diff=diff)


def uninstall(settings_path: Path = DEFAULT_SETTINGS_PATH) -> UninstallResult:
    """Remove every superhuman-owned hook entry (FR-9).

    Restores the prior state exactly against a seed with no pre-existing
    superhuman entry (TC-57). Against a seed carrying a migrated
    (worktree-rooted) superhuman entry, that entry is removed too --
    it is ours, wherever it points (R5).

    Args:
        settings_path: the settings file to modify. Defaults to the real
            operator location; every test passes this explicitly.

    Returns:
        The `UninstallResult` describing what was written.

    Raises:
        HooksInstallError: `settings_path` exists and is not valid JSON
            (item 4: named in the error, nothing written).
        OSError: the write failed.
    """
    if not settings_path.is_file():
        return UninstallResult(changed=False, diff="")

    before_text = _read_settings_text(settings_path)
    data = _parse_settings(settings_path, before_text)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        _remove_owned(hooks)
        if not hooks:
            data.pop("hooks", None)
    after_text = _dump_settings(data)

    changed = after_text != before_text
    diff = _diff(before_text, after_text, settings_path)
    if changed:
        _atomic_write(settings_path, after_text)

    return UninstallResult(changed=changed, diff=diff)


def _find_owned_command(hooks: dict[str, Any], hook_event: str, matcher: str, basename: str) -> str | None:
    """Find the registered command for one expected entry, if present.

    Args:
        hooks: the settings document's `"hooks"` sub-object.
        hook_event: e.g. `"SessionStart"`.
        matcher: the matcher string to look under.
        basename: the wrapper basename expected there.

    Returns:
        The matching command string, or None.
    """
    for group in hooks.get(hook_event, []):
        if group.get("matcher") != matcher:
            continue
        for command in group.get("hooks", []):
            if _owned_basename(command.get("command", "")) == basename:
                return command.get("command")
    return None


def status(settings_path: Path = DEFAULT_SETTINGS_PATH) -> InstallStatus:
    """Report whether each expected hook entry is installed (FR-9).

    Also checks that each registered command's path actually exists on
    disk (D7.8's silent-disable case: "a mistyped path in settings.json
    leaves the gate silently disabled") and whether its root is a linked
    git worktree (`worktree_pinned`) -- derived from the command string
    itself, so this is accurate even when called without the
    `--skill-root` an earlier `install()` used.

    Args:
        settings_path: the settings file to inspect. Defaults to the real
            operator location; every test passes this explicitly.

    Returns:
        The `InstallStatus`: `installed=True` iff every expected entry is
        present.

    Raises:
        HooksInstallError: `settings_path` exists and is not valid JSON
            (item 4: named in the error).
    """
    data = _parse_settings(settings_path, _read_settings_text(settings_path))
    hooks = data.get("hooks", {})

    entries = []
    for hook_event, matcher, basename in _expected_entries():
        command = _find_owned_command(hooks, hook_event, matcher, basename)
        present = command is not None
        path_exists = present and Path(command).exists()
        pinned = present and _is_linked_worktree(_root_from_command(command))
        entries.append(
            EntryStatus(
                hook_event=hook_event,
                matcher=matcher,
                basename=basename,
                present=present,
                command=command,
                command_path_exists=bool(path_exists),
                worktree_pinned=bool(pinned),
            )
        )

    return InstallStatus(installed=all(entry.present for entry in entries), entries=tuple(entries))
