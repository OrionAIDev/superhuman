"""Reads `Project-id:`/`Slug:` from a project's `SUPERHUMAN.md` front-matter.

Identity is **carried, not re-derived** (W-FR-6, Phase 1 Decision F): a
registration written during a project run must group under exactly the
`project_id` that project's own `SUPERHUMAN.md` records, so a later slug
rename does not orphan prior history. This module never invents or derives
an id — a missing file, missing front-matter keys, or an unparseable file
all resolve to `None` rather than a guessed value.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Matches a `**Project-id:** <value>` front-matter line, the same
#: bold-key-colon convention `SUPERHUMAN.md` uses for every other field
#: (`**Slug:**`, `**Started:**`, ...). `re.MULTILINE` so `^` anchors to any
#: line, not just the start of the file.
_PROJECT_ID_RE = re.compile(r"^\*\*Project-id:\*\*\s*(\S+)", re.MULTILINE)

#: Matches a `**Slug:** <value>` front-matter line — see `_PROJECT_ID_RE`.
_SLUG_RE = re.compile(r"^\*\*Slug:\*\*\s*(\S+)", re.MULTILINE)


def _superhuman_md_path(workspace: Path | str, slug: str) -> Path:
    """Return the path to a project's `SUPERHUMAN.md`.

    Args:
        workspace: the working tree root.
        slug: the superhuman project slug.

    Returns:
        Path: `<workspace>/docs/superhuman/<slug>/SUPERHUMAN.md`, matching
        the storage layout `scripts/fleet/cli.py`'s `_default_fleet_dir`
        uses for the fleet manifest directory.
    """
    return Path(workspace) / "docs" / "superhuman" / slug / "SUPERHUMAN.md"


def read_project_identity(workspace: Path | str, slug: str) -> tuple[str, str] | None:
    """Read `(project_id, slug)` from `<workspace>/docs/superhuman/<slug>/SUPERHUMAN.md`.

    Never raises and never invents an id: the file being absent, unreadable,
    present-but-missing a front-matter key, or present-but-unparseable all
    return `None`, exactly like every other identity-unresolved case in this
    package (`SessionIdentityUnresolved`'s fail-closed posture, restated
    here as a return value instead of an exception since this is a plain
    read helper, not a `SessionAdapter`).

    Args:
        workspace: the working tree root to look under.
        slug: the superhuman project slug — used only to locate the file;
            the returned slug is what the file itself says, not this
            argument, so a rename is detectable rather than papered over.

    Returns:
        tuple[str, str] | None: `(project_id, slug)` exactly as written in
        the file's own `**Project-id:**`/`**Slug:**` lines — not re-derived,
        not normalized — or `None` if either key is missing, blank, or the
        file cannot be read.
    """
    path = _superhuman_md_path(workspace, slug)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # `ValueError` covers `UnicodeDecodeError` on a non-UTF-8 file.
        return None

    project_match = _PROJECT_ID_RE.search(text)
    slug_match = _SLUG_RE.search(text)
    if project_match is None or slug_match is None:
        return None

    project_id = project_match.group(1).strip()
    file_slug = slug_match.group(1).strip()
    if not project_id or not file_slug:
        return None
    return (project_id, file_slug)
