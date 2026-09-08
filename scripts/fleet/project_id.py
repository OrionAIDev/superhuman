"""Mints and validates a project's `**Project-id:**` line (D3, FR-11/FR-12).

Deliberately a separate, fail-closed module from `scripts/fleet/project.py`:
`project.py`'s "never invents or derives an id" read contract stays
literally true by keeping every *write* path for the field entirely out of
it. `observe.py` never imports this module — minting is a kickoff/operator
-triggered write (`fleet project mint`, called once from Phase 0 kickoff's
additive step), not part of the fail-soft observation façade this package
otherwise implements.

Ids are `uuid4().hex[:16]` — random, and never derived from the slug, the
git remote, or any other mutable project property (Phase 1 Decision F,
reaffirmed by this project's own DECISIONS.md D3). A slug rename or a repo
move must never orphan a project's manifest history the way a
remote+slug-derived id would. Once an id is minted, it is permanent:
`mint_project_id` is a no-op on a record that already carries one, and
`check_project_id` (surfaced as `fleet project check`) is a fail-closed
**assertion** — a non-zero exit when the field is absent — deliberately
outside `observe.py`'s fail-soft posture.

**The live exception.** This project's own id (`7124ce46ccd0a49a`, minted
under the now-forbidden remote+slug hash before this rule existed) is
exactly the case the no-op guard protects: `mint_project_id` never
re-mints regardless of what scheme produced an existing value, because
re-minting would orphan the history already written under it
(DECISIONS.md D3, reaffirming Phase 1 Decision F).
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from . import project as fleet_project

#: Matches an existing `**Project-id:** <value-or-nothing>` front-matter
#: line, wherever it appears, so a blank/missing-value line can be
#: replaced in place rather than duplicated. Kept as an independent regex
#: (rather than importing `project.py`'s private `_PROJECT_ID_RE`) per
#: this module's "deliberately separate" rationale in the module
#: docstring.
_PROJECT_ID_LINE_RE = re.compile(r"^\*\*Project-id:\*\*.*$", re.MULTILINE)

#: Matches the `**Slug:**` front-matter line, used only to find the
#: correct insertion point for a newly minted id — immediately after it,
#: matching `templates/SUPERHUMAN.md.tpl`'s own field order.
_SLUG_LINE_RE = re.compile(r"^\*\*Slug:\*\*.*$", re.MULTILINE)


def _superhuman_md_path(workspace: Path | str, slug: str) -> Path:
    """Return the path to a project's `SUPERHUMAN.md`.

    Duplicates `project.py`'s private helper of the same name rather than
    importing it — see the module docstring's "deliberately separate"
    rationale; the two modules must not share write-adjacent internals.

    Args:
        workspace: the working tree root.
        slug: the superhuman project slug.

    Returns:
        Path: `<workspace>/docs/superhuman/<slug>/SUPERHUMAN.md`.
    """
    return Path(workspace) / "docs" / "superhuman" / slug / "SUPERHUMAN.md"


def check_project_id(workspace: Path | str, slug: str) -> str | None:
    """Return the project's `**Project-id:**`, or `None` if absent (FR-12).

    A thin wrapper over `project.read_project_identity` — this module
    invents nothing about *what* counts as present; it reuses the exact
    same read contract the rest of the package relies on (a record needs
    both a non-blank `**Project-id:**` and a non-blank `**Slug:**` to
    resolve), and narrows the result to just the id.

    Args:
        workspace: the working tree root to look under.
        slug: the superhuman project slug.

    Returns:
        str | None: the id exactly as written in the file, or `None` if
        the record is missing, unreadable, or lacks the field.
    """
    identity = fleet_project.read_project_identity(workspace, slug)
    if identity is None:
        return None
    project_id, _file_slug = identity
    return project_id


def mint_project_id(workspace: Path | str, slug: str) -> str:
    """Mint a random 16-hex `**Project-id:**` for a record that lacks one (FR-11).

    A no-op on a record that already has an id: this function never
    re-mints, regardless of how the existing value was produced (see the
    module docstring's "live exception" — a non-uuid4 provenance does not
    make an existing id eligible for replacement). When minting is
    needed, the new `**Project-id:** <id>` line is inserted immediately
    after the `**Slug:**` line (matching `templates/SUPERHUMAN.md.tpl`'s
    field order) if a `**Project-id:**` key is not already present; if
    the key is present but its value is blank, that line is replaced in
    place so the field's position in the file never moves.

    Args:
        workspace: the working tree root.
        slug: the superhuman project slug.

    Returns:
        str: the id — freshly minted, or the pre-existing one on a no-op.

    Raises:
        OSError: `<workspace>/docs/superhuman/<slug>/SUPERHUMAN.md` does
            not exist or cannot be read/written. Minting has nothing to
            inject an id into until the record itself exists (Phase 0
            kickoff creates it from the template before calling this).
    """
    existing = check_project_id(workspace, slug)
    if existing is not None:
        return existing

    path = _superhuman_md_path(workspace, slug)
    text = path.read_text(encoding="utf-8")

    new_id = uuid4().hex[:16]
    new_line = f"**Project-id:** {new_id}"

    if _PROJECT_ID_LINE_RE.search(text):
        # The key exists but `check_project_id` returned None (blank value,
        # or the file's own Slug is missing/blank) — replace the line in
        # place rather than inserting a second one.
        new_text = _PROJECT_ID_LINE_RE.sub(new_line, text, count=1)
    else:
        slug_match = _SLUG_LINE_RE.search(text)
        if slug_match is not None:
            insert_at = slug_match.end()
            new_text = text[:insert_at] + "\n" + new_line + text[insert_at:]
        else:
            # No **Slug:** line to anchor on (a badly malformed record) —
            # degrade to prepending rather than failing to mint at all.
            new_text = new_line + "\n" + text

    path.write_text(new_text, encoding="utf-8")
    return new_id
