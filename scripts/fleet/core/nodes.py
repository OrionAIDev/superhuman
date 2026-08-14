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

    Args:
        harness: the harness this session runs under (e.g. "claude", "portable").
        workspace: the workspace/repo root identifying this checkout.
        slug: the superhuman project slug.
        local_id: the harness-local session identifier.

    Returns:
        str: `<harness>/<workspace>/<slug>/<local_id>`, each component
        percent-encoded so it round-trips through `parse_node_id` exactly,
        even if a component itself contains `/`.
    """
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
