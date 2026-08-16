"""Ownership table + write-interface enforcement (CC-6/FR-8).

Per-field ownership is a contract *enforced in code*, not documentation
(ARCHITECTURE "Cross-component contracts" item 5): a non-owner write to an
owned field is rejected — raised, not merely logged or warned.
"""

from __future__ import annotations

from .errors import OwnershipError
from .schema import FIELD_OWNERS

#: The literal role class DESIGN calls "ceo" — the sole non-superhuman writer
#: class in Phase 1. Matched case-insensitively against the whole role
#: string, so "CEO" matches but "Developer" (which owns neither "ceo" as a
#: substring) does not.
_CEO_ROLE_NAME = "ceo"


def _role_class(writer_role: str) -> str:
    """Classify `writer_role` into an ownership class.

    Args:
        writer_role: the writer's role string (already NFR-6-validated by
            `core/schema.py` upstream of any ownership check).

    Returns:
        str: `"ceo"` if the role is the CEO role, else `"superhuman"` — every
        other role (Project Manager, Developer, Architect, QA, Tester, ...)
        is on the superhuman side of the ownership split.
    """
    return "ceo" if writer_role.strip().lower() == _CEO_ROLE_NAME else "superhuman"


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
