"""Project ownership: a pure fold over its own events, and a locked claim/stand-down (increment O).

Registration (`session_registered`) could never carry ownership: every
registration is keyed `register:<node_id>` and `core.events.append` dedupes
unconditionally, so a session first registered `observed` had no way to say
"I own this project now" (the defect this module exists to fix,
`REQUIREMENTS.md` "Amendment 2026-09-26"). Ownership is instead its own pair
of event types — `ownership_declared` / `ownership_stood_down` — with their
own idempotency keys, anchored on the ownership state they were decided
against (`own:<project_id>:<node_id>:after:<anchor>`), so a re-claim after a
takeover is never deduped as a repeat of an earlier claim, while a genuine
retry of one call always is.

This module never imports a harness-specific module (W-NFR-2): `claim`/
`stand_down` take plain data (a `liveness` string, an `attested_owner` node
id) — resolving those from real harness session records is the adapter
layer's job (`adapter/session_liveness.py`, a later chunk).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from .errors import OwnershipContended, OwnershipRefused, PreconditionUnmet
from .events import append_batch, read_all
from .schema import Event

#: Bounded re-evaluation budget for a claim/stand-down whose precondition is
#: rejected by a concurrent writer (O-FR-6, DESIGN O.5 step 6): "at most 3
#: re-evaluations, then raise OwnershipContended."
_MAX_REEVALUATIONS: Final[int] = 3

_DEFAULT_TIMEOUT: Final[float] = 10.0
_DEFAULT_RETRY_INTERVAL: Final[float] = 0.02

#: `decide_claim` outcome vocabulary (DESIGN O.5 step 3).
NOOP_ALREADY_OWNER: Final[str] = "NOOP_ALREADY_OWNER"
CLAIM_UNOWNED: Final[str] = "CLAIM_UNOWNED"
CLAIM_OVER: Final[str] = "CLAIM_OVER"
REFUSE_COORDINATION_REQUIRED: Final[str] = "REFUSE_COORDINATION_REQUIRED"
REFUSE_ATTESTATION_MISMATCH: Final[str] = "REFUSE_ATTESTATION_MISMATCH"

#: Liveness values that make a prior owner's ownership legitimately over
#: without any coordination step (O-FR-5).
_LIVENESS_ENDS_OWNERSHIP: Final[frozenset[str]] = frozenset({"archived", "deleted"})


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix.

    Returns:
        str: e.g. `"2026-09-27T12:00:00.000000Z"`.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class OwnerState:
    """A pure snapshot of a project's ownership, as folded from its event log.

    Attributes:
        owner: the current owner's `node_id`, or `None` if the project has
            no current owner (never declared, or the owner stood down with
            no successor yet).
        declaring_event_id: the `event_id` of the `ownership_declared` event
            that made `owner` current, or `None` when `owner` is `None`.
        anchor: the `event_id` of the newest valid ownership event for this
            project, or the literal string `"genesis"` if none exists. This
            is exactly the value a next claim's idempotency key anchors on.
        anchor_kind: `"genesis"`, `"declared"`, or `"stood_down"` — which
            kind of event `anchor` refers to.
        anchor_node: the `node_id` on the event `anchor` refers to, or
            `None` when `anchor_kind == "genesis"`.
        has_ownership_events: whether this project has any valid ownership
            event at all (an orphaned on-behalf stand-down does not count).
    """

    owner: str | None
    declaring_event_id: str | None
    anchor: str
    anchor_kind: str
    anchor_node: str | None
    has_ownership_events: bool


def _build_declared_index(events: list[Event], project_id: str) -> dict[str, str]:
    """Map each `ownership_declared` event's `idempotency_key` to its `node_id`.

    Shared by `fold_owner` and `resolve_legacy_owner` so both apply the exact
    same "is this stand-down for real" test (I1/M7).

    Args:
        events: event-log entries, e.g. as read by `core.events.read_all`.
        project_id: the project to index declarations for.

    Returns:
        dict[str, str]: `idempotency_key -> node_id` for every
        `ownership_declared` event in `events` for `project_id`.
    """
    return {
        e.idempotency_key: e.node_id
        for e in events
        if e.project_id == project_id and e.type == "ownership_declared"
    }


def _is_orphaned_standdown(event: Event, declared_index: dict[str, str]) -> bool:
    """Return whether `event` is an orphaned on-behalf stand-down (DESIGN O.5, I1/M7).

    An `ownership_stood_down` event with `payload["written_by"] ==
    "claimant"` is orphaned — as if it never happened — unless its
    `payload["claim_key"]` names a REAL `ownership_declared` event's
    `idempotency_key` for this project AND that declaration's `node_id`
    equals the stand-down's own `payload["claimant"]` (M7: a `claim_key`
    that merely happens to match some unrelated declaration is a forged or
    corrupted pairing, not a real one). This is what makes an interrupted
    `append_batch` (a torn write leaving only the on-behalf stand-down half)
    invisible to every reader — both `fold_owner` and `resolve_legacy_owner`
    apply this exact same test, so neither one alone can disagree about
    whether a given stand-down "really happened."

    Args:
        event: an `ownership_stood_down` event.
        declared_index: this project's declared-event index, from
            `_build_declared_index`.

    Returns:
        bool: True iff `event` is an orphan (must be skipped, as if absent).
    """
    payload = event.payload
    if payload.get("written_by") != "claimant":
        return False
    return declared_index.get(payload.get("claim_key")) != payload.get("claimant")


def fold_owner(events: list[Event], project_id: str) -> OwnerState:
    """Fold `events` into the current ownership state for `project_id` (O.6).

    Pure and total; walks the events in **log order** (never timestamps),
    considering only this project's `ownership_declared`/
    `ownership_stood_down` events.

    An `ownership_stood_down` event with `payload["written_by"] ==
    "claimant"` is skipped entirely (as if absent) unless its
    `payload["claim_key"]` matches the `idempotency_key` of some
    `ownership_declared` event anywhere in `events` for this project — this
    is what makes an interrupted `append_batch` (a torn write leaving only
    the on-behalf stand-down half) invisible to every reader (DESIGN O.5's
    "Torn batches" / Material debt #2).

    A declaration always makes its `node_id` the new owner, even displacing
    a prior owner with no stand-down in between (O.6: "A newer declaration
    displaces any owner, even without a stand-down, so a forged or legacy
    sequence still has one answer"). A stand-down only clears the owner when
    it names that owner's own `node_id` AND its `stood_down_from` matches
    that owner's declaring `event_id` exactly; any other valid stand-down
    still moves `anchor` (and `anchor_kind`/`anchor_node`) but leaves `owner`
    untouched.

    Args:
        events: event-log entries, e.g. as read by `core.events.read_all`.
        project_id: the project to fold ownership for.

    Returns:
        OwnerState: see the class docstring. A log with no ownership events
        for this project folds to `owner=None, has_ownership_events=False,
        anchor="genesis"` (O-NFR-3).
    """
    declared_index = _build_declared_index(events, project_id)

    owner: str | None = None
    declaring_event_id: str | None = None
    anchor = "genesis"
    anchor_kind = "genesis"
    anchor_node: str | None = None
    has_ownership_events = False

    for event in events:
        if event.project_id != project_id:
            continue

        if event.type == "ownership_declared":
            owner = event.node_id
            declaring_event_id = event.event_id
            anchor = event.event_id
            anchor_kind = "declared"
            anchor_node = event.node_id
            has_ownership_events = True
            continue

        if event.type != "ownership_stood_down":
            continue

        if _is_orphaned_standdown(event, declared_index):
            continue  # orphaned on-behalf stand-down — invisible (DESIGN O.5)

        has_ownership_events = True
        anchor = event.event_id
        anchor_kind = "stood_down"
        anchor_node = event.node_id

        if event.node_id == owner and event.payload.get("stood_down_from") == declaring_event_id:
            owner = None
            declaring_event_id = None

    return OwnerState(
        owner=owner,
        declaring_event_id=declaring_event_id,
        anchor=anchor,
        anchor_kind=anchor_kind,
        anchor_node=anchor_node,
        has_ownership_events=has_ownership_events,
    )


def resolve_legacy_owner(
    events: list[Event], project_id: str, claimant: str
) -> tuple[str, str] | None:
    """Return the OQ-1 legacy prior owner for `project_id`, or `None` (DESIGN O.13 OQ-1).

    Only meaningful when `fold_owner(...).has_ownership_events` is `False`:
    under the literal read rule, a project with zero ownership events has no
    owner at all, so its **first** claim would need no coordination — even
    when FR-28's own legacy rule already names an active `relayed`/`manual`
    `pm` registration as "the owner" today. The operator's ruling of
    2026-09-26 treats that registration as a `legacy` prior owner for the coordination
    check only; `fold_owner` itself stays pure and untouched by this
    function (O-NFR-3).

    PM ruling (2026-09-26, C1): the legacy prior owner is the newest
    `relayed`/`manual` `pm` registration by a node OTHER than `claimant`. A
    successor must coordinate with a prior (other) owner, never with itself
    — so `claimant`'s own registration is never eligible here, even when it
    is the newest one. If excluding it leaves no eligible registration, this
    returns `None` (the claim is `CLAIM_UNOWNED`, not "coordinate with
    yourself").

    Args:
        events: event-log entries, e.g. as read by `core.events.read_all`.
        project_id: the project to resolve a legacy owner for.
        claimant: the node about to claim; its own registrations are
            excluded from consideration.

    Returns:
        tuple[str, str] | None: `(node_id, event_id)` of the newest
        `relayed`/`manual` `session_registered` event written by `pm`, by a
        node other than `claimant`, for this project — unless a later valid
        `ownership_stood_down` names that registration's `event_id` as
        `stood_down_from` (already vacated; an orphaned on-behalf stand-down
        does not count, per `_is_orphaned_standdown` — the same predicate
        `fold_owner` uses, I1) — in which case, or if no such registration
        exists, `None`.
    """
    registration: Event | None = None
    for event in events:
        if event.project_id != project_id or event.type != "session_registered":
            continue
        if event.node_id == claimant:
            continue  # C1: a claimant is never its own legacy prior owner
        if event.payload.get("origination") not in ("relayed", "manual"):
            continue
        if event.writer_role.strip().lower() != "pm":
            continue
        registration = event  # last one wins — newest by log order

    if registration is None:
        return None

    declared_index = _build_declared_index(events, project_id)
    for event in events:
        if event.project_id != project_id or event.type != "ownership_stood_down":
            continue
        if _is_orphaned_standdown(event, declared_index):
            continue  # an orphan vacates nothing (shared with fold_owner, I1)
        if event.payload.get("stood_down_from") == registration.event_id:
            return None  # already vacated — no legacy owner remains

    return (registration.node_id, registration.event_id)


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    """The pure decision `decide_claim` reaches for one claim attempt (DESIGN O.5 step 3).

    Attributes:
        outcome: one of `NOOP_ALREADY_OWNER`, `CLAIM_UNOWNED`, `CLAIM_OVER`,
            `REFUSE_COORDINATION_REQUIRED`, `REFUSE_ATTESTATION_MISMATCH`.
        prior_owner: the displaced (or refused-against) owner's `node_id`,
            or `None` for `NOOP_ALREADY_OWNER`/`CLAIM_UNOWNED`.
        basis: `"unowned"`/`"notified"`/`"archived"`/`"deleted"` for
            `CLAIM_UNOWNED`/`CLAIM_OVER`; `None` otherwise.
        prior_owner_kind: `"none"`/`"declared"`/`"legacy"` — always set for
            `CLAIM_UNOWNED`/`CLAIM_OVER`/the two `REFUSE_*` outcomes; `None`
            for `NOOP_ALREADY_OWNER`.
        prior_owner_registration_event_id: for a `"legacy"` prior owner, the
            `event_id` of its `session_registered` event (the on-behalf
            stand-down's `stood_down_from`); `None` otherwise.
    """

    outcome: str
    prior_owner: str | None = None
    basis: str | None = None
    prior_owner_kind: str | None = None
    prior_owner_registration_event_id: str | None = None


def decide_claim(
    state: OwnerState,
    claimant: str,
    liveness: str,
    attested_owner: str | None,
    *,
    legacy: tuple[str, str] | None = None,
) -> ClaimDecision:
    """Decide the outcome of `claimant` claiming a project in `state` (O-FR-3/5, DESIGN O.5 step 3).

    Pure — makes no I/O and takes no lock; `core.project_owner.claim` calls
    this against a fresh snapshot on each (re-)evaluation.

    Args:
        state: the project's current `fold_owner` snapshot.
        claimant: the claiming node's `node_id`.
        liveness: the prior owner's liveness — `"active"`, `"unknown"`,
            `"archived"`, or `"deleted"` (O-FR-5: `"unknown"` counts as
            active — coordination is still required). Ignored when there is
            no prior owner.
        attested_owner: the `node_id` the caller attests it notified, or
            `None` if no attestation was given.
        legacy: the OQ-1 legacy prior owner (`resolve_legacy_owner`'s
            result), consulted only when `state.owner is None` — i.e. the
            project has literally no `ownership_declared` event yet.

    Returns:
        ClaimDecision: see the class docstring. `owned-by-self` always
        short-circuits to `NOOP_ALREADY_OWNER` regardless of `liveness`/
        `attested_owner`; `unowned` (no declared and no legacy owner) always
        short-circuits to `CLAIM_UNOWNED` the same way — DESIGN's own
        "regardless of" cells in the outcome matrix.
    """
    if state.owner == claimant:
        return ClaimDecision(outcome=NOOP_ALREADY_OWNER)

    if state.owner is not None:
        prior_owner = state.owner
        prior_owner_kind = "declared"
        prior_registration_event_id = None
    elif legacy is not None:
        prior_owner, prior_registration_event_id = legacy
        prior_owner_kind = "legacy"
    else:
        return ClaimDecision(outcome=CLAIM_UNOWNED, basis="unowned", prior_owner_kind="none")

    if liveness in _LIVENESS_ENDS_OWNERSHIP:
        return ClaimDecision(
            outcome=CLAIM_OVER,
            prior_owner=prior_owner,
            basis=liveness,
            prior_owner_kind=prior_owner_kind,
            prior_owner_registration_event_id=prior_registration_event_id,
        )

    if attested_owner is None:
        return ClaimDecision(
            outcome=REFUSE_COORDINATION_REQUIRED,
            prior_owner=prior_owner,
            prior_owner_kind=prior_owner_kind,
        )
    if attested_owner != prior_owner:
        return ClaimDecision(
            outcome=REFUSE_ATTESTATION_MISMATCH,
            prior_owner=prior_owner,
            prior_owner_kind=prior_owner_kind,
        )

    return ClaimDecision(
        outcome=CLAIM_OVER,
        prior_owner=prior_owner,
        basis="notified",
        prior_owner_kind=prior_owner_kind,
        prior_owner_registration_event_id=prior_registration_event_id,
    )


def _is_registered(events: list[Event], project_id: str, node_id: str) -> bool:
    """Return whether `node_id` has any `session_registered` event in `project_id`.

    Args:
        events: event-log entries, e.g. as read by `core.events.read_all`.
        project_id: the project to check.
        node_id: the node to check.

    Returns:
        bool: True iff at least one `session_registered` event for
        `node_id` exists in `project_id` (registration is monotonic, so an
        unlocked check against a `read_all` snapshot is sound — DESIGN O.5
        step 2).
    """
    return any(
        e.project_id == project_id and e.type == "session_registered" and e.node_id == node_id
        for e in events
    )


def _build_claim_event(
    *,
    project_id: str,
    claimant: str,
    writer_role: str,
    decision: ClaimDecision,
    anchor: str,
    liveness: str,
    liveness_source: str,
    attested_owner: str | None,
    notified_via: str | None,
) -> dict[str, Any]:
    """Build the raw `ownership_declared` event dict for a decided claim (O.1).

    Args:
        project_id: the project being claimed.
        claimant: the claiming node's `node_id`.
        writer_role: the writer role recording this event (`pm`, or `cto`
            when writing on the claimant's behalf).
        decision: the `decide_claim` result this claim is built from; must
            be `CLAIM_UNOWNED` or `CLAIM_OVER`.
        anchor: the ownership state's anchor this claim is decided against
            (the key's `:after:<anchor>` suffix).
        liveness: the prior owner's liveness, as passed to `decide_claim`.
        liveness_source: `"sessions-json"` or `"not-supplied"`.
        attested_owner: the attested node id, or `None`.
        notified_via: the attestation channel text, required iff
            `attested_owner` is given.

    Returns:
        dict[str, Any]: a raw event dict ready for `core.events.append_batch`
        (validated there, not here).
    """
    attestation = None
    if attested_owner is not None:
        attestation = {"notified_owner": attested_owner, "notified_via": notified_via}

    return {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": f"own:{project_id}:{claimant}:after:{anchor}",
        "ts": _now_iso(),
        "type": "ownership_declared",
        "project_id": project_id,
        "node_id": claimant,
        "writer_role": writer_role,
        "payload": {
            "claim_id": str(uuid4()),
            "anchor": anchor,
            "prior_owner": decision.prior_owner,
            "prior_owner_kind": decision.prior_owner_kind or "none",
            "basis": decision.basis,
            "prior_owner_liveness": liveness if decision.prior_owner is not None else None,
            "liveness_source": liveness_source,
            "attestation": attestation,
        },
    }


def _build_standdown_event(
    *,
    project_id: str,
    node: str,
    writer_role: str,
    stood_down_from: str,
    written_by: str,
    basis: str,
    claim_event: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build a raw `ownership_stood_down` event dict (O.1).

    Args:
        project_id: the owning project.
        node: the node standing down (the prior owner).
        writer_role: the writer role recording this event.
        stood_down_from: `event_id` of the `ownership_declared` (or, for a
            legacy owner, the `session_registered`) event being ended.
        written_by: `"self"` or `"claimant"`.
        basis: `"self"`, `"notified"`, `"archived"`, or `"deleted"`.
        claim_event: the paired claim event dict, required when
            `written_by == "claimant"` — supplies `claimant`/`claim_key` and
            the key's own `:by:<claimant>` suffix.
        reason: optional free-text reason (self stand-down only, by
            convention; not enforced here).

    Returns:
        dict[str, Any]: a raw event dict ready for `core.events.append_batch`.
    """
    if written_by == "claimant":
        assert claim_event is not None  # noqa: S101 - internal invariant, not user input
        claimant = claim_event["node_id"]
        claim_key = claim_event["idempotency_key"]
        key = f"standdown:{project_id}:{node}:from:{stood_down_from}:by:{claimant}"
    else:
        claimant = None
        claim_key = None
        key = f"standdown:{project_id}:{node}:from:{stood_down_from}:self"

    return {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": key,
        "ts": _now_iso(),
        "type": "ownership_stood_down",
        "project_id": project_id,
        "node_id": node,
        "writer_role": writer_role,
        "payload": {
            "stood_down_from": stood_down_from,
            "written_by": written_by,
            "basis": basis,
            "claimant": claimant,
            "claim_key": claim_key,
            "reason": reason,
        },
    }


def _find_event(events: list[Event], idempotency_key: str) -> Event | None:
    """Return the event in `events` with `idempotency_key`, or `None`.

    Args:
        events: event-log entries.
        idempotency_key: the key to look up.

    Returns:
        Event | None: the matching event, or `None` if not present.
    """
    for event in events:
        if event.idempotency_key == idempotency_key:
            return event
    return None


def claim(
    log_path: Path | str,
    *,
    project_id: str,
    claimant: str,
    writer_role: str,
    liveness: str = "unknown",
    liveness_source: str = "not-supplied",
    attested_owner: str | None = None,
    notified_via: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    retry_interval: float = _DEFAULT_RETRY_INTERVAL,
) -> Event | None:
    """Claim ownership of `project_id` on behalf of `claimant` (O-FR-1/3/4/6).

    Reads the log unlocked, decides (`decide_claim`), then writes under
    `core.events.append_batch`'s single lock hold with a precondition
    pinning the exact `OwnerState` the decision was based on — if the state
    changed under the lock, `PreconditionUnmet` triggers a bounded
    re-evaluation against the fresh state (at most `_MAX_REEVALUATIONS`
    times), per DESIGN O.5 step 6.

    Args:
        log_path: path to the project's event log.
        project_id: the project to claim.
        claimant: the claiming node's `node_id`.
        writer_role: `"pm"`, or `"cto"` when writing on the claimant's
            behalf (Decision O2 — must be in the ownership-event allowlist).
        liveness: the prior owner's liveness, if any — `"active"`,
            `"unknown"` (the default — conservative when the caller has not
            resolved liveness), `"archived"`, or `"deleted"`.
        liveness_source: `"sessions-json"` or `"not-supplied"` (default) —
            recorded on the event for audit, not interpreted here.
        attested_owner: the `node_id` the caller attests it notified, or
            `None`.
        notified_via: required together with `attested_owner`.
        timeout: seconds to keep retrying lock acquisition per attempt.
        retry_interval: seconds to sleep between lock-acquisition retries.

    Returns:
        Event | None: the appended `ownership_declared` event, or `None` if
        the claimant already owns the project (`NOOP_ALREADY_OWNER`) — a
        safe no-op, not an error.

    Raises:
        OwnershipRefused: `code="not_registered"` if `claimant` has no
            `session_registered` event in this project; `code=
            "coordination_required"` if an active prior owner exists with no
            attestation; `code="attestation_mismatch"` if the attestation
            names a node other than the current owner. Nothing is written.
        OwnershipContended: if the ownership state changed under the lock on
            every one of `_MAX_REEVALUATIONS` retries. Nothing is written.
        ValidationError, OwnershipError, LockTimeoutError: as raised by
            `core.events.append_batch`.
    """
    for _ in range(_MAX_REEVALUATIONS + 1):
        events = read_all(log_path)

        if not _is_registered(events, project_id, claimant):
            raise OwnershipRefused("not_registered", current_owner=None)

        state = fold_owner(events, project_id)
        legacy = (
            None
            if state.has_ownership_events
            else resolve_legacy_owner(events, project_id, claimant)
        )
        decision = decide_claim(state, claimant, liveness, attested_owner, legacy=legacy)

        if decision.outcome == NOOP_ALREADY_OWNER:
            return None
        if decision.outcome == REFUSE_COORDINATION_REQUIRED:
            raise OwnershipRefused("coordination_required", current_owner=decision.prior_owner)
        if decision.outcome == REFUSE_ATTESTATION_MISMATCH:
            raise OwnershipRefused("attestation_mismatch", current_owner=decision.prior_owner)

        claim_event = _build_claim_event(
            project_id=project_id,
            claimant=claimant,
            writer_role=writer_role,
            decision=decision,
            anchor=state.anchor,
            liveness=liveness,
            liveness_source=liveness_source,
            attested_owner=attested_owner,
            notified_via=notified_via,
        )

        batch = []
        if decision.outcome == CLAIM_OVER:
            stood_down_from = (
                decision.prior_owner_registration_event_id
                if decision.prior_owner_kind == "legacy"
                else state.declaring_event_id
            )
            batch.append(
                _build_standdown_event(
                    project_id=project_id,
                    node=decision.prior_owner,
                    writer_role=writer_role,
                    stood_down_from=stood_down_from,
                    written_by="claimant",
                    basis=decision.basis,
                    claim_event=claim_event,
                )
            )
        batch.append(claim_event)

        def _precondition(
            existing: list[Event],
            _state: OwnerState = state,
            _legacy: tuple[str, str] | None = legacy,
        ) -> bool:
            """Return whether `existing` still matches the decision's snapshot.

            Always re-checks `fold_owner`. When the decision was made against
            an unowned project (`not _state.has_ownership_events`), it ALSO
            re-checks `resolve_legacy_owner` (I2) — `fold_owner` alone cannot
            see a `relayed`/`manual` `pm` registration by another node
            landing between the unlocked read and this locked write, so
            without this second check a stale `CLAIM_UNOWNED` (or a stale
            legacy owner) could still be written.
            """
            if fold_owner(existing, project_id) != _state:
                return False
            if not _state.has_ownership_events:
                return resolve_legacy_owner(existing, project_id, claimant) == _legacy
            return True

        try:
            written = append_batch(
                log_path, batch, timeout=timeout, retry_interval=retry_interval, precondition=_precondition
            )
        except PreconditionUnmet:
            continue

        if not written:
            # A retry of this exact call (e.g. the CLI's own bounded-retry
            # wrapper around a lock timeout — that wrapper lives in the CLI,
            # increment O2, not here): every event in the batch already
            # exists — look our own claim event up by key.
            return _find_event(read_all(log_path), claim_event["idempotency_key"])

        # `_find_event` (not a bare `next(...)`, M4). If `written` somehow
        # lacks our claim (the claim half was already present), fall back to
        # the log rather than return `None`, which is reserved for the
        # already-owner no-op.
        return _find_event(written, claim_event["idempotency_key"]) or _find_event(
            read_all(log_path), claim_event["idempotency_key"]
        )

    raise OwnershipContended(
        f"claim of {project_id!r} by {claimant!r} exceeded {_MAX_REEVALUATIONS} "
        "re-evaluations against a changing ownership state"
    )


def stand_down(
    log_path: Path | str,
    *,
    project_id: str,
    node: str,
    writer_role: str,
    reason: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    retry_interval: float = _DEFAULT_RETRY_INTERVAL,
) -> Event | None:
    """Stand `node` down as `project_id`'s owner (O-FR-2).

    Args:
        log_path: path to the project's event log.
        project_id: the project to stand down from.
        node: the standing-down node's `node_id`; must be the current owner.
        writer_role: `"pm"`, or `"cto"` (Decision O2 allowlist).
        reason: optional single-line free-text reason.
        timeout: seconds to keep retrying lock acquisition per attempt.
        retry_interval: seconds to sleep between lock-acquisition retries.

    Returns:
        Event | None: the appended `ownership_stood_down` event, or `None`
        if `node` had already stood down (the newest valid ownership event
        for the project is already `node`'s own stand-down) — a safe no-op.

    Raises:
        OwnershipRefused: `code="not_current_owner"` if `node` is not the
            project's current owner and has not already stood down. Nothing
            is written.
        OwnershipContended: if the ownership state changed under the lock on
            every one of `_MAX_REEVALUATIONS` retries. Nothing is written.
        ValidationError, OwnershipError, LockTimeoutError: as raised by
            `core.events.append_batch`.
    """
    for _ in range(_MAX_REEVALUATIONS + 1):
        events = read_all(log_path)
        state = fold_owner(events, project_id)

        if state.owner != node:
            if state.anchor_kind == "stood_down" and state.anchor_node == node:
                return None  # already stood down — safe no-op
            raise OwnershipRefused("not_current_owner", current_owner=state.owner)

        event_dict = _build_standdown_event(
            project_id=project_id,
            node=node,
            writer_role=writer_role,
            stood_down_from=state.declaring_event_id,
            written_by="self",
            basis="self",
            reason=reason,
        )

        def _precondition(existing: list[Event], _state: OwnerState = state) -> bool:
            """Return whether `existing`'s ownership state still matches the decision's snapshot."""
            return fold_owner(existing, project_id) == _state

        try:
            written = append_batch(
                log_path,
                [event_dict],
                timeout=timeout,
                retry_interval=retry_interval,
                precondition=_precondition,
            )
        except PreconditionUnmet:
            continue

        if not written:
            return _find_event(read_all(log_path), event_dict["idempotency_key"])
        return written[0]

    raise OwnershipContended(
        f"stand_down of {project_id!r} by {node!r} exceeded {_MAX_REEVALUATIONS} "
        "re-evaluations against a changing ownership state"
    )
