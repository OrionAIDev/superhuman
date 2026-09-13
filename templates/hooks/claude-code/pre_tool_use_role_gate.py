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
        as a cache miss, falling back to a fresh `locate_project` call.
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
                    return _CachedLocation(Path(workspace), slug)
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
    """Print the one sanctioned `hookSpecificOutput` deny JSON object.

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
            mismatch_line=None,
        )
        return

    assert result.verdict in _DENYING_VERDICTS  # MISMATCH, UNMARKED
    reason = _build_deny_reason(result, roles_dir)
    _print_deny(reason)
    record_role_gate_decision(
        workspace,
        slug,
        session_id=session_id,
        verdict=result.verdict,
        role=result.role,
        subagent_type=subagent_type,
        mismatch_line=result.prompt_mismatch_line,
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
            raw_payload = sys.stdin.read()
        else:
            raw_payload = Path(args.hook_payload).read_text(encoding="utf-8")
        run(raw_payload=raw_payload, roles_dir=args.roles_dir, anchor=args.anchor)
    except Exception:  # noqa: BLE001 - this script's sole broad catch; see module docstring (NFR-9)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
