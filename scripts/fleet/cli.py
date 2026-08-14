"""``fleet`` CLI entry point (`argparse`, per `conventions/python.md`).

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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .adapter.base import SessionAdapter, SessionInfo
from .adapter.claude import ClaudeAdapter
from .adapter.portable import PortableAdapter
from .core.errors import LockTimeoutError, OwnershipError, ValidationError
from .core.events import append
from .core.projection import project_event
from .core.schema import Event, Fragment, validate_event
from .core.store import read_fragment
from .handoff import cancel as handoff_cancel
from .handoff import emit as handoff_emit
from .handoff import stale_report

#: `fleet --version` output. Not tied to `VERSION` at the skill root — this
#: is the manifest CLI's own schema-facing version, matching `schema_version`
#: in `core/schema.py` (both are v1 for Phase 1).
CLI_VERSION = "0.1.0 (schema v1)"

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

    Returns:
        Event | None: as `core.events.append`.

    Raises:
        LockTimeoutError: if every attempt timed out. The caller must treat
            this exactly like a single `append` timeout — nothing was
            written.
    """
    last_exc: LockTimeoutError | None = None
    for attempt in range(attempts):
        try:
            return append(log_path, event_dict)
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

    Returns:
        Fragment: the session's fragment after the registration is applied.
        On a repeat registration of the same session (idempotency-key
        dedupe), this is the *existing* fragment, read back rather than
        re-projected — the original event is the one of record.

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
        log_path, event_dict, attempts=lock_retry_attempts, backoff=lock_retry_backoff
    )

    if appended is None:
        # Dedupe no-op: an event with this idempotency_key already exists.
        # The event of record is whatever was appended first; re-projecting
        # this call's (possibly stale) payload on top would be wrong, so the
        # existing fragment is read back instead.
        existing = read_fragment(session.node_id, sessions_dir)
        if existing is not None:
            return existing
        # Fragment missing but the log entry exists (e.g. a corrupt/deleted
        # fragment) — re-validate and project the built event to rebuild it.
        appended = validate_event(event_dict)

    return project_event(appended, sessions_dir)


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


def _build_adapter(args: argparse.Namespace) -> SessionAdapter:
    """Construct the `SessionAdapter` selected by `args.harness`.

    Args:
        args: parsed CLI arguments for the `register` subcommand.

    Returns:
        SessionAdapter: a `ClaudeAdapter` or `PortableAdapter`, per
        `args.harness`.
    """
    if args.harness == "claude":
        sessions = None
        if args.sessions_json is not None:
            sessions = json.loads(args.sessions_json.read_text(encoding="utf-8"))
        return ClaudeAdapter(
            args.workspace,
            args.slug,
            current_session_id=args.session_id,
            sessions=sessions,
            session_relay_script=args.session_relay_script,
        )
    return PortableAdapter(args.workspace, args.slug, local_id=args.local_id)


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

    adapter = _build_adapter(args)

    try:
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
    adapter = _build_adapter(args)
    cwd = args.cwd or args.workspace
    prompt_text = args.prompt_file.read_text(encoding="utf-8")

    try:
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
    except (ValidationError, OwnershipError) as exc:
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
    except (ValidationError, OwnershipError) as exc:
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


def build_parser() -> argparse.ArgumentParser:
    """Build the `fleet` argument parser.

    Returns:
        argparse.ArgumentParser: with `--version`, `--help`, and the
        `register` subcommand wired. Later chunks add further subparsers
        here.
    """
    parser = argparse.ArgumentParser(
        prog="fleet", description="Superhuman session-fleet manifest CLI."
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
        choices=("claude", "portable"),
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
        help="--harness portable only: override the local session id "
        "(defaults to the current process id)",
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

    return parser


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
        choices=("claude", "portable"),
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
    emit_parser.add_argument("--local-id", default=None, help="--harness portable only")
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
