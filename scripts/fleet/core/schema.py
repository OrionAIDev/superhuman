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
    _assert_payload_status_values_are_valid(payload)

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
