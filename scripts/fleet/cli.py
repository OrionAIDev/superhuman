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
from . import observe as fleet_observe
from .adapter.base import SessionAdapter, SessionInfo
from .adapter.claude import ClaudeAdapter
from .adapter.portable import PortableAdapter
from .core.done import DONE_LEVELS
from .core.done import advance as done_advance
from .core.done import event_for as done_event_for
from .core.edges import resolve_graph
from .core.errors import (
    DonePolicyError,
    FragmentCorrupt,
    LockTimeoutError,
    OwnershipError,
    SessionIdentityUnresolved,
    ValidationError,
)
from .core.events import append
from .core.projection import project_event, rebuild
from .core.query import edges_of
from .core.schema import Event, Fragment, validate_event
from .core.store import read_fragment
from .handoff import cancel as handoff_cancel
from .handoff import emit as handoff_emit
from .handoff import extract_handoff_id
from .handoff import self_register as handoff_self_register
from .handoff import stale_report
from .view import render_status_table, write_fleet_md

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


def _cmd_observe_dispatch(args: argparse.Namespace) -> int:
    """Handle `fleet observe dispatch` (fleet-wiring Chunk 1, W-FR-1).

    Fail-soft wrapper over `observe.observe_dispatch` — see `observe.py`'s
    module docstring for the fail-soft/fail-closed boundary this crosses.
    Prints nothing on the normal path (DESIGN's Loudness tiers: `observe`
    subcommands other than `handoff-emit`/`status` carry no stdout payload).

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` — `observe.py` never raises and never signals
        failure through the exit code (Decision A).
    """
    adapter = _build_adapter(args)
    fleet_observe.observe_dispatch(
        adapter,
        workspace=args.workspace,
        slug=args.slug,
        dispatch_id=args.dispatch_id,
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
    adapter = _build_adapter(args)
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

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0`.
    """
    adapter = _build_adapter(args)
    prompt_text = args.prompt_file.read_text(encoding="utf-8")
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

    Args:
        args: parsed CLI arguments.

    Returns:
        int: always `0` (see `_cmd_observe_dispatch`).
    """
    adapter = _build_adapter(args)
    prompt_text = (
        args.prompt_file.read_text(encoding="utf-8") if args.prompt_file is not None else None
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
    _add_view_subparsers(subparsers)
    _add_observe_subparsers(subparsers)

    return parser


def _add_harness_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the shared `--harness`/session-identity arguments `_build_adapter` needs.

    Factored out of `_add_observe_subparsers` since every `observe`
    subcommand needs the identical set (matching `register`'s equivalent
    flags exactly, per `_build_adapter`).

    Args:
        parser: the subcommand parser to attach the arguments to.
    """
    parser.add_argument(
        "--harness",
        choices=("claude", "portable"),
        default="portable",
        help="which SessionAdapter implementation to use (default: portable)",
    )
    parser.add_argument(
        "--session-id", default=None, help="--harness claude only: see `register`'s equivalent flag"
    )
    parser.add_argument("--sessions-json", type=Path, default=None, help="--harness claude only")
    parser.add_argument(
        "--session-relay-script", type=Path, default=None, help="--harness claude only"
    )
    parser.add_argument("--local-id", default=None, help="--harness portable only")


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
    dispatch_parser.add_argument("--workspace", required=True, type=Path)
    dispatch_parser.add_argument("--slug", required=True, help="the superhuman project slug")
    dispatch_parser.add_argument(
        "--dispatch-id", required=True, help="the PM-minted id identifying the dispatch unit"
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
