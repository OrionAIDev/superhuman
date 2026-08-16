"""Namespaced node-id construction and parsing (CC-3).

Node ids are `<harness>/<workspace>/<slug>/<local-session-id>`, namespaced so
ids never collide across repos/harnesses. Each component is percent-encoded
independently (`urllib.parse.quote`/`unquote`) before joining on `/`, so a
component that itself contains `/` round-trips exactly instead of silently
shifting the parse — the failure mode a naive `"/".join(...)` would allow.
"""

from __future__ import annotations

from urllib.parse import quote, unquote

#: Number of namespaced components in a node id.
_COMPONENT_COUNT = 4


def make_node_id(harness: str, workspace: str, slug: str, local_id: str) -> str:
    """Build a namespaced node id from its four components.

    10th-round preflight, BLOCKING, PM-reproduced (R10-3): this is the
    structural class-killer for an empty namespaced component reaching a
    node id at all. Round 9 fixed `ClaudeAdapter.current_session()` to
    fail closed on a missing/blank `current_session_id`, but that guard
    lives in exactly one caller — any other adapter (present or future,
    Claude, Portable, or a third-party one) that ever passes an empty or
    whitespace-only component straight through would reproduce the same
    hole (e.g. `node_id="claude/<ws>/demo/"`, a node id with an empty
    trailing identity component) without ever touching that one guarded
    call site. Rejecting a blank component HERE, in the one function every
    adapter must funnel through to mint a node id, closes the whole class
    rather than the one reported instance.

    Args:
        harness: the harness this session runs under (e.g. "claude", "portable").
        workspace: the workspace/repo root identifying this checkout.
        slug: the superhuman project slug.
        local_id: the harness-local session identifier.

    Returns:
        str: `<harness>/<workspace>/<slug>/<local_id>`, each component
        percent-encoded so it round-trips through `parse_node_id` exactly,
        even if a component itself contains `/`.

    Raises:
        ValueError: if any of the four components is empty or
            whitespace-only.
    """
    parts = {"harness": harness, "workspace": workspace, "slug": slug, "local_id": local_id}
    for name, value in parts.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"make_node_id: {name} must be a non-empty, non-blank string, "
                f"got {value!r}"
            )
    return "/".join(quote(part, safe="") for part in (harness, workspace, slug, local_id))


def parse_node_id(node_id: str) -> tuple[str, str, str, str]:
    """Recover the four original components from a namespaced node id.

    Args:
        node_id: a node id produced by `make_node_id`.

    Returns:
        tuple[str, str, str, str]: `(harness, workspace, slug, local_id)`,
        exactly as passed to `make_node_id`.

    Raises:
        ValueError: if `node_id` does not have exactly four `/`-separated
            components.
    """
    parts = node_id.split("/", maxsplit=_COMPONENT_COUNT - 1)
    if len(parts) != _COMPONENT_COUNT:
        raise ValueError(
            f"node_id {node_id!r} does not have {_COMPONENT_COUNT} "
            f"namespaced components (got {len(parts)})"
        )
    harness, workspace, slug, local_id = (unquote(part) for part in parts)
    return harness, workspace, slug, local_id
