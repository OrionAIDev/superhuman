"""Event and fragment dataclasses, ownership table, and schema validation.

The event log is the source of truth (one JSON object per line); a fragment is
a materialized per-session projection of it. Both are validated here before
anything is persisted (NFR-7). Per DESIGN "Decision F" and FR-5, status is
**decomposed into five orthogonal fields** — the schema has no single
collapsing enum, and a caller cannot smuggle one in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any, Final

from .errors import ValidationError

#: Required keys on every event line (NFR-6/NFR-7; project_id per G3-1).
REQUIRED_EVENT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "event_id",
        "idempotency_key",
        "ts",
        "type",
        "project_id",
        "node_id",
        "writer_role",
    }
)

#: All event-line keys the schema recognizes; anything else is rejected.
_ALLOWED_EVENT_FIELDS: Final[frozenset[str]] = REQUIRED_EVENT_FIELDS | {"payload"}

#: The full event-type vocabulary (DESIGN "Decision F" — event types).
EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "session_registered",
        "handoff_emitted",
        "handoff_launched",
        "handoff_cancelled",
        "handoff_expired",
        "lifecycle_changed",
        "block_changed",
        "review_changed",
        "done_level_advanced",
        "edge_declared",
        "edge_derived",
        "cycle_flagged",
        "orphan_flagged",
        "observation",
        "recommendation",
    }
)

#: The five decomposed status fields (FR-5) — never collapsed into one enum.
STATUS_FIELDS: Final[tuple[str, ...]] = (
    "lifecycle",
    "block_state",
    "review_state",
    "adoption_state",
    "done_level",
)

#: The done-ladder, in strict ascending order (Decision F / FR-6). G5 F2:
#: moved here from `core/done.py` — the valid-value vocabulary for
#: `done_level` is a schema concern (this module is the single source of
#: truth `validate_event` checks a `done_level_advanced` payload against),
#: and keeping it here avoids a `schema -> done` import cycle for
#: `fold_done_level` below. `core/done.py` and `core/projection.py` both
#: import this rather than redefining it.
DONE_LEVELS: Final[tuple[str, ...]] = (
    "D0-code",
    "D1-merged",
    "D2-test",
    "D3-uat",
    "D4-prod",
)

#: Ladder position lookup, e.g. `_LEVEL_INDEX["D2-test"] == 2`. Private —
#: `fold_done_level` is this module's only consumer; other modules that need
#: a level's ladder position (e.g. `core/done.py`'s ceiling/adjacency checks)
#: derive their own copy from the imported `DONE_LEVELS`, since it is cheap,
#: deterministic, derived data, not a second copy of the vocabulary itself.
_LEVEL_INDEX: Final[dict[str, int]] = {level: i for i, level in enumerate(DONE_LEVELS)}

#: Required keys on every fragment; matches STATUS_FIELDS plus identity.
_REQUIRED_FRAGMENT_FIELDS: Final[frozenset[str]] = frozenset(
    {"node_id", "project_id", *STATUS_FIELDS}
)
_ALLOWED_FRAGMENT_FIELDS: Final[frozenset[str]] = _REQUIRED_FRAGMENT_FIELDS

#: Per-field ownership (CC-6/FR-8). "shared" fields accept a write from either
#: class. Fields absent from this table are unowned/free (no restriction).
#: `observation`/`recommendation` are payload *kinds* (event types), not
#: fragment fields, but they are ownership-checked the same way (ceo-owned),
#: per DESIGN "core/ownership.py" responsibility and ARCHITECTURE item 5.
FIELD_OWNERS: Final[dict[str, str]] = {
    "lifecycle": "superhuman",
    "block_state": "superhuman",
    "review_state": "superhuman",
    "adoption_state": "ceo",
    "done_level": "shared",
    "observation": "ceo",
    "recommendation": "ceo",
}

#: writer_role denylist (NFR-6) — model/vendor names, never a role. Substring
#: match, case-insensitive, so "claude-3", "Claude Sonnet 5", "gpt-4" etc. are
#: all caught by their vendor/family stem.
_MODEL_VENDOR_DENYLIST: Final[tuple[str, ...]] = (
    "claude",
    "anthropic",
    "opus",
    "sonnet",
    "haiku",
    "gpt",
    "openai",
    "chatgpt",
    "gemini",
    "bard",
    "palm",
    "mistral",
    "cohere",
    "llama",
    "copilot",
    "llm",
)

_ISO_8601_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)


@dataclass(frozen=True, slots=True)
class Event:
    """One validated event-log line (append-only source of truth).

    Attributes:
        schema_version: event-schema version; v1 for Phase 1.
        event_id: uuid4 identifying this specific event line.
        idempotency_key: type-specific dedup anchor; a repeat append is a no-op.
        ts: ISO-8601 UTC timestamp string.
        type: one of `EVENT_TYPES`.
        project_id: stable project-grouping key, minted once at project init.
        node_id: namespaced `<harness>/<workspace>/<slug>/<local-session-id>`.
        writer_role: the role that wrote this event; never a model/vendor name.
        payload: event-type-specific data.
    """

    schema_version: int
    event_id: str
    idempotency_key: str
    ts: str
    type: str
    project_id: str
    node_id: str
    writer_role: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Fragment:
    """A materialized per-session projection of the event log (FR-5).

    Five status fields are independent axes — e.g. `lifecycle="active"` and
    `block_state="blocked"` simultaneously is a legal, non-contradictory state.
    There is deliberately no single collapsing `status` field: the dataclass
    has no such slot, so a caller cannot construct one (see
    `tests/fleet/test_schema.py::TestFiveDecomposedStatusFields`).

    Attributes:
        node_id: namespaced session id this fragment tracks.
        project_id: the project this session belongs to.
        lifecycle: intra-project execution lifecycle (superhuman-owned).
        block_state: blocked/unblocked axis (superhuman-owned).
        review_state: review axis (superhuman-owned).
        adoption_state: orphan/adoption axis (ceo-owned).
        done_level: evidence-backed deployment rung; state machine in Chunk 5
            (ceo/superhuman shared field — advancement rules live in
            `core/done.py`).
    """

    node_id: str
    project_id: str
    lifecycle: str
    block_state: str
    review_state: str
    adoption_state: str
    done_level: str


def _require_dict(data: Any, what: str) -> None:
    """Raise ValidationError unless `data` is a plain dict.

    Args:
        data: the candidate value.
        what: noun used in the error message ("event" or "fragment").

    Raises:
        ValidationError: if `data` is not a dict.
    """
    if not isinstance(data, dict):
        raise ValidationError(f"{what} must be a dict, got {type(data).__name__}")


def validate_event(data: dict[str, Any]) -> Event:
    """Validate a raw event dict and return a typed `Event`.

    Rejects malformed writes before anything is persisted (NFR-7): a missing
    required field, an unrecognized `type`, a non-ISO-8601 `ts`, an unknown
    key, or a `writer_role` that names a model/vendor instead of a role
    (NFR-6). Does not mutate `data`.

    Args:
        data: a raw event dict, e.g. as decoded from one JSONL line.

    Returns:
        Event: the validated, typed event.

    Raises:
        ValidationError: on any of the rejection conditions above.
    """
    _require_dict(data, "event")

    missing = REQUIRED_EVENT_FIELDS - data.keys()
    if missing:
        raise ValidationError(f"event missing required field(s): {sorted(missing)}")

    unknown = data.keys() - _ALLOWED_EVENT_FIELDS
    if unknown:
        raise ValidationError(f"event has unrecognized field(s): {sorted(unknown)}")

    event_type = data["type"]
    if event_type not in EVENT_TYPES:
        raise ValidationError(f"unknown event type: {event_type!r}")

    ts = data["ts"]
    if not isinstance(ts, str) or not _ISO_8601_RE.match(ts):
        raise ValidationError(f"ts is not a valid ISO-8601 timestamp: {ts!r}")

    writer_role = data["writer_role"]
    _assert_role_only(writer_role)

    for key in ("event_id", "idempotency_key", "project_id", "node_id"):
        if not isinstance(data[key], str) or not data[key]:
            raise ValidationError(f"{key} must be a non-empty string")

    if not isinstance(data["schema_version"], int):
        raise ValidationError("schema_version must be an int")

    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a dict")
    _assert_done_level_write_boundary(event_type, payload)
    _assert_payload_status_values_are_valid(payload)
    _assert_done_level_value_is_recognized(event_type, payload)

    return Event(
        schema_version=data["schema_version"],
        event_id=data["event_id"],
        idempotency_key=data["idempotency_key"],
        ts=ts,
        type=event_type,
        project_id=data["project_id"],
        node_id=data["node_id"],
        writer_role=writer_role,
        payload=dict(payload),
    )


def is_model_vendor_name(value: str) -> bool:
    """Return whether `value` looks like a model/vendor name, not a role/human (NFR-6).

    Case-insensitive substring match against `_MODEL_VENDOR_DENYLIST` — the
    same judgment `validate_event` applies to `writer_role`. Exposed
    publicly so other modules needing an identical "is this a model, not a
    human/role" check (e.g. `core/done.py`'s human-approver gate, FR-6)
    reuse this one source of truth rather than re-deriving the denylist.

    Args:
        value: the candidate string to classify.

    Returns:
        bool: True if `value` is not a non-empty string, or matches the
        denylist (looks like a model/vendor name); False otherwise.
    """
    if not isinstance(value, str) or not value.strip():
        return True
    lowered = value.lower()
    return any(token in lowered for token in _MODEL_VENDOR_DENYLIST)


def _assert_role_only(writer_role: Any) -> None:
    """Raise ValidationError if `writer_role` names a model/vendor (NFR-6).

    Args:
        writer_role: the candidate writer_role value.

    Raises:
        ValidationError: if not a non-empty string, or if it matches the
            model/vendor denylist.
    """
    if not isinstance(writer_role, str) or not writer_role.strip():
        raise ValidationError("writer_role must be a non-empty string")
    lowered = writer_role.lower()
    for token in _MODEL_VENDOR_DENYLIST:
        if token in lowered:
            raise ValidationError(
                f"writer_role {writer_role!r} looks like a model/vendor name, "
                f"not a role (matched {token!r}); NFR-6 requires a role string"
            )


def _assert_done_level_write_boundary(event_type: str, payload: dict[str, Any]) -> None:
    """Raise ValidationError if `payload` carries `done_level` on a non-advance event.

    G5 fix #1(a): `done_level` is the entry point to the evidence-gated
    done-ladder state machine (`core.done.advance()`, FR-6/DP#5) — every one
    of its rung invariants (single-rung-forward, evidence gates, human-
    approver gate, D-ceiling) is enforced ONLY inside `advance()`. Without
    this check, any sanctioned event of another type (e.g.
    `lifecycle_changed`) could carry `done_level` in its payload and, via
    `core.projection`'s generic `STATUS_FIELDS` fold, set a node's projected
    done_level directly — bypassing every one of those gates entirely, since
    `done_level` is a `"shared"` field in `FIELD_OWNERS` (either class may
    write it; ownership alone does not close this hole). `core/projection.py`
    additionally excludes `done_level` from its own generic fold as
    defense-in-depth (fix #1(b)), so the two checks must always agree.

    Scoped to `done_level` only — the same generic-fold bypass technically
    exists for the other four `STATUS_FIELDS` (lifecycle/block_state/
    review_state/adoption_state), but those are not evidence-gated; the
    general "which event types may write which status field" question is a
    tracked follow-up (G5 decision), not fixed here.

    Args:
        event_type: the event's `type`.
        payload: the event's payload dict.

    Raises:
        ValidationError: if `"done_level"` is a key in `payload` and
            `event_type` is not `"done_level_advanced"`.
    """
    if "done_level" in payload and event_type != "done_level_advanced":
        raise ValidationError(
            "payload key 'done_level' may only be set by a "
            "'done_level_advanced' event — core.done.advance() is the sole "
            f"write path onto the done-ladder (FR-6/DP#5); got type {event_type!r}"
        )


def _assert_payload_status_values_are_valid(payload: dict[str, Any]) -> None:
    """Raise ValidationError if a payload status field has an invalid value.

    An event's `payload` may set any of the five decomposed status fields
    (`STATUS_FIELDS`) directly (`core/projection.py` applies these on
    replay). A blank or non-string value here would still pass the envelope
    checks above, get appended to the log, and only fail later when
    `validate_fragment` rejects the resulting fragment on read — silently
    dropping the session from every query (`iter_fragments(skip_corrupt=True)`
    skips it, and `projection.rebuild()` would just regenerate the same
    broken fragment from the same bad event forever). Rejecting here means
    nothing bad is ever persisted in the first place (NFR-7), matching how
    every other malformed-write case in this function is handled.

    Args:
        payload: the event's payload dict (already confirmed to be a dict).

    Raises:
        ValidationError: if any key in `payload` that names one of
            `STATUS_FIELDS` is not a non-empty (post-strip) string.
    """
    for field in STATUS_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(
                f"payload status field {field!r} must be a non-empty string, "
                f"got {value!r}"
            )


def _assert_done_level_value_is_recognized(event_type: str, payload: dict[str, Any]) -> None:
    """Raise ValidationError if a `done_level_advanced` payload's value is unrecognized.

    G5 fix #F2: `payload["done_level"]` must be one of `DONE_LEVELS` when
    `event_type` is `"done_level_advanced"` — without this check, a
    schema-valid-but-unrecognized value like `"D9-bogus"` (a non-empty
    string, so it already passes `_assert_payload_status_values_are_valid`)
    would be accepted at the write boundary and only be caught later, if at
    all, by the read-side tolerance every fold in this package applies to
    malformed log entries (`fold_done_level` below; `core.done._current_level`;
    `core.edges`'s own "skip defensively, never raise on read" pattern).
    Rejecting here means nothing bad is ever persisted in the first place
    (NFR-7), matching every other malformed-write case `validate_event`
    already handles. `core.events.read_all` also runs `validate_event` on
    every line it reads, so a bogus value written directly to the log file
    (bypassing `core.events.append` entirely) is skipped on read the same
    way a torn/corrupt line is — it never becomes an `Event` at all, which
    is why `fold_done_level`'s own read-time tolerance for this case is
    unreachable except via a raw file write, not via anything that ever
    passed through this function.

    Args:
        event_type: the event's `type`.
        payload: the event's payload dict (already confirmed to be a dict).

    Raises:
        ValidationError: if `event_type == "done_level_advanced"` and
            `payload["done_level"]` is present but not in `DONE_LEVELS`.
    """
    if event_type != "done_level_advanced" or "done_level" not in payload:
        return
    value = payload["done_level"]
    if value not in DONE_LEVELS:
        raise ValidationError(
            f"done_level_advanced payload 'done_level' {value!r} is not a "
            f"recognized done_level (expected one of {DONE_LEVELS})"
        )


def fold_done_level(current_level: str, event: Event) -> str:
    """Return the `done_level` after folding one event onto `current_level` (G5 F1).

    The single shared read-time derivation rule both `core.done._current_level`
    and `core.projection` (`_fresh_fragment`/`_apply`) fold through — so a
    node's *projected* `done_level` and its *policy-computed* current level
    (what `core.done.advance()`'s adjacency check reads) can never disagree,
    no matter what is actually in the log. Mirrors `core.edges.resolve_graph`'s
    own "re-derive on read, don't trust what was written" philosophy applied
    to the done-ladder.

    `event` advances the level ONLY if all three hold: `event.type ==
    "done_level_advanced"`; `event.payload["done_level"]` is a recognized
    `DONE_LEVELS` value; and that value's ladder position is exactly
    `current_level`'s position + 1 (single-rung forward, the same adjacency
    rule `core.done.advance()` enforces at write time). Any other event
    — a different type, a missing/unrecognized `done_level`, or a
    non-adjacent (skip-level, backward, same-level) value — leaves
    `current_level` unchanged. This closes the hole a direct `append()` of a
    correctly-typed `done_level_advanced` event used to exploit: bypassing
    `advance()` entirely no longer bypasses the ladder's adjacency rule too,
    because the read side re-derives it independently rather than trusting
    the payload verbatim.

    **Residual (accepted, out of scope for this fix):** a deliberately
    fully-forged *adjacent* chain — separate direct-append `done_level_advanced`
    events for D0->D1->D2->D3->D4, each one rung past the last, with
    fabricated evidence/approver values that were never actually checked —
    still advances all the way to D4-prod on read, because this function has
    no way to re-verify at read time whether evidence was genuinely recorded
    or an approver was genuinely human; only `advance()` enforces those gates,
    and only at write time. This is equivalent to a raw file write bypassing
    every in-process check by definition (the same caveat `core.projection`'s
    own module docstring already carries for `project_event`'s ownership
    re-check) — a determined caller with direct log-file write access is out
    of this scope, not a gap this fold could plausibly close.

    **G5 fix #N2 — an unrecognized `current_level` never raises.** A cached
    fragment (or a legacy/corrupt one, read back from disk outside this
    process's own writes) could carry a `done_level` value that is not one
    of `DONE_LEVELS` — `validate_fragment` checks only that it is a
    non-empty string, not that it is a recognized ladder rung (see
    `schema.validate_fragment`). Looking that value up in `_LEVEL_INDEX`
    directly used to raise `KeyError`, crashing `core.projection.project_event`
    and violating this module's own "a corrupt fragment is never fatal"
    contract (mirrored from `core.projection`'s module docstring). An
    unrecognized `current_level` is now treated as the `"D0-code"` floor for
    the purpose of this one fold: a subsequent legitimate event that is
    adjacent to `"D0-code"` (i.e. `"D1-merged"`) still applies from that
    known base, and `current_level` is returned unchanged (not silently
    reset to `"D0-code"`) for any event that is not. `rebuild()` — which
    ignores cached fragments entirely and replays only the log — remains the
    full-recovery path back to a fully correct value; this fix's guarantee
    is narrower and cheaper: never crash, never propagate the corrupt value
    any further than it already was.

    Args:
        current_level: the node's `done_level` before folding `event`
            (`"D0-code"` for a fresh/unseen node — every caller's own
            documented default). May be any string, including one not in
            `DONE_LEVELS` (G5 fix #N2) — never raises for that.
        event: the event to fold in.

    Returns:
        str: `event.payload["done_level"]` if it is a legal single-rung
        forward advance from `current_level` (or, if `current_level` is
        unrecognized, from the `"D0-code"` floor); `current_level` unchanged
        otherwise.
    """
    if event.type != "done_level_advanced":
        return current_level
    candidate = event.payload.get("done_level")
    if candidate not in _LEVEL_INDEX:
        return current_level
    # G5 fix #N2: `.get(..., 0)` instead of `[...]` — an unrecognized
    # `current_level` (e.g. a corrupt cached fragment's stale value) is
    # treated as the D0-code floor (index 0) rather than raising KeyError.
    base_index = _LEVEL_INDEX.get(current_level, 0)
    if _LEVEL_INDEX[candidate] != base_index + 1:
        return current_level
    return candidate


def validate_fragment(data: dict[str, Any]) -> Fragment:
    """Validate a raw fragment dict and return a typed `Fragment`.

    Rejects an unrecognized key outright (FR-5) — in particular, a caller
    cannot smuggle a single collapsing `status` field past this schema; the
    five decomposed status fields are the only status representation.

    Args:
        data: a raw fragment dict, e.g. as decoded from a fragment JSON file.

    Returns:
        Fragment: the validated, typed fragment.

    Raises:
        ValidationError: on a missing required field or an unrecognized key.
    """
    _require_dict(data, "fragment")

    missing = _REQUIRED_FRAGMENT_FIELDS - data.keys()
    if missing:
        raise ValidationError(f"fragment missing required field(s): {sorted(missing)}")

    unknown = data.keys() - _ALLOWED_FRAGMENT_FIELDS
    if unknown:
        raise ValidationError(
            f"fragment has unrecognized field(s): {sorted(unknown)} "
            "(status is decomposed into five fields per FR-5; there is no "
            "single collapsing 'status' key)"
        )

    for key in _REQUIRED_FRAGMENT_FIELDS:
        if not isinstance(data[key], str) or not data[key]:
            raise ValidationError(f"fragment field {key!r} must be a non-empty string")

    return Fragment(**{f.name: data[f.name] for f in fields(Fragment)})
