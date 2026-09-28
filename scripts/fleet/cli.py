"""Fleet CLI entry point (`argparse`, per `conventions/python.md`).

Invoked as ``python -m scripts.fleet.cli <subcommand>`` from the superhuman
skill root. There is deliberately no ``fleet`` executable: this repo ships no
packaging, so nothing installs a console script, and the package-relative
imports below mean this file cannot be run as a loose script either.

This chunk wires only the `register` subcommand — the registrar for FR-1's
spawned and relayed origination paths (Decision C: `session-relay`'s KICKOFF
and a native `spawn_task` call are both *callers* of `register_session`,
never independent writers). Later chunks extend `build_parser()` with
`handoff emit|cancel|stale`, `status`, `validate`, `query`, and `gen-view`
subparsers, per DESIGN's component table — the subparser structure below is
deliberately left easy to extend rather than a flat single-command script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import superhuman_profile
from . import config as fleet_config
from . import doctor as fleet_doctor
from . import hook_payload as fleet_hook_payload
from . import hooks_install as fleet_hooks_install
from . import observe as fleet_observe
from . import path_safety as fleet_path_safety
from . import project as fleet_project
from . import project_id as fleet_project_id
from . import role_block as fleet_role_block
from .adapter.base import SessionAdapter, SessionInfo, extract_claude_session_local_id
from .adapter.claude import ClaudeAdapter
from .adapter.portable import PortableAdapter
from .adapter.session_liveness import resolve_liveness
from .adapter.subagent import SubagentAdapter
from .core.done import DONE_LEVELS
from .core.done import advance as done_advance
from .core.done import event_for as done_event_for
from .core.edges import resolve_graph
from .core.errors import (
    DonePolicyError,
    FragmentCorrupt,
    LockTimeoutError,
    OwnershipContended,
    OwnershipError,
    OwnershipRefused,
    SessionIdentityUnresolved,
    ValidationError,
)
from .core.events import append, read_all
from .core.nodes import parse_node_id
from .core.projection import project_event, rebuild
from .core.project_owner import claim as owner_claim
from .core.project_owner import fold_owner as owner_fold_owner
from .core.project_owner import resolve_legacy_owner_unrestricted as owner_legacy_owner
from .core.project_owner import stand_down as owner_stand_down
from .core.query import edges_of
from .core.schema import Event, Fragment, validate_event
from .core.store import read_fragment
from .handoff import cancel as handoff_cancel
from .handoff import emit as handoff_emit
from .handoff import extract_handoff_id
from .handoff import self_register as handoff_self_register
from .handoff import stale_report
from .locate import locate_project
from .view import render_status_table, write_fleet_md

#: `fleet --version` output. Not tied to `VERSION` at the skill root — this
#: is the manifest CLI's own schema-facing version, matching `schema_version`
#: in `core/schema.py` (both are v1 for Phase 1).
CLI_VERSION = "0.1.0 (schema v1)"

#: This file lives at <skill_root>/scripts/fleet/cli.py — used only as the
#: default `--roles-dir` for `role-block check` (chunk 7a), matching
#: `pre_tool_use_role_gate.py`'s identical `_SKILL_ROOT` derivation.
_SKILL_ROOT = Path(__file__).resolve().parents[2]

#: Registrar-level bounded retry defaults for lock contention (on top of
#: `core.events.append`'s own internal timeout/retry). A second, short-lived
#: retry tier catches the case where the first whole attempt's timeout
#: window happened to land entirely inside another writer's hold — it never
#: proceeds as if a write succeeded; see `register_session`.
_DEFAULT_LOCK_RETRY_ATTEMPTS = 3
_DEFAULT_LOCK_RETRY_BACKOFF = 0.1


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix.

    Returns:
        str: e.g. `"2026-08-14T12:00:00.000000Z"`.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_session_registered_event(
    session: SessionInfo,
    *,
    origination: str,
    project_id: str,
    writer_role: str,
) -> dict[str, Any]:
    """Build the raw `session_registered` event dict for one session.

    Args:
        session: the session to register, as returned by a `SessionAdapter`.
        origination: which of FR-1's origination paths produced this session
            — "spawned" | "relayed" | "manual".
        project_id: the owning project's stable id (Decision F).
        writer_role: the role writing this event; never a model/vendor
            string (NFR-6) — `core.schema.validate_event` rejects the latter.

    Returns:
        dict[str, Any]: an event dict ready for `core.events.append`, with
        `idempotency_key` set to `register:<node_id>` (Decision F) so a
        double-registration of the same session dedupes to one event.
    """
    return {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": f"register:{session.node_id}",
        "ts": _now_iso(),
        "type": "session_registered",
        "project_id": project_id,
        "node_id": session.node_id,
        "writer_role": writer_role,
        "payload": {
            "harness": session.harness,
            "workspace": session.workspace,
            "local_id": session.local_id,
            "branch": session.branch or "",
            "origination": origination,
        },
    }


def _resolve_target_session(
    adapter: SessionAdapter, target_session_id: str | None
) -> SessionInfo:
    """Resolve which session `register_session` should register.

    Args:
        adapter: the adapter to read session facts from.
        target_session_id: if given, the `local_id` of a session to find via
            `adapter.enumerate_sessions()` (the "a parent registers a child
            it just learned about" shape — the spawned path). If `None`,
            `adapter.current_session()` is used instead (the "a session
            self-registers" shape — the relayed path).

    Returns:
        SessionInfo: the resolved session.

    Raises:
        ValueError: if `target_session_id` was given but no session with
            that `local_id` appears in `adapter.enumerate_sessions()`.
    """
    if target_session_id is None:
        return adapter.current_session()
    for candidate in adapter.enumerate_sessions():
        if candidate.local_id == target_session_id:
            return candidate
    raise ValueError(
        f"no session with id {target_session_id!r} found via enumerate_sessions()"
    )


def _append_with_bounded_retry(
    log_path: Path | str,
    event_dict: dict[str, Any],
    *,
    attempts: int,
    backoff: float,
    timeout: float | None = None,
) -> Event | None:
    """Call `core.events.append`, retrying a bounded number of times on lock contention.

    `core.events.append` already retries internally up to its own `timeout`;
    this is a second, coarser tier for the case where one whole attempt's
    retry window happened to fall entirely inside another writer's hold.
    Never proceeds as if the event were written — either `append` eventually
    succeeds (returns an `Event` or `None` for a dedupe no-op) within the
    attempt budget, or `LockTimeoutError` propagates to the caller.

    Args:
        log_path: path to the event log.
        event_dict: the raw event dict to append.
        attempts: total attempts, including the first (must be >= 1).
        backoff: seconds to sleep between attempts.
        timeout: per-attempt lock-acquisition timeout, passed to
            `core.events.append`'s own `timeout` parameter (additive
            passthrough, fleet-wiring Chunk 1, W-NFR-7). `None` (the
            default) omits the keyword entirely, so `append` uses its own
            default (10.0s) exactly as every pre-wiring caller already
            observes — this must never change existing behavior.

    Returns:
        Event | None: as `core.events.append`.

    Raises:
        LockTimeoutError: if every attempt timed out. The caller must treat
            this exactly like a single `append` timeout — nothing was
            written.
    """
    kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
    last_exc: LockTimeoutError | None = None
    for attempt in range(attempts):
        try:
            return append(log_path, event_dict, **kwargs)
        except LockTimeoutError as exc:
            last_exc = exc
            if attempt < attempts - 1 and backoff > 0:
                time.sleep(backoff)
    assert last_exc is not None  # attempts >= 1 guarantees at least one raise
    raise last_exc


def _positive_int(value: str) -> int:
    """Argparse `type=` for a count that must be at least 1.

    Args:
        value: the raw command-line string.

    Returns:
        int: the parsed value.

    Raises:
        argparse.ArgumentTypeError: if `value` is not an integer >= 1, so
            argparse reports a usage error (exit 2) instead of an
            AssertionError from `_call_with_bounded_lock_retry`, which
            never calls core when attempts < 1.
    """
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer >= 1, got {value!r}") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected an integer >= 1, got {parsed}")
    return parsed


def _call_with_bounded_lock_retry(
    call: Callable[[], Event | None], *, attempts: int, backoff: float
) -> Event | None:
    """Call `call()`, retrying a bounded number of times on `LockTimeoutError` (I3).

    `core.project_owner.claim`/`stand_down` already retry internally against
    a *changing ownership state* (bounded re-evaluation, `OwnershipContended`
    on exhaustion) — this is a distinct, coarser tier for plain lock
    *contention*, the same second-tier retry every other manifest-writing
    verb already gets via `_append_with_bounded_retry`. Never proceeds as if
    the call succeeded — either `call()` eventually returns within the
    attempt budget, or `LockTimeoutError` propagates to the caller.

    Args:
        call: a zero-argument callable wrapping one `owner_claim`/
            `owner_stand_down` invocation.
        attempts: total attempts, including the first (must be >= 1).
        backoff: seconds to sleep between attempts.

    Returns:
        Event | None: whatever `call()` returns.

    Raises:
        LockTimeoutError: if every attempt timed out. The caller must treat
            this exactly like a single call's timeout — nothing was written.
    """
    last_exc: LockTimeoutError | None = None
    for attempt in range(attempts):
        try:
            return call()
        except LockTimeoutError as exc:
            last_exc = exc
            if attempt < attempts - 1 and backoff > 0:
                time.sleep(backoff)
    assert last_exc is not None  # attempts >= 1 guarantees at least one raise
    raise last_exc


def register_session(
    adapter: SessionAdapter,
    *,
    origination: str,
    project_id: str,
    writer_role: str,
    log_path: Path | str,
    sessions_dir: Path | str,
    target_session_id: str | None = None,
    lock_retry_attempts: int = _DEFAULT_LOCK_RETRY_ATTEMPTS,
    lock_retry_backoff: float = _DEFAULT_LOCK_RETRY_BACKOFF,
    lock_timeout: float | None = None,
) -> Fragment:
    """Register one session as a `session_registered` event and project its fragment.

    This is the sole registrar entry point for the spawned and relayed
    origination paths (FR-1 x2; Decision C) — `session-relay`'s KICKOFF and a
    native `spawn_task` call are both *callers* of this function (directly,
    or via the `fleet register` CLI wrapping it), never independent writers.
    The write always goes through `core.events.append` (validation and
    ownership enforcement live there, per DESIGN's data flow); nothing is
    ever written to the log or a fragment by any other path.

    Args:
        adapter: the `SessionAdapter` to read session facts from.
        origination: which of FR-1's origination paths produced this
            registration — "spawned" | "relayed" | "manual".
        project_id: the owning project's stable id (Decision F).
        writer_role: a role name, never a model/vendor string (NFR-6) —
            `core.schema.validate_event` rejects the latter.
        log_path: path to the project's event log.
        sessions_dir: path to the project's fragment directory.
        target_session_id: if given, register the session with this
            `local_id` found via `adapter.enumerate_sessions()` (the spawned
            path's shape — the caller already knows about a specific child).
            If `None`, register `adapter.current_session()` instead (the
            relayed path's shape — self-registration).
        lock_retry_attempts: bounded registrar-level retry count on top of
            `append`'s own internal retry/timeout (see
            `_append_with_bounded_retry`).
        lock_retry_backoff: seconds to sleep between registrar-level retries.
        lock_timeout: per-attempt lock-acquisition timeout (additive
            passthrough, fleet-wiring Chunk 1). `None` (the default,
            unchanged for every existing caller) uses `append`'s own
            default (10.0s); only `observe.py`'s own calls pass a smaller
            value explicitly (W-NFR-7).

    Returns:
        Fragment: the session's fragment after the registration is applied.
        On a repeat registration of the same session (idempotency-key
        dedupe), this is the *existing* fragment, read back rather than
        re-projected — the original event is the one of record. Correct
        even if the cached fragment was found corrupt on disk and had to
        be recovered via `core.projection.rebuild()` (G5 round-5,
        #P4-1/#P4-2) — the registration event is already durably appended
        to the log by that point, so a corrupt fragment cache is
        recovered, never fatal.

    Raises:
        ValidationError: if the built event fails schema validation.
        OwnershipError: if `writer_role` may not write `session_registered`.
        LockTimeoutError: if the shared log lock could not be acquired
            within the bounded retry budget. The caller must NOT assume the
            session was registered — nothing was written.
        ValueError: if `target_session_id` was given but not found via
            `adapter.enumerate_sessions()`.
    """
    session = _resolve_target_session(adapter, target_session_id)
    event_dict = build_session_registered_event(
        session, origination=origination, project_id=project_id, writer_role=writer_role
    )

    appended = _append_with_bounded_retry(
        log_path,
        event_dict,
        attempts=lock_retry_attempts,
        backoff=lock_retry_backoff,
        timeout=lock_timeout,
    )

    if appended is None:
        # Dedupe no-op: an event with this idempotency_key already exists.
        # The event of record is whatever was appended first; re-projecting
        # this call's (possibly stale) payload on top would be wrong, so the
        # existing fragment is read back instead.
        try:
            existing = read_fragment(session.node_id, sessions_dir)
        except FragmentCorrupt:
            existing = None
        if existing is not None:
            return existing
        # Fragment missing but the log entry exists (e.g. a corrupt/deleted
        # fragment) — re-validate and project the built event to rebuild it.
        appended = validate_event(event_dict)

    try:
        return project_event(appended, sessions_dir)
    except FragmentCorrupt:
        # G5 round-5 (#P4-1/#P4-2): the cached fragment exists but cannot be
        # read — recover by replaying the whole log (which already contains
        # `appended`, just durably written by `_append_with_bounded_retry`
        # above) rather than letting `project_event` guess at a partial
        # fragment. Full, correct, all-fields reconstruction.
        fragments = rebuild(log_path, sessions_dir, project_id=project_id)
        return fragments[session.node_id]


def _default_fleet_dir(workspace: Path, slug: str) -> Path:
    """Return the default per-project fleet manifest directory.

    Args:
        workspace: the project's working tree root.
        slug: the superhuman project slug.

    Returns:
        Path: `<workspace>/docs/superhuman/<slug>/fleet`, per DESIGN's
        storage layout.
    """
    return workspace / "docs" / "superhuman" / slug / "fleet"


def _resolved_git_timeout_override(workspace: Path | str) -> float | None:
    """Resolve the `git_timeout=` override `_build_adapter` should forward
    to the adapter it constructs (chunk 9, PM rulings R10 and R11).

    `FleetConfig.git_timeout_seconds` (config.py) was declared,
    documented, and parsed but consumed by NOTHING -- no adapter
    construction site threaded it into `git_timeout=`, so an operator who
    set `fleet.git_timeout_seconds:` in their profile got silence, not
    effect (R10). This closes that gap at `_build_adapter`, its ONE
    construction choke point.

    A direct passthrough, deliberately: R11 corrected R10's first attempt,
    which forwarded `cfg.git_timeout_seconds` only when it DIFFERED from a
    package-default constant -- a VALUE comparison. That made an operator
    who wrote `git_timeout_seconds: 0.25` (config.py's own documented
    default, and therefore one of the likeliest values someone being
    explicit would pick) indistinguishable from one who never set the key
    at all, so their deliberate 0.25 was silently discarded in favor of
    the adapter's 30s default -- 120x what they asked for. The defect
    R10 exists to close reappeared in miniature, on one specific value
    instead of every value.

    R11's fix moves the distinction to where it belongs: `FleetConfig.
    git_timeout_seconds` is now `None` unless the profile's `fleet:`
    block sets a genuinely usable (positive, non-bool) number -- a
    PRESENCE signal, not a value one one caller here can misread. `None`
    is also each adapter constructor's own default for `git_timeout`, so
    this passthrough is a byte-identical no-op for anyone who has not set
    the key, and forwards ANY deliberately-set value verbatim, including
    one that happens to equal the package default.

    Args:
        workspace: the working tree to resolve fleet configuration for.

    Returns:
        float | None: the profile's `git_timeout_seconds` if the operator
        set one; `None` otherwise (absent key, a malformed value, fleet
        disabled, or no profile at all -- `resolve_fleet_config` never
        raises and every one of those cases already resolves to `None`
        at the config layer, per its own docstring).
    """
    cfg = fleet_config.resolve_fleet_config(workspace)
    return cfg.git_timeout_seconds


#: Refuse a `--sessions-json` file larger than this many bytes outright,
#: rather than reading an arbitrarily large file into memory on a bad or
#: hostile path (I1).
_MAX_SESSIONS_JSON_BYTES = 50 * 1024 * 1024


class SessionsJsonUnusable(ValueError):
    """`--sessions-json` could not be loaded as a list of session records (I1).

    A `ValueError` subclass so it is still caught anywhere an existing
    `except ValueError` already wraps adapter construction — but every
    `owner` verb handler catches this specific type first, so the printed
    reason is never mislabeled as an identity-resolution failure.
    """


def _load_sessions_json(path: Path) -> list[dict[str, Any]]:
    """Load and validate a `--sessions-json` file (I1).

    Args:
        path: the `--sessions-json` path.

    Returns:
        list[dict[str, Any]]: the parsed session records.

    Raises:
        SessionsJsonUnusable: for a missing or unreadable file, a file over
            `_MAX_SESSIONS_JSON_BYTES`, bytes that are not valid UTF-8,
            content that is not valid JSON, or JSON that is not a list of
            objects (a bare object, a list of non-dict items, etc). The
            reason is always human-readable and never a raw traceback.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SessionsJsonUnusable(f"could not stat {path}: {exc}") from exc
    if size > _MAX_SESSIONS_JSON_BYTES:
        raise SessionsJsonUnusable(
            f"{path} is too large ({size} bytes, limit {_MAX_SESSIONS_JSON_BYTES})"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SessionsJsonUnusable(f"could not read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SessionsJsonUnusable(f"{path} is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SessionsJsonUnusable(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise SessionsJsonUnusable(f"{path} must contain a JSON array of objects")
    return data


def _build_adapter(args: argparse.Namespace) -> SessionAdapter:
    """Construct the `SessionAdapter` selected by `args.harness`.

    Args:
        args: parsed CLI arguments for the `register` subcommand.

    Returns:
        SessionAdapter: a `ClaudeAdapter`, `SubagentAdapter`, or
        `PortableAdapter`, per `args.harness`.

    Raises:
        ValueError: `--harness subagent` was given without `--local-id` — a
            subagent dispatch has no honest process-level fallback identity
            the way `PortableAdapter` does (`str(os.getpid())`; see
            `adapter/subagent.py`'s module docstring), so the PM-minted
            dispatch id must be supplied explicitly.
        SessionsJsonUnusable: `--sessions-json` was given but is unusable
            (I1) — a `ValueError` subclass, so any pre-existing `except
            ValueError` around adapter construction still catches it.
    """
    git_timeout = _resolved_git_timeout_override(args.workspace)
    if args.harness == "claude":
        sessions = None
        if args.sessions_json is not None:
            sessions = _load_sessions_json(args.sessions_json)
        return ClaudeAdapter(
            args.workspace,
            args.slug,
            current_session_id=args.session_id,
            sessions=sessions,
            session_relay_script=args.session_relay_script,
            # Always a real attribute now (`_add_harness_arguments` registers
            # `--git-facts-root` on every observe subcommand) -- direct access,
            # not a getattr fallback that would hide the attribute's existence
            # from a reader.
            git_facts_root=args.git_facts_root,
            # Chunk 9, PM ruling R10: `None` (the overwhelming common case --
            # see `_resolved_git_timeout_override`) is this constructor's own
            # default too, so this line is a byte-identical no-op for every
            # caller who has not set `fleet.git_timeout_seconds`.
            git_timeout=git_timeout,
        )
    if args.harness == "subagent":
        if not args.local_id:
            raise ValueError(
                "--harness subagent requires --local-id (the PM-minted dispatch id) "
                "— there is no fabricated fallback for a dispatch's identity"
            )
        return SubagentAdapter(
            args.workspace,
            args.slug,
            local_id=args.local_id,
            # Chunk 7 fix: this was computed onto `args` by every
            # --hook-payload consumer (`_cmd_observe_dispatch`'s own
            # docstring/comment) but silently dropped here — only
            # ClaudeAdapter received it above. `SubagentStart`'s production
            # hook always dispatches `--harness subagent` (see
            # templates/hooks/claude-code/subagent-start), so this was the
            # live path the defect was measured on, not a theoretical gap.
            git_facts_root=args.git_facts_root,
            git_timeout=git_timeout,
        )
    return PortableAdapter(
        args.workspace,
        args.slug,
        local_id=args.local_id,
        # Same fix, same rationale, for this CLI's own default harness
        # (`--harness` defaults to "portable" — see `_add_harness_arguments`).
        git_facts_root=args.git_facts_root,
        git_timeout=git_timeout,
    )


def _cmd_register(args: argparse.Namespace) -> int:
    """Handle `fleet register`.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` on success; `1` if the registration was rejected or the
        manifest lock could not be acquired.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"
    sessions_dir = fleet_dir / "sessions"

    try:
        adapter = _build_adapter(args)
        fragment = register_session(
            adapter,
            origination=args.origination,
            project_id=args.project_id,
            writer_role=args.writer_role,
            log_path=log_path,
            sessions_dir=sessions_dir,
            target_session_id=args.target_session_id,
            lock_retry_attempts=args.lock_retry_attempts,
        )
    except LockTimeoutError as exc:
        print(f"fleet register: could not acquire the manifest lock: {exc}", file=sys.stderr)
        return 1
    except SessionIdentityUnresolved as exc:
        print(f"fleet register: rejected: {exc}", file=sys.stderr)
        return 1
    except (ValidationError, OwnershipError, ValueError) as exc:
        print(f"fleet register: rejected: {exc}", file=sys.stderr)
        return 1

    print(f"registered {fragment.node_id} (lifecycle={fragment.lifecycle})")
    return 0


def _cmd_handoff_emit(args: argparse.Namespace) -> int:
    """Handle `fleet handoff emit`.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` on success; `1` if the write was rejected or the manifest
        lock could not be acquired.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"
    sessions_dir = fleet_dir / "sessions"
    cwd = args.cwd or args.workspace
    prompt_text = args.prompt_file.read_text(encoding="utf-8")

    try:
        adapter = _build_adapter(args)
        result = handoff_emit(
            adapter,
            slug=args.slug,
            project_id=args.project_id,
            prompt_text=prompt_text,
            cwd=cwd,
            branch=args.branch,
            writer_role=args.writer_role,
            log_path=log_path,
            sessions_dir=sessions_dir,
            lock_retry_attempts=args.lock_retry_attempts,
        )
    except LockTimeoutError as exc:
        print(f"fleet handoff emit: could not acquire the manifest lock: {exc}", file=sys.stderr)
        return 1
    except (ValidationError, OwnershipError, ValueError) as exc:
        # `ValueError` covers `make_node_id`'s blank-component guard (11th-round
        # preflight, R11-B): a blank `--slug`/`--workspace` must render the same
        # one-line rejection every other subcommand does, never a traceback.
        print(f"fleet handoff emit: rejected: {exc}", file=sys.stderr)
        return 1

    if args.output_file is not None:
        args.output_file.write_text(result.prompt_text, encoding="utf-8")
        print(f"emitted handoff {result.handoff_id} ({result.node_id}) -> {args.output_file}")
    else:
        print(f"emitted handoff {result.handoff_id} ({result.node_id})")
        print(result.prompt_text)
    return 0


def _cmd_handoff_cancel(args: argparse.Namespace) -> int:
    """Handle `fleet handoff cancel`.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` on success; `1` if the write was rejected or the manifest
        lock could not be acquired.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"
    sessions_dir = fleet_dir / "sessions"

    try:
        fragment = handoff_cancel(
            args.node_id,
            project_id=args.project_id,
            writer_role=args.writer_role,
            log_path=log_path,
            sessions_dir=sessions_dir,
            lock_retry_attempts=args.lock_retry_attempts,
        )
    except LockTimeoutError as exc:
        print(f"fleet handoff cancel: could not acquire the manifest lock: {exc}", file=sys.stderr)
        return 1
    except (ValidationError, OwnershipError, ValueError) as exc:
        # See `_cmd_handoff_emit` (11th-round preflight, R11-B).
        print(f"fleet handoff cancel: rejected: {exc}", file=sys.stderr)
        return 1

    print(f"cancelled {fragment.node_id} (lifecycle={fragment.lifecycle})")
    return 0


def _cmd_handoff_stale(args: argparse.Namespace) -> int:
    """Handle `fleet handoff stale`.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` — listing is read-only and has nothing to reject.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"
    sessions_dir = fleet_dir / "sessions"

    rows = stale_report(
        log_path=log_path, sessions_dir=sessions_dir, expiry_seconds=args.expiry_seconds
    )
    if not rows:
        print("no stale handoffs")
        return 0
    for row in rows:
        print(
            f"{row['node_id']}  handoff_id={row['handoff_id']}  "
            f"age_seconds={row['age_seconds']:.0f}"
        )
    return 0


def _cmd_handoff_self_register(args: argparse.Namespace) -> int:
    """Handle `fleet handoff self-register` (review FIX #1).

    This is the launched session's actual first-action invocation surface
    for FR-2's launch flip — `handoff.self_register()` was Python-only
    before this fix, so "launching flips awaiting-launch to active" had no
    real shell/CLI path a spawned session could call.

    `--handoff-id` is the primary anchor (Decision E). If it is not given
    directly, `--prompt-file` is grepped for the embedded
    `FLEET-HANDOFF-ID:` line (`handoff.extract_handoff_id`) — the literal
    "first action greps its own prompt for the token" DESIGN describes. If
    an id still cannot be recovered, this falls back to the fuzzy
    `(cwd, branch)` path: `--cwd`/`--branch` if given explicitly, else
    derived from the adapter's own `git_facts()` (the launched session's
    actual checkout) — never fabricated, matching every other adapter fact
    in this package.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` if the row is now `active` (freshly launched, or already
        was — idempotent repeat); `1` if no candidate matched, the match was
        closed (cancelled/expired), or the lock/id could not be resolved;
        `2` if the fuzzy match was ambiguous — refused, never auto-picked,
        with every candidate printed for human/PM disambiguation.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"
    sessions_dir = fleet_dir / "sessions"

    handoff_id = args.handoff_id
    if handoff_id is None and args.prompt_file is not None:
        handoff_id = extract_handoff_id(args.prompt_file.read_text(encoding="utf-8"))

    cwd = args.cwd
    branch = args.branch

    try:
        if handoff_id is None and (cwd is None or branch is None):
            # Only the fuzzy path needs cwd/branch at all — never touch the
            # adapter (or its git subprocess calls) when an id was recovered.
            adapter = _build_adapter(args)
            facts = adapter.git_facts()
            if cwd is None:
                cwd = facts.toplevel or args.workspace
            if branch is None:
                branch = facts.branch

        result = handoff_self_register(
            log_path=log_path,
            sessions_dir=sessions_dir,
            writer_role=args.writer_role,
            handoff_id=handoff_id,
            cwd=cwd,
            branch=branch,
            lock_retry_attempts=args.lock_retry_attempts,
        )
    except LockTimeoutError as exc:
        print(
            f"fleet handoff self-register: could not acquire the manifest lock: {exc}",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(f"fleet handoff self-register: rejected: {exc}", file=sys.stderr)
        return 1

    if result.status in ("launched", "already_launched"):
        print(f"{result.status}: {result.node_id} (match={result.match_method})")
        return 0

    if result.status == "ambiguous":
        print(
            "fleet handoff self-register: ambiguous fuzzy match — refusing to "
            "auto-flip; candidates for human/PM disambiguation:",
            file=sys.stderr,
        )
        for candidate in result.candidates:
            print(f"  - {candidate}", file=sys.stderr)
        return 2

    print(
        f"fleet handoff self-register: {result.status}"
        + (f": {result.node_id}" if result.node_id else ""),
        file=sys.stderr,
    )
    return 1


#: Default D-ceiling when nothing resolves one (the top of the ladder — no
#: ceiling in practice). See `_resolve_d_ceiling`.
_DEFAULT_D_CEILING = "D4-prod"

#: The rung `labels` key `_resolve_d_ceiling` looks for. See its docstring
#: for why this piggybacks on `labels` rather than a dedicated schema field.
_D_CEILING_LABEL_KEY = "d_ceiling"


def _resolve_d_ceiling(workspace: Path) -> str:
    """Resolve the project's D-ceiling from the operator's deployment profile.

    `core/done.py` treats the D-ceiling as a plain caller-supplied parameter
    (DP#5 — it never reads a profile itself, per this chunk's brief); this
    is the one place that bridges the two, matching DESIGN's `advance(node,
    level, evidence, approver, ceiling)` signature intent (`ceiling` comes
    from the CLI, not from `core/done.py`).

    **Flagged as a design call, not a settled contract (see this chunk's
    report to PM/Architect).** `scripts/superhuman_profile.py`'s schema —
    the general Lab/Test/UAT/Prod deployment-rung ladder read from
    `.superhuman/profile.yaml` — has no dedicated field for a *done-ladder*
    ceiling; adding one would mean extending that already-shipped module's
    schema, which is out of this chunk's scope (`core/done.py` +
    `cli.py`'s `done advance` wiring only, per PLAN.md). This resolver
    instead reuses each matched rung's existing free-form `labels` mapping
    (`superhuman_profile.Rung.labels`, documented there as "Free-form
    metadata carried into resolver output") and looks for a `d_ceiling`
    label naming one of `core.done.DONE_LEVELS` — the minimal wiring that
    satisfies PLAN.md's "D-ceiling from profile" step without inventing a
    new top-level profile schema key unilaterally. An operator opts in by
    adding e.g. `labels: {d_ceiling: "D2-test"}` to a rung in their
    `profile.yaml`; absent that, every project is unrestricted (`D4-prod`).

    Args:
        workspace: the project's working tree root — the profile search
            starts here (`superhuman_profile.find_profile`).

    **G5 fix #F5 — fail CLOSED on a present-but-invalid value.** If the
    matched rung declares a `d_ceiling` label at all, it must name a
    recognized `DONE_LEVELS` value or this function raises — it never
    silently falls back to the unrestricted `_DEFAULT_D_CEILING` for a value
    an operator actually configured. Before this fix, ANY unrecognized
    `d_ceiling` value (a typo like `"D2_test"`, a stale/renamed level) fell
    through the same `ceiling if ceiling in DONE_LEVELS else
    _DEFAULT_D_CEILING` line as "no label configured," silently granting the
    unrestricted top of the ladder — exactly the opposite of what an
    operator who bothered to set a ceiling at all almost certainly intended.
    Only the genuinely-absent case (no label, no matching rung, no profile,
    or an unreadable profile) still defaults to `_DEFAULT_D_CEILING`.

    **G5 fix #N3 — distinguish ABSENT from PRESENT-BUT-CORRUPT.**
    `superhuman_profile.find_profile(workspace)` returns `None` when no
    profile file exists at all ("zero-config" — a legitimate case for a
    developer with no deployment ladder configured, per
    `superhuman_profile.load_profile`'s own `path=None` -> built-in-default
    contract, which never raises `ProfileError`) versus a `Path` when one
    was found. Before this fix, ANY `ProfileError` — whether from "no
    profile" or from a genuinely present-but-unreadable/malformed profile
    file — fell through to the same permissive `_DEFAULT_D_CEILING`
    (`D4-prod`, unrestricted). That meant a corrupt profile (bad YAML, an
    unknown top-level key, a schema violation) failed OPEN to the top of the
    done-ladder instead of failing closed — exactly backwards for a
    ceiling-enforcement mechanism, and inconsistent with this same
    function's own #F5 fix below (present-but-invalid `d_ceiling` *label*
    already failed closed; only the "profile itself won't load" case still
    failed open). Now: `find_profile` is called first and its result
    inspected directly. A `ProfileError` while loading a genuinely absent
    profile (`path is None`) is not actually reachable — see
    `superhuman_profile.load_profile`'s docstring — so this is defensive,
    not the primary branch; a `ProfileError` while loading a profile that
    DOES exist (`path is not None`) now raises instead of defaulting.

    Returns:
        str: the resolved D-ceiling, one of `core.done.DONE_LEVELS`.
        `_DEFAULT_D_CEILING` if no profile is found at all, no rung matches
        the workspace, or the matched rung declares no `d_ceiling` label.

    Raises:
        ValueError: if the matched rung DOES declare a `d_ceiling` label,
            but its value is not one of `DONE_LEVELS` (G5 fix #F5); or if a
            profile file WAS found but failed to load/parse (G5 fix #N3) —
            failing closed rather than silently granting the unrestricted
            `D4-prod` default for a profile an operator configured but that
            is now corrupt. `_cmd_done_advance` catches this the same way it
            catches every other rejection — a clean nonzero exit, never an
            uncaught traceback.
    """
    path = superhuman_profile.find_profile(workspace)
    try:
        profile = superhuman_profile.load_profile(path)
        resolution = superhuman_profile.resolve(workspace, profile)
    except superhuman_profile.ProfileError as exc:
        if path is None:
            # Defensive only (see docstring): `load_profile(None)` builds
            # the built-in default and does not raise. Kept as a fallback
            # rather than an assertion, in case that contract ever changes.
            return _DEFAULT_D_CEILING
        raise ValueError(
            f"profile at {path} was found but failed to load — failing "
            "closed rather than silently granting the unrestricted D4-prod "
            f"default (G5 fix #N3): {exc}"
        ) from exc
    if resolution.stage is None:
        return _DEFAULT_D_CEILING
    if _D_CEILING_LABEL_KEY not in resolution.stage.labels:
        return _DEFAULT_D_CEILING
    ceiling = resolution.stage.labels[_D_CEILING_LABEL_KEY]
    if ceiling not in DONE_LEVELS:
        raise ValueError(
            f"profile d_ceiling label {ceiling!r} is not a recognized "
            f"done_level (expected one of {DONE_LEVELS}) — failing closed "
            "rather than silently granting the unrestricted D4-prod default "
            "(G5 F5)"
        )
    return ceiling


def _cmd_done_advance(args: argparse.Namespace) -> int:
    """Handle `fleet done advance` (PLAN.md Chunk 5, FR-6).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` on success (including a deduped repeat of an already-
        recorded transition); `1` if the advance was rejected (policy,
        validation, ownership, or an unrecognized level) or the manifest
        lock could not be acquired.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"
    sessions_dir = fleet_dir / "sessions"

    try:
        # G5 fix #F5: ceiling resolution moved INSIDE the try (it used to run
        # before this block even started) — `_resolve_d_ceiling` now raises
        # `ValueError` on a present-but-unrecognized `d_ceiling` profile
        # label (failing closed) instead of silently defaulting to the
        # unrestricted D4-prod, and that raise must produce a clean nonzero
        # exit here, never an uncaught traceback.
        ceiling = args.ceiling or _resolve_d_ceiling(args.workspace)

        # G5 fix #4: evidence-JSON parsing moved INSIDE the try (it used to
        # run before this block even started), and the decoded value is
        # checked to actually be a JSON object — a missing --evidence-json
        # path, a malformed file, or a well-formed-but-non-object top-level
        # value (e.g. `[1, 2]`) must all produce a clean nonzero exit and a
        # stderr message, never an uncaught traceback.
        evidence: dict[str, Any] = {}
        if args.evidence_json is not None:
            decoded = json.loads(args.evidence_json.read_text(encoding="utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError(
                    "--evidence-json must contain a JSON object, got "
                    f"{type(decoded).__name__}"
                )
            evidence = decoded

        result = done_advance(
            args.node_id,
            args.target_level,
            evidence=evidence,
            approver=args.approver,
            ceiling=ceiling,
            project_id=args.project_id,
            writer_role=args.writer_role,
            log_path=log_path,
        )
    except LockTimeoutError as exc:
        print(f"fleet done advance: could not acquire the manifest lock: {exc}", file=sys.stderr)
        return 1
    except (
        ValidationError,
        OwnershipError,
        ValueError,
        DonePolicyError,
        TypeError,
        FileNotFoundError,
    ) as exc:
        print(f"fleet done advance: rejected: {exc}", file=sys.stderr)
        return 1

    # `core/done.py` may not import `core/projection` (NFR-2 — see
    # done.py's module docstring), so projecting the fragment happens here,
    # the same boundary `register_session` and `handoff.py` already draw.
    event = done_event_for(args.node_id, args.target_level, log_path)
    if event is not None:
        try:
            project_event(event, sessions_dir)
        except FragmentCorrupt:
            # G5 round-5 (#P4-1/#P4-2): the transition is already durably
            # appended to the log by `done_advance` above; a corrupt cached
            # fragment must not turn a successful transition into a crash,
            # and must not silently reset the other status fields either
            # (the bug this round eliminates). Recover via a full replay —
            # the just-appended event is already in the log, so the
            # rebuilt fragment ends at the correct current state.
            rebuild(log_path, sessions_dir, project_id=args.project_id)

    print(f"{result.status}: {result.node_id} -> {result.level} (ceiling={ceiling})")
    return 0


def _cmd_query_edges(args: argparse.Namespace) -> int:
    """Handle `fleet query edges` (PLAN.md Chunk 4).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` — a read-only query has nothing to reject.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"

    if args.node:
        edges = edges_of(args.node, log_path)
    else:
        graph = resolve_graph(log_path)
        edges = [
            {
                "src": e.src,
                "type": e.type,
                "dst": e.dst,
                "source": e.source,
                "evidence": dict(e.evidence),
            }
            for e in graph.edges
        ]

    if not edges:
        print("no edges")
        return 0
    for edge in edges:
        print(
            f"{edge['src']} --{edge['type']}--> {edge['dst']}  "
            f"source={edge['source']}  evidence={edge['evidence']}"
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Handle `fleet status` (PLAN.md Chunk 6, FR-7/CC-7).

    Prints the read-only session/status/edges table to stdout. Read-only:
    `view.render_status_table` reads exclusively via `core/query` and never
    writes the manifest.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` — a read-only view has nothing to reject.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"
    sessions_dir = fleet_dir / "sessions"

    table = render_status_table(sessions_dir, log_path, project_id=args.project_id)
    print(table)
    return 0


def _cmd_gen_view(args: argparse.Namespace) -> int:
    """Handle `fleet gen-view` (PLAN.md Chunk 6, DESIGN "Decision A").

    Writes/refreshes `docs/superhuman/<slug>/FLEET.md` — a generated DOC,
    not the manifest (FR-7/CC-7's prohibition is on writing `events.jsonl` /
    fragments / `.lock`, which this never touches).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` — generating a read-only view has nothing to reject.
    """
    fleet_dir = args.fleet_dir or _default_fleet_dir(args.workspace, args.slug)
    log_path = fleet_dir / "events.jsonl"
    sessions_dir = fleet_dir / "sessions"

    target = write_fleet_md(
        sessions_dir,
        log_path,
        slug=args.slug,
        workspace=args.workspace,
        project_id=args.project_id,
    )
    print(f"wrote {target}")
    return 0


def _cmd_locate(args: argparse.Namespace) -> int:
    """Handle `fleet locate` (PLAN.md Chunk 2, FR-4, D1).

    Prints the resolved slug on a resolvable `--cwd`, prints nothing on an
    ambiguous/unresolvable one — never a traceback, never a nonzero exit
    (`locate_project` never raises; see `locate.py`'s module docstring).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0`.
    """
    result = locate_project(args.cwd)
    if result is not None:
        print(result.slug)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Handle `fleet doctor` (PLAN.md Chunk 3, D6, FR-10, FR-13).

    Read-only estate-health scan: for every `SUPERHUMAN.md` found under the
    given `--scan` roots, report whether a hook could write to it right now
    and, if not, why (`scripts.fleet.doctor.scan`'s four states). Also
    reports the operator's git version and flags anything below the 2.31
    floor `locate.py`'s root discovery depends on. Never a fail-closed
    assertion — this diagnoses, it never edits a record or writes a
    manifest row.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` — a read-only scan has nothing to reject.
    """
    report = fleet_doctor.scan(args.scan)

    print(f"scanned roots: {', '.join(str(root) for root in report.roots)}")

    git = report.git_version
    if git.ok:
        print(f"git version: {git.raw or '(unknown)'}")
    else:
        floor = ".".join(str(part) for part in fleet_doctor.MIN_GIT_VERSION)
        print(
            f"git version: {git.raw or '(could not be determined)'} -- BELOW the "
            f"{floor} floor locate.py's root discovery depends on "
            "(`git rev-parse --path-format=absolute`); observation may be silently "
            "dead on this machine"
        )

    if not report.records:
        print("no project records found under the scanned roots")
        return 0

    for record in sorted(report.records, key=lambda r: (str(r.root), r.slug)):
        print(f"{record.root}  {record.slug}  {record.state}  ({record.detail})")
        if record.role_gate is not None:
            gate = record.role_gate
            if gate.state == "unknown":
                print(f"    role gate: UNKNOWN — {gate.detail}")
            else:
                print(f"    role gate: {gate.detail}")
    return 0


def _cmd_project_mint(args: argparse.Namespace) -> int:
    """Handle `fleet project mint` (PLAN.md Chunk 4, D3, FR-11).

    Mints a random 16-hex `**Project-id:**` for a record that lacks one; a
    no-op (never a re-mint) on a record that already has one. Always exits
    0 — minting is a write helper, not the fail-closed assertion (`check`
    is that).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0`.
    """
    project_id = fleet_project_id.mint_project_id(args.workspace, args.slug)
    print(project_id)
    return 0


def _cmd_project_check(args: argparse.Namespace) -> int:
    """Handle `fleet project check` (PLAN.md Chunk 4, D3, FR-12).

    The fail-closed assertion D3 calls for: exits non-zero when the record
    has no `**Project-id:**`, deliberately outside `observe.py`'s
    fail-soft posture (this is an assertion, not observation).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` when the id is present (also printed to stdout); `1`
        when it is absent.
    """
    project_id = fleet_project_id.check_project_id(args.workspace, args.slug)
    if project_id is None:
        print(
            f"no **Project-id:** found for slug {args.slug!r} under {args.workspace} "
            "-- run `python -m scripts.fleet.cli project mint` to assign one",
            file=sys.stderr,
        )
        return 1
    print(project_id)
    return 0


def _cmd_role_block_check(args: argparse.Namespace) -> int:
    """Handle `fleet role-block check` (chunk 7a's fail-closed assertion, D7.6).

    Reads `--prompt-file` (or stdin, for `-`) and runs it through the SAME
    `check_role_block` function the `PreToolUse` adapter
    (`pre_tool_use_role_gate.py`) calls (TC-87 asserts this) — exits 0 for
    `ROLE`/`NON_ROLE` and 1 for anything else (`MISMATCH`, `UNMARKED`, or
    `FAULT`). Unlike the hook's own fail-SOFT posture (NFR-9: a fault must
    never turn into a denial), this is a manual, fail-CLOSED assertion tool
    in the mould of `fleet project check` — a `FAULT` here (e.g. an
    unreadable `roles/` directory) cannot certify compliance, so it exits
    non-zero exactly like a genuine mismatch would.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` for `ROLE`/`NON_ROLE`; `1` otherwise.
    """
    if str(args.prompt_file) == "-":
        try:
            # Read raw bytes and decode as UTF-8 explicitly -- a verbatim
            # role dispatch piped in from a real harness arrives as UTF-8
            # bytes (RFC 8259 §8.1
            # <https://www.rfc-editor.org/rfc/rfc8259#section-8.1>), but
            # `sys.stdin.read()` decodes with the process's
            # locale-preferred encoding, which on Windows is the
            # console/ANSI code page (e.g. cp1252), not UTF-8 -- corrupting
            # every role file's em dash and falsely reporting MISMATCH.
            prompt = sys.stdin.buffer.read().decode("utf-8")
        except UnicodeDecodeError:
            # Unlike the hook's fail-SOFT posture (NFR-9), this is a
            # manual/CI fail-CLOSED assertion tool (see the docstring
            # above): a fault cannot certify compliance, so it prints the
            # `FAULT` verdict -- one line, matching the normal path's own
            # `print(result.verdict.value)` -- and exits 1, exactly like a
            # genuine MISMATCH/UNMARKED would.
            print(fleet_role_block.Verdict.FAULT.value)
            return 1
    else:
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            # `ValueError` covers `UnicodeDecodeError` on a non-UTF-8 file.
            print(f"fleet role-block check: could not read --prompt-file: {exc}", file=sys.stderr)
            return 1

    result = fleet_role_block.check_role_block(prompt, args.roles_dir)
    print(result.verdict.value)
    return 0 if result.verdict in (fleet_role_block.Verdict.ROLE, fleet_role_block.Verdict.NON_ROLE) else 1


def _cmd_hooks_install(args: argparse.Namespace) -> int:
    """Handle `fleet hooks install` (Chunk 8, FR-9, A3).

    All logic lives in `hooks_install.install`; this only translates CLI
    args to the module API and prints a result. `--harness` is required
    with a single choice (D4: no auto-detection) since `hooks_install.py`
    is the one module in `scripts/` DESIGN.md names as harness-specific.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` on success (including a no-op `--dry-run`); `1` if the
        resolved skill root is a linked git worktree or git resolution
        failed outright.
    """
    try:
        result = fleet_hooks_install.install(
            args.settings_path, skill_root=args.skill_root, dry_run=args.dry_run
        )
    except fleet_hooks_install.HooksInstallError as exc:
        print(f"fleet hooks install: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        if result.changed:
            print(result.diff, end="")
        else:
            print("fleet hooks install --dry-run: already up to date; nothing to write")
        return 0

    print(f"installed (root: {result.root})" if result.changed else f"already installed (root: {result.root})")
    if result.worktree_pinned:
        print(
            "WARNING: --skill-root points inside a linked git worktree "
            "(worktree-pinned) -- this install breaks if that worktree is reaped",
            file=sys.stderr,
        )
    return 0


def _cmd_hooks_uninstall(args: argparse.Namespace) -> int:
    """Handle `fleet hooks uninstall` (Chunk 8, FR-9).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` on success -- removing an already-absent entry is a
        no-op, not an error; `1` if `settings_path` exists and is not
        valid JSON (Phase 3.3 preflight item 4 -- named in the printed
        error rather than a raw traceback).
    """
    try:
        result = fleet_hooks_install.uninstall(args.settings_path)
    except fleet_hooks_install.HooksInstallError as exc:
        print(f"fleet hooks uninstall: {exc}", file=sys.stderr)
        return 1
    print("uninstalled" if result.changed else "already not installed")
    return 0


def _cmd_hooks_status(args: argparse.Namespace) -> int:
    """Handle `fleet hooks status` (Chunk 8, FR-9).

    Also reports D7.8's silent-disable case (a registered command whose
    path no longer exists) and whether a registered command is
    worktree-pinned.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` on success -- a status report otherwise has nothing to
        reject; `1` if `settings_path` exists and is not valid JSON
        (Phase 3.3 preflight item 4 -- named in the printed error rather
        than a raw traceback).
    """
    try:
        result = fleet_hooks_install.status(args.settings_path)
    except fleet_hooks_install.HooksInstallError as exc:
        print(f"fleet hooks status: {exc}", file=sys.stderr)
        return 1
    for entry in result.entries:
        line = f"{entry.hook_event}/{entry.matcher}: {'installed' if entry.present else 'NOT installed'}"
        if entry.present and not entry.command_path_exists:
            line += " -- command path does NOT exist (silently disabled)"
        if entry.present and entry.worktree_pinned:
            line += " -- WORKTREE-PINNED"
        print(line)
    print("installed" if result.installed else "not installed")
    return 0


def _safe_build_adapter_for_observe(
    args: argparse.Namespace, *, event: str
) -> SessionAdapter | None:
    """Build an adapter for an `observe` subcommand without ever raising (Decision A).

    `_build_adapter` legitimately raises `ValueError` for a malformed
    `--harness subagent` invocation (missing `--local-id`; see its
    docstring) — every other `observe` subcommand call site relies on
    `observe.py`'s own broad catch to enforce "always exits 0", but that
    catch only wraps the write itself, not adapter *construction*, which
    happens one line earlier in each `_cmd_observe_*` handler. Without this
    wrapper, a malformed `--harness subagent` call would raise past
    `observe.py` entirely and violate the exit-0 contract every other
    failure mode of this verb group already honors.

    Phase 3.3 preflight FIX 4: this rejection is now also routed through
    `observe.journal_adapter_construction_failure` — before this fix, it was
    printed to stderr only and never reached `observe-failures.log`,
    invisible to `fleet observe status` (contradicting W-FR-8 and
    W-NFR-1's "recorded, not swallowed silently").

    Args:
        args: parsed CLI arguments for one `observe` subcommand.
        event: which `observe` verb this is (`"dispatch"` | `"relay"` |
            `"handoff-emit"` | `"launch"`), forwarded to the journal entry.

    Returns:
        SessionAdapter | None: the built adapter, or `None` if construction
        itself failed — the caller must treat `None` as "print nothing
        beyond this line, exit 0", matching how `observe.py` itself
        surfaces every other rejection (journaled, not raised, never a
        nonzero exit).
    """
    try:
        return _build_adapter(args)
    except ValueError as exc:
        print(f"fleet observe: {exc}", file=sys.stderr)
        fleet_observe.journal_adapter_construction_failure(
            args.workspace, args.slug, event=event, error_text=str(exc)
        )
        return None


def _cmd_observe_dispatch(args: argparse.Namespace) -> int:
    """Handle `fleet observe dispatch` (fleet-wiring Chunk 1, W-FR-1; chunk 7
    adds `--hook-payload`/`--anchor`, FR-7/FR-17).

    `--hook-payload <file|->` (D2b, mirroring `_cmd_observe_session_start`)
    is mutually exclusive in effect with `--workspace`/`--slug`/
    `--dispatch-id`: when given, `--workspace`/`--slug` are derived from the
    payload's `cwd` via `locate.locate_project`, and the payload's `agent_id`
    (CHUNK-1-FINDINGS.md finding 1 — the per-dispatch identifier) is used as
    both `--dispatch-id` and `--local-id` unless the caller already supplied
    one explicitly. A malformed/absent payload or a locator refusal both
    mean "nothing to do here": exit 0, having written nothing, exactly like
    every other `observe` outcome.

    `--anchor <dir>` behaves identically to `_cmd_observe_session_start`'s
    (Chunk 6, ARCHITECTURE.md Addendum §A): tried BEFORE the payload's `cwd`
    when given and non-empty. `templates/hooks/claude-code/subagent-start`
    passes `$CLAUDE_PROJECT_DIR` here. When `--anchor` is absent (the
    default), behavior is byte-identical to before this option existed.

    Whichever of `--anchor`/payload `cwd` actually resolved the project is
    threaded through as `args.git_facts_root` (chunk 6 G6 addendum, carried
    to this verb per PLAN.md chunk 7's PM ruling 5) — see
    `_cmd_observe_session_start`'s identical comment for why this must never
    be silently left at the `workspace` default.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` — `observe.py` never raises and never signals
        failure through the exit code (Decision A).
    """
    workspace = args.workspace
    slug = args.slug
    dispatch_id = args.dispatch_id

    if args.hook_payload is not None:
        payload = fleet_hook_payload.read_hook_payload(args.hook_payload)
        if payload is None:
            return 0
        location = None
        resolved_from = None
        if getattr(args, "anchor", None):
            location = locate_project(args.anchor)
            if location is not None:
                resolved_from = args.anchor
        if location is None:
            location = locate_project(payload.cwd)
            if location is not None:
                resolved_from = payload.cwd
        if location is None:
            return 0
        workspace = location.workspace
        slug = location.slug
        # `resolved_from` is the session's OWN working tree, before D1's
        # outward hop to `workspace` -- see `_cmd_observe_session_start`'s
        # identical comment for the chunk-6 branch-attribution defect this
        # threading avoids reintroducing here.
        args.git_facts_root = resolved_from
        if dispatch_id is None:
            dispatch_id = payload.agent_id
        if args.local_id is None:
            args.local_id = payload.agent_id

    if workspace is None or slug is None or dispatch_id is None:
        print(
            "fleet observe dispatch: --workspace/--slug/--dispatch-id are required "
            "unless --hook-payload resolves them",
            file=sys.stderr,
        )
        return 0
    args.workspace = workspace
    args.slug = slug

    adapter = _safe_build_adapter_for_observe(args, event="dispatch")
    if adapter is None:
        return 0
    fleet_observe.observe_dispatch(
        adapter,
        workspace=workspace,
        slug=slug,
        dispatch_id=dispatch_id,
        writer_role=args.writer_role,
    )
    return 0


def _cmd_observe_relay(args: argparse.Namespace) -> int:
    """Handle `fleet observe relay` (fleet-wiring Chunk 1, W-FR-2).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` (see `_cmd_observe_dispatch`).
    """
    adapter = _safe_build_adapter_for_observe(args, event="relay")
    if adapter is None:
        return 0
    fleet_observe.observe_relay(
        adapter, workspace=args.workspace, slug=args.slug, writer_role=args.writer_role
    )
    return 0


def _cmd_observe_handoff_emit(args: argparse.Namespace) -> int:
    """Handle `fleet observe handoff-emit` (fleet-wiring Chunk 1, W-FR-3).

    Unlike `dispatch`/`relay`/`launch`, this always carries a stdout (or
    `--output-file`) payload — the deliverable prompt — even when fleet is
    disabled or the write fails (DESIGN's "single most important fail-soft
    behavior"; see `observe.observe_handoff_emit`'s docstring).

    Phase 3.3 preflight FIX 5: `--prompt-file` is required for this verb, so
    a missing/unreadable/non-UTF-8 file is a genuine terminal failure — there
    is no draft to deliver. Unlike every other failure mode of this façade,
    this one has no fallback prompt to hand back; it is journaled and
    reported clearly on stderr, and still exits `0` (Decision A / W-NFR-1's
    "always exits 0" contract), never an unhandled exception.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0`.
    """
    try:
        prompt_text = args.prompt_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"fleet observe handoff-emit: could not read --prompt-file "
            f"{args.prompt_file}: {exc}",
            file=sys.stderr,
        )
        fleet_observe.journal_early_cli_failure(
            args.workspace,
            args.slug,
            event="handoff-emit",
            error_class="prompt_file_unreadable",
            error_text=f"could not read --prompt-file {args.prompt_file}: {exc}",
        )
        return 0
    adapter = _safe_build_adapter_for_observe(args, event="handoff-emit")
    if adapter is None:
        # DESIGN's "single most important fail-soft behavior" still applies
        # to a bad --harness invocation: the draft is delivered untouched
        # rather than lost, exactly as every other observe_handoff_emit
        # failure path already delivers `prompt_text` unmodified.
        if args.output_file is not None:
            args.output_file.write_text(prompt_text, encoding="utf-8")
        else:
            print(prompt_text)
        return 0
    result = fleet_observe.observe_handoff_emit(
        adapter,
        workspace=args.workspace,
        slug=args.slug,
        prompt_text=prompt_text,
        cwd=args.cwd,
        branch=args.branch,
        writer_role=args.writer_role,
    )
    delivered = result.prompt_text if result.prompt_text is not None else prompt_text
    if args.output_file is not None:
        args.output_file.write_text(delivered, encoding="utf-8")
    else:
        print(delivered)
    return 0


def _cmd_observe_launch(args: argparse.Namespace) -> int:
    """Handle `fleet observe launch` (fleet-wiring Chunk 1, W-FR-4).

    Phase 3.3 preflight FIX 5: unlike `handoff-emit`, `--prompt-file` is
    optional here (`observe_launch`'s `prompt_text` parameter is optional,
    used only to recover a `FLEET-HANDOFF-ID` when `--handoff-id` was not
    given directly) — a missing/unreadable/non-UTF-8 file is not a terminal
    failure. It is treated exactly like "no --prompt-file was given at all":
    `prompt_text` falls back to `None`, and `handoff_self_register`'s own
    `(cwd, branch)` fuzzy-match path takes over unchanged, never an
    unhandled exception past this function.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` (see `_cmd_observe_dispatch`).
    """
    adapter = _safe_build_adapter_for_observe(args, event="launch")
    if adapter is None:
        return 0
    prompt_text: str | None = None
    if args.prompt_file is not None:
        try:
            prompt_text = args.prompt_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(
                f"fleet observe launch: could not read --prompt-file "
                f"{args.prompt_file}, falling back to (cwd, branch) fuzzy match: {exc}",
                file=sys.stderr,
            )
    fleet_observe.observe_launch(
        adapter,
        workspace=args.workspace,
        slug=args.slug,
        handoff_id=args.handoff_id,
        prompt_text=prompt_text,
        cwd=args.cwd,
        branch=args.branch,
        writer_role=args.writer_role,
    )
    return 0


def _cmd_observe_session_start(args: argparse.Namespace) -> int:
    """Handle `fleet observe session-start` (PLAN.md Chunk 3, FR-5/FR-13/FR-17/D2a/D6).

    `--hook-payload <file|->` (D2b) is mutually exclusive in effect with
    `--workspace`/`--slug`: when given, `--workspace`/`--slug` are derived
    from the payload's `cwd` via `locate.locate_project`, and the harness's
    own `session_id` (FR-17) is threaded through as `--session-id`
    (`--harness claude`) / `--local-id` (`--harness portable`/`subagent`) —
    whichever the selected harness actually consumes — unless the caller
    already supplied one explicitly. A malformed/absent payload or a
    locator refusal both mean "nothing to do here": exit 0, having written
    nothing, exactly like every other `observe` outcome (D2b).

    `--anchor <dir>` (Chunk 6, ARCHITECTURE.md Addendum §A) is tried BEFORE
    the payload's `cwd` when given and non-empty: `templates/hooks/claude-code/
    session-start` passes `$CLAUDE_PROJECT_DIR` here, since it names the
    session's outer root and is stable for the session's lifetime, whereas
    `cwd` drifts as the shell moves. This option is deliberately named
    generically (D4: no harness name in `scripts/fleet/**`) — the harness
    knowledge that it should be `$CLAUDE_PROJECT_DIR` lives only in the
    template. `--anchor` is harness-SUPPLIED (an environment variable only
    the harness process can set), never repo-authored, which is exactly why
    it is admissible as a locator input where a repo-authored string is not
    (NFR-4). NFR-4 confinement itself is unweakened by this: the anchor is
    only a starting point, and `locate_project` still derives the returned
    workspace via `git rev-parse` — no path construction happens here. When
    `--anchor` is absent (the default, `None`), behavior is byte-identical
    to before this option existed: only `payload.cwd` is ever tried.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` (see `_cmd_observe_dispatch`).
    """
    workspace = args.workspace
    slug = args.slug

    if args.hook_payload is not None:
        payload = fleet_hook_payload.read_hook_payload(args.hook_payload)
        if payload is None:
            return 0
        location = None
        resolved_from = None
        if getattr(args, "anchor", None):
            location = locate_project(args.anchor)
            if location is not None:
                resolved_from = args.anchor
        if location is None:
            location = locate_project(payload.cwd)
            if location is not None:
                resolved_from = payload.cwd
        if location is None:
            return 0
        workspace = location.workspace
        slug = location.slug
        # `resolved_from` is whichever of --anchor/payload.cwd actually
        # located this project -- the session's OWN working tree, before
        # D1's outward hop to `workspace`. Chunk-6-review defect: with no
        # git_facts_root, ClaudeAdapter's git_facts() queried `workspace`
        # itself for branch/commit state, so a session in a linked worktree
        # got the MAIN CHECKOUT's currently-checked-out branch recorded as
        # its own -- a real value answering a different question, and
        # non-deterministic besides (that checkout's branch can change from
        # unrelated activity in another session). Measured in production:
        # a fleet-deterministic-seams-worktree session's row recorded
        # branch="fix/242-arm-guards-everywhere", the MAIN checkout's branch
        # at that moment. `git -C <dir>` auto-discovers the enclosing
        # worktree from any subdirectory, so `resolved_from` need not be an
        # exact repo root.
        args.git_facts_root = resolved_from
        if args.session_id is None:
            args.session_id = payload.session_id
        if args.local_id is None:
            args.local_id = payload.session_id

    if workspace is None or slug is None:
        print(
            "fleet observe session-start: --workspace/--slug are required unless "
            "--hook-payload resolves them",
            file=sys.stderr,
        )
        return 0
    args.workspace = workspace
    args.slug = slug

    adapter = _safe_build_adapter_for_observe(args, event="session-start")
    if adapter is None:
        return 0

    prompt_text: str | None = None
    if args.prompt_file is not None:
        try:
            prompt_text = args.prompt_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(
                f"fleet observe session-start: could not read --prompt-file "
                f"{args.prompt_file}, continuing without an id-anchored flip: {exc}",
                file=sys.stderr,
            )

    result = fleet_observe.observe_session_start(
        adapter,
        workspace=workspace,
        slug=slug,
        handoff_id=args.handoff_id,
        prompt_text=prompt_text,
        writer_role=args.writer_role,
    )
    if result.error_class == "identity_unresolved":
        # D6: the ONE line `observe.py` itself never prints (its loudness
        # tiers reserve stdout entirely for CLI callers). At SessionStart
        # this stdout becomes session context, reaching a human and the
        # model — the whole point of FR-13.
        print(
            f"fleet observe session-start: project {slug!r} resolved at {workspace} but "
            "its SUPERHUMAN.md has no **Project-id:** line, so nothing was recorded -- "
            "run `fleet project mint` (or `fleet doctor --scan <root>...` for the "
            "estate-wide picture)"
        )
    return 0


def _cmd_observe_status(args: argparse.Namespace) -> int:
    """Handle `fleet observe status` (fleet-wiring Chunk 1, W-FR-8).

    The one `observe` subcommand whose entire purpose is its stdout payload
    — the human-readable enablement/activity report.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` — a read-only report has nothing to reject.
    """
    print(fleet_observe.observe_status(args.workspace, args.slug))
    return 0


#: Exit codes for the `fleet owner` verb group (DESIGN O.3). `2` is
#: argparse's own reserved usage-error code (unchanged); these three are
#: this verb group's own, distinct from every other subcommand's 0/1 split.
_OWNER_EXIT_COORDINATION_REQUIRED = 3
_OWNER_EXIT_STATE_MISMATCH = 4
_OWNER_EXIT_NOT_APPLICABLE = 5


def _resolve_owner_manifest(args: argparse.Namespace) -> tuple[Path, str] | None:
    """Resolve `(log_path, project_id)` for an `owner` verb, or `None` if not applicable.

    A deliberate act, never routed through `observe.py`'s fail-soft façade
    (O-NFR-1) — this prints **exactly one** stderr line and returns `None`
    when fleet is disabled, the slug fails path-confinement validation, or
    the project's identity cannot be resolved (OQ-5); the caller must then
    return exit `5` without writing anything.

    Args:
        args: parsed CLI arguments carrying `workspace` and `slug`.

    Returns:
        tuple[Path, str] | None: `(log_path, project_id)`, or `None`.
    """
    cfg = fleet_config.resolve_fleet_config(args.workspace)
    if not cfg.enabled:
        print(f"fleet owner: not applicable: {cfg.reason}", file=sys.stderr)
        return None

    # M6: validate the slug even when an operator `manifest_dir` override
    # means `default_fleet_dir` (the usual validator, below) is never
    # reached — `read_project_identity` builds a path from this same raw
    # slug regardless of `cfg.manifest_dir`, so an unsafe slug must be
    # rejected before that call every time, not only when there is no
    # override.
    if not fleet_path_safety.slug_is_safe(args.slug):
        print(
            f"fleet owner: not applicable: invalid slug {args.slug!r}: "
            "path separators and '..' are not permitted",
            file=sys.stderr,
        )
        return None

    try:
        fleet_dir = cfg.manifest_dir or fleet_path_safety.default_fleet_dir(args.workspace, args.slug)
    except fleet_path_safety.InvalidSlug as exc:
        print(f"fleet owner: not applicable: {exc}", file=sys.stderr)
        return None

    identity = fleet_project.read_project_identity(args.workspace, args.slug)
    if identity is None:
        print(
            "fleet owner: not applicable: project identity could not be resolved "
            f"(no Project-id in {args.workspace}/docs/superhuman/{args.slug}/SUPERHUMAN.md)",
            file=sys.stderr,
        )
        return None
    project_id, _file_slug = identity

    return fleet_dir / "events.jsonl", project_id


def _node_id_conflicts_with_self_identity(args: argparse.Namespace) -> bool:
    """Return whether `--node-id` was given together with a self-identity flag (M3).

    DESIGN O.3 names `claim`/`stand-down`'s identity flags as two MUTUALLY
    EXCLUSIVE groups: self (`_add_harness_arguments`'s set) or on-behalf
    (`--node-id`). `--harness` defaults to `"portable"` even when never
    typed, so a bare `--node-id` (the documented on-behalf shape) must NOT
    be flagged — only an EXPLICIT self-identity flag conflicts: a
    non-default `--harness`, or any of `--session-id`/`--local-id`/
    `--session-relay-script` (none of which has a meaningful non-`None`
    default).

    Args:
        args: parsed CLI arguments for `owner claim`/`stand-down`.

    Returns:
        bool: `True` iff `args.node_id is not None` and at least one
        self-identity flag was also given.
    """
    if args.node_id is None:
        return False
    return (
        args.harness != "portable"
        or args.session_id is not None
        or args.local_id is not None
        or args.session_relay_script is not None
    )


def _resolve_owner_identity(args: argparse.Namespace, *, verb: str) -> str | None:
    """Resolve the acting node id for an `owner claim`/`stand-down` call.

    Args:
        args: parsed CLI arguments carrying `node_id`/`writer_role`, plus
            the harness-identity flags (`_add_harness_arguments`) when
            acting for self.
        verb: `"claim"` or `"stand-down"`, for the printed message only.

    Returns:
        str | None: the resolved `node_id`, or `None` if resolution failed
        — the caller must then return exit `4` (`--node-id` without
        `--writer-role cto`) or `1` (adapter construction / identity
        failure); the message already printed distinguishes the two.
    """
    if args.node_id is not None:
        if args.writer_role.strip().lower() != "cto":
            print(f"fleet owner {verb}: --node-id requires --writer-role cto", file=sys.stderr)
            return None
        return args.node_id

    try:
        adapter = _build_adapter(args)
        return adapter.current_session().node_id
    except SessionsJsonUnusable as exc:
        # I1: never misreported as an identity-resolution failure, even
        # though this happens inside `_build_adapter`.
        print(f"fleet owner {verb}: --sessions-json unusable: {exc}", file=sys.stderr)
        return None
    except (ValueError, SessionIdentityUnresolved) as exc:
        print(f"fleet owner {verb}: could not resolve identity: {exc}", file=sys.stderr)
        return None


#: `fleet owner claim`/`stand-down`'s own usage-error exit code (O14-b),
#: reusing argparse's reserved `2` the same way the existing
#: `--prior-owner-notified`/`--node-id` usage checks in `_cmd_owner_claim`
#: already do -- a missing/unusable self-identity flag is a usage error, not
#: a write failure (`1`) or a refusal (`3`/`4`/`5`).
_OWNER_EXIT_USAGE_ERROR = 2


def _resolve_owner_self_identity_flags(args: argparse.Namespace, *, verb: str) -> int | None:
    """Validate/complete an owner verb's SELF-identity flags before adapter construction (O14-b).

    Run the launch instruction literally in two separate processes (the
    session-start hook process, then a later `owner claim` process) surfaced
    a real defect: the session-start line registers `portable/<ws>/<slug>/<pid>`
    (no `--harness`, so `PortableAdapter`'s pid-fallback `local_id` wins),
    but the claim line then resolved a DIFFERENT node -- the claiming
    process's OWN pid (a new process, a new pid), or, on `--harness claude`
    with no `--session-id`, an id the model was never told to supply. Either
    way `owner claim` hit `not_registered` on every default path. The fix is
    at this layer, never in `core/`: an owner verb requires an EXPLICIT,
    cross-process-stable identity, resolved here before `_build_adapter` is
    ever called.

    Only the self path matters (`--node-id` unset) -- the on-behalf path
    (`--node-id` + `--writer-role cto`) names its target directly and has no
    adapter to construct for itself.

    Args:
        args: parsed CLI arguments. For `--harness claude` with no
            `--session-id`, `args.session_id` is filled in from the
            `CLAUDE_CODE_SESSION_ID` environment variable when present --
            the one Claude-Code-exposed carrier of "which session am I" a
            plain Python process can read (see `adapter/claude.py`'s module
            docstring for why nothing else is available).
        verb: `"claim"` or `"stand-down"`, for the printed message only.

    Returns:
        int | None: `_OWNER_EXIT_USAGE_ERROR` (`2`), with one stderr line
        already printed, if a required self-identity flag is missing and
        could not be filled in; `None` if the self-identity flags are usable
        (including the on-behalf path, which has nothing to check here).
    """
    if args.node_id is not None:
        return None  # on-behalf path: no self-identity flags to validate

    if args.harness == "claude":
        if args.session_id and args.session_id.strip():
            return None
        env_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
        if env_session_id:
            args.session_id = env_session_id
            return None
        print(
            f"fleet owner {verb}: --harness claude requires --session-id, or "
            "CLAUDE_CODE_SESSION_ID in the environment -- neither was found",
            file=sys.stderr,
        )
        return _OWNER_EXIT_USAGE_ERROR

    if args.local_id and args.local_id.strip():
        return None
    print(
        f"fleet owner {verb}: --harness {args.harness} requires --local-id -- a bare "
        "process id is not a stable identity across the claim/stand-down pair (it is a "
        "NEW process every invocation)",
        file=sys.stderr,
    )
    return _OWNER_EXIT_USAGE_ERROR


def _resolve_claimant_identity(
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any] | None] | None:
    """Resolve `fleet owner claim`'s claimant node id, plus session facts (O14-a).

    Unlike `_resolve_owner_identity` (shared with `stand-down`, which never
    needs to register anyone), this also returns the claimant's own session
    facts when resolving for self, so `core.project_owner.claim` can
    register the claimant in the SAME locked write if it has no prior
    `session_registered` event, instead of refusing `not_registered` (O14-a).
    The on-behalf (`--node-id`) path has no adapter for the target -- this
    process never learns that node's harness/workspace/local_id -- so it
    returns `(node_id, None)`: the pre-O14 `not_registered` refusal (exit 4)
    still applies there.

    Args:
        args: parsed CLI arguments carrying `node_id`/`writer_role`, plus
            the harness-identity flags when acting for self.

    Returns:
        tuple[str, dict[str, Any] | None] | None: `(node_id, session_facts)`,
        or `None` if resolution failed (the caller maps this the same way
        `_resolve_owner_identity`'s `None` return already is: exit `1` for a
        self-identity failure, exit `4` for `--node-id` without
        `--writer-role cto`).
    """
    if args.node_id is not None:
        if args.writer_role.strip().lower() != "cto":
            print("fleet owner claim: --node-id requires --writer-role cto", file=sys.stderr)
            return None
        return args.node_id, None

    try:
        adapter = _build_adapter(args)
        session = adapter.current_session()
    except SessionsJsonUnusable as exc:
        print(f"fleet owner claim: --sessions-json unusable: {exc}", file=sys.stderr)
        return None
    except (ValueError, SessionIdentityUnresolved) as exc:
        print(f"fleet owner claim: could not resolve identity: {exc}", file=sys.stderr)
        return None

    session_facts = {
        "harness": session.harness,
        "workspace": session.workspace,
        "local_id": session.local_id,
        "branch": session.branch,
    }
    return session.node_id, session_facts


def _find_latest_registration(events: list[Event], project_id: str, node_id: str) -> Event | None:
    """Return the newest `session_registered` event for `node_id`, or `None`.

    Args:
        events: event-log entries, e.g. as read by `core.events.read_all`.
        project_id: the project to search.
        node_id: the registered node to search for.

    Returns:
        Event | None: the last (newest, by log order) matching registration,
        or `None` if `node_id` was never registered in `project_id`.
    """
    match: Event | None = None
    for event in events:
        if event.project_id == project_id and event.type == "session_registered" and event.node_id == node_id:
            match = event
    return match


def _find_session_record_title(sessions: list[dict[str, Any]] | None, local_id: str) -> str | None:
    """Return the `title` of the supplied session record matching `local_id`, if any.

    Args:
        sessions: raw session records from `--sessions-json`, or `None`.
        local_id: the harness-local session id to match (same extraction
            rule as `adapter.session_liveness`/`ClaudeAdapter.enumerate_sessions`).

    Returns:
        str | None: the matching record's `title`, iff EXACTLY ONE record
        matches `local_id` (M5: the same exactly-one-match rule
        `adapter.session_liveness.resolve_liveness` applies) and that
        record has a `title`. Zero matches, two or more matches (ambiguous
        — printing either one's title could name the wrong session), or a
        blank/missing `title` all return `None`.
    """
    if not sessions:
        return None
    matches = [record for record in sessions if extract_claude_session_local_id(record) == local_id]
    if len(matches) != 1:
        return None
    title = matches[0].get("title")
    return str(title) if title else None


def _format_coordination_refusal(
    log_path: Path,
    project_id: str,
    owner_node_id: str,
    sessions: list[dict[str, Any]] | None,
) -> str:
    """Build the exit-3 message naming the current owner (DESIGN O.3).

    Prints the owner's `node_id` (the exact, pastable string for a re-run's
    `--prior-owner-notified`), its harness/local id, and the
    workspace/branch/origination from its registration — written for the
    claiming *agent*, per DESIGN's "the prose seams tell the agent to name
    the session by title, not id, when reporting to a person."

    Args:
        log_path: path to the project's event log (re-read fresh, since the
            refusal's `current_owner` may differ from any owner this call
            resolved liveness against earlier).
        project_id: the owning project's id.
        owner_node_id: the current owner's `node_id`, from `OwnershipRefused.current_owner`.
        sessions: raw session records from `--sessions-json`, or `None`.

    Returns:
        str: a multi-line message, no trailing newline.
    """
    events = read_all(log_path)
    try:
        harness, _workspace, _slug, local_id = parse_node_id(owner_node_id)
    except ValueError:
        harness, local_id = "", ""

    lines = [
        "fleet owner claim: refused - coordination required",
        f"current owner: {owner_node_id}",
    ]
    if harness or local_id:
        lines.append(f"  harness={harness} local_id={local_id}")

    registration = _find_latest_registration(events, project_id, owner_node_id)
    if registration is not None:
        payload = registration.payload
        lines.append(
            f"  workspace={payload.get('workspace')} branch={payload.get('branch')!r} "
            f"origination={payload.get('origination')}"
        )

    if harness == "claude":
        title = _find_session_record_title(sessions, local_id)
        if title:
            lines.append(f"  title={title}")

    lines.append(
        f"re-run adding --prior-owner-notified {owner_node_id} --notified-via <how you reached it>"
    )
    return "\n".join(lines)


def _cmd_owner_claim(args: argparse.Namespace) -> int:
    """Handle `fleet owner claim` (O-FR-1/3/4/5/6, DESIGN O.3/O.4).

    A deliberate act: never routed through the fail-soft `observe` façade
    (O-NFR-1). Every refusal or failure is a stated, non-zero exit, and the
    manifest log is left byte-unchanged on every one of them.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` written, or an already-owner no-op; `1` could not write
            (lock timeout after bounded retry, validation/ownership error,
            re-evaluation budget exhausted, adapter-construction/identity
            failure, an I/O error); `2` a usage error -- `--prior-owner-notified`
            and `--notified-via` were not given together, `--node-id` was
            combined with a self-identity flag, or a required self-identity
            flag is missing (`--harness claude` with no `--session-id`/
            `CLAUDE_CODE_SESSION_ID`, or a non-claude harness with no
            `--local-id`, O14-b); `3` refused,
            coordination required (an active prior owner with no or
            mismatched attestation); `4` refused, state is not what was
            assumed (`--node-id` without `--writer-role cto`, or the
            claimant is not registered in this project); `5` not
            applicable (fleet disabled, an invalid slug, or the project
            identity could not be resolved).
    """
    if (args.prior_owner_notified is None) != (args.notified_via is None):
        print(
            "fleet owner claim: --prior-owner-notified and --notified-via "
            "must be given together",
            file=sys.stderr,
        )
        return 2

    if _node_id_conflicts_with_self_identity(args):
        print(
            "fleet owner claim: --node-id is mutually exclusive with self-identity flags "
            "(--harness/--session-id/--local-id/--session-relay-script)",
            file=sys.stderr,
        )
        return 2

    resolved = _resolve_owner_manifest(args)
    if resolved is None:
        return _OWNER_EXIT_NOT_APPLICABLE
    log_path, project_id = resolved

    identity_flags_exit = _resolve_owner_self_identity_flags(args, verb="claim")
    if identity_flags_exit is not None:
        return identity_flags_exit

    resolved_identity = _resolve_claimant_identity(args)
    if resolved_identity is None:
        return 1 if args.node_id is None else _OWNER_EXIT_STATE_MISMATCH
    claimant, claimant_session = resolved_identity

    sessions: list[dict[str, Any]] | None = None
    if args.sessions_json is not None:
        try:
            sessions = _load_sessions_json(args.sessions_json)
        except SessionsJsonUnusable as exc:
            print(f"fleet owner claim: --sessions-json unusable: {exc}", file=sys.stderr)
            return 1

    try:
        events = read_all(log_path)
    except OSError as exc:
        # M4: bare read_all calls elsewhere crashed uncaught on this; wrap
        # it the same way `owner show` already does.
        print(f"fleet owner claim: could not read manifest: {exc}", file=sys.stderr)
        return 1
    # C1: build liveness for EVERY node registered in this project, not just
    # the one owner this unlocked read happens to see. `claim()` re-resolves
    # the prior owner on every re-evaluation under the lock (DESIGN O.5 step
    # 6); a mapping keyed by node id lets it look up the right node's
    # liveness each time, instead of this call blindly handing it a single
    # value resolved for whatever owner was current a moment ago.
    registered_nodes = {
        event.node_id
        for event in events
        if event.project_id == project_id and event.type == "session_registered"
    }
    liveness_map = {node_id: resolve_liveness(node_id, sessions) for node_id in registered_nodes}
    liveness_source = "sessions-json" if args.sessions_json is not None else "not-supplied"

    try:
        event = _call_with_bounded_lock_retry(
            lambda: owner_claim(
                log_path,
                project_id=project_id,
                claimant=claimant,
                writer_role=args.writer_role,
                liveness=liveness_map,
                liveness_source=liveness_source,
                attested_owner=args.prior_owner_notified,
                notified_via=args.notified_via,
                claimant_session=claimant_session,
            ),
            attempts=args.lock_retry_attempts,
            backoff=_DEFAULT_LOCK_RETRY_BACKOFF,
        )
    except OwnershipRefused as exc:
        if exc.code in ("coordination_required", "attestation_mismatch"):
            try:
                message = _format_coordination_refusal(log_path, project_id, exc.current_owner, sessions)
            except OSError as read_exc:
                # M4: `_format_coordination_refusal` re-reads the manifest
                # (its own bare `read_all`) to enrich the exit-3 message;
                # wrap it the same way `owner show` already does.
                print(f"fleet owner claim: could not read manifest: {read_exc}", file=sys.stderr)
                return 1
            print(message, file=sys.stderr)
            return _OWNER_EXIT_COORDINATION_REQUIRED
        print(f"fleet owner claim: refused ({exc.code}): {exc}", file=sys.stderr)
        return _OWNER_EXIT_STATE_MISMATCH
    except OwnershipContended as exc:
        print(f"fleet owner claim: {exc}", file=sys.stderr)
        return 1
    except (LockTimeoutError, ValidationError, OwnershipError, ValueError, OSError) as exc:
        print(f"fleet owner claim: could not write: {exc}", file=sys.stderr)
        return 1

    if event is None:
        print(f"{claimant} already owns this project")
    else:
        print(f"{claimant} claimed ownership (event_id={event.event_id})")
    return 0


def _cmd_owner_stand_down(args: argparse.Namespace) -> int:
    """Handle `fleet owner stand-down` (O-FR-2, DESIGN O.3).

    A deliberate act, never routed through `observe.py` (O-NFR-1).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` written, or an already-stood-down no-op; `1` could not
            write (lock timeout, validation/ownership error, re-evaluation
            budget exhausted, adapter-construction/identity failure, an I/O
            error); `2` a usage error -- `--node-id` was combined with a
            self-identity flag, or a required self-identity flag is missing
            (O14-b, same rule as `claim`); `4` refused (`--node-id` without
            `--writer-role cto`, or `node` is neither the project's current
            declared owner nor its legacy owner -- O14-c); `5` not
            applicable (fleet disabled, an invalid slug, or the project
            identity could not be resolved).
    """
    if _node_id_conflicts_with_self_identity(args):
        print(
            "fleet owner stand-down: --node-id is mutually exclusive with self-identity flags "
            "(--harness/--session-id/--local-id/--session-relay-script)",
            file=sys.stderr,
        )
        return 2

    resolved = _resolve_owner_manifest(args)
    if resolved is None:
        return _OWNER_EXIT_NOT_APPLICABLE
    log_path, project_id = resolved

    identity_flags_exit = _resolve_owner_self_identity_flags(args, verb="stand-down")
    if identity_flags_exit is not None:
        return identity_flags_exit

    node = _resolve_owner_identity(args, verb="stand-down")
    if node is None:
        return 1 if args.node_id is None else _OWNER_EXIT_STATE_MISMATCH

    try:
        event = _call_with_bounded_lock_retry(
            lambda: owner_stand_down(
                log_path,
                project_id=project_id,
                node=node,
                writer_role=args.writer_role,
                reason=args.reason,
            ),
            attempts=args.lock_retry_attempts,
            backoff=_DEFAULT_LOCK_RETRY_BACKOFF,
        )
    except OwnershipRefused as exc:
        print(f"fleet owner stand-down: refused ({exc.code}): {exc}", file=sys.stderr)
        return _OWNER_EXIT_STATE_MISMATCH
    except OwnershipContended as exc:
        print(f"fleet owner stand-down: {exc}", file=sys.stderr)
        return 1
    except (LockTimeoutError, ValidationError, OwnershipError, ValueError, OSError) as exc:
        print(f"fleet owner stand-down: could not write: {exc}", file=sys.stderr)
        return 1

    if event is None:
        print(f"{node} already stood down")
    else:
        print(f"{node} stood down (event_id={event.event_id})")
    return 0


def _cmd_owner_show(args: argparse.Namespace) -> int:
    """Handle `fleet owner show` (O-NFR-3, DESIGN O.3).

    Read-only: prints the current owner, the basis of the claim that made
    it owner (via the log's own events), and the last few ownership events.

    Args:
        args: parsed CLI arguments.

    Returns:
        int: `0` if readable; `1` on a read failure; `5` not applicable
            (fleet disabled, an invalid slug, or the project identity could
            not be resolved).
    """
    resolved = _resolve_owner_manifest(args)
    if resolved is None:
        return _OWNER_EXIT_NOT_APPLICABLE
    log_path, project_id = resolved

    try:
        events = read_all(log_path)
    except OSError as exc:
        print(f"fleet owner show: could not read manifest: {exc}", file=sys.stderr)
        return 1

    state = owner_fold_owner(events, project_id)

    # I4: the declaring event carries the basis/attestation/prior_owner the
    # current owner's claim was decided on (DESIGN O.3: "`show` ... prints
    # the current owner, the basis and attestation of the claim that made it
    # owner"). `None` when there is no current owner.
    declaring_event = (
        next((e for e in events if e.event_id == state.declaring_event_id), None)
        if state.declaring_event_id is not None
        else None
    )
    basis = declaring_event.payload.get("basis") if declaring_event is not None else None
    prior_owner = declaring_event.payload.get("prior_owner") if declaring_event is not None else None
    attestation = declaring_event.payload.get("attestation") if declaring_event is not None else None

    # O14-c: when the project has no ownership events at all, name the OQ-1
    # legacy owner (if any) too -- the FR-28-style coordination target a
    # claim would need to notify or a stand-down could vacate, even though
    # neither is a real `ownership_declared`/`ownership_stood_down` event
    # yet. `resolve_legacy_owner_unrestricted` excludes no one, matching
    # what "show" (an observer, not a claimant) should report.
    legacy_owner = (
        owner_legacy_owner(events, project_id) if not state.has_ownership_events else None
    )

    if args.json:
        print(
            json.dumps(
                {
                    "owner": state.owner,
                    "has_ownership_events": state.has_ownership_events,
                    "anchor": state.anchor,
                    "anchor_kind": state.anchor_kind,
                    "anchor_node": state.anchor_node,
                    "basis": basis,
                    "prior_owner": prior_owner,
                    "attestation": attestation,
                    "legacy_owner": legacy_owner[0] if legacy_owner is not None else None,
                }
            )
        )
        return 0

    if not state.has_ownership_events:
        if legacy_owner is not None:
            print(
                f"no declared owner (no ownership events); legacy owner: {legacy_owner[0]} "
                "(a prior relayed/manual pm registration -- a claim over this project must "
                "coordinate with it unless it is stood down first)"
            )
        else:
            print("no declared owner (no ownership events)")
        return 0
    if state.owner is None:
        print(f"no declared owner (stood down; last anchor={state.anchor})")
        return 0

    print(f"owner: {state.owner}")
    print(f"  basis: {basis}")
    print(f"  prior_owner: {prior_owner}")
    print(f"  attestation: {attestation}")
    recent = [
        e
        for e in events
        if e.project_id == project_id and e.type in ("ownership_declared", "ownership_stood_down")
    ][-5:]
    for e in recent:
        print(f"  {e.ts}  {e.type}  node={e.node_id}  event_id={e.event_id}")
    return 0


def _add_owner_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `owner claim|stand-down|show` verb group (O-NFR-1, DESIGN O.3).

    A sibling of `register`/`handoff`/`done` — deliberately **not** inside
    the `observe` verb group: these are deliberate acts that fail loudly
    (non-zero exit, a stated reason), never the fail-soft façade's
    always-exits-0 contract.

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    owner_parser = subparsers.add_parser(
        "owner",
        help="Declare, hand over, or inspect a project's owning session. "
        "Deliberate acts: fails loudly, never silently (see `observe` for the fail-soft façade).",
    )
    owner_subparsers = owner_parser.add_subparsers(dest="owner_command", required=True)

    claim_parser = owner_subparsers.add_parser(
        "claim", help="Claim ownership of a project (O-FR-1/3/4/5/6)."
    )
    claim_parser.add_argument("--workspace", required=True, type=Path, help="the project's working tree root")
    claim_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    claim_parser.add_argument(
        "--writer-role", default="pm", help="a role name, never an AI/model/vendor string"
    )
    claim_parser.add_argument(
        "--node-id",
        default=None,
        help="claim on behalf of this node id instead of self (requires --writer-role cto)",
    )
    claim_parser.add_argument(
        "--prior-owner-notified",
        default=None,
        help="the current owner's node id, attesting it was notified (must be given "
        "together with --notified-via)",
    )
    claim_parser.add_argument(
        "--notified-via",
        default=None,
        help="how the prior owner named by --prior-owner-notified was reached "
        "(must be given together with --prior-owner-notified)",
    )
    claim_parser.add_argument(
        "--lock-retry-attempts", type=_positive_int, default=_DEFAULT_LOCK_RETRY_ATTEMPTS
    )
    _add_harness_arguments(
        claim_parser,
        sessions_json_help=(
            "harness session records used ONLY for prior-owner liveness (DESIGN O.4), "
            "for the claimant's own harness or any prior owner's -- not just --harness claude"
        ),
    )
    claim_parser.set_defaults(func=_cmd_owner_claim)

    stand_down_parser = owner_subparsers.add_parser(
        "stand-down", help="Stand down as a project's owner (O-FR-2)."
    )
    stand_down_parser.add_argument("--workspace", required=True, type=Path, help="the project's working tree root")
    stand_down_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    stand_down_parser.add_argument(
        "--writer-role", default="pm", help="a role name, never an AI/model/vendor string"
    )
    stand_down_parser.add_argument(
        "--node-id",
        default=None,
        help="stand down this node id instead of self (requires --writer-role cto)",
    )
    stand_down_parser.add_argument("--reason", default=None, help="optional single-line free-text reason")
    stand_down_parser.add_argument(
        "--lock-retry-attempts", type=_positive_int, default=_DEFAULT_LOCK_RETRY_ATTEMPTS
    )
    _add_harness_arguments(
        stand_down_parser,
        sessions_json_help="unused by stand-down (present only so --harness claude self-identity resolution finds the attribute)",
    )
    stand_down_parser.set_defaults(func=_cmd_owner_stand_down)

    show_parser = owner_subparsers.add_parser(
        "show", help="Show a project's current declared owner (O-NFR-3)."
    )
    show_parser.add_argument("--workspace", required=True, type=Path, help="the project's working tree root")
    show_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    show_parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead")
    show_parser.set_defaults(func=_cmd_owner_show)


def build_parser() -> argparse.ArgumentParser:
    """Build the `fleet` argument parser.

    Returns:
        argparse.ArgumentParser: with `--version`, `--help`, and the
        `register` subcommand wired. Later chunks add further subparsers
        here.
    """
    parser = argparse.ArgumentParser(
        prog="python -m scripts.fleet.cli",
        description="Superhuman session-fleet manifest CLI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {CLI_VERSION}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser(
        "register", help="Register the current or a just-spawned session in the fleet manifest."
    )
    register_parser.add_argument("--project-id", required=True, help="the owning project's id")
    register_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    register_parser.add_argument(
        "--workspace", required=True, type=Path, help="the working tree to register from"
    )
    register_parser.add_argument(
        "--harness",
        choices=("claude", "portable", "subagent"),
        default="portable",
        help="which SessionAdapter implementation to use (default: portable)",
    )
    register_parser.add_argument(
        "--origination",
        choices=("spawned", "relayed", "manual"),
        required=True,
        help="which FR-1 origination path produced this registration",
    )
    register_parser.add_argument(
        "--writer-role", required=True, help="a role name, never an AI/model/vendor string"
    )
    register_parser.add_argument(
        "--target-session-id",
        default=None,
        help="register the session with this id found via enumerate_sessions() "
        "(the spawned path); omit to self-register current_session() (the relayed path)",
    )
    register_parser.add_argument(
        "--session-id",
        default=None,
        help="--harness claude only: this session's native id, supplied by the "
        "orchestrator (see scripts/fleet/adapter/claude.py's module docstring)",
    )
    register_parser.add_argument(
        "--sessions-json",
        type=Path,
        default=None,
        help="--harness claude only: path to a JSON dump from the list_sessions tool, "
        "supplied by the orchestrator",
    )
    register_parser.add_argument(
        "--session-relay-script",
        type=Path,
        default=None,
        help="--harness claude only: path to session-relay's scripts/session_scan.py, "
        "for optional git-fact enrichment of --sessions-json",
    )
    register_parser.add_argument(
        "--local-id",
        default=None,
        help="--harness portable: override the local session id (defaults to the "
        "current process id); --harness subagent: the PM-minted dispatch id (required)",
    )
    register_parser.add_argument(
        "--git-facts-root",
        type=Path,
        default=None,
        help=(
            "--harness claude only: see the identical flag on the `observe` "
            "subcommands (_add_harness_arguments). `register` has no "
            "--hook-payload of its own, so this stays at its default here; "
            "registered anyway so `_build_adapter` never sees a Namespace "
            "missing the attribute regardless of which subcommand built it."
        ),
    )
    register_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    register_parser.add_argument(
        "--lock-retry-attempts",
        type=int,
        default=_DEFAULT_LOCK_RETRY_ATTEMPTS,
        help=f"bounded registrar-level lock-contention retries (default: "
        f"{_DEFAULT_LOCK_RETRY_ATTEMPTS})",
    )
    register_parser.set_defaults(func=_cmd_register)

    _add_handoff_subparsers(subparsers)
    _add_done_subparsers(subparsers)
    _add_query_subparsers(subparsers)
    _add_view_subparsers(subparsers)
    _add_observe_subparsers(subparsers)
    _add_owner_subparsers(subparsers)
    _add_locate_subparser(subparsers)
    _add_doctor_subparser(subparsers)
    _add_project_subparsers(subparsers)
    _add_role_block_subparsers(subparsers)
    _add_hooks_subparsers(subparsers)

    return parser


def _add_locate_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `locate` subcommand (PLAN.md Chunk 2, D1).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    locate_parser = subparsers.add_parser(
        "locate",
        help="Resolve --cwd to its (workspace, slug) superhuman project (D1). "
        "Prints the slug or nothing; always exits 0.",
    )
    locate_parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="the directory to resolve from (default: the process cwd)",
    )
    locate_parser.set_defaults(func=_cmd_locate)


def _add_doctor_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `doctor` subcommand (PLAN.md Chunk 3, D6, FR-10, FR-13).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Read-only estate-health scan: which project records can write right now, "
        "and why not (D6, FR-10, FR-13). Always exits 0.",
    )
    doctor_parser.add_argument(
        "--scan",
        nargs="+",
        required=True,
        type=Path,
        metavar="ROOT",
        help="one or more roots to scan under (each expected to be a git repository "
        "root; shell glob expansion, e.g. ~/dev/*, is expected to already have turned "
        "a wildcard into one argument per repository)",
    )
    doctor_parser.set_defaults(func=_cmd_doctor)


def _add_project_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `project mint|check` subcommands (PLAN.md Chunk 4, D3, FR-11/FR-12).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    project_parser = subparsers.add_parser(
        "project",
        help="Project-id minting and fail-closed validation (D3, FR-11/FR-12).",
    )
    project_subparsers = project_parser.add_subparsers(dest="project_command", required=True)

    mint_parser = project_subparsers.add_parser(
        "mint",
        help="Mint a random 16-hex Project-id if the record lacks one; a no-op "
        "(never a re-mint) if it already has one. Always exits 0.",
    )
    mint_parser.add_argument(
        "--workspace", required=True, type=Path, help="the working tree root"
    )
    mint_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    mint_parser.set_defaults(func=_cmd_project_mint)

    check_parser = project_subparsers.add_parser(
        "check",
        help="Fail-closed assertion (FR-12): exits non-zero when the record has "
        "no **Project-id:**, 0 when it does.",
    )
    check_parser.add_argument(
        "--workspace", required=True, type=Path, help="the working tree root"
    )
    check_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    check_parser.set_defaults(func=_cmd_project_check)


def _add_role_block_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `role-block check` subcommand (chunk 7a, D7.6, FR-20).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    role_block_parser = subparsers.add_parser(
        "role-block",
        help="Chunk 7a's role-block predicate, exposed as a manual/CI-usable "
        "verb (D7.6).",
    )
    role_block_subparsers = role_block_parser.add_subparsers(
        dest="role_block_command", required=True
    )

    check_parser = role_block_subparsers.add_parser(
        "check",
        help="Fail-closed assertion: exits 0 for ROLE/NON_ROLE, 1 otherwise "
        "(MISMATCH/UNMARKED/FAULT). Prints one verdict line.",
    )
    check_parser.add_argument(
        "--prompt-file",
        required=True,
        help="path to the prompt text to check, or '-' for stdin",
    )
    check_parser.add_argument(
        "--roles-dir",
        type=Path,
        default=_SKILL_ROOT / "roles",
        help="the directory holding roles/*.md (default: this skill's own roles/)",
    )
    check_parser.set_defaults(func=_cmd_role_block_check)


def _add_hooks_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `hooks install|uninstall|status` subcommands (Chunk 8, FR-9, A3).

    All logic lives in `scripts/fleet/hooks_install.py`; this is thin verb
    wiring only, per the PM's file-list ruling (PLAN.md Chunk 8).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    hooks_parser = subparsers.add_parser(
        "hooks",
        help="Idempotently register/remove/report superhuman's own harness hook "
        "entries in a settings file (Chunk 8, FR-9, A3).",
    )
    hooks_subparsers = hooks_parser.add_subparsers(dest="hooks_command", required=True)

    install_parser = hooks_subparsers.add_parser(
        "install",
        help="Register every superhuman hook entry, replacing any prior "
        "superhuman-owned entry rather than duplicating it (R5).",
    )
    install_parser.add_argument(
        "--harness",
        required=True,
        choices=("claude-code",),
        help="the target harness; no auto-detection (D4)",
    )
    install_parser.add_argument(
        "--settings-path",
        type=Path,
        default=fleet_hooks_install.DEFAULT_SETTINGS_PATH,
        help="the settings.json to modify (default: ~/.claude/settings.json)",
    )
    install_parser.add_argument(
        "--skill-root",
        type=Path,
        default=None,
        help="explicit operator override for the root registered commands point "
        "at (default: auto-resolve the main checkout via git; refuses if that "
        "resolves inside a linked worktree -- R1)",
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the diff and write nothing",
    )
    install_parser.set_defaults(func=_cmd_hooks_install)

    uninstall_parser = hooks_subparsers.add_parser(
        "uninstall",
        help="Remove every superhuman-owned hook entry, restoring the prior "
        "state exactly.",
    )
    uninstall_parser.add_argument(
        "--harness",
        required=True,
        choices=("claude-code",),
        help="the target harness; no auto-detection (D4)",
    )
    uninstall_parser.add_argument(
        "--settings-path",
        type=Path,
        default=fleet_hooks_install.DEFAULT_SETTINGS_PATH,
        help="the settings.json to modify (default: ~/.claude/settings.json)",
    )
    uninstall_parser.set_defaults(func=_cmd_hooks_uninstall)

    status_parser = hooks_subparsers.add_parser(
        "status",
        help="Report whether each superhuman hook entry is installed.",
    )
    status_parser.add_argument(
        "--harness",
        required=True,
        choices=("claude-code",),
        help="the target harness; no auto-detection (D4)",
    )
    status_parser.add_argument(
        "--settings-path",
        type=Path,
        default=fleet_hooks_install.DEFAULT_SETTINGS_PATH,
        help="the settings.json to inspect (default: ~/.claude/settings.json)",
    )
    status_parser.set_defaults(func=_cmd_hooks_status)


def _add_harness_arguments(
    parser: argparse.ArgumentParser, *, sessions_json_help: str | None = None
) -> None:
    """Attach the shared `--harness`/session-identity arguments `_build_adapter` needs.

    Factored out of `_add_observe_subparsers` since every `observe`
    subcommand needs the identical set (matching `register`'s equivalent
    flags exactly, per `_build_adapter`).

    Args:
        parser: the subcommand parser to attach the arguments to.
        sessions_json_help: override for `--sessions-json`'s help text
            (M2) — every `observe` verb and `register` use `--sessions-json`
            strictly for `--harness claude` self-identity resolution, so the
            default text ("--harness claude only") is correct for them. The
            `owner` verb group's callers pass their own: `claim` uses it for
            prior-owner liveness regardless of the CLAIMANT's harness
            (DESIGN O.3), and `stand-down` never reads it at all (it is
            registered there only so `_build_adapter`'s `--harness claude`
            branch always finds the attribute).
    """
    parser.add_argument(
        "--harness",
        choices=("claude", "portable", "subagent"),
        default="portable",
        help="which SessionAdapter implementation to use (default: portable)",
    )
    parser.add_argument(
        "--session-id", default=None, help="--harness claude only: see `register`'s equivalent flag"
    )
    parser.add_argument(
        "--sessions-json",
        type=Path,
        default=None,
        help=sessions_json_help or "--harness claude only",
    )
    parser.add_argument(
        "--session-relay-script", type=Path, default=None, help="--harness claude only"
    )
    parser.add_argument("--local-id", default=None, help="--harness portable or subagent only (required for subagent)")
    parser.add_argument(
        "--git-facts-root",
        type=Path,
        default=None,
        help=(
            "--harness claude/portable/subagent (chunk 7 fix: all three, not "
            "just claude): directory git_facts() actually queries for "
            "branch/commit state, when it must differ from --workspace "
            "(chunk-6 branch-attribution defect). Not meant to be typed by a "
            "human -- a --hook-payload consumer sets this programmatically "
            "on `args` before adapter construction, threading through "
            "whichever of --anchor/payload.cwd actually resolved the "
            "project (the session's own working tree, BEFORE D1's outward "
            "hop). Registered here, not left as an ad-hoc Namespace "
            "attribute, so every future --hook-payload verb inherits a "
            "visible default=None rather than a getattr fallback nothing "
            "signals the existence of. Any new payload-consuming verb MUST "
            "set this explicitly or it silently inherits the stale-"
            "workspace default; and `_build_adapter` MUST forward it to "
            "whichever adapter class `--harness` selects, not only "
            "ClaudeAdapter (the chunk-7 defect this comment now documents) "
            "-- see test_every_hook_payload_verb_threads_git_facts_root and "
            "test_every_hook_payload_verb_and_harness_builds_an_adapter_"
            "that_uses_git_facts_root in tests/fleet/test_observe_session.py, "
            "which fail red for either omission."
        ),
    )


def _add_observe_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `observe dispatch|relay|handoff-emit|launch|status` verb group.

    The fail-soft observation façade's CLI surface (fleet-wiring Chunk 1,
    Decision A/B) — every subcommand always exits `0` and calls straight
    into `observe.py`, which never raises. This is the boundary every
    origination seam (spawned dispatch, relay, manual handoff emit/launch)
    is meant to invoke, whether triggered by portable prose or an optional
    operator-installed hook (both call the identical entry point).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    observe_parser = subparsers.add_parser(
        "observe",
        help="Fail-soft observation façade: dispatch, relay, handoff-emit, launch, status. "
        "Always exits 0.",
    )
    observe_subparsers = observe_parser.add_subparsers(dest="observe_command", required=True)

    dispatch_parser = observe_subparsers.add_parser(
        "dispatch", help="Observe a spawned role dispatch (W-FR-1). Always exits 0."
    )
    dispatch_parser.add_argument(
        "--workspace", type=Path, default=None, help="required unless --hook-payload resolves it"
    )
    dispatch_parser.add_argument(
        "--slug", default=None, help="the superhuman project slug (see --workspace)"
    )
    dispatch_parser.add_argument(
        "--dispatch-id",
        default=None,
        help="the PM-minted id identifying the dispatch unit; required unless "
        "--hook-payload resolves it from the payload's agent_id (chunk 7, FR-7)",
    )
    dispatch_parser.add_argument(
        "--hook-payload",
        default=None,
        help="read a harness hook JSON payload from this file, or '-' for stdin (D2b); "
        "derives --workspace/--slug via the locator from the payload's cwd, and uses the "
        "payload's agent_id as --dispatch-id/--local-id (chunk 7, FR-7/FR-17)",
    )
    dispatch_parser.add_argument(
        "--anchor",
        default=None,
        help="with --hook-payload: try locating the project from THIS directory first, "
        "falling back to the payload's cwd only if it does not resolve (ARCHITECTURE.md "
        "Addendum §A) — a harness-supplied starting point (e.g. $CLAUDE_PROJECT_DIR), "
        "never a repo-authored one; omitting this flag leaves behavior unchanged",
    )
    dispatch_parser.add_argument(
        "--writer-role", default="pm", help="a role name, never an AI/model/vendor string"
    )
    _add_harness_arguments(dispatch_parser)
    dispatch_parser.set_defaults(func=_cmd_observe_dispatch)

    relay_parser = observe_subparsers.add_parser(
        "relay", help="Observe a session-relay handoff (W-FR-2). Always exits 0."
    )
    relay_parser.add_argument("--workspace", required=True, type=Path)
    relay_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    relay_parser.add_argument(
        "--writer-role", default="pm", help="a role name, never an AI/model/vendor string"
    )
    _add_harness_arguments(relay_parser)
    relay_parser.set_defaults(func=_cmd_observe_relay)

    handoff_emit_parser = observe_subparsers.add_parser(
        "handoff-emit",
        help="Observe a manual-handoff emission; always delivers the prompt (W-FR-3). "
        "Always exits 0.",
    )
    handoff_emit_parser.add_argument("--workspace", required=True, type=Path)
    handoff_emit_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    handoff_emit_parser.add_argument(
        "--prompt-file", required=True, type=Path, help="path to the draft prompt body"
    )
    handoff_emit_parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="write the deliverable prompt here instead of stdout",
    )
    handoff_emit_parser.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help="the target working directory for the launched session (defaults to --workspace)",
    )
    handoff_emit_parser.add_argument("--branch", default=None, help="the target git branch")
    handoff_emit_parser.add_argument(
        "--writer-role", default="pm", help="a role name, never an AI/model/vendor string"
    )
    _add_harness_arguments(handoff_emit_parser)
    handoff_emit_parser.set_defaults(func=_cmd_observe_handoff_emit)

    launch_parser = observe_subparsers.add_parser(
        "launch", help="Observe a handoff launch flip (W-FR-4). Always exits 0."
    )
    launch_parser.add_argument("--workspace", required=True, type=Path)
    launch_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    launch_parser.add_argument(
        "--handoff-id", default=None, help="the id recovered from this session's own prompt"
    )
    launch_parser.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="grep this file's FLEET-HANDOFF-ID line when --handoff-id is not given directly",
    )
    launch_parser.add_argument("--cwd", type=Path, default=None, help="override the fuzzy cwd anchor")
    launch_parser.add_argument("--branch", default=None, help="override the fuzzy branch anchor")
    launch_parser.add_argument(
        "--writer-role", default="pm", help="a role name, never an AI/model/vendor string"
    )
    _add_harness_arguments(launch_parser)
    launch_parser.set_defaults(func=_cmd_observe_launch)

    session_start_parser = observe_subparsers.add_parser(
        "session-start",
        help="Observe a plain session start with no pending handoff (FR-5, D2a). "
        "Always exits 0.",
    )
    session_start_parser.add_argument(
        "--workspace", type=Path, default=None, help="required unless --hook-payload resolves it"
    )
    session_start_parser.add_argument(
        "--slug", default=None, help="the superhuman project slug (see --workspace)"
    )
    session_start_parser.add_argument(
        "--hook-payload",
        default=None,
        help="read a harness hook JSON payload from this file, or '-' for stdin (D2b); "
        "derives --workspace/--slug via the locator from the payload's cwd, and threads "
        "the harness's own session id through (FR-17)",
    )
    session_start_parser.add_argument(
        "--anchor",
        default=None,
        help="with --hook-payload: try locating the project from THIS directory first, "
        "falling back to the payload's cwd only if it does not resolve (ARCHITECTURE.md "
        "Addendum §A) — a harness-supplied starting point (e.g. $CLAUDE_PROJECT_DIR), "
        "never a repo-authored one; omitting this flag leaves behavior unchanged",
    )
    session_start_parser.add_argument(
        "--handoff-id",
        default=None,
        help="an explicit, id-anchored handoff to attempt flipping first "
        "(D2a: never a fuzzy cwd/branch match)",
    )
    session_start_parser.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="grep this file's FLEET-HANDOFF-ID line when --handoff-id is not given directly",
    )
    session_start_parser.add_argument(
        "--writer-role", default="pm", help="a role name, never an AI/model/vendor string"
    )
    _add_harness_arguments(session_start_parser)
    session_start_parser.set_defaults(func=_cmd_observe_session_start)

    status_parser = observe_subparsers.add_parser(
        "status", help="Report enablement/activity for a workspace (W-FR-8). Always exits 0."
    )
    status_parser.add_argument("--workspace", required=True, type=Path)
    status_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    status_parser.set_defaults(func=_cmd_observe_status)


def _add_handoff_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `handoff emit|cancel|stale` subcommands (PLAN.md Chunk 3).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    handoff_parser = subparsers.add_parser(
        "handoff", help="Manual-handoff intent row: emit, cancel, stale report."
    )
    handoff_subparsers = handoff_parser.add_subparsers(dest="handoff_command", required=True)

    emit_parser = handoff_subparsers.add_parser(
        "emit", help="Write an awaiting-launch intent row and embed a durable handoff id."
    )
    emit_parser.add_argument("--project-id", required=True, help="the owning project's id")
    emit_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    emit_parser.add_argument(
        "--workspace", required=True, type=Path, help="the working tree to emit the handoff from"
    )
    emit_parser.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help="the target working directory for the launched session "
        "(defaults to --workspace) — the fuzzy-match anchor",
    )
    emit_parser.add_argument(
        "--branch", default=None, help="the target git branch — the other fuzzy-match anchor"
    )
    emit_parser.add_argument(
        "--prompt-file", required=True, type=Path, help="path to the prompt body to emit"
    )
    emit_parser.add_argument(
        "--writer-role", required=True, help="a role name, never an AI/model/vendor string"
    )
    emit_parser.add_argument(
        "--harness",
        choices=("claude", "portable", "subagent"),
        default="portable",
        help="which SessionAdapter implementation formats the emitted prompt "
        "(default: portable; both adapters embed the id identically)",
    )
    emit_parser.add_argument(
        "--session-id", default=None, help="--harness claude only: see `register`'s equivalent flag"
    )
    emit_parser.add_argument(
        "--sessions-json", type=Path, default=None, help="--harness claude only"
    )
    emit_parser.add_argument(
        "--session-relay-script", type=Path, default=None, help="--harness claude only"
    )
    emit_parser.add_argument("--local-id", default=None, help="--harness portable or subagent only (required for subagent)")
    emit_parser.add_argument(
        "--git-facts-root",
        type=Path,
        default=None,
        help=(
            "see the identical flag on `register` (_build_adapter needs "
            "every one of its callers' parsers to register this, chunk 7 "
            "fix — `emit` has no --hook-payload of its own, so this stays "
            "at its default here; registered anyway so `_build_adapter` "
            "never sees a Namespace missing the attribute regardless of "
            "which subcommand built it)."
        ),
    )
    emit_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    emit_parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="write the emitted prompt here instead of stdout",
    )
    emit_parser.add_argument(
        "--lock-retry-attempts", type=int, default=_DEFAULT_LOCK_RETRY_ATTEMPTS
    )
    emit_parser.set_defaults(func=_cmd_handoff_emit)

    cancel_parser = handoff_subparsers.add_parser(
        "cancel", help="Close an open awaiting-launch handoff row."
    )
    cancel_parser.add_argument("--node-id", required=True, help="the handoff row's node id")
    cancel_parser.add_argument("--project-id", required=True, help="the owning project's id")
    cancel_parser.add_argument(
        "--writer-role", required=True, help="a role name, never an AI/model/vendor string"
    )
    cancel_parser.add_argument("--workspace", required=True, type=Path)
    cancel_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    cancel_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    cancel_parser.add_argument(
        "--lock-retry-attempts", type=int, default=_DEFAULT_LOCK_RETRY_ATTEMPTS
    )
    cancel_parser.set_defaults(func=_cmd_handoff_cancel)

    stale_parser = handoff_subparsers.add_parser(
        "stale", help="List awaiting-launch handoff rows past expiry."
    )
    stale_parser.add_argument("--workspace", required=True, type=Path)
    stale_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    stale_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    stale_parser.add_argument(
        "--expiry-seconds",
        type=float,
        default=None,
        help="override the staleness threshold (defaults to the profile-driven "
        "value from ~/.superhuman/profile.yaml, NFR-5)",
    )
    stale_parser.set_defaults(func=_cmd_handoff_stale)

    self_register_parser = handoff_subparsers.add_parser(
        "self-register",
        help="Flip an awaiting-launch handoff row to active "
        "(the launched session's own first action).",
    )
    self_register_parser.add_argument("--workspace", required=True, type=Path)
    self_register_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    self_register_parser.add_argument(
        "--handoff-id",
        default=None,
        help="the id recovered from this session's own prompt (primary anchor, "
        "Decision E); omit to use --prompt-file or the fuzzy (cwd, branch) fallback",
    )
    self_register_parser.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="grep this file's FLEET-HANDOFF-ID line for the id, when --handoff-id "
        "is not given directly",
    )
    self_register_parser.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help="override the fuzzy-match cwd anchor "
        "(defaults to the adapter's git toplevel, or --workspace)",
    )
    self_register_parser.add_argument(
        "--branch",
        default=None,
        help="override the fuzzy-match branch anchor (defaults to the adapter's "
        "current branch)",
    )
    self_register_parser.add_argument(
        "--writer-role", required=True, help="a role name, never an AI/model/vendor string"
    )
    self_register_parser.add_argument(
        "--harness",
        choices=("claude", "portable", "subagent"),
        default="portable",
        help="which SessionAdapter implementation derives cwd/branch for the "
        "fuzzy fallback (default: portable; ignored when --handoff-id resolves)",
    )
    self_register_parser.add_argument(
        "--session-id", default=None, help="--harness claude only: see `register`'s equivalent flag"
    )
    self_register_parser.add_argument(
        "--sessions-json", type=Path, default=None, help="--harness claude only"
    )
    self_register_parser.add_argument(
        "--session-relay-script", type=Path, default=None, help="--harness claude only"
    )
    self_register_parser.add_argument("--local-id", default=None, help="--harness portable or subagent only (required for subagent)")
    self_register_parser.add_argument(
        "--git-facts-root",
        type=Path,
        default=None,
        help=(
            "see the identical flag on `register` (_build_adapter needs "
            "every one of its callers' parsers to register this, chunk 7 "
            "fix — `self-register` has no --hook-payload of its own, so "
            "this stays at its default here; registered anyway so "
            "`_build_adapter` never sees a Namespace missing the attribute "
            "regardless of which subcommand built it)."
        ),
    )
    self_register_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    self_register_parser.add_argument(
        "--lock-retry-attempts", type=int, default=_DEFAULT_LOCK_RETRY_ATTEMPTS
    )
    self_register_parser.set_defaults(func=_cmd_handoff_self_register)


def _add_done_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `done advance` subcommand (PLAN.md Chunk 5, FR-6).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    done_parser = subparsers.add_parser(
        "done", help="Evidence-backed done_level state machine (D0-code..D4-prod)."
    )
    done_subparsers = done_parser.add_subparsers(dest="done_command", required=True)

    advance_parser = done_subparsers.add_parser(
        "advance", help="Advance a node's done_level by exactly one rung (FR-6)."
    )
    advance_parser.add_argument("--node-id", required=True, help="the node id to advance")
    advance_parser.add_argument(
        "--target-level",
        required=True,
        choices=DONE_LEVELS,
        help="the done_level to advance to (must be exactly one rung above current)",
    )
    advance_parser.add_argument("--project-id", required=True, help="the owning project's id")
    advance_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    advance_parser.add_argument(
        "--workspace", required=True, type=Path, help="the working tree to advance from"
    )
    advance_parser.add_argument(
        "--writer-role", required=True, help="a role name, never an AI/model/vendor string"
    )
    advance_parser.add_argument(
        "--evidence-json",
        type=Path,
        default=None,
        help="path to a JSON file of evidence fields (commit, pr, deploy_id, ci_run, env, ...); "
        "omit for no evidence",
    )
    advance_parser.add_argument(
        "--approver",
        default=None,
        help="the recorded approver's identity — required (and must not be "
        "model/vendor-shaped) for --target-level D3-uat or D4-prod",
    )
    advance_parser.add_argument(
        "--ceiling",
        default=None,
        choices=DONE_LEVELS,
        help="override the project's D-ceiling instead of resolving it from the "
        "operator's deployment profile (see _resolve_d_ceiling / "
        "scripts/superhuman_profile.py)",
    )
    advance_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    advance_parser.set_defaults(func=_cmd_done_advance)


def _add_query_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `query edges` subcommand (PLAN.md Chunk 4).

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    query_parser = subparsers.add_parser("query", help="Read-side queries over the manifest.")
    query_subparsers = query_parser.add_subparsers(dest="query_command", required=True)

    edges_parser = query_subparsers.add_parser(
        "edges", help="List dependency edges, optionally filtered to one node."
    )
    edges_parser.add_argument("--workspace", required=True, type=Path)
    edges_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    edges_parser.add_argument(
        "--node", default=None, help="only show edges touching this node id"
    )
    edges_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    edges_parser.set_defaults(func=_cmd_query_edges)


def _add_view_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `status` and `gen-view` subcommands (PLAN.md Chunk 6, FR-7/CC-7).

    Both are top-level subcommands (not nested under a group, unlike
    `handoff`/`done`/`query`) — each is a single read-only action, matching
    DESIGN's component table naming `status`/`gen-view` directly.

    Args:
        subparsers: the top-level `fleet` subparsers action to attach to.
    """
    status_parser = subparsers.add_parser(
        "status", help="Print every tracked session's status fields + dependency edges."
    )
    status_parser.add_argument("--workspace", required=True, type=Path)
    status_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    status_parser.add_argument(
        "--project-id",
        default=None,
        help="only show sessions with this exact project_id (default: all)",
    )
    status_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    status_parser.set_defaults(func=_cmd_status)

    gen_view_parser = subparsers.add_parser(
        "gen-view",
        help="Write/refresh docs/superhuman/<slug>/FLEET.md (DESIGN Decision A).",
    )
    gen_view_parser.add_argument("--workspace", required=True, type=Path)
    gen_view_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    gen_view_parser.add_argument(
        "--project-id",
        default=None,
        help="only include sessions with this exact project_id (default: all)",
    )
    gen_view_parser.add_argument(
        "--fleet-dir",
        type=Path,
        default=None,
        help="override the fleet manifest directory "
        "(defaults to <workspace>/docs/superhuman/<slug>/fleet)",
    )
    gen_view_parser.set_defaults(func=_cmd_gen_view)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: command-line arguments, defaulting to `sys.argv[1:]`.

    Returns:
        int: the selected subcommand's exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
