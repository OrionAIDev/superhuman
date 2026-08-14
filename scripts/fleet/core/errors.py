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
