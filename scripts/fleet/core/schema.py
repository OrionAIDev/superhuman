"""Event and fragment dataclasses, ownership table, and schema validation.

The event log is the source of truth (one JSON object per line); a fragment is
a materialized per-session projection of it. Both are validated here before
anything is persisted (NFR-7). Per DESIGN "Decision F" and FR-5, status is
**decomposed into five orthogonal fields** — the schema has no single
collapsing enum, and a caller cannot smuggle one in.
"""

from __future__ import annotations

import re
import unicodedata
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

#: The only `schema_version` this module knows how to interpret as valid v1
#: manifest truth. 9th-round preflight, BLOCKING, PM-reproduced:
#: `validate_event` previously only type-checked `schema_version` (any int
#: passed), so an otherwise-well-formed event carrying `schema_version=2`
#: was accepted and replayed as valid v1 data. Pinned here so a future v2
#: schema is an explicit, deliberate change to this constant (and to the
#: validation/projection logic that interprets it), never an accidental
#: silent acceptance.
SCHEMA_VERSION_V1: Final[int] = 1

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
        "ownership_declared",
        "ownership_stood_down",
    }
)

#: `ownership_declared` payload — required keys, exactly (increment O, O.1).
_OWNERSHIP_DECLARED_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "claim_id",
        "anchor",
        "prior_owner",
        "prior_owner_kind",
        "basis",
        "prior_owner_liveness",
        "liveness_source",
        "attestation",
    }
)

#: `ownership_stood_down` payload — required keys, exactly (increment O, O.1).
_OWNERSHIP_STOOD_DOWN_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"stood_down_from", "written_by", "basis", "claimant", "claim_key", "reason"}
)

#: Enum vocabularies for the two new event types (increment O, O.1).
_PRIOR_OWNER_KINDS: Final[frozenset[str]] = frozenset({"none", "declared", "legacy"})
_CLAIM_BASIS_VALUES: Final[frozenset[str]] = frozenset(
    {"unowned", "notified", "archived", "deleted"}
)
_LIVENESS_VALUES: Final[frozenset[str]] = frozenset({"active", "unknown", "archived", "deleted"})
_LIVENESS_SOURCE_VALUES: Final[frozenset[str]] = frozenset({"sessions-json", "not-supplied"})
#: R1 (increment O amendment): `"on_behalf"` is an HONEST third `written_by`
#: value for a stand-down recorded by a caller other than the standing-down
#: node itself (the CLI's `--node-id` path) — distinct from `"self"` (the
#: node stood itself down) and `"claimant"` (a claim's own paired takeover
#: stand-down). Before this, `--node-id` on-behalf stand-downs were written
#: `written_by="self"`, misreporting a third party's action as the node's
#: own voluntary choice.
_STANDDOWN_WRITTEN_BY_VALUES: Final[frozenset[str]] = frozenset({"self", "claimant", "on_behalf"})
#: `basis="on_behalf"` is the sole basis value paired with
#: `written_by="on_behalf"` (mirroring `basis="self"` <-> `written_by="self"`,
#: below) — chosen over reusing `basis="self"` because that would still say
#: the SAME thing R1 exists to stop saying.
_STANDDOWN_BASIS_VALUES: Final[frozenset[str]] = frozenset(
    {"self", "on_behalf", "notified", "archived", "deleted"}
)

#: F1: public aliases of the four enum vocabularies above, for a consumer
#: outside this package that needs to interpret the two ownership event
#: types' payloads (e.g. `superhuman-cto`'s FR-29, which imports
#: `core.project_owner.fold_owner`) without hand-copying this module's own
#: private vocabulary. `core.project_owner` re-exports these under the same
#: names (see its own module-level `__all__`); this module is the one
#: source of truth both places read from.
PRIOR_OWNER_KINDS: Final[frozenset[str]] = _PRIOR_OWNER_KINDS
CLAIM_BASES: Final[frozenset[str]] = _CLAIM_BASIS_VALUES
STANDDOWN_WRITTEN_BY: Final[frozenset[str]] = _STANDDOWN_WRITTEN_BY_VALUES
STANDDOWN_BASES: Final[frozenset[str]] = _STANDDOWN_BASIS_VALUES

_ATTESTATION_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"notified_owner", "notified_via"})
_NOTIFIED_VIA_MAX_LEN: Final[int] = 200
_REASON_MAX_LEN: Final[int] = 1000

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
#: fragment fields, but they are ownership-checked the same way (cto-owned),
#: per DESIGN "core/ownership.py" responsibility and ARCHITECTURE item 5.
FIELD_OWNERS: Final[dict[str, str]] = {
    "lifecycle": "superhuman",
    "block_state": "superhuman",
    "review_state": "superhuman",
    "adoption_state": "cto",
    "done_level": "shared",
    "observation": "cto",
    "recommendation": "cto",
    # DESIGN O.2 Decision O2: both classes may write these event types in
    # principle; the actual restriction to `pm`/`cto` is a role ALLOWLIST
    # (`core.ownership._EVENT_WRITER_ROLES`), checked before this table is
    # ever consulted, so this entry never changes what is actually allowed
    # — it just keeps the two ownership event types documented here too,
    # the way `done_level`'s "shared" entry documents its own dual writers.
    "ownership_declared": "shared",
    "ownership_stood_down": "shared",
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
        adoption_state: orphan/adoption axis (cto-owned).
        done_level: evidence-backed deployment rung; state machine in Chunk 5
            (cto/superhuman shared field — advancement rules live in
            `core/done.py`).
    """

    node_id: str
    project_id: str
    lifecycle: str
    block_state: str
    review_state: str
    adoption_state: str
    done_level: str


def _assert_strict_int(value: Any, what: str) -> None:
    """Raise ValidationError unless `value` is a strict `int`, not a `bool`.

    10th-round preflight, BLOCKING, PM-reproduced: ``isinstance(x, int)`` is
    `True` for `bool` (Python's `bool` subclasses `int`, and `True == 1`), so
    a loose `isinstance(x, int)` check anywhere in this module would accept
    `True`/`False` for an integer field and silently coerce it into `1`/`0`
    on replay. This is the single, documented strict-int check every integer
    field in this module routes through (currently `schema_version` only —
    fragment fields are all non-empty strings, so `validate_fragment` has no
    integer field to route), per the project's standing bias: distinguish
    "absent" from "present-but-falsy", and never rely on a loose truthiness/
    type check for a correctness or safety property.

    Args:
        value: the candidate value.
        what: noun/description used in the error message (e.g. the field
            name), so the caller controls how the offending value is named.

    Raises:
        ValidationError: if `value` is a `bool`, or is not an `int` at all.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{what} must be an int (not bool), got {value!r}")


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

    schema_version = data["schema_version"]
    _assert_strict_int(schema_version, "schema_version")
    if schema_version != SCHEMA_VERSION_V1:
        raise ValidationError(
            f"schema_version must be {SCHEMA_VERSION_V1}, got {schema_version!r}"
        )

    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a dict")
    _assert_done_level_write_boundary(event_type, payload)
    _assert_payload_status_values_are_valid(payload)
    _assert_done_level_value_is_recognized(event_type, payload)
    _assert_ownership_payload_is_valid(event_type, payload)

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


def _assert_single_line_bounded(value: Any, what: str, *, max_len: int) -> None:
    """Raise ValidationError unless `value` is a non-empty single-line string within `max_len`.

    Shared bound for free-text payload fields on the two ownership event
    types (increment O, O-NFR-2): `notified_via` and `reason` must each be
    non-empty, one line, and bounded, so a runaway, blank, or multi-line
    value can never be persisted (NFR-7).

    The single-line check uses `len(value.splitlines()) > 1` rather than a
    literal `"\\n" in value or "\\r" in value` search (M5): Python's
    `str.splitlines()` also treats U+2028 LINE SEPARATOR, U+0085 NEL,
    vertical tab (`\\v`), and form feed (`\\f`) as line breaks, so this
    catches every one of those, not just `\\n`/`\\r`.

    F2: separately, any remaining Unicode "Cc" (control) character — ESC
    (`\\x1b`), BEL (`\\x07`), and the rest of the C0/C1 control ranges — is
    rejected too. `splitlines()` above only catches the control characters
    Python treats as line terminators; most C0/C1 controls are not among
    them (ESC and BEL do not end a line), so a value like `"done\\x1b[31mred"`
    would otherwise pass every check above and be persisted verbatim.

    Item 6: any Unicode "Cf" (format) character is rejected too — bidi
    overrides (U+202E RIGHT-TO-LEFT OVERRIDE and the rest of the
    directional-formatting block) and zero-width characters (U+200B
    ZERO WIDTH SPACE, U+2066 LEFT-TO-RIGHT ISOLATE, etc.). None of these are
    "Cc" controls, so the check above never caught them, yet a bidi override
    can visually reorder a rendered value (e.g. to disguise its content in a
    terminal or log viewer) without tripping the control-character or
    line-break checks at all.

    Args:
        value: the candidate value.
        what: noun/description used in the error message.
        max_len: the maximum allowed length, inclusive.

    Raises:
        ValidationError: if `value` is not a string, is empty/blank,
            contains an embedded line break of any kind, contains any
            Unicode control ("Cc" category) or format ("Cf" category)
            character, or exceeds `max_len` characters.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{what} must be a string, got {value!r}")
    if not value.strip():
        raise ValidationError(f"{what} must not be empty")
    # `splitlines() != [value]`, not `len(...) > 1`: `splitlines()` drops a
    # final terminator, so the length form would accept "abc\n".
    if value.splitlines() != [value]:
        raise ValidationError(
            f"{what} must be a single line (no embedded line break, including "
            "U+2028/U+0085/vertical-tab/form-feed)"
        )
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise ValidationError(f"{what} must not contain a control character (e.g. ESC, BEL)")
    if any(unicodedata.category(ch) == "Cf" for ch in value):
        raise ValidationError(
            f"{what} must not contain a Unicode format character (e.g. a bidi override or "
            "zero-width character)"
        )
    if len(value) > max_len:
        raise ValidationError(f"{what} must be at most {max_len} characters, got {len(value)}")


def _assert_nullable_nonempty_string(value: Any, what: str) -> None:
    """Raise ValidationError unless `value` is `None` or a non-empty string.

    Args:
        value: the candidate value.
        what: noun/description used in the error message.

    Raises:
        ValidationError: if `value` is neither `None` nor a non-empty string.
    """
    if value is not None and (not isinstance(value, str) or not value):
        raise ValidationError(f"{what} must be null or a non-empty string, got {value!r}")


def _assert_ownership_declared_payload(payload: dict[str, Any]) -> None:
    """Raise ValidationError if an `ownership_declared` payload is malformed (O.1).

    Args:
        payload: the event's payload dict (already confirmed to be a dict).

    Raises:
        ValidationError: on a missing/unrecognized key, an out-of-enum
            value, or a malformed `attestation`.
    """
    missing = _OWNERSHIP_DECLARED_REQUIRED_KEYS - payload.keys()
    if missing:
        raise ValidationError(
            f"ownership_declared payload missing required key(s): {sorted(missing)}"
        )
    unknown = payload.keys() - _OWNERSHIP_DECLARED_REQUIRED_KEYS
    if unknown:
        raise ValidationError(
            f"ownership_declared payload has unrecognized key(s): {sorted(unknown)}"
        )

    if not isinstance(payload["claim_id"], str) or not payload["claim_id"]:
        raise ValidationError("ownership_declared payload 'claim_id' must be a non-empty string")
    if not isinstance(payload["anchor"], str) or not payload["anchor"]:
        raise ValidationError("ownership_declared payload 'anchor' must be a non-empty string")
    _assert_nullable_nonempty_string(
        payload["prior_owner"], "ownership_declared payload 'prior_owner'"
    )
    if payload["prior_owner_kind"] not in _PRIOR_OWNER_KINDS:
        raise ValidationError(
            "ownership_declared payload 'prior_owner_kind' must be one of "
            f"{sorted(_PRIOR_OWNER_KINDS)}, got {payload['prior_owner_kind']!r}"
        )
    if payload["basis"] not in _CLAIM_BASIS_VALUES:
        raise ValidationError(
            f"ownership_declared payload 'basis' must be one of {sorted(_CLAIM_BASIS_VALUES)}, "
            f"got {payload['basis']!r}"
        )
    if payload["basis"] == "unowned" and payload["prior_owner"] is not None:
        raise ValidationError(
            "ownership_declared payload basis='unowned' requires 'prior_owner' to be null"
        )
    if payload["prior_owner_kind"] == "none" and payload["prior_owner"] is not None:
        raise ValidationError(
            "ownership_declared payload prior_owner_kind='none' requires 'prior_owner' to be null"
        )
    if payload["basis"] != "unowned" and (
        payload["prior_owner"] is None or payload["prior_owner_kind"] == "none"
    ):
        raise ValidationError(
            f"ownership_declared payload basis={payload['basis']!r} is a takeover and requires "
            "a non-null 'prior_owner' with 'prior_owner_kind' other than 'none'"
        )
    liveness = payload["prior_owner_liveness"]
    if liveness is not None and liveness not in _LIVENESS_VALUES:
        raise ValidationError(
            "ownership_declared payload 'prior_owner_liveness' must be null or one of "
            f"{sorted(_LIVENESS_VALUES)}, got {liveness!r}"
        )
    if payload["liveness_source"] not in _LIVENESS_SOURCE_VALUES:
        raise ValidationError(
            "ownership_declared payload 'liveness_source' must be one of "
            f"{sorted(_LIVENESS_SOURCE_VALUES)}, got {payload['liveness_source']!r}"
        )

    attestation = payload["attestation"]
    if attestation is not None:
        if not isinstance(attestation, dict):
            raise ValidationError(
                "ownership_declared payload 'attestation' must be null or a dict"
            )
        if attestation.keys() != _ATTESTATION_REQUIRED_KEYS:
            raise ValidationError(
                "ownership_declared payload 'attestation' must have exactly keys "
                f"{sorted(_ATTESTATION_REQUIRED_KEYS)}, got {sorted(attestation.keys())}"
            )
        if not isinstance(attestation["notified_owner"], str) or not attestation["notified_owner"]:
            raise ValidationError(
                "ownership_declared payload attestation 'notified_owner' must be a "
                "non-empty string"
            )
        _assert_single_line_bounded(
            attestation["notified_via"],
            "ownership_declared payload attestation 'notified_via'",
            max_len=_NOTIFIED_VIA_MAX_LEN,
        )


def _assert_ownership_stood_down_payload(payload: dict[str, Any]) -> None:
    """Raise ValidationError if an `ownership_stood_down` payload is malformed (O.1).

    Args:
        payload: the event's payload dict (already confirmed to be a dict).

    Raises:
        ValidationError: on a missing/unrecognized key, an out-of-enum
            value, or `written_by="claimant"` missing `claimant`/`claim_key`.
    """
    missing = _OWNERSHIP_STOOD_DOWN_REQUIRED_KEYS - payload.keys()
    if missing:
        raise ValidationError(
            f"ownership_stood_down payload missing required key(s): {sorted(missing)}"
        )
    unknown = payload.keys() - _OWNERSHIP_STOOD_DOWN_REQUIRED_KEYS
    if unknown:
        raise ValidationError(
            f"ownership_stood_down payload has unrecognized key(s): {sorted(unknown)}"
        )

    if not isinstance(payload["stood_down_from"], str) or not payload["stood_down_from"]:
        raise ValidationError(
            "ownership_stood_down payload 'stood_down_from' must be a non-empty string"
        )
    written_by = payload["written_by"]
    if written_by not in _STANDDOWN_WRITTEN_BY_VALUES:
        raise ValidationError(
            "ownership_stood_down payload 'written_by' must be one of "
            f"{sorted(_STANDDOWN_WRITTEN_BY_VALUES)}, got {written_by!r}"
        )
    if payload["basis"] not in _STANDDOWN_BASIS_VALUES:
        raise ValidationError(
            "ownership_stood_down payload 'basis' must be one of "
            f"{sorted(_STANDDOWN_BASIS_VALUES)}, got {payload['basis']!r}"
        )
    _assert_nullable_nonempty_string(
        payload["claimant"], "ownership_stood_down payload 'claimant'"
    )
    _assert_nullable_nonempty_string(
        payload["claim_key"], "ownership_stood_down payload 'claim_key'"
    )
    if written_by == "claimant" and (payload["claimant"] is None or payload["claim_key"] is None):
        raise ValidationError(
            "ownership_stood_down payload written_by='claimant' requires both "
            "'claimant' and 'claim_key' to be set"
        )
    if written_by != "claimant" and (
        payload["claimant"] is not None or payload["claim_key"] is not None
    ):
        raise ValidationError(
            f"ownership_stood_down payload written_by={written_by!r} requires both "
            "'claimant' and 'claim_key' to be null"
        )
    # R1: `"self"`/`"on_behalf"` each pair with the identically-named basis
    # value (a self stand-down is basis='self'; an honest on-behalf
    # stand-down is basis='on_behalf' — never basis='self', which would be
    # exactly the misreport R1 exists to stop). `written_by='claimant'`
    # instead pairs with one of the takeover bases.
    if written_by in ("self", "on_behalf"):
        if payload["basis"] != written_by:
            raise ValidationError(
                f"ownership_stood_down payload written_by={written_by!r} requires "
                f"basis={written_by!r} too, got {payload['basis']!r}"
            )
    elif payload["basis"] not in ("notified", "archived", "deleted"):
        raise ValidationError(
            "ownership_stood_down payload written_by='claimant' requires 'basis' to be "
            f"one of ('notified', 'archived', 'deleted'), got {payload['basis']!r}"
        )
    reason = payload["reason"]
    if written_by == "on_behalf" and reason is None:
        raise ValidationError(
            "ownership_stood_down payload written_by='on_behalf' requires a non-null "
            "'reason' (R1: an on-behalf stand-down must state why)"
        )
    if reason is not None:
        _assert_single_line_bounded(
            reason, "ownership_stood_down payload 'reason'", max_len=_REASON_MAX_LEN
        )


def _assert_ownership_payload_is_valid(event_type: str, payload: dict[str, Any]) -> None:
    """Dispatch payload validation for the two ownership event types (increment O).

    A no-op for every other event type — this is purely additive to the
    existing checks `validate_event` already runs.

    Args:
        event_type: the event's `type`.
        payload: the event's payload dict (already confirmed to be a dict).

    Raises:
        ValidationError: see `_assert_ownership_declared_payload` /
            `_assert_ownership_stood_down_payload`.
    """
    if event_type == "ownership_declared":
        _assert_ownership_declared_payload(payload)
    elif event_type == "ownership_stood_down":
        _assert_ownership_stood_down_payload(payload)


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
    of `DONE_LEVELS`. Looking that value up in `_LEVEL_INDEX` directly used
    to raise `KeyError`, crashing `core.projection.project_event` and
    violating this module's own "a corrupt fragment is never fatal"
    contract (mirrored from `core.projection`'s module docstring). An
    unrecognized `current_level` is now treated as the `"D0-code"` floor for
    the purpose of this fold's adjacency check: a subsequent legitimate
    event that is adjacent to `"D0-code"` (i.e. `"D1-merged"`) still applies
    from that known base.

    **G5 round-3 fix #R3-1(b) — an unrecognized `current_level` is also
    floored for the RESULT, not just the adjacency check.** Round 2's fix
    above floored only the ladder-position lookup used to decide
    adjacency, but still RETURNED the unchanged garbage `current_level`
    string whenever the folded-in event was not adjacent (or was not even a
    `done_level_advanced` event at all) — so a cached fragment with a bogus
    `done_level` retained that exact bogus value across every subsequent
    non-advancing fold, indefinitely, until a full `rebuild()`. Since
    `validate_fragment` now rejects a non-`DONE_LEVELS` `done_level`
    outright (G5 fix #R3-1(a) — no legitimate fragment can carry one), the
    only way this function ever sees an unrecognized `current_level` at all
    is a fragment that bypassed schema validation on write (never happens
    through `core.projection`) or was corrupted on disk after the fact —
    either way, never a value this fold should perpetuate. This function is
    now belt-and-suspenders on its own: an unrecognized `current_level` is
    floored to `"D0-code"` for BOTH the adjacency check AND the value
    returned when no adjacent advance applies — the result is always a
    recognized `DONE_LEVELS` member, never garbage in, garbage out.
    `rebuild()` — which ignores cached fragments entirely and replays only
    the log — remains the full-recovery path back to a fully correct
    (possibly higher) value; this fold's guarantee is narrower and cheaper:
    never crash, never emit anything but a valid ladder rung, possibly
    conservative (stale-low) relative to the log's true history.

    Args:
        current_level: the node's `done_level` before folding `event`
            (`"D0-code"` for a fresh/unseen node — every caller's own
            documented default). May be any string, including one not in
            `DONE_LEVELS` (G5 fix #N2) — never raises for that.
        event: the event to fold in.

    Returns:
        str: `event.payload["done_level"]` if it is a legal single-rung
        forward advance from `current_level` (or, if `current_level` is
        unrecognized, from the `"D0-code"` floor); otherwise `current_level`
        unchanged if it is a recognized `DONE_LEVELS` member, or
        `"D0-code"` if it is not (G5 fix #R3-1(b)) — the return value is
        always one of `DONE_LEVELS`.
    """
    # G5 fix #R3-1(b): an unrecognized `current_level` is floored to
    # "D0-code" up front — used as the base for BOTH the adjacency check
    # below AND the fallback return value, so this function can never echo
    # back a value outside DONE_LEVELS.
    base = current_level if current_level in _LEVEL_INDEX else "D0-code"
    if event.type != "done_level_advanced":
        return base
    candidate = event.payload.get("done_level")
    if candidate not in _LEVEL_INDEX:
        return base
    if _LEVEL_INDEX[candidate] != _LEVEL_INDEX[base] + 1:
        return base
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
        ValidationError: on a missing required field, an unrecognized key,
            or a `done_level` that is not one of `DONE_LEVELS` (G5 round-3
            fix #R3-1(a)).
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

    # G5 round-3 fix #R3-1(a): a schema-valid-but-unrecognized `done_level`
    # (a non-empty string, so it already passed the loop above, but not a
    # member of DONE_LEVELS — e.g. "D9-bogus") used to be accepted outright.
    # No legitimate fragment can carry such a value — `core.projection`
    # only ever writes a `done_level` derived from `fold_done_level`, which
    # itself only ever returns a recognized `DONE_LEVELS` member (G5 fix
    # #R3-1(b), below) — so rejecting here makes a cached fragment
    # corrupted to a bogus `done_level` hit `core.projection.project_event`'s
    # existing "treated as absent" `ValidationError` path (the same path a
    # schema-invalid fragment already takes), rather than being accepted
    # and the garbage value retained indefinitely.
    if data["done_level"] not in DONE_LEVELS:
        raise ValidationError(
            f"fragment field 'done_level' {data['done_level']!r} is not a "
            f"recognized done_level (expected one of {DONE_LEVELS})"
        )

    return Fragment(**{f.name: data[f.name] for f in fields(Fragment)})
