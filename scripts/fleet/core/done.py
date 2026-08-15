"""Evidence-backed ``done_level`` state machine + D-ceiling (CC-5/FR-6, DP#5).

The done-ladder is a strict total order — ``D0-code < D1-merged < D2-test <
D3-uat < D4-prod`` — and ``advance()`` is the sole write path onto it. Every
rejection is a deterministic function of `(current level, evidence, ceiling,
approver)`; nothing here calls, imports, or infers from a model (DP#5 — this
module must be callable and fully testable with no model in the loop; see
``tests/fleet/test_done.py``'s AST guard, TC-32). This module imports nothing
from ``store``, ``projection``, or ``adapter`` — the node's current level is
derived from the event log directly (`core.events.read_all`), never from a
fragment or an adapter-supplied fact, keeping ``core/`` harness-neutral
(NFR-2).

**Transition rules (G4-approved, TEST.md Chunk 5):**

- **Single-rung forward only.** ``target`` must equal ``current + 1`` on the
  ladder. Backward moves (a G4 decision) and same-level "advances" are both
  rejected — a rollback or a re-affirmation of the current level is never
  recorded as a ``done_level`` decrement/no-op; it is simply not a legal
  input to this function. Skip-level forward moves are rejected regardless
  of evidence.
- **Evidence gates.** ``D1-merged`` requires merge evidence (a ``commit`` or
  ``pr`` reference in ``evidence``). ``D2-test`` requires **both** deploy
  evidence (``deploy_id``) and test evidence (``ci_run``) — a conjunction;
  either alone is rejected.
- **Human-approver gate.** ``D3-uat`` and ``D4-prod`` require a recorded
  ``approver`` that is present, non-blank, and not model/vendor-shaped
  (reuses ``core.schema.is_model_vendor_name`` — the same judgment
  ``writer_role`` is held to, NFR-6). This is deterministic string
  classification, not an attempt to infer whether a name genuinely belongs
  to a human; see TC-28's own note and this chunk's report to PM for the
  concern that flags.
- **D-ceiling.** ``ceiling`` is a plain parameter — the caller (the CLI)
  resolves it from the operator's profile before calling ``advance()``;
  nothing here reads a profile file. Advancing above ``ceiling`` is
  rejected even with full evidence and a human approver.

**No retry-idempotency, by design (TC-30).** Every other ``append()`` caller
in this package (`core.edges.add_edge`, `handoff.self_register`) treats a
repeat of an already-recorded write as a safe no-op via the shared
``idempotency_key`` dedupe. ``advance()`` cannot offer that for a *sequential*
repeat of an already-completed transition, because TC-30 requires a
same-level attempt (e.g. a node already at ``D2-test`` calling
``advance(..., "D2-test", ...)`` again) to be *rejected*, not silently
absorbed — and, in this strictly linear ladder, "the target level is already
the current level" and "an event for this exact `(node, target)` pair
already exists" are the same condition. Resolving that in TC-30's favor means
the current-level/adjacency check runs and rejects **before** ``append()``'s
own idempotency dedupe ever gets a chance to short-circuit it (see
``_reject_unless_single_rung_forward``, called both as a pre-lock fast-fail
and, defensively, inside ``append()``'s ``precondition`` under the lock).
Concurrent racers attempting the *same* legitimate next step still dedupe
safely against each other (they share the same idempotency key and the
loser's write is a genuine no-op) — only a *sequential* repeat by one caller
is turned into a rejection. Flagged as a deliberate, TEST.md-driven deviation
from the sibling modules' retry-idempotency convention, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from .errors import DonePolicyError, PreconditionUnmet
from .events import append, read_all
from .schema import Event, is_model_vendor_name

#: The done-ladder, in strict ascending order (Decision F / FR-6).
DONE_LEVELS: Final[tuple[str, ...]] = (
    "D0-code",
    "D1-merged",
    "D2-test",
    "D3-uat",
    "D4-prod",
)

#: Ladder position lookup, e.g. `_LEVEL_INDEX["D2-test"] == 2`.
_LEVEL_INDEX: Final[dict[str, int]] = {level: i for i, level in enumerate(DONE_LEVELS)}

#: `evidence` keys that count as "merge evidence" for the D1-merged gate —
#: either alone is sufficient (a commit reference or a PR reference).
_MERGE_EVIDENCE_KEYS: Final[tuple[str, ...]] = ("commit", "pr")

#: `evidence` keys for the D2-test gate — both are required (a conjunction).
_DEPLOY_EVIDENCE_KEY: Final[str] = "deploy_id"
_TEST_EVIDENCE_KEY: Final[str] = "ci_run"

#: Levels whose gate requires a recorded human approver (FR-6).
_APPROVER_REQUIRED_LEVELS: Final[frozenset[str]] = frozenset({"D3-uat", "D4-prod"})


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix.

    Returns:
        str: e.g. `"2026-08-14T12:00:00.000000Z"`.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    """The outcome of `advance()`.

    Attributes:
        status: `"advanced"` (a new `done_level_advanced` event was
            appended) or `"deduped"` (an event with this exact `(node_id,
            target_level)` idempotency key already existed — a genuine
            concurrent-race no-op, not an error; see the module docstring's
            "No retry-idempotency" note for why a *sequential* repeat never
            reaches this path).
        node_id: the node this call targeted.
        level: the target level this call attempted to record.
    """

    status: str
    node_id: str
    level: str


def _validate_known_level(value: str, what: str) -> None:
    """Raise ValueError unless `value` is a recognized done-ladder level.

    Args:
        value: the candidate level string.
        what: noun used in the error message (e.g. `"target_level"`).

    Raises:
        ValueError: if `value` is not one of `DONE_LEVELS`.
    """
    if value not in _LEVEL_INDEX:
        raise ValueError(f"{what} {value!r} is not a recognized done_level (expected one of {DONE_LEVELS})")


def _has_merge_evidence(evidence: dict[str, Any]) -> bool:
    """Return whether `evidence` satisfies the D1-merged gate.

    Args:
        evidence: the candidate evidence dict.

    Returns:
        bool: True iff a non-blank `commit` or `pr` value is present.
    """
    return any(bool(evidence.get(key)) for key in _MERGE_EVIDENCE_KEYS)


def _has_deploy_and_test_evidence(evidence: dict[str, Any]) -> bool:
    """Return whether `evidence` satisfies the D2-test gate (a conjunction).

    Args:
        evidence: the candidate evidence dict.

    Returns:
        bool: True iff **both** a non-blank `deploy_id` and a non-blank
        `ci_run` value are present — either alone is insufficient (FR-6).
    """
    return bool(evidence.get(_DEPLOY_EVIDENCE_KEY)) and bool(evidence.get(_TEST_EVIDENCE_KEY))


def _is_human_approver(approver: str | None) -> bool:
    """Return whether `approver` satisfies the D3-uat/D4-prod human-approver gate.

    Reuses `core.schema.is_model_vendor_name` — the same deterministic
    "looks like a model/vendor name, not a role/human" judgment
    `writer_role` is held to (NFR-6) — so this module has no second
    denylist to drift out of sync with `schema.py`'s. This is a string
    classification, not a genuine human/bot determination; see the module
    docstring.

    Args:
        approver: the candidate approver identity, or `None`.

    Returns:
        bool: True iff `approver` is a non-blank string that does not match
        the model/vendor denylist.
    """
    if approver is None:
        return False
    return not is_model_vendor_name(approver)


def _current_level(events: list[Event], node_id: str) -> str:
    """Return `node_id`'s current `done_level`, from a list of log events.

    Args:
        events: event-log entries, e.g. as read by `core.events.read_all`.
        node_id: the node to compute the current level for.

    Returns:
        str: the `done_level` payload of the *last* `done_level_advanced`
        event for `node_id` in `events` (append order — later entries win);
        `"D0-code"` if none is found, matching `core.projection`'s own
        default for a freshly registered node. A malformed payload (missing
        or unrecognized `done_level` value) is skipped defensively rather
        than raised — the same read-time tolerance `core.edges` applies to
        its own event payloads (G5 F3) — since every event that reached the
        log through `advance()` itself always carries a recognized level;
        only a directly-forged log entry could trigger this branch.
    """
    current = "D0-code"
    for event in events:
        if event.type != "done_level_advanced" or event.node_id != node_id:
            continue
        level = event.payload.get("done_level")
        if level in _LEVEL_INDEX:
            current = level
    return current


def _reject_unless_single_rung_forward(current: str, target: str, node_id: str) -> None:
    """Raise DonePolicyError unless `target` is exactly one rung above `current`.

    The sole adjacency rule for the whole ladder (TC-30): a skip-level
    forward move, a backward move, and a same-level "advance" are all
    rejected by this one check — there is no separate backward-specific or
    same-level-specific code path, since all three reduce to "not exactly
    `current + 1`" on a strict total order.

    Args:
        current: the node's current `done_level`.
        target: the level `advance()` was asked to move to.
        node_id: the node this check is for (error-message context only).

    Raises:
        DonePolicyError: if `target`'s ladder position is not exactly one
            past `current`'s.
    """
    if _LEVEL_INDEX[target] != _LEVEL_INDEX[current] + 1:
        raise DonePolicyError(
            f"{node_id}: cannot advance from {current!r} to {target!r} — the "
            "done_level ladder only allows a single rung forward at a time "
            "(skip-level, backward, and same-level transitions are all "
            "rejected, regardless of evidence)"
        )


def advance(
    node_id: str,
    target_level: str,
    *,
    evidence: dict[str, Any] | None,
    approver: str | None,
    ceiling: str,
    project_id: str,
    writer_role: str,
    log_path: Path | str,
) -> AdvanceResult:
    """Advance `node_id` to `target_level` on the done-ladder (FR-6, DP#5).

    Appends a `done_level_advanced` event via `core.events.append`,
    idempotency key `done:<node_id>:<target_level>`. Every rejection below
    is checked, and raises, **before** anything is persisted (NFR-7) — a
    rejected call never appends anything, including no partial/placeholder
    event.

    Checked in this order (cheapest/most input-only first): level/ceiling
    values are recognized ladder rungs; `target_level` does not exceed
    `ceiling`; `target_level`'s evidence gate (D1-merged: merge evidence;
    D2-test: deploy+test evidence, a conjunction); `target_level`'s
    human-approver gate (D3-uat/D4-prod); and finally the current-level
    adjacency check (`_reject_unless_single_rung_forward`) — computed fresh
    from the event log both as a pre-lock fast-fail and, defensively, again
    inside `append()`'s `precondition`, evaluated under the lock against the
    log exactly as `core.edges.add_edge` and `handoff.self_register` do for
    their own atomic under-lock checks. See the module docstring's "No
    retry-idempotency" note for why the pre-lock check runs before
    `append()`'s own idempotency dedupe, not after.

    Args:
        node_id: the session/node id to advance.
        target_level: the `done_level` to advance to; must be one of
            `DONE_LEVELS`.
        evidence: evidence fields backing this transition (e.g. `commit`,
            `pr`, `deploy_id`, `ci_run`). `None` is treated as `{}`.
        approver: the recorded approver's identity, or `None`. Only checked
            (and required to be human) for `target_level in
            {"D3-uat", "D4-prod"}`; ignored otherwise, but still recorded on
            the event payload if given.
        ceiling: the project's D-ceiling — the highest `done_level` this
            project may ever reach. Caller-supplied (the CLI resolves it
            from the operator's profile, per DP#5 — this module never reads
            a profile itself); must be one of `DONE_LEVELS`.
        project_id: the owning project's stable id.
        writer_role: a role name, never a model/vendor string (NFR-6).
            `done_level` is a `"shared"` field (`core.schema.FIELD_OWNERS`)
            — any valid non-model role may write it.
        log_path: path to the project's event log.

    Returns:
        AdvanceResult: see the class docstring for the status vocabulary.

    Raises:
        ValueError: if `target_level` or `ceiling` is not one of
            `DONE_LEVELS`.
        DonePolicyError: for a past-ceiling attempt, an insufficient
            evidence gate, a missing/non-human approver where required, or
            a non-single-rung-forward transition (skip-level, backward, or
            same-level). Nothing is written on any of these.
        ValidationError: if the built event fails schema validation.
        OwnershipError: if `writer_role` may not write `done_level`.
        LockTimeoutError: if the shared log lock could not be acquired in
            time. Nothing was written.
    """
    _validate_known_level(target_level, "target_level")
    _validate_known_level(ceiling, "ceiling")

    evidence_dict = dict(evidence or {})

    if _LEVEL_INDEX[target_level] > _LEVEL_INDEX[ceiling]:
        raise DonePolicyError(
            f"{node_id}: cannot advance to {target_level!r} — past the "
            f"project's D-ceiling ({ceiling!r})"
        )

    if target_level == "D1-merged" and not _has_merge_evidence(evidence_dict):
        raise DonePolicyError(
            f"{node_id}: cannot advance to D1-merged without merge evidence "
            f"(a non-blank {_MERGE_EVIDENCE_KEYS[0]!r} or {_MERGE_EVIDENCE_KEYS[1]!r})"
        )

    if target_level == "D2-test" and not _has_deploy_and_test_evidence(evidence_dict):
        raise DonePolicyError(
            f"{node_id}: cannot advance to D2-test without BOTH deploy "
            f"evidence ({_DEPLOY_EVIDENCE_KEY!r}) and test evidence "
            f"({_TEST_EVIDENCE_KEY!r}) — either alone is insufficient"
        )

    if target_level in _APPROVER_REQUIRED_LEVELS and not _is_human_approver(approver):
        raise DonePolicyError(
            f"{node_id}: cannot advance to {target_level!r} without a "
            "recorded human approver (missing, blank, or a model/vendor-"
            "shaped identity are all rejected)"
        )

    current = _current_level(read_all(log_path), node_id)
    _reject_unless_single_rung_forward(current, target_level, node_id)

    event_dict: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "idempotency_key": f"done:{node_id}:{target_level}",
        "ts": _now_iso(),
        "type": "done_level_advanced",
        "project_id": project_id,
        "node_id": node_id,
        "writer_role": writer_role,
        "payload": {
            "done_level": target_level,
            "evidence": evidence_dict,
            "approver": approver,
        },
    }

    result_holder: dict[str, str] = {}

    def _precondition(existing: list[Event]) -> bool:
        fresh_current = _current_level(existing, node_id)
        result_holder["current"] = fresh_current
        return _LEVEL_INDEX[target_level] == _LEVEL_INDEX[fresh_current] + 1

    try:
        appended = append(log_path, event_dict, precondition=_precondition)
    except PreconditionUnmet:  # pragma: no cover
        # Provably unreachable given this module's own invariants, not
        # merely untested: every level on this ladder can only ever be
        # reached by writing exactly one rung at a time, so *any* history
        # that moved `node_id` past this call's pre-lock-checked `current`
        # necessarily already wrote the event with idempotency key
        # `done:<node_id>:<target_level>` (the intermediate rung this exact
        # call is trying to write) as part of getting there. That means a
        # racing writer that changes `current` between this call's pre-lock
        # check (above) and its `append()` always collides with this call's
        # own idempotency key first — `append()`'s dedupe short-circuits
        # before `precondition` ever runs, returning `None`, not raising
        # `PreconditionUnmet` (see the `appended is None` branch below).
        # This handler exists anyway, matching `core.edges.add_edge` /
        # `handoff.self_register`'s own under-lock `precondition` pattern
        # (mirrored per this chunk's brief) as defense-in-depth against a
        # *future* change that relaxes the single-rung-forward invariant —
        # kept intentionally excluded from the 100%-branch-coverage target
        # rather than exercised by a contrived monkeypatch of dead code.
        _reject_unless_single_rung_forward(
            result_holder.get("current", current), target_level, node_id
        )
        raise DonePolicyError(
            f"{node_id}: precondition rejected advancing to {target_level!r}"
        )

    if appended is None:
        return AdvanceResult(status="deduped", node_id=node_id, level=target_level)

    return AdvanceResult(status="advanced", node_id=node_id, level=target_level)


def event_for(node_id: str, target_level: str, log_path: Path | str) -> Event | None:
    """Look up the persisted `done_level_advanced` event for one `(node, level)` pair.

    `AdvanceResult` deliberately carries no `Event` (its `event_id`/`ts` are
    non-deterministic, which would make the equality-based assertions used
    throughout `tests/fleet/test_done.py` — and any caller's own idempotent-
    retry check — brittle). A caller that needs the actual persisted event
    (e.g. `cli.py`'s `fleet done advance`, to hand to
    `core.projection.project_event` — `core/done.py` itself may not import
    `projection`, per the module docstring) looks it up here instead, by the
    same idempotency key `advance()` uses (`done:<node_id>:<target_level>`).
    Works identically whether `advance()`'s own call just wrote the event or
    deduped against one written earlier/by another writer.

    Args:
        node_id: the node the transition targeted.
        target_level: the `done_level` that was advanced to.
        log_path: path to the project's event log.

    Returns:
        Event | None: the matching `done_level_advanced` event, or `None`
        if no such transition was ever recorded (e.g. `advance()` for this
        exact pair never succeeded).
    """
    key = f"done:{node_id}:{target_level}"
    for event in read_all(log_path):
        if event.type == "done_level_advanced" and event.idempotency_key == key:
            return event
    return None
