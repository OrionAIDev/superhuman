#!/usr/bin/env python3
"""Regenerate the vendored-core content manifest (roadmap #194).

`scripts/fleet/core/` is the portable, harness-agnostic core. TC-7 asserts it
is byte-unchanged. That assertion used to name a git revision, which rotted
the moment history was rewritten (roadmap #184) and needed full history in CI.
A content manifest is immune to rewrites, rebases, squash-merges and re-homing,
and it makes a deliberate re-sync show up in review as a readable diff.

Run this ONLY when the core is deliberately changed, and commit the result in
the SAME commit as the change it describes — a manifest regenerated separately
records nothing a reviewer can check.

    python scripts/regen_core_manifest.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: Directory whose contents the manifest pins.
CORE_DIR = Path(__file__).resolve().parent / "fleet" / "core"

#: Where the manifest lives. Deliberately OUTSIDE `core/` — a manifest stored
#: inside the directory it pins would have to exclude itself, and a
#: self-excluding guard is one edit away from excluding everything.
MANIFEST_PATH = Path(__file__).resolve().parent / "fleet" / "core-manifest.json"

#: Never part of the manifest: build artifacts are not source.
_EXCLUDED_DIRS = frozenset({"__pycache__"})


def iter_core_files(core_dir: Path = CORE_DIR) -> list[Path]:
    """Return every manifest-eligible file under `core_dir`, sorted.

    Args:
        core_dir: The directory to walk.

    Returns:
        Absolute paths, sorted by their POSIX-relative form so the order is
        identical on every platform.
    """
    files = [
        path
        for path in core_dir.rglob("*")
        if path.is_file() and not (_EXCLUDED_DIRS & set(path.relative_to(core_dir).parts))
    ]
    return sorted(files, key=lambda p: p.relative_to(core_dir).as_posix())


def digest(path: Path) -> str:
    """Return the SHA-256 of `path`'s bytes.

    Hashed as BYTES, never as decoded text: a line-ending translation must
    register as a change, because it is one to every consumer that reads the
    file as bytes.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(core_dir: Path = CORE_DIR) -> dict[str, str]:
    """Return `{posix-relative-path: sha256}` for the whole core."""
    return {
        path.relative_to(core_dir).as_posix(): digest(path)
        for path in iter_core_files(core_dir)
    }


def main() -> int:
    manifest = build_manifest()
    if not manifest:
        raise SystemExit(f"refusing to write an EMPTY manifest: no files under {CORE_DIR}")
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {MANIFEST_PATH} ({len(manifest)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
