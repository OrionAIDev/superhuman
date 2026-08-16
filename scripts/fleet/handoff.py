"""Manual-handoff intent row: `handoff_id` mint/embed, self-register flip,
fuzzy `(cwd, branch)` reconciliation, stale report, cancel (CC-4/FR-2/FR-3).

This is the third of FR-1's three origination paths (spawned, relayed,
manual) — the one where no session exists yet at write time. `emit()` writes
an `awaiting-launch` intent row and mints a UUID `handoff_id`, embedded as a
literal `FLEET-HANDOFF-ID: <uuid>` line in the returned prompt (Decision E).
The launched session's first action calls `self_register()` with that id;
if the operator edited the prompt and the id is gone, `self_register()`
falls back to a fuzzy `(cwd, branch)` match against open rows — and refuses
to guess when more than one candidate matches (DESIGN Open issues, "Fuzzy
reconciliation false-merge risk").

Every write goes through `core.events.append` (validation + ownership
enforcement live there, per DESIGN's data flow) — nothing here writes the
log or a fragment any other way. `lifecycle` is a superhuman-owned field
(`core.schema.FIELD_OWNERS`), so a `writer_role` on the "ceo" side of
`core.ownership`'s split is rejected by `append` itself before anything is
persisted; this module does not duplicate that check.

**Why `_append_with_bounded_retry` is duplicated here rather than imported
from `cli.py`:** `cli.py` imports this module's `emit`/`cancel`/`stale_report`
to wire the `handoff emit|cancel|stale` subcommands (PLAN.md Chunk 3). A
`handoff.py -> cli.py` import back the other way, for just this one helper,
would create an import cycle. The helper is ~15 lines and behavior-identical
to `cli.py`'s; duplicating it is the documented alternative the chunk brief
allows ("Reuse cli.py's `_append_with_bounded_retry` (or the same pattern)").
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .adapter.base import HANDOFF_ID_LINE_PREFIX, SessionAdapter, workspace_component
from .core.errors import FragmentCorrupt, LockTimeoutError, PreconditionUnmet
from .core.events import append, read_all
from .core.nodes import make_node_id
from .core.projection import project_event, rebuild
from .core.query import stale_handoffs
from .core.schema import Event, Fragment, validate_event
from .core.store import iter_fragments, read_fragment

#: Registrar-level bounded retry defaults, matching `cli.py`'s.
_DEFAULT_LOCK_RETRY_ATTEMPTS = 3
_DEFAULT_LOCK_RETRY_BACKOFF = 0.1

#: Matches the literal `FLEET-HANDOFF-ID: <token>` line `adapter.emit_prompt`
#: embeds (Decision E), capturing everything after the prefix up to the end
#: of that line, trimmed. `re.MULTILINE` so `^` anchors to any line, not just
#: the start of the whole text — the marker line can appear anywhere in an
#: edited prompt.
_HANDOFF_ID_LINE_RE = re.compile(
    rf"^{re.escape(HANDOFF_ID_LINE_PREFIX)}\s*(\S+)", re.MULTILINE
)

#: Generic, non-operator-specific fallback (NFR-5: expiry is config/profile
#: driven — this is the default used only when no profile overrides it, not
#: the only value the system can ever produce). 24h is long enough that a
#: normal work-hours handoff never trips it, short enough that a truly
#: abandoned handoff surfaces within a day.
_DEFAULT_HANDOFF_EXPIRY_SECONDS: float = 24.0 * 60.0 * 60.0

#: The `refs/heads/` prefix a fuzzy branch match must normalize away
#: (TC-19's documented normalization).
_REFS_HEADS_PREFIX = "refs/heads/"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix.

    Returns:
        str: e.g. `"2026-08-14T12:00:00.000000Z"`.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalize_cwd(cwd: Path | str) -> str:
    """Normalize a working-directory path for fuzzy-match comparison (TC-19).

    Args:
        cwd: the path to normalize.

    Returns:
        str: forward-slash separators, no trailing slash (except a bare
        root). Two spellings of the same directory (a trailing slash, or
        backslash vs. forward-slash separators) normalize identically.
    """
    text = str(cwd).replace("\\", "/")
    while len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text


def _normalize_branch(branch: str) -> str:
    """Normalize a branch name for fuzzy-match comparison (TC-19).

    Args:
        branch: the branch name to normalize.

    Returns:
        str: with a leading `refs/heads/` stripped, if present, and
        surrounding whitespace removed.
    """
    stripped = branch.strip()
    if stripped.startswith(_REFS_HEADS_PREFIX):
        stripped = stripped[len(_REFS_HEADS_PREFIX) :]
    return stripped


def extract_handoff_id(prompt_text: str) -> str | None:
    """Recover a `handoff_id` from its own emitted prompt text (Decision E).

    The launched session's first action is meant to "grep its own prompt for
    the token" — this is that grep, exposed as a public, directly-testable
    function (review FIX #1) rather than left as an inline detail of a CLI
    command. Public so any caller (the `fleet handoff self-register` CLI
    subcommand, or a future harness-native first-action hook) can reuse it
    without re-implementing the marker format.

    Args:
        prompt_text: the (possibly edited) prompt text to search.

    Returns:
        str | None: the token from the first `FLEET-HANDOFF-ID:` line found,
        or `None` if no such line is present (the edited-prompt case FR-2's
        fuzzy fallback exists for).
    """
    match = _HANDOFF_ID_LINE_RE.search(prompt_text)
    return match.group(1) if match else None


def _append_with_bounded_retry(
    log_path: Path | str,
    event_dict: dict[str, Any],
    *,
    attempts: int,
    backoff: float,
    precondition: Callable[[list[Event]], bool] | None = None,
) -> Event | None:
    """Call `core.events.append`, retrying a bounded number of times on lock contention.

    Identical in behavior to `cli.py`'s helper of the same name (see this
    module's docstring for why it is duplicated rather than imported), plus
    an optional `precondition` pass-through (review FIX #2) — `cli.py`'s own
    copy has no equivalent parameter since `register`/`emit` never need one.
    Never proceeds as if the event were written — either `append` succeeds
    within the attempt budget, or an exception propagates to the caller.
    `PreconditionUnmet` is never retried (retrying a precondition failure
    that depends on committed, not transient, state cannot succeed by
    trying again) — only `LockTimeoutError` triggers a retry.

    Args:
        log_path: path to the event log.
        event_dict: the raw event dict to append.
        attempts: total attempts, including the first (must be >= 1).
        backoff: seconds to sleep between attempts.
        precondition: passed through to `core.events.append` unchanged.

    Returns:
        Event | None: as `core.events.append`.

    Raises:
        LockTimeoutError: if every attempt timed out.
        PreconditionUnmet: if `precondition` is given and rejects the write
            (propagates immediately, not retried).
    """
    last_exc: LockTimeoutError | None = None
    for attempt in range(attempts):
        try:
            return append(log_path, event_dict, precondition=precondition)
        except LockTimeoutError as exc:
            last_exc = exc
            if attempt < attempts - 1 and backoff > 0:
                time.sleep(backoff)
    assert last_exc is not None  # attempts >= 1 guarantees at least one raise
    raise last_exc


@dataclass(frozen=True, slots=True)
class HandoffEmission:
    """The result of `emit()`.

    Attributes:
        handoff_id: the minted UUID (Decision E's durable token).
        node_id: the namespaced node id this handoff's intent row was
            recorded under — the same id `self_register()` flips to
            `lifecycle=active`.
        prompt_text: `prompt_text` as given, with the literal
            `FLEET-HANDOFF-ID: <uuid>` line embedded by the adapter.
    """

    handoff_id: str
    node_id: str
    prompt_text: str


def emit(
    adapter: SessionAdapter,
    *,
    slug: str,
    project_id: str,
    prompt_text: str,
    cwd: Path | str,
    writer_role: str,
    log_path: Path | str,
    sessions_dir: Path | str,
    branch: str | None = None,
    handoff_id: str | None = None,
    harness: str = "handoff",
    lock_retry_attempts: int = _DEFAULT_LOCK_RETRY_ATTEMPTS,
    lock_retry_backoff: float = _DEFAULT_LOCK_RETRY_BACKOFF,
) -> HandoffEmission:
    """Write an `awaiting-launch` intent row and embed a durable `handoff_id` (FR-1 3rd path / FR-2).

    Mints a UUID `handoff_id` (unless one is given, for idempotent-retry
    callers), writes a `handoff_emitted` event whose payload sets
    `lifecycle="awaiting-launch"` (projected onto a fresh fragment exactly
    like any other status-field payload key — no special-casing needed in
    `core/projection.py`), and returns the prompt with the literal
    `FLEET-HANDOFF-ID: <uuid>` line embedded via `adapter.emit_prompt`
    (Decision E) so a launched session's first action can recover it even if
    everything else in the prompt was edited.

    `cwd`/`branch` are recorded on the event (not the fragment — `Fragment`
    has no room for them, and they are cache-only lookup data, not status)
    so `self_register()`'s fuzzy fallback and `core.query.stale_handoffs()`
    can join back to them later, from the log alone.

    Args:
        adapter: the `SessionAdapter` whose `emit_prompt` embeds the
            `handoff_id` line and whose `workspace_component` (via
            `adapter.base.workspace_component`) keys the intent row's node
            id, matching how every other node id in this package is built.
        slug: the superhuman project slug.
        project_id: the owning project's stable id (Decision F).
        prompt_text: the prompt body to emit.
        cwd: the target working directory the launched session is expected
            to run in — the fuzzy-match anchor (TC-19).
        writer_role: a role name, never a model/vendor string (NFR-6).
            `lifecycle` is superhuman-owned (`core.schema.FIELD_OWNERS`), so
            a "ceo"-class role is rejected by `core.events.append` itself
            (FR-8) — not re-checked here.
        log_path: path to the project's event log.
        sessions_dir: path to the project's fragment directory.
        branch: the target git branch, if known — the other half of the
            fuzzy-match anchor. `""` (recorded, matches nothing) if omitted.
        handoff_id: override the minted id (idempotent-retry callers only).
        harness: the node-id harness component for this pending intent row.
            Defaults to the literal `"handoff"`, distinguishing a
            not-yet-launched row from any real harness session's own node id
            — constructed through the same `make_node_id` path as every
            other node id (CC-3), just with a distinct harness label.
        lock_retry_attempts: bounded registrar-level retry count (see
            `_append_with_bounded_retry`).
        lock_retry_backoff: seconds to sleep between registrar-level
            retries.

    Returns:
        HandoffEmission: the minted id, the intent row's node id, and the
        prompt with the handoff-id line embedded. Returned even if the
        node's cached fragment was found corrupt on disk and had to be
        recovered via `core.projection.rebuild()` (G5 round-5, #P4-1/#P4-2)
        — the event is already durably appended to the log by this point,
        so a corrupt fragment cache is recovered, never fatal.

    Raises:
        ValidationError: if the built event fails schema validation.
        OwnershipError: if `writer_role` may not write `lifecycle`.
        LockTimeoutError: if the shared log lock could not be acquired
            within the bounded retry budget. Nothing was written.
    """
    minted_id = handoff_id or str(uuid4())
    node_id = make_node_id(harness, workspace_component(cwd), slug, f"handoff-{minted_id}")

    event_dict: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": f"emit:{minted_id}",
        "ts": _now_iso(),
        "type": "handoff_emitted",
        "project_id": project_id,
        "node_id": node_id,
        "writer_role": writer_role,
        "payload": {
            "lifecycle": "awaiting-launch",
            "handoff_id": minted_id,
            "cwd": _normalize_cwd(cwd),
            "branch": _normalize_branch(branch) if branch else "",
        },
    }

    appended = _append_with_bounded_retry(
        log_path, event_dict, attempts=lock_retry_attempts, backoff=lock_retry_backoff
    )
    if appended is None:
        # Dedupe no-op (a repeat emit of the same handoff_id) — the event of
        # record is whatever was appended first; project the built event
        # anyway so the fragment is guaranteed to exist (matches
        # cli.register_session's precedent for the same situation).
        appended = validate_event(event_dict)
    try:
        project_event(appended, sessions_dir)
    except FragmentCorrupt:
        # G5 round-5 (#P4-1/#P4-2): recover via full replay rather than
        # letting `project_event` guess at a partial fragment — `appended`
        # is already durably in the log by this point.
        rebuild(log_path, sessions_dir, project_id=project_id)

    emitted_prompt = adapter.emit_prompt(prompt_text, minted_id)
    return HandoffEmission(handoff_id=minted_id, node_id=node_id, prompt_text=emitted_prompt)


@dataclass(frozen=True, slots=True)
class _HandoffRow:
    """One open (`awaiting-launch`) handoff row, joined from log + fragment.

    Attributes:
        node_id: the intent row's node id.
        handoff_id: the handoff's minted id.
        cwd: the normalized target cwd recorded at emit time.
        branch: the normalized target branch recorded at emit time.
    """

    node_id: str
    handoff_id: str
    cwd: str
    branch: str


def _open_awaiting_launch_rows(
    log_path: Path | str, sessions_dir: Path | str
) -> list[_HandoffRow]:
    """Return every currently-open (`lifecycle=awaiting-launch`) handoff row.

    A cancelled row is excluded by construction: `handoff_cancelled` flips
    `lifecycle` away from `"awaiting-launch"` (see `cancel()`), so it never
    appears in `iter_fragments()`'s `awaiting-launch` subset — the same
    invariant `core.query.stale_handoffs()` relies on.

    Args:
        log_path: path to the project's event log.
        sessions_dir: path to the project's fragment directory.

    Returns:
        list[_HandoffRow]: one row per open handoff, in log order. A
        fragment with no matching `handoff_emitted` event (not expected in
        normal operation) is skipped rather than guessed at.

    A corrupt cached fragment must never silently drop a live candidate out
    of this pool (G6 systematic sweep, GPT-5 P5-2, BLOCKING): unlike
    `core.query.list_sessions`'s best-effort listing, this pool's contents
    *drive a decision* — `self_register`'s fuzzy match. `iter_fragments`'s
    default `skip_corrupt=True` would make a corrupt awaiting-launch row
    vanish from `open_node_ids` with no error, so the real match is silently
    reported `not_found`. This calls `iter_fragments(skip_corrupt=False)`
    instead, and on `FragmentCorrupt` recovers via a full
    `core.projection.rebuild()` from the log (the source of truth) before
    retrying once — the same recover-not-guess contract every other
    decision-driving fragment read in this module follows.

    Nor must a corrupt cached fragment crash this decision when it has
    nothing to do with it (#R6-1, GPT-5 round-6, BLOCKING, PM-reproduced):
    `rebuild()` only rewrites fragments backed by a log event — it cannot
    remove or fix an *orphan* fragment file (no matching `handoff_emitted`
    event in the log at all, e.g. dropped in by an external process). Such
    an orphan is still corrupt after the rebuild, so a naive retry with
    `skip_corrupt=False` re-raises `FragmentCorrupt` and crashes an
    otherwise-legitimate fuzzy `self_register` that never touches it. The
    retry below therefore uses `skip_corrupt=True`: any fragment still
    corrupt AFTER a full `rebuild()` is *provably* an orphan, because
    `rebuild()` rewrites every log-backed node to a valid, readable
    fragment — and an orphan, being not log-backed, can never be the
    awaiting-launch row being matched here (that row is always reachable
    via its own `handoff_emitted` event, read separately below). Skipping
    post-rebuild corrupt files therefore drops only proven non-candidates —
    it does not reintroduce the P5-2 silent-drop of a real, log-backed
    awaiting-launch row, since a real row is always rebuilt to a valid
    fragment and never lands in the skipped set.
    """
    try:
        current_fragments = iter_fragments(sessions_dir, skip_corrupt=False)
    except FragmentCorrupt:
        rebuild(log_path, sessions_dir)
        current_fragments = iter_fragments(sessions_dir, skip_corrupt=True)

    open_node_ids = {f.node_id for f in current_fragments if f.lifecycle == "awaiting-launch"}
    if not open_node_ids:
        return []

    rows: list[_HandoffRow] = []
    for event in read_all(log_path):
        if event.type != "handoff_emitted" or event.node_id not in open_node_ids:
            continue
        rows.append(
            _HandoffRow(
                node_id=event.node_id,
                handoff_id=str(event.payload.get("handoff_id", "")),
                cwd=_normalize_cwd(str(event.payload.get("cwd", ""))),
                branch=_normalize_branch(str(event.payload.get("branch", ""))),
            )
        )
    return rows


#: Event types that permanently close a handoff row. Checked atomically,
#: under the lock, by `_not_terminated` — the pre-lock `current.lifecycle`
#: read in `self_register` cannot make this guarantee on its own, since a
#: racing `cancel()` can commit in the window between that read and the
#: lock actually being acquired for the launch write (review FIX #2).
_TERMINAL_HANDOFF_EVENT_TYPES = frozenset({"handoff_cancelled", "handoff_expired"})


def _not_terminated(node_id: str) -> Callable[[list[Event]], bool]:
    """Build an `append()` precondition refusing a launch for a closed handoff.

    Args:
        node_id: the handoff row's node id.

    Returns:
        Callable[[list[Event]], bool]: a predicate over the fresh event list
        `core.events.append` reads under the lock (review FIX #2) — `False`
        (refuse the write) iff a `handoff_cancelled`/`handoff_expired` event
        already exists for `node_id`.
    """

    def _check(existing: list[Event]) -> bool:
        return not any(
            e.node_id == node_id and e.type in _TERMINAL_HANDOFF_EVENT_TYPES for e in existing
        )

    return _check


def _find_by_handoff_id(log_path: Path | str, handoff_id: str) -> str | None:
    """Return the node id emitted for `handoff_id`, or `None` if not found.

    Args:
        log_path: path to the project's event log.
        handoff_id: the handoff id to look up.

    Returns:
        str | None: the matching `handoff_emitted` event's `node_id`, or
        `None` if no such event exists in the log.
    """
    for event in read_all(log_path):
        if event.type == "handoff_emitted" and event.payload.get("handoff_id") == handoff_id:
            return event.node_id
    return None


@dataclass(frozen=True, slots=True)
class SelfRegisterResult:
    """The outcome of `self_register()`.

    Attributes:
        status: one of `"launched"` (the flip happened on this call),
            `"already_launched"` (idempotent repeat — no second event),
            `"ambiguous"` (fuzzy match found more than one open candidate;
            refused to auto-flip either), `"not_found"` (no open candidate
            matched), or `"not_launchable"` (the exact-id match resolved to
            a row that is no longer `awaiting-launch`, e.g. cancelled).
        node_id: the matched/flipped node id, or `None` for `"ambiguous"`/
            `"not_found"`.
        match_method: `"exact"` or `"fuzzy"`, or `None` when nothing
            matched. Recorded on the `handoff_launched` event itself for
            auditability (TC-19).
        candidates: for `"ambiguous"` only — every open candidate's node id,
            sorted, so disambiguation surfaces a deterministic list rather
            than an unordered one (TC-20's determinism requirement).
    """

    status: str
    node_id: str | None
    match_method: str | None
    candidates: tuple[str, ...] = ()


def self_register(
    *,
    log_path: Path | str,
    sessions_dir: Path | str,
    writer_role: str,
    handoff_id: str | None = None,
    cwd: Path | str | None = None,
    branch: str | None = None,
    lock_retry_attempts: int = _DEFAULT_LOCK_RETRY_ATTEMPTS,
    lock_retry_backoff: float = _DEFAULT_LOCK_RETRY_BACKOFF,
) -> SelfRegisterResult:
    """Flip an `awaiting-launch` handoff row to `active` (FR-2).

    Exact match (`handoff_id` given) is primary and authoritative. Fuzzy
    match (`handoff_id` omitted, `cwd`/`branch` given) is the documented
    fallback for an edited prompt (TC-19) — normalizing a trailing slash and
    a `refs/heads/` prefix before comparing. If the fuzzy match finds **more
    than one** open row sharing the same `(cwd, branch)`, this refuses to
    auto-flip either: no `handoff_launched` event is written for any
    candidate, and every candidate is returned for human/PM disambiguation
    (TC-20 — DESIGN's "Fuzzy reconciliation false-merge risk" mitigation).
    This refusal is deterministic — it never guesses, so re-running the same
    ambiguous scenario never non-deterministically picks a winner.

    A second call for an already-launched `handoff_id` (or an already-open
    row matched again by fuzzy criteria) is a no-op: this checks the current
    fragment's `lifecycle` before writing anything, so a repeat call returns
    `"already_launched"` without a second `handoff_launched` event — the
    same guarantee the `launch:<handoff_id>` idempotency key gives under
    true concurrent racing (see `tests/fleet/test_concurrency.py`'s
    double-launch worker, which exercises the underlying `core.events.append`
    path directly).

    **Cancel is terminal, even against a racing self_register (review FIX
    #2).** The `current.lifecycle` check above reads the fragment *before*
    the log lock is acquired, so on its own it can only see a snapshot that
    may already be stale by the time the write actually happens — a
    genuinely concurrent `cancel()` can commit in that exact window. The
    write itself is therefore additionally guarded by an `append()`
    `precondition` (`_not_terminated`) that re-checks, under the lock and
    against the fresh log, that no `handoff_cancelled`/`handoff_expired`
    event exists for this node — closing the race the pre-lock read cannot.
    When that precondition fires, this returns `"not_launchable"` exactly as
    the pre-lock check would have, rather than propagating the underlying
    `PreconditionUnmet`; the caller sees one consistent outcome for "this
    handoff is closed" regardless of which of the two checks actually caught
    it.

    Args:
        log_path: path to the project's event log.
        sessions_dir: path to the project's fragment directory.
        writer_role: a role name, never a model/vendor string (NFR-6).
            `lifecycle` is superhuman-owned, so a "ceo"-class role is
            rejected by `core.events.append` itself (FR-8).
        handoff_id: the id recovered from the launched session's own prompt
            (Decision E's primary anchor). `None` selects the fuzzy path.
        cwd: the launched session's actual working directory — required for
            the fuzzy path, ignored otherwise.
        branch: the launched session's actual git branch — required for the
            fuzzy path, ignored otherwise.
        lock_retry_attempts: bounded registrar-level retry count.
        lock_retry_backoff: seconds to sleep between registrar-level
            retries.

    Returns:
        SelfRegisterResult: see the class docstring for the status
        vocabulary. A `"launched"` result is still returned even if the
        node's cached fragment was found corrupt on disk and had to be
        recovered via `core.projection.rebuild()` (G5 round-5,
        #P4-1/#P4-2) — the launch event is already durably appended to the
        log by that point, so a corrupt fragment cache is recovered, never
        fatal.

    Raises:
        ValueError: if `handoff_id` is given but no `handoff_emitted` event
            for it exists in the log, or if `handoff_id` is `None` and
            either `cwd` or `branch` is missing.
        LockTimeoutError: if the shared log lock could not be acquired
            within the bounded retry budget. Nothing was written.
    """
    if handoff_id is not None:
        node_id = _find_by_handoff_id(log_path, handoff_id)
        if node_id is None:
            raise ValueError(
                f"no handoff_emitted event found for handoff_id={handoff_id!r}"
            )
        match_method = "exact"
    else:
        if cwd is None or branch is None:
            raise ValueError("fuzzy self_register requires both cwd and branch")
        norm_cwd = _normalize_cwd(cwd)
        norm_branch = _normalize_branch(branch)
        candidates = [
            row
            for row in _open_awaiting_launch_rows(log_path, sessions_dir)
            if row.cwd == norm_cwd and row.branch == norm_branch
        ]
        if not candidates:
            return SelfRegisterResult(status="not_found", node_id=None, match_method=None)
        if len(candidates) > 1:
            return SelfRegisterResult(
                status="ambiguous",
                node_id=None,
                match_method=None,
                candidates=tuple(sorted(row.node_id for row in candidates)),
            )
        node_id = candidates[0].node_id
        handoff_id = candidates[0].handoff_id
        match_method = "fuzzy"

    already_rebuilt = False
    try:
        current = read_fragment(node_id, sessions_dir)
    except FragmentCorrupt:
        # G6 (systematic sweep, GPT-5 P5-1, BLOCKING): a corrupt cached
        # fragment must never crash a launch — `node_id` is already resolved
        # (from the log, not the fragment) by this point either way, so
        # recover by replaying the whole log and retry the read once.
        # `project_id` is not yet known here (only `node_id` is), so this
        # rebuild is unscoped — it replays every project sharing this
        # log/sessions pair, which is correct, just broader than strictly
        # necessary.
        rebuild(log_path, sessions_dir)
        already_rebuilt = True
        current = read_fragment(node_id, sessions_dir)
    if current is None and match_method == "exact" and not already_rebuilt:
        # GPT-5 round-9 preflight, BLOCKING, PM-reproduced: on the exact-id
        # path, a cached fragment that is merely ABSENT (e.g. externally
        # deleted) — not corrupt — must recover the same way the corrupt
        # branch above does: `node_id` is already resolved from the log
        # (the source of truth), so an absent fragment is just an
        # unrebuilt cache, never a reason to give up. Bounded to a single
        # retry (`already_rebuilt` guards against rebuilding twice when the
        # corrupt branch already ran) — if the fragment is still absent
        # after a full rebuild, there really is no log-backed row for this
        # node and `not_found` is correct. The fuzzy path is intentionally
        # left alone here: its candidates are already sourced from
        # `_open_awaiting_launch_rows`, which reads fragments itself.
        rebuild(log_path, sessions_dir)
        current = read_fragment(node_id, sessions_dir)
    if current is None:
        return SelfRegisterResult(status="not_found", node_id=node_id, match_method=match_method)
    if current.lifecycle == "active":
        return SelfRegisterResult(
            status="already_launched", node_id=node_id, match_method=match_method
        )
    if current.lifecycle != "awaiting-launch":
        # Cancelled (or any other non-open state) — never resurrect it by
        # flipping it to active out from under a cancel (TC-21's "a
        # cancelled row must not count as a live candidate", extended here
        # to the exact-id path too).
        return SelfRegisterResult(
            status="not_launchable", node_id=node_id, match_method=match_method
        )

    event_dict: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": f"launch:{handoff_id}",
        "ts": _now_iso(),
        "type": "handoff_launched",
        "project_id": current.project_id,
        "node_id": node_id,
        "writer_role": writer_role,
        "payload": {"lifecycle": "active", "match_method": match_method, "handoff_id": handoff_id},
    }

    try:
        appended = _append_with_bounded_retry(
            log_path,
            event_dict,
            attempts=lock_retry_attempts,
            backoff=lock_retry_backoff,
            precondition=_not_terminated(node_id),
        )
    except PreconditionUnmet:
        # A cancel (or expiry) committed between the pre-lock fragment read
        # above and the lock actually being acquired for this write — the
        # atomic guard caught what the pre-lock check could not (review
        # FIX #2). No event was written.
        return SelfRegisterResult(
            status="not_launchable", node_id=node_id, match_method=match_method
        )

    if appended is None:
        # Idempotency-key dedupe under true concurrency (TC-12's
        # double-launch worker) — the flip already happened on the racing
        # writer's event; nothing more to project here.
        return SelfRegisterResult(
            status="already_launched", node_id=node_id, match_method=match_method
        )

    try:
        project_event(appended, sessions_dir)
    except FragmentCorrupt:
        # G5 round-5 (#P4-1/#P4-2): recover via full replay — `appended` is
        # already durably in the log by this point.
        rebuild(log_path, sessions_dir, project_id=current.project_id)
    return SelfRegisterResult(status="launched", node_id=node_id, match_method=match_method)


def cancel(
    node_id: str,
    *,
    project_id: str,
    writer_role: str,
    log_path: Path | str,
    sessions_dir: Path | str,
    lock_retry_attempts: int = _DEFAULT_LOCK_RETRY_ATTEMPTS,
    lock_retry_backoff: float = _DEFAULT_LOCK_RETRY_BACKOFF,
) -> Fragment:
    """Close an open handoff row (FR-3).

    Writes a `handoff_cancelled` event flipping `lifecycle` to `"cancelled"`
    — a value distinct from `"awaiting-launch"`, so the row stops appearing
    in `core.query.stale_handoffs()` and `self_register()`'s fuzzy-match
    candidate pool (both filter on `lifecycle == "awaiting-launch"`
    specifically), without needing any new machinery in either.

    Args:
        node_id: the handoff intent row's node id.
        project_id: the owning project's stable id.
        writer_role: a role name, never a model/vendor string (NFR-6).
            `lifecycle` is superhuman-owned, so a "ceo"-class role is
            rejected by `core.events.append` itself (FR-8).
        log_path: path to the project's event log.
        sessions_dir: path to the project's fragment directory.
        lock_retry_attempts: bounded registrar-level retry count.
        lock_retry_backoff: seconds to sleep between registrar-level
            retries.

    Returns:
        Fragment: the row's fragment after the cancel is applied. Correct
        even if the cached fragment was found corrupt on disk and had to
        be recovered via `core.projection.rebuild()` (G5 round-5,
        #P4-1/#P4-2) — the cancel event is already durably appended to the
        log by that point, so a corrupt fragment cache is recovered, never
        fatal.

    Raises:
        ValidationError: if the built event fails schema validation.
        OwnershipError: if `writer_role` may not write `lifecycle`.
        LockTimeoutError: if the shared log lock could not be acquired
            within the bounded retry budget. Nothing was written.
    """
    event_dict: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": f"cancel:{node_id}",
        "ts": _now_iso(),
        "type": "handoff_cancelled",
        "project_id": project_id,
        "node_id": node_id,
        "writer_role": writer_role,
        "payload": {"lifecycle": "cancelled"},
    }

    appended = _append_with_bounded_retry(
        log_path, event_dict, attempts=lock_retry_attempts, backoff=lock_retry_backoff
    )
    if appended is None:
        try:
            existing = read_fragment(node_id, sessions_dir)
        except FragmentCorrupt:
            existing = None
        if existing is not None:
            return existing
        appended = validate_event(event_dict)

    try:
        return project_event(appended, sessions_dir)
    except FragmentCorrupt:
        # G5 round-5 (#P4-1/#P4-2): recover via full replay — `appended` is
        # already durably in the log by this point.
        fragments = rebuild(log_path, sessions_dir, project_id=project_id)
        return fragments[node_id]


def _resolve_handoff_expiry_seconds(profile_path: Path | None = None) -> float:
    """Resolve the handoff staleness threshold (NFR-5 — config/profile-driven).

    Looks for an optional `fleet.handoff_expiry_seconds` key in
    `~/.superhuman/profile.yaml` (located via the existing
    `superhuman_profile.find_profile` search, reused rather than
    reimplemented). Reads the YAML **directly** rather than through
    `superhuman_profile.load_profile` — that loader validates only the
    deployment-ladder schema (`version`/`ladder`/`conventions`/`models`/...)
    and would reject an unrecognized top-level `fleet` key; this function
    must not require every existing profile to grow a ladder-schema change
    just to gain an optional handoff-expiry override. Any failure to locate,
    read, or parse a profile degrades to the built-in default rather than
    raising — this is advisory config, never a hard requirement.

    Args:
        profile_path: override the profile file to read (for tests); `None`
            uses `superhuman_profile.find_profile`'s normal search.

    Returns:
        float: the configured `fleet.handoff_expiry_seconds`, or
        `_DEFAULT_HANDOFF_EXPIRY_SECONDS` if no profile is found, it fails
        to parse, or the key is absent/non-positive.
    """
    path = profile_path
    if path is None:
        try:
            from ..superhuman_profile import find_profile
        except ImportError:
            return _DEFAULT_HANDOFF_EXPIRY_SECONDS
        path = find_profile(Path.cwd())

    if path is None or not path.is_file():
        return _DEFAULT_HANDOFF_EXPIRY_SECONDS

    try:
        import yaml
    except ImportError:
        return _DEFAULT_HANDOFF_EXPIRY_SECONDS

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        # `ValueError` covers `UnicodeDecodeError` (raised by `read_text` on
        # invalid UTF-8 bytes) — a non-UTF-8 profile must degrade to the
        # default like every other malformed-profile case, not crash
        # `stale_report()` (review finding #3).
        return _DEFAULT_HANDOFF_EXPIRY_SECONDS

    if not isinstance(raw, dict):
        return _DEFAULT_HANDOFF_EXPIRY_SECONDS
    fleet_cfg = raw.get("fleet")
    if not isinstance(fleet_cfg, dict):
        return _DEFAULT_HANDOFF_EXPIRY_SECONDS
    value = fleet_cfg.get("handoff_expiry_seconds")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return _DEFAULT_HANDOFF_EXPIRY_SECONDS


def stale_report(
    *,
    log_path: Path | str,
    sessions_dir: Path | str,
    now: datetime | None = None,
    expiry_seconds: float | None = None,
    profile_path: Path | None = None,
) -> list[dict[str, Any]]:
    """List `awaiting-launch` handoff rows past expiry (FR-3).

    A thin wrapper over `core.query.stale_handoffs()`: this resolves `now`
    (default: the current UTC time) and `expiry_seconds` (default: the
    profile-driven value from `_resolve_handoff_expiry_seconds()`), then
    delegates the actual manifest-only computation to `core/query.py`, which
    takes no adapter or config dependency of its own.

    Args:
        log_path: path to the project's event log.
        sessions_dir: path to the project's fragment directory.
        now: the reference time; defaults to `datetime.now(timezone.utc)`.
        expiry_seconds: override the staleness threshold; `None` resolves it
            from `~/.superhuman/profile.yaml` (NFR-5).
        profile_path: override the profile file to read (for tests); ignored
            if `expiry_seconds` is given explicitly.

    Returns:
        list[dict[str, Any]]: as `core.query.stale_handoffs()`.
    """
    resolved_now = now if now is not None else datetime.now(timezone.utc)
    resolved_expiry = (
        expiry_seconds
        if expiry_seconds is not None
        else _resolve_handoff_expiry_seconds(profile_path)
    )
    return stale_handoffs(
        log_path, sessions_dir, now=resolved_now, expiry_seconds=resolved_expiry
    )
