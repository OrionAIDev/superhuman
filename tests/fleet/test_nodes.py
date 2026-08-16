"""Tests for ``scripts.fleet.core.nodes`` — TC-5.

Covers CC-3: namespaced node-id construction/parsing round-trips, and two
different (workspace, slug) pairs never collide on the same node id even when
components contain the separator character itself.
"""

from __future__ import annotations

import pytest

from scripts.fleet.core.nodes import make_node_id, parse_node_id


@pytest.mark.parametrize(
    "harness,workspace,slug,local_id",
    [
        ("claude", "C:/Users/dev/repo", "session-tracking", "abc123"),
        ("portable", "/home/dev/repo", "entry-tiering", "xyz-789"),
        ("hermeslab", "workspace-name", "slug_with_underscore", "local.id.with.dots"),
        # Components containing the raw separator itself must round-trip, not
        # silently corrupt the parse.
        ("claude", "a/b/c", "slug", "local-1"),
        ("claude", "workspace", "a/b", "local-1"),
        ("claude", "workspace", "slug", "a/b/c"),
    ],
)
def test_make_then_parse_round_trips_all_four_components(
    harness: str, workspace: str, slug: str, local_id: str
) -> None:
    node_id = make_node_id(harness, workspace, slug, local_id)
    parsed = parse_node_id(node_id)
    assert parsed == (harness, workspace, slug, local_id)


def test_different_workspace_slug_pairs_never_collide() -> None:
    # The exact ambiguity a naive "/".join would allow: "a/b" as workspace
    # with slug "c" must not collide with workspace "a" and slug "b/c".
    id_a = make_node_id("claude", "a/b", "c", "local-1")
    id_b = make_node_id("claude", "a", "b/c", "local-1")
    assert id_a != id_b
    assert parse_node_id(id_a) == ("claude", "a/b", "c", "local-1")
    assert parse_node_id(id_b) == ("claude", "a", "b/c", "local-1")


def test_node_id_is_a_plain_string() -> None:
    node_id = make_node_id("claude", "ws", "slug", "local-1")
    assert isinstance(node_id, str)


class TestMakeNodeIdRejectsBlankComponents:
    """10th-round preflight, BLOCKING, PM-reproduced (R10-3): the real
    class-killer for a blank `local_id` (or any other component) reaching a
    node id at all — not just at the one call site an adapter happened to
    guard, but structurally, for every current and future adapter. An empty
    or whitespace-only component must be rejected here, independent of
    which caller supplied it."""

    @pytest.mark.parametrize("blank", ["", "   "])
    @pytest.mark.parametrize("index", [0, 1, 2, 3])
    def test_blank_component_is_rejected(self, blank: str, index: int) -> None:
        parts = ["claude", "ws", "slug", "local-1"]
        parts[index] = blank
        with pytest.raises(ValueError):
            make_node_id(*parts)

    def test_valid_components_still_construct_and_parse_unchanged(self) -> None:
        node_id = make_node_id("claude", "ws", "slug", "local-1")
        assert parse_node_id(node_id) == ("claude", "ws", "slug", "local-1")
