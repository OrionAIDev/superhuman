"""Per-session fragment read/write via temp-file + atomic ``os.replace`` (CC-1).

A fragment is a materialized projection of the event log for one session —
cache, not truth. Each session owns its own fragment file (writer-partitioned
by `node_id`), so fragments need no lock; the atomicity comes entirely from
writing to a temp file in the same directory and then `os.replace`-ing it
over the target, which is atomic on both POSIX and Windows. A corrupt or
missing fragment is never fatal — `core/projection.py` rebuilds it from the
log.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from .errors import ValidationError
from .schema import Fragment, validate_fragment


def fragment_path(node_id: str, sessions_dir: Path | str) -> Path:
    """Return the fragment file path for `node_id` under `sessions_dir`.

    Args:
        node_id: the namespaced node id (may contain `/`, per `core/nodes.py`).
        sessions_dir: the directory holding per-session fragment files.

    Returns:
        Path: `sessions_dir / "<percent-encoded node_id>.json"` — encoding the
        whole node id (not just escaping `/`) keeps the mapping a pure
        function of `node_id`, distinct for every distinct node id.
    """
    return Path(sessions_dir) / f"{quote(node_id, safe='')}.json"


def write_fragment(fragment: Fragment, sessions_dir: Path | str) -> None:
    """Write `fragment` atomically: temp file in `sessions_dir`, then replace.

    If anything fails between the temp write and the rename (including a
    monkeypatched/crashed `os.replace`), the target path is left exactly as
    it was before this call — either the previous complete content, or
    absent. The temp file itself is cleaned up on failure; nothing readable
    is ever left half-written.

    Args:
        fragment: the fragment to persist.
        sessions_dir: the directory holding per-session fragment files.

    Raises:
        OSError: if the write or replace fails (e.g. disk full, or a
            simulated crash in tests). The target is left untouched.
    """
    sessions_dir = Path(sessions_dir)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    target = fragment_path(fragment.node_id, sessions_dir)

    fd, tmp_name = tempfile.mkstemp(dir=str(sessions_dir), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(fragment), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except OSError:
        try:
            os.remove(tmp_name)
        except FileNotFoundError:
            pass
        raise


def read_fragment(node_id: str, sessions_dir: Path | str) -> Fragment | None:
    """Read the fragment for `node_id`, or `None` if it does not exist.

    Args:
        node_id: the namespaced node id to look up.
        sessions_dir: the directory holding per-session fragment files.

    Returns:
        Fragment | None: the validated fragment, or `None` if no fragment
        file exists for `node_id`.

    Raises:
        ValidationError: if the fragment file exists but fails schema
            validation (e.g. corrupt or wrong schema version). Callers that
            need to tolerate a corrupt fragment should use
            `core/projection.py`'s rebuild path instead of catching this
            directly.
    """
    path = fragment_path(node_id, sessions_dir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_fragment(data)


def iter_fragments(sessions_dir: Path | str, *, skip_corrupt: bool = True) -> list[Fragment]:
    """Read every fragment file under `sessions_dir`.

    Args:
        sessions_dir: the directory holding per-session fragment files.
        skip_corrupt: when True (the default, matching the read-only query
            surface's best-effort contract), a fragment file that fails to
            parse or validate is skipped rather than raised — one session's
            corrupt cache must never block listing every other session
            (ARCHITECTURE "Isolation guarantee"). When False, the first
            corrupt fragment's error propagates.

    Returns:
        list[Fragment]: every readable fragment, in filename order.
    """
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return []

    fragments: list[Fragment] = []
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fragments.append(validate_fragment(data))
        except (json.JSONDecodeError, ValidationError, OSError):
            if skip_corrupt:
                continue
            raise
    return fragments
