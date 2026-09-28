"""Typed exceptions for the manifest core.

Every rejection path in ``scripts.fleet.core`` (malformed events, non-owner
writes, lock contention) raises one of these instead of a bare ``ValueError``
or ``Exception``, so callers can distinguish "reject and fix the input" from
"reject and retry" from "reject, this is a policy violation."
"""

from __future__ import annotations


class FleetError(Exception):
    """Base class for every error raised by ``scripts.fleet.core``."""


class ValidationError(FleetError):
    """An event or fragment failed schema validation.

    Raised by ``core.schema.validate_event`` / ``core.schema.validate_fragment``
    before anything is persisted (NFR-7). Nothing is written on this error.
    """


class OwnershipError(FleetError):
    """A writer attempted to write a field it does not own (FR-8).

    Raised by ``core.ownership.assert_writer_may`` before any append. This is a
    rejection, never a warning — DESIGN is explicit that a non-owner write is
    "rejected, not merely discouraged."
    """


class LockTimeoutError(FleetError):
    """The shared event-log lockfile could not be acquired within the timeout.

    Raised by ``core.events.acquire_lock`` after bounded retry. The caller
    should retry the whole operation later; the log is never written unlocked.
    """


class PreconditionUnmet(FleetError):
    """An ``append()`` caller's ``precondition`` rejected the write.

    Raised by ``core.events.append`` when a ``precondition`` callable is
    given and returns falsy for the event list read under the lock — after
    the idempotency-key dedupe check, so a genuine duplicate append still
    returns ``None`` as before; this is reserved for the distinct case of "a
    fresh, non-duplicate event, refused by caller-supplied policy." Nothing
    is written on this error, same guarantee as every other ``append()``
    rejection. Distinguishing this from the dedupe ``None`` return matters:
    ``None`` means "already recorded, no-op is correct"; this exception
    means "must not be recorded, something changed underneath the caller."
    """


class FragmentCorrupt(FleetError):
    """A cached fragment on disk could not be read as a valid `Fragment`.

    Raised by `core.store.read_fragment` (and `core.store.iter_fragments`
    when `skip_corrupt=False`) for an EXISTING fragment file whose bytes
    are not valid UTF-8, whose content is not valid JSON, whose content
    fails `core.schema.validate_fragment`, or that cannot be read at all
    (permissions, disk error) — as opposed to a genuinely absent fragment
    file, which is not an error (`read_fragment` returns `None` for that
    case, unchanged). The log (`events.jsonl`) remains the source of
    truth regardless: `core.projection.rebuild()` replays it from scratch
    and fully recovers, independent of whatever is (or is not, or is
    corrupt) on disk in the fragment cache.
    """


class SessionIdentityUnresolved(FleetError):
    """A `SessionAdapter` was asked for `current_session()` with no way to
    know which real session it is.

    Raised by `adapter.claude.ClaudeAdapter.current_session()` when
    `current_session_id` was never supplied at construction. The Claude
    harness has no Python-accessible source for "which session am I" (see
    `adapter/claude.py`'s module docstring) — fabricating an id (e.g. from
    a per-object memory address) is not a fallback, it is a distinct
    phantom identity on every process, which breaks NFR-1 idempotency by
    minting duplicate `session_registered` events/node_ids for one real
    session (GPT-5 round-9 preflight, BLOCKING, PM-reproduced). This fails
    closed instead: the caller must supply the real id (`--session-id` on
    the CLI, or `current_session_id=` on the constructor).
    """


class OwnershipRefused(FleetError):
    """A ``fleet owner claim``/``stand-down`` call was refused by policy (O-NFR-1).

    Raised by ``core.project_owner.claim``/``stand_down`` for a deliberate,
    loud refusal — never for a malformed input (that is ``ValidationError``)
    or a non-owner writer_role (that is ``OwnershipError``). Nothing is
    written on this error; the caller (the CLI, in a later increment) maps
    ``code`` to a specific non-zero exit status.

    Attributes:
        code: one of ``"coordination_required"`` (an active prior owner
            exists and no attestation was given, or none matching),
            ``"attestation_mismatch"`` (the attestation names a node that is
            not the current owner), ``"not_current_owner"`` (a stand-down by
            a session that does not currently own the project), or
            ``"not_registered"`` (the claimant/target has no
            ``session_registered`` event in this project).
        current_owner: the project's current owner `node_id` at the moment
            of refusal, or `None` if there is no current owner (e.g.
            ``not_registered``).
    """

    def __init__(self, code: str, *, current_owner: str | None = None, message: str | None = None) -> None:
        """Initialize an OwnershipRefused error.

        Args:
            code: the refusal code (see class docstring).
            current_owner: the project's current owner `node_id`, or `None`.
            message: an optional human-readable message; a default is
                generated from `code` and `current_owner` if omitted.
        """
        self.code = code
        self.current_owner = current_owner
        super().__init__(
            message or f"ownership action refused ({code}); current_owner={current_owner!r}"
        )


class OwnershipContended(FleetError):
    """A ``claim``/``stand_down`` exhausted its bounded re-evaluation budget (O-FR-6).

    Raised when the underlying ownership state kept changing out from under
    the caller across every retry `core.project_owner` allows (see its
    module docstring). This is a genuine contention failure, not a policy
    refusal — the caller should retry the whole operation later. Nothing is
    written on this error.
    """


class DonePolicyError(FleetError):
    """A ``done_level`` advance was rejected by policy, not malformed input.

    Raised by ``core.done.advance()`` for a skip-level, backward, or
    same-level transition attempt; a missing/insufficient evidence gate (FR-6:
    merge evidence for D1-merged, deploy+test evidence for D2-test); a
    missing or non-human approver for D3-uat/D4-prod; or an attempt past the
    project's D-ceiling. Every rejection is deterministic and code-only
    (DP#5) — never inferred. Kept distinct from ``ValidationError``
    (malformed event shape) and ``OwnershipError`` (wrong writer role) so a
    caller can tell "this is a policy rejection" apart from those other
    rejection classes. Nothing is written on this error.
    """
