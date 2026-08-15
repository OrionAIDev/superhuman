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
