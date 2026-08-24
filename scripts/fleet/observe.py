"""The fail-soft observation façade — the sole boundary superhuman's normal
operation writes through to record what it did (Decision A, W-NFR-1).

**Why this module is the only one in the package permitted to catch broadly
(`except Exception`).** Everything above this façade may be prose-mediated
and unreliable; everything below it — `cli.register_session`, `handoff.py`,
`core/*` — is Phase 1's existing, unchanged, fail-closed machinery, and its
rejections (`ValidationError`, `OwnershipError`, a policy-refused write) are
*correct* and must never be silently discarded. This façade's job is
different: record that an *observation* was attempted, and never let a
failure to observe change the result of the operation being observed
(W-NFR-1's omission-vs-commission asymmetry — see `DESIGN.md` Decision A).
`fleet observe` must always exit 0, so an unanticipated exception escaping
this module would violate its own contract worse than a broad-but-documented
catch would. Every other module in this package keeps Phase 1's narrow,
named-exception discipline unchanged; this is the single, deliberate
exception, and it is documented at every site it fires.

**Loudness (DESIGN's three tiers).** (1) A JSON line per failure, appended
to `<fleet_dir>/observe-failures.log` — a diagnostic side-file, not the
manifest, not lock-guarded, bounded to a tail. (2) Exactly one stderr line
if the journal write itself fails. (3) Never stdout, never a non-zero exit
— this module's own stdout, if any, is left entirely to its CLI callers in
`cli.py`.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from . import config as fleet_config
from . import project as fleet_project
from .adapter.base import SessionAdapter
from .core.errors import LockTimeoutError, OwnershipError, SessionIdentityUnresolved, ValidationError
from .handoff import emit as handoff_emit_impl
from .handoff import extract_handoff_id
from .handoff import self_register as handoff_self_register_impl

_T = TypeVar("_T")

#: Registrar-level retry defaults for the façade's own internal write calls
#: — matches `cli.py`'s / `handoff.py`'s existing defaults exactly; only the
#: per-attempt `timeout` is smaller here (see `config.FleetConfig`).
_LOCK_RETRY_ATTEMPTS = 3
_LOCK_RETRY_BACKOFF = 0.1

#: Bounded tail kept in the failure journal (a rolling window, not an
#: ordered archive — old lines are dropped from the front).
_JOURNAL_MAX_LINES = 500

_JOURNAL_FILENAME = "observe-failures.log"


def _default_fleet_dir(workspace: Path | str, slug: str) -> Path:
    """Return the default per-project fleet manifest directory.

    Deliberately duplicated from `cli.py`'s private helper of the same name
    (not imported): `cli.py` imports this module to wire the `observe` verb
    group, so this module must not import `cli.py` at module-load time (it
    does import `cli.register_session` lazily, inside function bodies, for
    exactly this reason — see `_observe_register`).

    Args:
        workspace: the project's working tree root.
        slug: the superhuman project slug.

    Returns:
        Path: `<workspace>/docs/superhuman/<slug>/fleet`.
    """
    return Path(workspace) / "docs" / "superhuman" / slug / "fleet"


def _journal_path(fleet_dir: Path) -> Path:
    """Return the failure-journal path for a fleet manifest directory.

    Args:
        fleet_dir: the project's fleet manifest directory.

    Returns:
        Path: `<fleet_dir>/observe-failures.log`.
    """
    return fleet_dir / _JOURNAL_FILENAME


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class ObserveResult:
    """The outcome of one `observe_*` call — never an exception (W-NFR-1).

    Attributes:
        ok: whether the underlying observation write succeeded.
        disabled: whether fleet observation is disabled/unconfigured for
            this workspace — a distinct, non-error outcome (W-FR-7). When
            `True`, nothing was written and no journal entry exists.
        reason: a short, human-readable explanation, always present.
        node_id: the registered/flipped/emitted node id, when known.
        prompt_text: for `observe_handoff_emit` only — the deliverable
            prompt text (the emitted prompt on success, the untouched draft
            on any failure or when disabled). `None` for every other verb.
    """

    ok: bool
    disabled: bool = False
    reason: str = ""
    node_id: str | None = None
    prompt_text: str | None = None


class _Disabled(Exception):
    """Internal sentinel: fleet is disabled/unconfigured for this workspace.

    Caller must return before any I/O and without a journal entry (W-FR-7).
    """

    def __init__(self, reason: str) -> None:
        """Store the disabled reason for the caller to surface unchanged.

        Args:
            reason: `FleetConfig.reason` — a human-readable explanation of
                why fleet observation is disabled/unconfigured (W-FR-8).
        """
        super().__init__(reason)
        self.reason = reason


class _IdentityUnresolved(Exception):
    """Internal sentinel: `SUPERHUMAN.md` identity could not be resolved.

    Unlike `_Disabled`, fleet IS enabled here, so the caller journals
    `identity_unresolved` before returning (W-FR-6 — never invents an id).
    """


class _DeadlineExceeded(Exception):
    """Internal sentinel: the wall-clock observe budget expired (W-NFR-7)."""


class _TimedOut(Exception):
    """Internal sentinel: a bounded-thread stage did not return in time.

    Raised by `_run_bounded` — distinct from `_DeadlineExceeded` (which
    fires on a *pre-stage* check) so both share one `_classify` outcome
    (`"deadline_exceeded"`) without conflating "we never started the stage"
    and "the stage itself ran past its bound."
    """


@dataclass(frozen=True, slots=True)
class _ObserveContext:
    """Resolved config + identity + start time for one `observe_*` call.

    Attributes:
        cfg: the resolved, enabled `FleetConfig`.
        fleet_dir: the manifest directory to write through.
        project_id: the owning project's stable id, carried from
            `SUPERHUMAN.md` (W-FR-6).
        file_slug: the slug as recorded in `SUPERHUMAN.md` (may differ from
            the caller's `slug` argument after a rename — carried, not
            re-derived).
        start: `time.monotonic()` at context-resolution time; the anchor
            every deadline check measures against.
    """

    cfg: fleet_config.FleetConfig
    fleet_dir: Path
    project_id: str
    file_slug: str
    start: float


def _resolve_context(workspace: Path | str, slug: str) -> _ObserveContext:
    """Resolve config + identity for one observe call, or raise a sentinel.

    Args:
        workspace: the project's working tree root.
        slug: the superhuman project slug.

    Returns:
        _ObserveContext: ready for a bounded write attempt.

    Raises:
        _Disabled: fleet is disabled/unconfigured (W-FR-7).
        _IdentityUnresolved: `SUPERHUMAN.md` identity could not be read.
    """
    cfg = fleet_config.resolve_fleet_config(workspace)
    if not cfg.enabled:
        raise _Disabled(cfg.reason)

    fleet_dir = cfg.manifest_dir or _default_fleet_dir(workspace, slug)
    identity = fleet_project.read_project_identity(workspace, slug)
    if identity is None:
        raise _IdentityUnresolved(
            f"could not read Project-id/Slug from {workspace}'s SUPERHUMAN.md "
            f"for slug {slug!r}"
        )
    project_id, file_slug = identity
    return _ObserveContext(
        cfg=cfg, fleet_dir=fleet_dir, project_id=project_id, file_slug=file_slug,
        start=time.monotonic(),
    )


def _elapsed_ms(ctx: _ObserveContext) -> float:
    """Return milliseconds elapsed since `ctx.start`."""
    return (time.monotonic() - ctx.start) * 1000.0


def _remaining(ctx: _ObserveContext) -> float:
    """Return seconds remaining in `ctx`'s wall-clock budget (never negative)."""
    remaining = ctx.cfg.observe_deadline_seconds - (time.monotonic() - ctx.start)
    return max(remaining, 0.0)


def _check_deadline(ctx: _ObserveContext, *, stage: str) -> None:
    """Raise `_DeadlineExceeded` if `ctx`'s budget is already spent.

    Args:
        ctx: the in-flight observe context.
        stage: a short label for which stage was about to start, folded
            into the raised message for the journal's `error_text`.

    Raises:
        _DeadlineExceeded: if no time remains before starting `stage`.
    """
    if _remaining(ctx) <= 0:
        raise _DeadlineExceeded(f"deadline exceeded before stage {stage!r}")


def _run_bounded(fn: Callable[[], _T], timeout_seconds: float) -> _T:
    """Run `fn()` bounded by a hard wall-clock timeout, via a daemon thread.

    A per-call `timeout` kwarg (already threaded through `register_session`
    / `handoff.emit` / `handoff.self_register` / `collect_git_facts` as
    additive passthroughs) bounds *most* stalls at the source. This wrapper
    is the second, unconditional layer: it bounds `fn` even if the stall is
    inside something that ignores its own timeout parameter entirely (e.g.
    a fault-injection test that monkeypatches the lock acquisition itself
    to block indefinitely) — the wall-clock ceiling (W-NFR-7) must hold
    regardless of which layer the stall is actually in.

    Args:
        fn: a zero-argument callable to run.
        timeout_seconds: the maximum time to wait for `fn` to return.

    Returns:
        _T: `fn()`'s return value.

    Raises:
        _TimedOut: if `fn` did not return within `timeout_seconds`. The
            backing thread is daemonic and left to finish (or not) on its
            own — it never blocks process exit, and this call itself always
            returns/raises within the bound.
        BaseException: whatever `fn()` itself raised, re-raised unchanged.
    """
    outcome: dict[str, Any] = {}

    def _target() -> None:
        """Run `fn()` in the background thread, capturing its result or exception."""
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised verbatim below
            outcome["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise _TimedOut(f"did not complete within {timeout_seconds:.2f}s")
    if "exc" in outcome:
        raise outcome["exc"]
    return outcome["value"]  # type: ignore[return-value]


def _classify(exc: BaseException) -> tuple[str, str]:
    """Map an exception to a stable `(error_class, error_text)` journal pair.

    Args:
        exc: the exception caught at the façade's outermost boundary.

    Returns:
        tuple[str, str]: a short machine-stable tag and a human-readable
        detail string. Unrecognized exceptions classify as `"internal"` —
        this is what makes an unanticipated failure inside the façade
        itself (as opposed to a documented rejection from the layer below)
        still visible via `observe status`, never a silent swallow.
    """
    if isinstance(exc, (_DeadlineExceeded, _TimedOut)):
        return "deadline_exceeded", str(exc)
    if isinstance(exc, LockTimeoutError):
        return "lock_timeout", str(exc)
    if isinstance(exc, SessionIdentityUnresolved):
        return "identity_unresolved", str(exc)
    if isinstance(exc, (ValidationError, OwnershipError, ValueError)):
        return "rejected", str(exc)
    return "internal", f"{type(exc).__name__}: {exc}"


def _write_journal(
    fleet_dir: Path,
    *,
    event: str,
    error_class: str,
    error_text: str,
    elapsed_ms: float,
    node_hint: str | None = None,
) -> None:
    """Append one JSON line to the failure journal; fall back to one stderr line.

    Args:
        fleet_dir: the project's fleet manifest directory.
        event: which observe verb this is (`"dispatch"` | `"relay"` |
            `"handoff-emit"` | `"launch"`).
        error_class: a short machine-stable failure-mode tag (see
            `_classify`, plus verb-specific tags like `"ambiguous_fuzzy_match"`).
        error_text: a short human-readable detail.
        elapsed_ms: how long the attempt ran before failing.
        node_hint: the node id being observed, if known at failure time.

    Never raises (this module's documented broad-catch posture). On its own
    failure — the fleet directory unwritable, which is often the very
    failure that caused the primary write to fail too — this emits exactly
    one `fleet observe:`-prefixed stderr line and gives up (DESIGN's
    Loudness tier 2), never retried, never a second line.
    """
    line = json.dumps(
        {
            "ts": _now_iso(),
            "event": event,
            "node_hint": node_hint,
            "error_class": error_class,
            "error_text": error_text,
            "elapsed_ms": round(elapsed_ms, 1),
        },
        sort_keys=True,
    )
    try:
        fleet_dir.mkdir(parents=True, exist_ok=True)
        path = _journal_path(fleet_dir)
        existing: list[str] = []
        if path.is_file():
            existing = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
        existing.append(line)
        if len(existing) > _JOURNAL_MAX_LINES:
            existing = existing[-_JOURNAL_MAX_LINES:]
        path.write_text("\n".join(existing) + "\n", encoding="utf-8")
    except OSError as exc:
        print(
            f"fleet observe: {event} failed ({error_class}) and the failure "
            f"journal itself could not be written: {exc}",
            file=sys.stderr,
        )


def _observe_register(
    *,
    event: str,
    adapter: SessionAdapter,
    workspace: Path | str,
    slug: str,
    origination: str,
    target_session_id: str | None,
    writer_role: str,
) -> ObserveResult:
    """Shared fail-soft wrapper over `cli.register_session` (dispatch + relay).

    Args:
        event: `"dispatch"` or `"relay"` — journaled as-is.
        adapter: the `SessionAdapter` to register through.
        workspace: the project's working tree root.
        slug: the superhuman project slug.
        origination: `"spawned"` or `"relayed"`.
        target_session_id: as `register_session`'s parameter of the same
            name — a dispatch id for the spawned path, `None` for the
            relayed (self-registering) path.
        writer_role: a role name, never a model/vendor string (NFR-6).

    Returns:
        ObserveResult: never raises regardless of what `register_session`
        or the adapter does underneath.
    """
    try:
        ctx = _resolve_context(workspace, slug)
    except _Disabled as exc:
        return ObserveResult(ok=False, disabled=True, reason=exc.reason)
    except _IdentityUnresolved as exc:
        _write_journal(
            _default_fleet_dir(workspace, slug),
            event=event,
            error_class="identity_unresolved",
            error_text=str(exc),
            elapsed_ms=0.0,
        )
        return ObserveResult(ok=False, reason=str(exc))

    try:
        _check_deadline(ctx, stage="pre-write")
        from .cli import register_session  # deferred: avoids a cli.py <-> observe.py import cycle

        fragment = _run_bounded(
            lambda: register_session(
                adapter,
                origination=origination,
                project_id=ctx.project_id,
                writer_role=writer_role,
                log_path=ctx.fleet_dir / "events.jsonl",
                sessions_dir=ctx.fleet_dir / "sessions",
                target_session_id=target_session_id,
                lock_retry_attempts=_LOCK_RETRY_ATTEMPTS,
                lock_retry_backoff=_LOCK_RETRY_BACKOFF,
                lock_timeout=ctx.cfg.lock_timeout_seconds,
            ),
            _remaining(ctx),
        )
    except Exception as exc:  # noqa: BLE001 - the façade's sole broad catch (see module docstring)
        error_class, error_text = _classify(exc)
        _write_journal(
            ctx.fleet_dir,
            event=event,
            error_class=error_class,
            error_text=error_text,
            elapsed_ms=_elapsed_ms(ctx),
        )
        return ObserveResult(ok=False, reason=error_text)

    return ObserveResult(ok=True, node_id=fragment.node_id, reason="registered")


def observe_dispatch(
    adapter: SessionAdapter,
    *,
    workspace: Path | str,
    slug: str,
    dispatch_id: str,
    writer_role: str = "pm",
) -> ObserveResult:
    """Fail-soft wrapper over `cli.register_session` for the spawned path (W-FR-1).

    Args:
        adapter: the `SessionAdapter` to resolve the dispatched session's
            facts from (found via `adapter.enumerate_sessions()`).
        workspace: the dispatching project's working tree root.
        slug: the superhuman project slug.
        dispatch_id: the PM-minted id identifying the dispatch unit —
            passed as `register_session`'s `target_session_id`.
        writer_role: a role name, never a model/vendor string (NFR-6).

    Returns:
        ObserveResult: `ok=True` with `node_id` set on a successful
        registration; `disabled=True` if fleet is unconfigured; otherwise
        `ok=False` with a journaled failure — never an exception.
    """
    return _observe_register(
        event="dispatch",
        adapter=adapter,
        workspace=workspace,
        slug=slug,
        origination="spawned",
        target_session_id=dispatch_id,
        writer_role=writer_role,
    )


def observe_relay(
    adapter: SessionAdapter,
    *,
    workspace: Path | str,
    slug: str,
    writer_role: str = "pm",
) -> ObserveResult:
    """Fail-soft wrapper over `cli.register_session` for the relayed path (W-FR-2).

    Args:
        adapter: the `SessionAdapter` the relayed session self-registers
            through (`adapter.current_session()`).
        workspace: the relayed session's working tree root.
        slug: the superhuman project slug.
        writer_role: a role name, never a model/vendor string (NFR-6).

    Returns:
        ObserveResult: as `observe_dispatch` — never an exception.
    """
    return _observe_register(
        event="relay",
        adapter=adapter,
        workspace=workspace,
        slug=slug,
        origination="relayed",
        target_session_id=None,
        writer_role=writer_role,
    )


def observe_handoff_emit(
    adapter: SessionAdapter,
    *,
    workspace: Path | str,
    slug: str,
    prompt_text: str,
    cwd: Path | str | None = None,
    branch: str | None = None,
    writer_role: str = "pm",
) -> ObserveResult:
    """Fail-soft wrapper over `handoff.emit` — the sanctioned prompt producer (W-FR-3).

    **The single most important fail-soft behavior in this package**
    (DESIGN): `result.prompt_text` is *always* set to a deliverable prompt —
    the emitted prompt (with its `FLEET-HANDOFF-ID` line) on success, or
    `prompt_text` exactly as given, untouched, on every failure path
    (disabled, identity unresolved, deadline exceeded, lock timeout,
    rejected, or any internal error). A `FLEET-HANDOFF-ID` line is only
    ever present when the row backing it was actually, durably written —
    this façade never embeds an id it cannot back.

    Args:
        adapter: the `SessionAdapter` whose `emit_prompt` embeds the id and
            whose facts key the intent row's node id.
        workspace: the emitting project's working tree root.
        slug: the superhuman project slug.
        prompt_text: the draft prompt body.
        cwd: the target working directory for the launched session (the
            fuzzy-match anchor); defaults to `workspace`.
        branch: the target git branch (the other fuzzy-match anchor).
        writer_role: a role name, never a model/vendor string (NFR-6).

    Returns:
        ObserveResult: `result.prompt_text` is always the deliverable (see
        above). `ok=True` with `node_id` set only on a durable write.
    """
    try:
        ctx = _resolve_context(workspace, slug)
    except _Disabled as exc:
        return ObserveResult(
            ok=False, disabled=True, reason=exc.reason, prompt_text=prompt_text
        )
    except _IdentityUnresolved as exc:
        _write_journal(
            _default_fleet_dir(workspace, slug),
            event="handoff-emit",
            error_class="identity_unresolved",
            error_text=str(exc),
            elapsed_ms=0.0,
        )
        return ObserveResult(ok=False, reason=str(exc), prompt_text=prompt_text)

    try:
        _check_deadline(ctx, stage="pre-write")
        emission = _run_bounded(
            lambda: handoff_emit_impl(
                adapter,
                slug=ctx.file_slug,
                project_id=ctx.project_id,
                prompt_text=prompt_text,
                cwd=cwd if cwd is not None else workspace,
                branch=branch,
                writer_role=writer_role,
                log_path=ctx.fleet_dir / "events.jsonl",
                sessions_dir=ctx.fleet_dir / "sessions",
                lock_retry_attempts=_LOCK_RETRY_ATTEMPTS,
                lock_retry_backoff=_LOCK_RETRY_BACKOFF,
                lock_timeout=ctx.cfg.lock_timeout_seconds,
            ),
            _remaining(ctx),
        )
    except Exception as exc:  # noqa: BLE001 - the façade's sole broad catch (see module docstring)
        error_class, error_text = _classify(exc)
        _write_journal(
            ctx.fleet_dir,
            event="handoff-emit",
            error_class=error_class,
            error_text=error_text,
            elapsed_ms=_elapsed_ms(ctx),
        )
        return ObserveResult(ok=False, reason=error_text, prompt_text=prompt_text)

    return ObserveResult(
        ok=True, node_id=emission.node_id, reason="emitted", prompt_text=emission.prompt_text
    )


def observe_launch(
    adapter: SessionAdapter,
    *,
    workspace: Path | str,
    slug: str,
    handoff_id: str | None = None,
    prompt_text: str | None = None,
    cwd: Path | str | None = None,
    branch: str | None = None,
    writer_role: str = "pm",
) -> ObserveResult:
    """Fail-soft wrapper over `handoff.self_register` — the launch flip (W-FR-4).

    Args:
        adapter: the `SessionAdapter` used only for the fuzzy `(cwd,
            branch)` fallback's own facts (`adapter.git_facts()`) — never
            touched when an id resolves via `handoff_id`/`prompt_text`.
        workspace: the launched session's working tree root.
        slug: the superhuman project slug.
        handoff_id: the id recovered from this session's own prompt
            (Decision E's primary anchor). Omit to use `prompt_text` or the
            fuzzy fallback.
        prompt_text: this session's own prompt text, grepped for the
            embedded `FLEET-HANDOFF-ID` line via `handoff.extract_handoff_id`
            when `handoff_id` is not given directly.
        cwd: override the fuzzy-match cwd anchor; defaults to the adapter's
            git toplevel, or `workspace`.
        branch: override the fuzzy-match branch anchor; defaults to the
            adapter's current branch.
        writer_role: a role name, never a model/vendor string (NFR-6).

    Returns:
        ObserveResult: `ok=True` with `node_id` set on `"launched"` or
        `"already_launched"` (idempotent repeat); `ok=False` otherwise,
        including the ambiguous-fuzzy-match case, which is journaled with
        the candidate list and never guesses (unchanged from
        `handoff.self_register`'s own refusal).
    """
    resolved_handoff_id = handoff_id
    if resolved_handoff_id is None and prompt_text is not None:
        resolved_handoff_id = extract_handoff_id(prompt_text)

    try:
        ctx = _resolve_context(workspace, slug)
    except _Disabled as exc:
        return ObserveResult(ok=False, disabled=True, reason=exc.reason)
    except _IdentityUnresolved as exc:
        _write_journal(
            _default_fleet_dir(workspace, slug),
            event="launch",
            error_class="identity_unresolved",
            error_text=str(exc),
            elapsed_ms=0.0,
        )
        return ObserveResult(ok=False, reason=str(exc))

    resolved_cwd = cwd
    resolved_branch = branch
    try:
        _check_deadline(ctx, stage="pre-write")
        if resolved_handoff_id is None and (resolved_cwd is None or resolved_branch is None):
            facts = adapter.git_facts()
            if resolved_cwd is None:
                resolved_cwd = facts.toplevel or workspace
            if resolved_branch is None:
                resolved_branch = facts.branch

        register_outcome = _run_bounded(
            lambda: handoff_self_register_impl(
                log_path=ctx.fleet_dir / "events.jsonl",
                sessions_dir=ctx.fleet_dir / "sessions",
                writer_role=writer_role,
                handoff_id=resolved_handoff_id,
                cwd=resolved_cwd,
                branch=resolved_branch,
                lock_retry_attempts=_LOCK_RETRY_ATTEMPTS,
                lock_retry_backoff=_LOCK_RETRY_BACKOFF,
                lock_timeout=ctx.cfg.lock_timeout_seconds,
            ),
            _remaining(ctx),
        )
    except Exception as exc:  # noqa: BLE001 - the façade's sole broad catch (see module docstring)
        error_class, error_text = _classify(exc)
        _write_journal(
            ctx.fleet_dir,
            event="launch",
            error_class=error_class,
            error_text=error_text,
            elapsed_ms=_elapsed_ms(ctx),
        )
        return ObserveResult(ok=False, reason=error_text)

    if register_outcome.status == "ambiguous":
        _write_journal(
            ctx.fleet_dir,
            event="launch",
            error_class="ambiguous_fuzzy_match",
            error_text=f"candidates={list(register_outcome.candidates)}",
            elapsed_ms=_elapsed_ms(ctx),
        )
        return ObserveResult(ok=False, reason="ambiguous fuzzy match — refused to guess")

    if register_outcome.status in ("launched", "already_launched"):
        return ObserveResult(
            ok=True, node_id=register_outcome.node_id, reason=register_outcome.status
        )

    # "not_found" / "not_launchable" — a legitimate, non-crashing outcome,
    # still journaled so `observe status` can surface it (W-FR-8).
    _write_journal(
        ctx.fleet_dir,
        event="launch",
        error_class=register_outcome.status,
        error_text=f"node_id={register_outcome.node_id}",
        elapsed_ms=_elapsed_ms(ctx),
    )
    return ObserveResult(
        ok=False, node_id=register_outcome.node_id, reason=register_outcome.status
    )


def journal_early_cli_failure(
    workspace: Path | str, slug: str, *, event: str, error_class: str, error_text: str
) -> None:
    """Journal a failure `cli.py` catches before any `observe_*` context exists.

    Phase 3.3 preflight FIXES 4 and 5. Some `observe` subcommand failures —
    a malformed `--harness subagent` invocation (`cli._build_adapter`), an
    unreadable/missing `--prompt-file` (`cli._cmd_observe_handoff_emit` /
    `cli._cmd_observe_launch`) — happen one or more lines *before* any
    `observe_*` function in this module runs, too early for that function's
    own context/broad-catch/journal machinery to see them. Without this
    helper such a failure was visible only as a stderr line from `cli.py`,
    invisible to `fleet observe status` and contradicting W-FR-8
    ("auditable ... without reading source") and this module's own W-NFR-1
    ("recorded, not swallowed silently") — and, compounded with the hook
    templates' `>/dev/null 2>&1 || true` redirect, a hook-triggered instance
    of either failure was fully silent end-to-end.

    Never raises (this module's documented broad-catch posture — see the
    module docstring). Mirrors `_resolve_context`'s own disabled-workspace
    short-circuit: if fleet observation is disabled/unconfigured for
    `workspace`, this writes nothing at all — W-FR-7's zero-I/O guarantee
    holds even for this earlier, pre-context failure point.

    Args:
        workspace: the project's working tree root.
        slug: the superhuman project slug.
        event: which `observe` verb this is (`"dispatch"` | `"relay"` |
            `"handoff-emit"` | `"launch"`) — journaled as-is, matching every
            other `_write_journal` call site in this module.
        error_class: a short machine-stable failure-mode tag, matching
            `_write_journal`'s convention (e.g.
            `"adapter_construction_failed"`, `"prompt_file_unreadable"`).
        error_text: a short human-readable detail.
    """
    try:
        cfg = fleet_config.resolve_fleet_config(workspace)
        if not cfg.enabled:
            return
        fleet_dir = cfg.manifest_dir or _default_fleet_dir(workspace, slug)
        _write_journal(
            fleet_dir,
            event=event,
            error_class=error_class,
            error_text=error_text,
            elapsed_ms=0.0,
        )
    except Exception:  # noqa: BLE001 - never allowed to raise past this helper (see module docstring)
        pass


def journal_adapter_construction_failure(
    workspace: Path | str, slug: str, *, event: str, error_text: str
) -> None:
    """Journal a malformed `--harness` invocation caught during adapter construction.

    Phase 3.3 preflight FIX 4. See `journal_early_cli_failure` for the full
    rationale; this is the `error_class="adapter_construction_failed"`
    specialization `cli._safe_build_adapter_for_observe` calls.

    Args:
        workspace: the project's working tree root.
        slug: the superhuman project slug.
        event: which `observe` verb this is, forwarded as-is.
        error_text: the caught `ValueError`'s message.
    """
    journal_early_cli_failure(
        workspace,
        slug,
        event=event,
        error_class="adapter_construction_failed",
        error_text=error_text,
    )


def observe_status(workspace: Path | str, slug: str) -> str:
    """Report fleet-observation enablement + activity for `workspace` (W-FR-8).

    Never raises. Answers "is registration active, and why (not)?" without
    the caller reading source, in one of four documented shapes:

    - `"not configured: <reason>"`
    - `"configured and enabled, zero writes recorded for this project"`
    - `"configured and enabled, last write for this project succeeded"`
    - `"configured and enabled, last write for this project failed: <detail>"`

    Args:
        workspace: the working tree to report on.
        slug: the superhuman project slug.

    Returns:
        str: one of the four shapes above.
    """
    cfg = fleet_config.resolve_fleet_config(workspace)
    if not cfg.enabled:
        return f"not configured: {cfg.reason}"

    fleet_dir = cfg.manifest_dir or _default_fleet_dir(workspace, slug)
    journal_path = _journal_path(fleet_dir)
    last_failure: str | None = None
    if journal_path.is_file():
        try:
            lines = [ln for ln in journal_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            lines = []
        if lines:
            last_failure = lines[-1]

    identity = fleet_project.read_project_identity(workspace, slug)
    has_writes = False
    if identity is not None:
        project_id, _file_slug = identity
        sessions_dir = fleet_dir / "sessions"
        try:
            from .core.query import list_sessions

            has_writes = bool(list_sessions(sessions_dir, project_id))
        except Exception:  # noqa: BLE001 - a status report must never raise (W-FR-8)
            has_writes = False

    if last_failure is not None:
        return f"configured and enabled, last write for this project failed: {last_failure}"
    if not has_writes:
        return "configured and enabled, zero writes recorded for this project"
    return "configured and enabled, last write for this project succeeded"
