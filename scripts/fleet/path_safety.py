"""Shared path-safety helper for joining a caller-supplied `slug` onto a
project path (Phase 3.3 preflight FIX 4 / B5).

`observe.py`'s `_default_fleet_dir` and `role_block.py`'s
`record_role_gate_decision` both build a path under
`<workspace>/docs/superhuman/<slug>/...` from a `slug` that ultimately
traces back to external input — a CLI argument for `observe.py`, and (for
`role_block.py`, B5) a locator cache file the agent itself writes into its
own scratchpad, which is agent-writable and so no more trustworthy than a
raw CLI argument. A slug containing a path separator or a `..` segment
would let the joined path escape the project tree it is meant to be
confined to.

One validator here, imported by both, replaces what B5 found was two
independently-maintained copies of the same check: `observe.py` grew this
guard at an earlier preflight (FIX 4); `role_block.py` never inherited it,
so the identical class of defect stood unfixed in a sibling module.
"""

from __future__ import annotations


def slug_is_safe(slug: str) -> bool:
    """Return whether `slug` is safe to join onto a filesystem path.

    Args:
        slug: the superhuman project slug to validate.

    Returns:
        bool: `True` iff `slug` contains none of `/`, `\\`, or `..` — the
        three shapes that could make `<workspace>/docs/superhuman/<slug>`
        resolve outside `<workspace>/docs/superhuman/`.
    """
    return "/" not in slug and "\\" not in slug and ".." not in slug
