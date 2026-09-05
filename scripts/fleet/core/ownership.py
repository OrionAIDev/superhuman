"""Ownership table + write-interface enforcement (CC-6/FR-8).

Per-field ownership is a contract *enforced in code*, not documentation
(ARCHITECTURE "Cross-component contracts" item 5): a non-owner write to an
owned field is rejected — raised, not merely logged or warned.
"""

from __future__ import annotations

from .errors import OwnershipError
from typing import Final

from .schema import FIELD_OWNERS

#: The ownership-class id for the sole non-superhuman writer class. The role's
#: NAME is CTO (org chart, and the specs since roadmap #189); this constant is
#: the class id used in `FIELD_OWNERS`, which is internal to this package and
#: never persisted or emitted — renaming it needed no data migration.
CTO_OWNER_CLASS: Final[str] = "cto"

#: The ownership-class id for every superhuman project role (Project Manager,
#: Developer, Architect, QA, Tester, ...).
SUPERHUMAN_OWNER_CLASS: Final[str] = "superhuman"

#: Role names that resolve to `CTO_OWNER_CLASS`. Matched case-insensitively
#: against the WHOLE role string (never as a substring), so "CTO" matches but
#: "Developer" does not.
#:
#: "ceo" is retained as a LEGACY ALIAS, not a synonym worth writing: the role
#: was called "CEO overseer" through Phase 1/1.1, and `writer_role` IS
#: persisted verbatim in manifest fragments. Dropping it would silently
#: reclassify every already-written "CEO" row as `superhuman` — the exact
#: fail-open this module exists to prevent (roadmap #207). New writers should
#: introduce themselves as "CTO"; old rows keep classifying correctly forever.
_CTO_ROLE_NAMES: Final[frozenset[str]] = frozenset({"cto", "ceo"})


def _role_class(writer_role: str) -> str:
    """Classify `writer_role` into an ownership class.

    Args:
        writer_role: the writer's role string (already NFR-6-validated by
            `core/schema.py` upstream of any ownership check).

    Returns:
        str: `CTO_OWNER_CLASS` if the role is the CTO role (or its legacy
        "CEO" spelling), else `SUPERHUMAN_OWNER_CLASS` — every other role
        (Project Manager, Developer, Architect, QA, Tester, ...) is on the
        superhuman side of the ownership split.
    """
    if writer_role.strip().lower() in _CTO_ROLE_NAMES:
        return CTO_OWNER_CLASS
    return SUPERHUMAN_OWNER_CLASS


def assert_writer_may(field: str, writer_role: str) -> None:
    """Raise unless `writer_role` may write `field` (FR-8).

    A field absent from `FIELD_OWNERS` is unowned/free — no restriction. A
    field owned `"shared"` accepts either class. Otherwise the writer's role
    class must match the field's declared owner exactly.

    Args:
        field: the fragment field (or payload kind, e.g. `"observation"`)
            being written.
        writer_role: the role attempting the write.

    Raises:
        OwnershipError: if `field` is owned by a class other than
            `writer_role`'s class. Raised *before* any append — the caller
            must not persist anything on this error.
    """
    owner = FIELD_OWNERS.get(field)
    if owner is None or owner == "shared":
        return
    if _role_class(writer_role) != owner:
        raise OwnershipError(
            f"writer_role {writer_role!r} may not write field {field!r} "
            f"(owned by {owner!r}) — FR-8 rejects non-owner writes"
        )
