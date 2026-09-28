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

from pathlib import Path, PureWindowsPath


class InvalidSlug(ValueError):
    """`slug` cannot be safely joined onto a project path (FIX 4).

    Raised by `default_fleet_dir` — never a path-traversal-shaped or
    symlink-confined result, only a clean rejection before any I/O.
    """


def slug_is_safe(slug: str) -> bool:
    """Return whether `slug` is safe to join onto a filesystem path.

    Args:
        slug: the superhuman project slug to validate.

    Returns:
        bool: `True` iff `slug` contains none of `/`, `\\`, or `..`, is
        neither `""` nor `"."`, and is not a Windows drive-qualified or
        drive-relative path segment.

    Phase 3.3 preflight RE-RUN item E: the original three-character check
    missed a Windows drive-relative slug entirely -- `slug_is_safe("C:evil")`
    was `True`, because `"C:evil"` contains none of `/`, `\\`, or `..`. But
    joining a drive-qualified segment onto ANY base path with `pathlib`
    does not extend that base path -- it REPLACES it outright, silently:
    `Path("D:/ws") / "C:evil" == Path("C:evil")` (verified). A caller
    building `<workspace>/docs/superhuman/<slug>/fleet` from such a slug
    therefore lands entirely outside the workspace, with no error and no
    trace of the intended path. `PureWindowsPath(slug).drive` catches
    both the drive-relative shape (`"C:evil"`) and a bare drive
    (`"C:"`) -- used unconditionally (not only on Windows), since a slug
    is a platform-independent identifier and this class of escape must be
    rejected the same way regardless of which OS validates it.

    `""` and `"."` are rejected too: an earlier version of this function
    treated an empty slug as safe on the theory that `Path` drops an empty
    path segment, making it "a harmless no-op". That is true for the
    escape concern specifically, but it also means every misconfigured
    caller silently collapses onto the SAME `docs/superhuman/fleet`
    directory instead of failing loudly -- and `"."` is the identical
    no-real-identity shape one path segment later. A slug is meant to
    name one specific project; neither spelling does.
    """
    if not slug or slug == ".":
        return False
    if "/" in slug or "\\" in slug or ".." in slug:
        return False
    if PureWindowsPath(slug).drive:
        return False
    return True


def default_fleet_dir(workspace: Path | str, slug: str) -> Path:
    """Return the slug-validated default per-project fleet manifest directory.

    The single shared, validated home for `<workspace>/docs/superhuman/<slug>/
    fleet` (Phase 3.3 preflight FIX 4). Callers resolving a verb's manifest
    directory go through `resolve_fleet_dir` below, which applies the
    operator's `fleet.manifest_dir` override first and falls back to this.
    `cli.py`'s old unvalidated `_default_fleet_dir` (DESIGN O.3: "Do not
    reuse `cli._default_fleet_dir`: it lacks FIX 4's slug validation") is
    gone; no caller builds this path itself any more.

    Args:
        workspace: the project's working tree root.
        slug: the superhuman project slug.

    Returns:
        Path: `<workspace>/docs/superhuman/<slug>/fleet`.

    Raises:
        InvalidSlug: if `slug` contains `/`, `\\`, `..`, or a Windows drive
            segment (`slug_is_safe` is `False`), or if the final `fleet`
            path component itself resolves outside
            `<workspace>/docs/superhuman/<slug>/` — defense in depth
            against `fleet` specifically being (or being replaced by) a
            symlink pointing elsewhere. F8: this check is scoped to that one
            component; it does NOT defend against `workspace`, `docs`,
            `superhuman`, or `<slug>` themselves being symlinks elsewhere on
            disk (both `fleet_dir` and `expected_root` below are built from,
            and resolve through, the SAME unresolved `workspace_path`, so a
            symlink earlier in the path is followed consistently by both
            sides and never trips this check either way) — a caller passing
            an attacker-influenced `workspace` is a distinct, unaddressed
            concern.
    """
    if not slug_is_safe(slug):
        raise InvalidSlug(f"invalid slug {slug!r}: path separators and '..' are not permitted")
    workspace_path = Path(workspace)
    fleet_dir = workspace_path / "docs" / "superhuman" / slug / "fleet"
    expected_root = (workspace_path / "docs" / "superhuman" / slug).resolve()
    if not fleet_dir.resolve().is_relative_to(expected_root):
        raise InvalidSlug(f"slug {slug!r} resolves outside the workspace ({workspace_path})")
    return fleet_dir


def resolve_fleet_dir(workspace: Path | str, slug: str, manifest_dir: Path | None) -> Path:
    """Return the fleet manifest directory every verb family must agree on.

    One resolution order for the `owner` verbs, the `observe` façade, and
    the older write/read verbs (`register`, `handoff`, `done`, `query`,
    `status`, `gen-view`): an operator's `fleet.manifest_dir` override when
    set, else the slug-validated default. Before this helper those older
    verbs ignored the override, so a `pm` registered through `fleet
    register` landed in a different log from the one `fleet owner claim`
    reads, and the legacy-owner coordination rule found no prior owner.

    The slug is validated first, override or not (the owner verbs' M6
    rule): callers still build paths from the raw slug elsewhere (the
    `SUPERHUMAN.md` identity read, `FLEET.md`), so an override must not
    switch that check off. A caller's own explicit `--fleet-dir` flag is
    applied before this function, not inside it.

    Args:
        workspace: the project's working tree root.
        slug: the superhuman project slug.
        manifest_dir: `FleetConfig.manifest_dir` — `None` when the profile
            sets no override (or fleet is disabled).

    Returns:
        Path: `manifest_dir` if set, else `default_fleet_dir(workspace, slug)`.

    Raises:
        InvalidSlug: if `slug` fails `slug_is_safe`, or the default path
            fails `default_fleet_dir`'s confinement check.
    """
    if not slug_is_safe(slug):
        raise InvalidSlug(f"invalid slug {slug!r}: path separators and '..' are not permitted")
    return manifest_dir or default_fleet_dir(workspace, slug)
