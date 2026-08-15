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

from .. import superhuman_profile
from .adapter.base import SessionAdapter, SessionInfo
from .adapter.claude import ClaudeAdapter
from .adapter.portable import PortableAdapter
from .core.done import DONE_LEVELS
from .core.done import advance as done_advance
from .core.done import event_for as done_event_for
from .core.edges import resolve_graph
from .core.errors import DonePolicyError, LockTimeoutError, OwnershipError, ValidationError
from .core.events import append
from .core.projection import project_event
from .core.query import edges_of
from .core.schema import Event, Fragment, validate_event
from .core.store import read_fragment
from .handoff import cancel as handoff_cancel
from .handoff import emit as handoff_emit
from .handoff import extract_handoff_id
from .handoff import self_register as handoff_self_register
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
    if handoff_id is None and (cwd is None or branch is None):
        # Only the fuzzy path needs cwd/branch at all — never touch the
        # adapter (or its git subprocess calls) when an id was recovered.
        adapter = _build_adapter(args)
        facts = adapter.git_facts()
        if cwd is None:
            cwd = facts.toplevel or args.workspace
        if branch is None:
            branch = facts.branch

    try:
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

    Returns:
        str: the resolved D-ceiling, one of `core.done.DONE_LEVELS`.
        `_DEFAULT_D_CEILING` if no profile is found, no rung matches the
        workspace, the matched rung declares no `d_ceiling` label, or the
        profile itself fails to load (a malformed profile should not by
        itself block every `done advance` call — it already fails loudly
        for the deployment-rung concerns `superhuman_profile`'s own CLI
        commands cover).
    """
    try:
        profile = superhuman_profile.load_profile(superhuman_profile.find_profile(workspace))
        resolution = superhuman_profile.resolve(workspace, profile)
    except superhuman_profile.ProfileError:
        return _DEFAULT_D_CEILING
    if resolution.stage is None:
        return _DEFAULT_D_CEILING
    ceiling = resolution.stage.labels.get(_D_CEILING_LABEL_KEY)
    return ceiling if ceiling in DONE_LEVELS else _DEFAULT_D_CEILING


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

    ceiling = args.ceiling or _resolve_d_ceiling(args.workspace)

    try:
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
        project_event(event, sessions_dir)

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
    _add_done_subparsers(subparsers)
    _add_query_subparsers(subparsers)

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
        choices=("claude", "portable"),
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
    self_register_parser.add_argument("--local-id", default=None, help="--harness portable only")
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
