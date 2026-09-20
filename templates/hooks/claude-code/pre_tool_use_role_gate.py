#!/usr/bin/env python3
"""Claude Code `PreToolUse` adapter for chunk 7a's role gate (DESIGN.md D7,
D7.6). Invoked by `templates/hooks/claude-code/pre-tool-use-role-gate`
(the shell wrapper), once per `Agent`/`Task` tool call.

**Everything harness-shaped lives here (D7.6).** The `PreToolUse` payload
fields (`tool_name`, `tool_input.prompt`, `tool_input.subagent_type`,
`agent_id`, `cwd`, `session_id`, `scratchpad_dir` — the common-input-fields
shape documented at
<https://code.claude.com/docs/en/hooks#common-input-fields> and
<https://code.claude.com/docs/en/hooks#pretooluse-input>), and the
`hookSpecificOutput` JSON this script itself prints on stdout
(<https://code.claude.com/docs/en/hooks#pretooluse-decision-control>). The
portable predicate — `check_role_block` — lives in
`scripts/fleet/role_block.py`; this module imports it rather than
duplicating it (D7.6: "no new harness name enters `scripts/`").

**Contract with the bash wrapper.** The wrapper passes the harness's raw
stdin JSON straight through on this script's own stdin, and leaves this
script's stdout ALONE (only stderr is discarded) — exactly one JSON object
on stdout is a deny; every other outcome prints NOTHING at all. This
script's own exit status carries no meaning to the wrapper — the wrapper's
own `trap ... EXIT` guarantees exit 0 regardless (NFR-9), matching
`subagent_dispatch_filter.py`'s identical contract with `subagent-start`.

**Scope (D7.4).** In scope iff: `tool_name` is `Agent`/`Task`; `agent_id` is
ABSENT from the payload (per
<https://code.claude.com/docs/en/hooks#common-input-fields>, "present only
when the hook fires inside a subagent call" — this limits the gate to the
main thread); and chunk 2's locator resolves the session to exactly one
project. **The locator is called ONLY when the verdict could deny or is
`NON_ROLE`** (D7.9 acceptance; TC-84 spies this) — a compliant `ROLE`
dispatch, and a `FAULT` verdict, never pay for it and never reach the
locator.

**Two `roles/` directories (G6/B4, DESIGN.md D7 "Decisions locked",
2026-09-19T18:30Z; AMENDED 2026-09-20T00:20Z — see below).** The primary
check above always runs against `roles_dir` — the checkout THIS HOOK
ITSELF runs from, fixed once at install time (`--roles-dir`, default
`_SKILL_ROOT / "roles"`). That is not necessarily the checkout the SESSION
is actually working from: a worktree on another branch, or a branch that
edits `roles/pm.md`, is this estate's normal working mode, so a dispatch
that is verbatim against the session's own copy can still earn a
would-deny verdict here. When the primary verdict is `MISMATCH`/`UNMARKED`
and the locator-resolved `workspace` is ITSELF a distinct superhuman
checkout (`workspace/roles/` a directory, `workspace/SKILL.md` present
with frontmatter `name: superhuman`, and its `roles/` resolving to a
different directory than `roles_dir`) **AND `workspace` belongs to the
SAME git repository as `roles_dir`** (`_same_repository`: equal
`git rev-parse --path-format=absolute --git-common-dir` from each side),
`run` re-runs `check_role_block` against `workspace/roles/` before
committing to the deny. The locked invariant: **a second `roles/` can only
WIDEN a pass, never cause a deny.** `ROLE`/`NON_ROLE` from the second check
overrides the pending deny (print nothing, log per the verdict); only
`MISMATCH`/`UNMARKED` on BOTH checks lets the original deny stand, and
then the deny reason and the logged fields name the SESSION's own role
file (the copy it actually reads), not the hook's.

**2026-09-20T00:20Z amendment (G6/B4 preflight re-run, roadmap#217).** The
PM reproduced two ways the pre-amendment seam let ANY repository disable
this gate entirely: (1) a throwaway repo carrying its own `SKILL.md`
(`name: superhuman`) and its own `roles/<name>.md`, dispatched verbatim
against that role file, was wrongly ALLOWED — the second check never
verified `workspace` had anything to do with the hook's own checkout;
(2) the same shape with an EMPTY `roles/` FAULTed the second check, and a
second-check FAULT used to release the pending deny unconditionally — so
an attacker-controlled repo with no role files at all also bypassed the
gate. Two changes close this: **(a) same-repository gating** —
`_same_repository` above is now a mandatory THIRD condition (alongside
`_is_superhuman_checkout` and `_resolved_paths_differ`) before the second
check ever runs at all, so a repository unrelated to the hook's own
checkout is never consulted, full stop; if git cannot answer
`--git-common-dir` for either side, `_same_repository` returns `False`
(refuse), never `True` (NFR-9's "under-reach is safe, over-reach is not"
extends here). **(b) a second-check FAULT no longer widens** — NFR-9 is
already satisfied by the PRIMARY check having completed successfully; an
unreadable or empty copy on the session side is not evidence the dispatch
complies, so the pending deny (against the hook's own `roles_dir`) stands
unchanged instead of being released. `workspace` here is still the
locator's resolved value, which may come back from an agent-writable
cache file (`_cached_locate`'s own scratchpad cache); that cache is now
validated at its own boundary (an absolute, existing directory and a
`slug_is_safe` slug) before ever being trusted, but the real backstop
against a poisoned `workspace` is `_same_repository` itself — a cache
entry redirected at an attacker directory fails the same-repository test
regardless, because that directory is (by construction) not part of the
hook's own repository.

**Decision log (D7.7).** For an in-scope `NON_ROLE`/`MISMATCH`/`UNMARKED`
verdict, one line is appended to the project's `role-gate.jsonl` via
`scripts.fleet.role_block.record_role_gate_decision` — never for `ROLE`
(chunk 7's own dispatch-registration row already counts it) or `FAULT` (a
fault is never a decision).

**Never raises, never prints a traceback.** Every failure mode (corrupt
JSON, an unreadable `roles/` directory, a locator exception, a missing
interpreter feature) degrades to "print nothing" — this script's own
`main()` wraps every step in the same broad-catch discipline
`subagent_dispatch_filter.py` established, for the identical reason
(NFR-9).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

#: This file lives at <skill_root>/templates/hooks/claude-code/pre_tool_use_role_gate.py.
_SKILL_ROOT = Path(__file__).resolve().parents[3]

# So `from scripts.fleet... import ...` resolves when this script is run
# directly (not via `-m`) — it is invoked as a plain script by
# `pre-tool-use-role-gate`, not as a package module.
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.fleet.locate import LocateResult, locate_project  # noqa: E402
from scripts.fleet.path_safety import slug_is_safe  # noqa: E402
from scripts.fleet.role_block import (  # noqa: E402
    Verdict,
    check_role_block,
    record_role_gate_decision,
)

_DISPATCH_TOOL_NAMES = ("Agent", "Task")

#: Verdicts for which the locator is even considered (D7.9's latency note;
#: TC-84's spy). `ROLE` and `FAULT` never reach this set.
_VERDICTS_NEEDING_SCOPE = (Verdict.NON_ROLE, Verdict.MISMATCH, Verdict.UNMARKED)

#: Verdicts that, once in scope, produce a deny.
_DENYING_VERDICTS = (Verdict.MISMATCH, Verdict.UNMARKED)

_LOCATOR_CACHE_FILENAME_PREFIX = "role-gate-locator-"

#: `SKILL.md`'s own leading frontmatter block. Mirrors
#: `scripts/fleet/role_block.py`'s `_FRONTMATTER_RE`/`_NAME_LINE_RE`
#: byte-for-byte (not imported — those are that module's private members,
#: and duplicating two small regexes here is cheaper and safer than
#: widening role_block.py's public surface for a single caller; same
#: rationale role_block.py itself gives for not importing
#: dispatch_predicate.py's identical pair).
_SKILL_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)
_SKILL_NAME_LINE_RE = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)

#: The `name:` value `SKILL.md` must carry for `_is_superhuman_checkout` to
#: treat a workspace as a distinct superhuman checkout (G6/B4).
_SUPERHUMAN_SKILL_NAME = "superhuman"


class _CachedLocation(NamedTuple):
    """The subset of `LocateResult` this adapter's cache needs.

    Attributes:
        workspace: the resolved project's working tree root.
        slug: the resolved project's slug.
    """

    workspace: Path
    slug: str


def _safe_cache_key(session_id: str) -> str:
    """Return a filesystem-safe cache filename fragment for `session_id`.

    Args:
        session_id: the harness session id — treated as untrusted text for
            path-building purposes (never assumed to already be a safe
            filename).

    Returns:
        str: `session_id` with every character outside `[A-Za-z0-9_-]`
        replaced by `_`.
    """
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session_id)


def _cached_locate(
    cwd: str, *, scratchpad_dir: str | None, session_id: str | None
) -> LocateResult | _CachedLocation | None:
    """Resolve `cwd` to a project, cached per `session_id` in `scratchpad_dir`.

    D7.9's latency note: "if the locator path is slow, the locator result
    is cached per `session_id` in `scratchpad_dir`." A session's own
    working tree does not change project mid-session, so this caches for
    the lifetime of the scratchpad directory itself (which the harness
    already scopes per-session) rather than adding a separate TTL.

    Args:
        cwd: the directory to resolve from (the anchor, or the payload's
            own `cwd`).
        scratchpad_dir: the payload's `scratchpad_dir`, if present. When
            absent, this falls back to an uncached `locate_project` call.
        session_id: the payload's `session_id`, if present. When absent,
            this falls back to an uncached `locate_project` call — there
            is no safe cache key without it.

    Returns:
        LocateResult | _CachedLocation | None: the resolved location
        (freshly computed, or read back from the cache), or `None` if
        resolution refuses. Never raises: any cache I/O failure is treated
        as a cache miss, falling back to a fresh `locate_project` call. A
        cached `workspace`/`slug` pair is used only after passing the
        boundary check (an absolute, existing directory; a `slug_is_safe`
        slug) — G6/B4's 2026-09-20T00:20Z amendment; see the call site
        below for what an entry that fails this check falls back to.
    """
    if not scratchpad_dir or not session_id:
        return locate_project(cwd)

    cache_file = (
        Path(scratchpad_dir) / f"{_LOCATOR_CACHE_FILENAME_PREFIX}{_safe_cache_key(session_id)}.json"
    )

    try:
        if cache_file.is_file():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and "workspace" in cached and "slug" in cached:
                workspace = cached["workspace"]
                slug = cached["slug"]
                if isinstance(workspace, str) and isinstance(slug, str):
                    workspace_path = Path(workspace)
                    if (
                        workspace_path.is_absolute()
                        and workspace_path.is_dir()
                        and slug_is_safe(slug)
                    ):
                        return _CachedLocation(workspace_path, slug)
                    # G6/B4 (2026-09-20T00:20Z amendment): this cache file
                    # lives in the agent-writable scratchpad, so a
                    # `workspace`/`slug` pair that fails this boundary
                    # check (not an absolute existing directory, or an
                    # unsafe slug — e.g. a path-traversal segment) is
                    # treated exactly like an ordinary cache MISS below,
                    # never trusted and never returned. This is distinct
                    # from the legitimate "cached unresolvable" sentinel
                    # just below (non-string `workspace`/`slug`), which
                    # already means "a fresh lookup previously refused" —
                    # that outcome is still honoured as-is.
                else:
                    return None  # a cached "unresolvable" outcome
    except (OSError, ValueError):
        pass  # treat any unreadable/corrupt cache entry as a miss

    result = locate_project(cwd)
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = (
            {"workspace": str(result.workspace), "slug": result.slug}
            if result is not None
            else {"workspace": None, "slug": None}
        )
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass  # best-effort cache write only
    return result


def _is_superhuman_checkout(workspace: Path) -> bool:
    """Return whether `workspace` is itself a distinct superhuman skill checkout.

    G6/B4 (2026-09-19T18:30Z): the session's own working tree may be a
    DIFFERENT superhuman copy than the one the hook itself runs from (a
    worktree on another branch is this estate's normal working mode) —
    this predicate decides when that is true, so `run` knows a second,
    session-scoped `roles/` comparison is even meaningful. Never raises:
    every failure (missing/unreadable `SKILL.md`, no frontmatter, no
    `name:` line) resolves to `False`.

    Args:
        workspace: the locator-resolved project workspace to test.

    Returns:
        bool: `True` iff `workspace/roles/` is a directory AND
        `workspace/SKILL.md` exists with frontmatter naming
        `name: superhuman`.
    """
    roles_dir = workspace / "roles"
    skill_md = workspace / "SKILL.md"
    try:
        if not roles_dir.is_dir():
            return False
        if not skill_md.is_file():
            return False
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # `ValueError` covers `UnicodeDecodeError` on a non-UTF-8 file.
        return False
    match = _SKILL_FRONTMATTER_RE.match(text)
    if match is None:
        return False
    name_match = _SKILL_NAME_LINE_RE.search(match.group("body"))
    return name_match is not None and name_match.group(1) == _SUPERHUMAN_SKILL_NAME


def _resolved_paths_differ(first: Path, second: Path) -> bool:
    """Return whether `first` and `second` resolve to different filesystem paths.

    Never raises: an `OSError` from either `.resolve()` call is treated as
    "differ" — the safer direction here is to attempt the second
    `check_role_block` call rather than silently skip it, since that
    call's own verdicts can only widen a pending deny into a pass (see
    `_is_superhuman_checkout`'s caller), never cause one.

    Args:
        first: the hook checkout's `roles/` directory.
        second: the session workspace's own `roles/` directory.

    Returns:
        bool: `True` if the two do not resolve to the same path.
    """
    try:
        return first.resolve() != second.resolve()
    except OSError:
        return True


#: Timeout for `_git_common_dir`'s own `git rev-parse --git-common-dir`
#: calls. This seam runs on the deny path only (after `check_role_block`
#: has already produced a would-deny verdict against `roles_dir`), so it
#: is bounded the same way `scripts/fleet/locate.py`'s own git calls are
#: (`_GIT_TIMEOUT_SECONDS`, 0.25s) rather than `hooks_install.py`'s 10s
#: one-shot install-time budget — this is a per-dispatch hot path, not a
#: one-time resolve.
_SAME_REPO_GIT_TIMEOUT_SECONDS = 0.25


def _git_common_dir(cwd: Path) -> Path | None:
    """Return `git rev-parse --path-format=absolute --git-common-dir` from `cwd`.

    Mirrors `scripts/fleet/hooks_install.py`'s `_default_skill_root`
    subprocess form (`-C`, `--path-format=absolute`, stdout decoded
    explicitly as UTF-8 — never `subprocess.run(..., text=True)`, which
    decodes with the process's locale-preferred encoding, e.g. cp1252 on
    Windows) but degrades to `None` instead of raising: `_same_repository`
    below must be able to say "git could not answer" without that turning
    into an exception this module's own `except Exception` would otherwise
    have to catch anyway — naming the refusal explicitly here keeps the
    call site's "both sides must answer" contract legible.

    Args:
        cwd: directory to run the command in.

    Returns:
        Path | None: the repository's common `.git` directory (NOT its
        parent — callers compare this value directly; they do not derive a
        working-tree root from it), or `None` if `cwd` is not inside a git
        working tree, git is unavailable, the call fails or times out, or
        its stdout is not valid UTF-8.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            timeout=_SAME_REPO_GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        text = completed.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return Path(text) if text else None


def _same_repository(first: Path, second: Path) -> bool:
    """Return whether `first` and `second` sit in the SAME git repository.

    G6/B4's 2026-09-20T00:20Z amendment: the second `roles/` check must be
    admitted only when the session's workspace is a linked worktree (or the
    main checkout) of the IDENTICAL repository the hook itself runs from —
    comparing each side's `git rev-parse --git-common-dir` is the same
    identity test `scripts/fleet/locate.py`'s own H0' step and
    `hooks_install.py`'s `_default_skill_root` both already rely on (a
    repository's common `.git` directory names one repository regardless of
    which linked worktree asks). This still admits the worktree-on-another-
    branch case the second check exists for; a third-party repository
    answers with a DIFFERENT common dir and is excluded — exactly the
    boundary the PM's two reproductions exploited (a hostile repository can
    never claim to be "the same repository" as the hook's own checkout).

    Args:
        first: the hook checkout's own directory (its `roles_dir`, or any
            directory inside the hook's checkout).
        second: the session workspace's directory to compare against.

    Returns:
        bool: `True` iff both sides resolve a `--git-common-dir` AND the
        two values are equal. `False` whenever EITHER side's git call
        fails — "git cannot answer for either side" refuses the second
        check (this predicate's sole caller never consults the second
        `roles/` on a `False` result), never assumed `True`.
    """
    first_common = _git_common_dir(first)
    if first_common is None:
        return False
    second_common = _git_common_dir(second)
    if second_common is None:
        return False
    return first_common == second_common


def _build_deny_reason(result: Any, roles_dir: Path) -> str:
    """Build the `permissionDecisionReason` text for a `MISMATCH`/`UNMARKED` verdict.

    D7.5: "the reason text quotes the exact line to add or the file to
    paste, so complying is always one retry away."

    Args:
        result: the `RoleCheckResult` (from `scripts.fleet.role_block`)
            that produced this deny.
        roles_dir: the directory holding `roles/*.md` — named in the
            reason so a retry knows exactly what to paste.

    Returns:
        str: a one/two-line human-readable reason.
    """
    if result.verdict == Verdict.MISMATCH:
        role_file = result.role_file if result.role_file is not None else roles_dir / f"{result.role}.md"
        return (
            f"This dispatch's prompt claims to be a {result.role!r} role dispatch (its "
            "frontmatter names that role) but its body is not the FULL, UNEDITED content "
            f"of {role_file} — first differing line: prompt has {result.prompt_mismatch_line!r}, "
            f"the role file has {result.file_mismatch_line!r}. Paste {role_file}'s full, "
            "unedited content as the leading block of the prompt (per-dispatch overrides go "
            "in the task brief, never inside the role block), or open the prompt with the "
            "literal line 'superhuman-dispatch: non-role' if this is not a role dispatch."
        )
    # Verdict.UNMARKED
    return (
        "This dispatch's prompt opens with neither the full, unedited content of an "
        f"existing role file under {roles_dir} nor the exact line "
        "'superhuman-dispatch: non-role'. Add one of these as the very first content of "
        "the prompt: paste the intended role's full roles/<name>.md content unedited, or "
        "open with the literal non-role line if this dispatch has no role."
    )


def _print_deny(reason: str) -> None:
    """Print the one sanctioned `hookSpecificOutput` deny JSON object, then flush.

    Preflight item 1 (Critical, PM-reproduced): a killed child process (the
    harness's own `"timeout": 10` `PreToolUse` budget, `hooks_install.py::
    _TIMEOUT_SECONDS`) delivers ZERO bytes of an unflushed `print()` when
    stdout is a pipe — CPython fully block-buffers a non-tty stdout, so the
    printed bytes sit in the interpreter's own buffer, not the OS pipe,
    until several KB accumulate, an explicit flush happens, or the process
    exits NORMALLY. `run()` calls `record_role_gate_decision` immediately
    after this function returns, and that call can block for up to
    `bounded_journal`'s `_DEFAULT_LOCK_TIMEOUT_SECONDS` (1.0s) on a
    contended journal lock. If the harness kills this process while that
    call is blocked, no normal interpreter shutdown ever happens, and the
    already-decided deny — already printed — is silently lost with it.

    Fixed here by flushing immediately after `print`, not by reordering
    `run()` to record before printing: recording first only moves the same
    loss earlier (a kill during a now-first, blocked record call would
    lose the deny before it was ever printed at all). Flushing right after
    print instead delivers the deny to the OS pipe, and therefore to the
    harness, before the record call's own blocking I/O even starts — so
    whatever happens to this process afterward, the decision already
    reached its caller.

    Args:
        reason: the `permissionDecisionReason` text (see `_build_deny_reason`).
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.stdout.flush()


def run(
    *,
    raw_payload: str,
    roles_dir: Path,
    anchor: str | None,
) -> None:
    """Evaluate one `PreToolUse` payload and print a deny JSON, or nothing.

    Never raises (NFR-9) — every branch below either returns having printed
    nothing, or prints exactly one JSON object and returns.

    Args:
        raw_payload: the raw JSON text read from stdin (or `--hook-payload`).
        roles_dir: the directory holding `roles/*.md`.
        anchor: `$CLAUDE_PROJECT_DIR`, if the wrapper's environment carried
            it; tried before the payload's own `cwd` (mirrors
            `subagent-start`'s identical anchor precedent).
    """
    try:
        payload = json.loads(raw_payload)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(payload, dict):
        return

    tool_name = payload.get("tool_name")
    if tool_name not in _DISPATCH_TOOL_NAMES:
        return
    if payload.get("agent_id"):
        # Present and non-empty: this PreToolUse fired inside a subagent's
        # own call, not the main thread (D7.4 clause 2) — out of scope.
        return

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return
    subagent_type = tool_input.get("subagent_type")
    if not isinstance(subagent_type, str):
        subagent_type = None

    result = check_role_block(prompt, roles_dir)
    if result.verdict in (Verdict.ROLE, Verdict.FAULT):
        # Locator not needed: ROLE never denies, FAULT never denies either
        # (D7.5) — both are "print nothing", and the locator is skipped
        # entirely (D7.9's latency requirement; TC-84's spy).
        return
    assert result.verdict in _VERDICTS_NEEDING_SCOPE  # NON_ROLE, MISMATCH, UNMARKED

    session_id = payload.get("session_id")
    session_id = session_id if isinstance(session_id, str) else None
    scratchpad_dir = payload.get("scratchpad_dir")
    scratchpad_dir = scratchpad_dir if isinstance(scratchpad_dir, str) else None
    payload_cwd = payload.get("cwd")

    # The session-keyed cache wraps only the PRIMARY lookup (anchor when
    # given, else the payload's own cwd) — never the rare anchor-fails
    # fallback below. Both attempts would otherwise share one cache key
    # (session_id), so a cached "anchor did not resolve" result would be
    # read back for the fallback's different cwd too, defeating the
    # fallback before it ever tried `locate_project` on `payload_cwd`.
    primary_cwd = anchor if anchor else payload_cwd
    location = None
    if isinstance(primary_cwd, str) and primary_cwd:
        try:
            location = _cached_locate(
                primary_cwd, scratchpad_dir=scratchpad_dir, session_id=session_id
            )
        except Exception:  # noqa: BLE001 - the locator must never turn into a false deny
            location = None
    if (
        location is None
        and anchor
        and isinstance(payload_cwd, str)
        and payload_cwd
        and payload_cwd != primary_cwd
    ):
        # Anchor given but did not resolve: fall back to the payload's own
        # cwd, mirroring session-start/subagent-start's fallback contract.
        # Deliberately UNCACHED (see above) — this is the rare path.
        try:
            location = locate_project(payload_cwd)
        except Exception:  # noqa: BLE001
            location = None

    if location is None:
        # The session does not resolve to exactly one project (D7.4 clause
        # 3) — out of scope. Print nothing.
        return

    workspace = location.workspace
    slug = location.slug

    if result.verdict == Verdict.NON_ROLE:
        record_role_gate_decision(
            workspace,
            slug,
            session_id=session_id,
            verdict=Verdict.NON_ROLE,
            role=None,
            subagent_type=subagent_type,
            mismatch_line_number=None,
        )
        return

    assert result.verdict in _DENYING_VERDICTS  # MISMATCH, UNMARKED

    # G6/B4 (2026-09-19T18:30Z, AMENDED 2026-09-20T00:20Z): one more chance
    # against the SESSION's own roles/, when `workspace` is itself a
    # distinct superhuman checkout differing from the hook's own
    # `roles_dir` AND belongs to the SAME git repository as `roles_dir`
    # (`_same_repository` — the 2026-09-20T00:20Z amendment; see the
    # module docstring for the two bypasses this closes). Widen-only:
    # ROLE/NON_ROLE below overrides this pending deny; MISMATCH/UNMARKED
    # on both checks, OR the second check never running at all (different
    # repository, or a FAULT there — no longer a widen), lets it stand.
    effective_result = result
    effective_roles_dir = roles_dir
    session_roles_dir = workspace / "roles"
    if (
        _is_superhuman_checkout(workspace)
        and _resolved_paths_differ(roles_dir, session_roles_dir)
        and _same_repository(roles_dir, workspace)
    ):
        second_result = check_role_block(prompt, session_roles_dir)
        if second_result.verdict == Verdict.ROLE:
            # Widened to a pass: print nothing, log nothing (D7.7 — a ROLE
            # verdict is never logged).
            return
        if second_result.verdict == Verdict.NON_ROLE:
            record_role_gate_decision(
                workspace,
                slug,
                session_id=session_id,
                verdict=Verdict.NON_ROLE,
                role=None,
                subagent_type=subagent_type,
                mismatch_line_number=None,
            )
            return
        if second_result.verdict in _DENYING_VERDICTS:  # MISMATCH, UNMARKED
            # Both copies deny: the original deny stands, but D7.5 item 4 —
            # the reason (and the logged fields) name the copy the SESSION
            # itself reads, not the hook checkout's, so the file this names
            # is always one the session can actually open and fix.
            effective_result = second_result
            effective_roles_dir = session_roles_dir
        # else: Verdict.FAULT. 2026-09-20T00:20Z amendment — a second-check
        # FAULT no longer widens. NFR-9 is already satisfied by the PRIMARY
        # check having completed successfully; an unreadable/empty copy on
        # the session side is not evidence the dispatch complies, so
        # `effective_result`/`effective_roles_dir` are left untouched and
        # the pending deny (against the hook's own `roles_dir`) stands.

    reason = _build_deny_reason(effective_result, effective_roles_dir)
    _print_deny(reason)
    record_role_gate_decision(
        workspace,
        slug,
        session_id=session_id,
        verdict=effective_result.verdict,
        role=effective_result.role,
        subagent_type=subagent_type,
        mismatch_line_number=effective_result.mismatch_line_number,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: read the payload, print a deny JSON or nothing, exit 0.

    Args:
        argv: argument vector (defaults to `sys.argv[1:]`).

    Returns:
        int: always `0` — this script's own exit status carries no
        meaning to its caller (the bash wrapper traps to exit 0
        regardless); the decision is read from stdout. Never raises past
        this function.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hook-payload",
        default="-",
        help="read the harness hook JSON payload from this file, or '-' for stdin",
    )
    parser.add_argument(
        "--roles-dir",
        type=Path,
        default=_SKILL_ROOT / "roles",
        help="the directory holding roles/*.md (default: this skill's own roles/)",
    )
    parser.add_argument(
        "--anchor",
        default=None,
        help="try locating the project from this directory first (e.g. "
        "$CLAUDE_PROJECT_DIR), falling back to the payload's own cwd",
    )
    try:
        args = parser.parse_args(argv)
        if str(args.hook_payload) == "-":
            # Read raw bytes and decode as UTF-8 explicitly -- the harness
            # writes the hook payload as UTF-8 bytes (JSON's own encoding
            # rule, RFC 8259 §8.1
            # <https://www.rfc-editor.org/rfc/rfc8259#section-8.1>), but
            # `sys.stdin.read()` decodes with the process's
            # locale-preferred encoding, which on Windows is the
            # console/ANSI code page (e.g. cp1252), not UTF-8. A role file
            # containing any non-ASCII byte (every role file has an em
            # dash) would then never compare equal, denying every
            # correctly-formed dispatch. A decode failure here is caught by
            # this function's own broad `except Exception` below, same as
            # any other fault (NFR-9: never deny on this gate's own fault).
            raw_payload = sys.stdin.buffer.read().decode("utf-8")
        else:
            raw_payload = Path(args.hook_payload).read_text(encoding="utf-8")
        run(raw_payload=raw_payload, roles_dir=args.roles_dir, anchor=args.anchor)
    except Exception:  # noqa: BLE001 - this script's sole broad catch; see module docstring (NFR-9)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
