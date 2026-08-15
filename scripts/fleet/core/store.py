"""Per-session fragment read/write via temp-file + atomic ``os.replace`` (CC-1).

A fragment is a materialized projection of the event log for one session —
cache, not truth. Each session owns its own fragment file (writer-partitioned
by `node_id`), so fragments need no lock; the atomicity comes entirely from
writing to a temp file in the same directory and then `os.replace`-ing it
over the target, which is atomic on both POSIX and Windows. A corrupt or
missing fragment is never fatal — `core/projection.py` rebuilds it from the
log.

**Not a write-boundary (HARDEN #3, GPT-5 review):** `write_fragment` has no
`writer_role` to check anything against — a `Fragment` carries no attribution
— so ownership enforcement can only happen one layer up, over the `Event`
that produced a given `Fragment`. `core/events.append` is the sole enforced
entry for events; `core/projection.project_event` re-checks ownership before
calling this function with a caller-supplied `Event`, so this module never
needs to (and structurally cannot).

**`fragment_path` is length-bounded (Chunk-2 review follow-up):** the
filename is capped and falls back to a digest name for pathological
`node_id`s — see `fragment_path`'s own docstring. The `sessions_dir`
*directory* prefix itself is not similarly capped: on an unusually deep
workspace it could itself push a real path toward Windows' historical
MAX_PATH, but that is a property of where the caller chose to root the
project's fleet directory, not of anything this module controls per-call —
assessed as low priority per the reviewer's own note, not fixed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from .errors import FragmentCorrupt, ValidationError
from .schema import Fragment, validate_fragment

#: Above this length, the readable percent-encoded filename risks the
#: Windows MAX_PATH-adjacent limits and the NTFS 255-char single-component
#: cap once ".json" and a real sessions_dir prefix are added — `node_id` is
#: already percent-encoded once per component by `core.nodes.make_node_id`,
#: so re-encoding the whole (already-encoded) string here can grow a single
#: special character by ~5x (`:` -> `%3A` -> `%253A`), with no cap otherwise.
#: 150 is well under 255 and leaves headroom for ".json" (+5) and a
#: reasonably deep sessions_dir path.
_MAX_READABLE_ENCODED_LEN = 150

#: Marker prefixing every digest-fallback filename. Provably disjoint from
#: any `quote(node_id, safe="")` output: `urllib.parse.quote` only ever
#: emits a literal `%` as the start of a `%XX` escape, and XX is always two
#: UPPERCASE hex digits (`quote` never lowercases them) — it never emits `%`
#: followed by a lowercase letter. `%zz-` (lowercase z) can therefore never
#: be a prefix of any percent-encoded readable name, for any node_id, at any
#: length — see `tests/fleet/test_store.py` for a direct proof of this claim.
_DIGEST_PREFIX = "%zz-"


def fragment_path(node_id: str, sessions_dir: Path | str) -> Path:
    """Return the fragment file path for `node_id` under `sessions_dir`.

    A pure, collision-free function of `node_id` in either branch:

    - **Short node_id (the common case):** the readable percent-encoded name
      (unchanged from before this fix) — `quote(node_id, safe="")`, unique by
      construction since `quote` is an injective encoding.
    - **Pathologically long/special node_id:** falls back to a
      `_DIGEST_PREFIX`-marked SHA-256 hex digest of the *full* `node_id`.
      256 bits of digest makes an accidental collision negligible, and the
      prefix guarantees this filename can never collide with a readable one
      (see `_DIGEST_PREFIX`). The digest is not required to be decodable —
      `node_id` is always recovered from the fragment's own content
      (`Fragment.node_id`), never from the filename.

    Args:
        node_id: the namespaced node id (may contain `/`, per `core/nodes.py`).
        sessions_dir: the directory holding per-session fragment files.

    Returns:
        Path: the fragment file path — readable or digest-named per the
        length check above, distinct for every distinct `node_id` either way.
    """
    encoded = quote(node_id, safe="")
    if len(encoded) <= _MAX_READABLE_ENCODED_LEN:
        name = f"{encoded}.json"
    else:
        digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()
        name = f"{_DIGEST_PREFIX}{digest}.json"
    return Path(sessions_dir) / name


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
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(fragment), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
        replaced = True
    finally:
        # Clean up the temp file on ANY exception (not just OSError — a
        # `json.dump` failure, for instance, is a ValueError/TypeError), so
        # a non-OSError failure can never leak a `.tmp-*` file. Once
        # `os.replace` has actually succeeded, `tmp_name` no longer exists
        # under its original name (it *is* `target` now), so this is a
        # harmless no-op on the success path.
        if not replaced:
            try:
                os.remove(tmp_name)
            except FileNotFoundError:
                pass


def _read_fragment_file(path: Path) -> Fragment:
    """Read and validate one fragment file that is known to exist.

    The single place that turns "any failure to produce a valid `Fragment`
    from an existing file" into one uniform typed error — undecodable bytes
    (`UnicodeDecodeError`), unparseable JSON (`json.JSONDecodeError`),
    schema-invalid content (`ValidationError`), or an unreadable file
    (`OSError`, e.g. permissions) all collapse to `FragmentCorrupt` here, so
    every caller (`read_fragment`, `iter_fragments`) has exactly one
    exception type to handle instead of four.

    Args:
        path: the fragment file path. Caller is responsible for having
            already established the file exists — this function does not
            distinguish "absent" from "corrupt," only "corrupt."

    Returns:
        Fragment: the validated fragment.

    Raises:
        FragmentCorrupt: if the file cannot be decoded as UTF-8, parsed as
            JSON, validated against the fragment schema, or read at all.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_fragment(data)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, OSError) as exc:
        raise FragmentCorrupt(f"corrupt fragment file: {path}") from exc


def read_fragment(node_id: str, sessions_dir: Path | str) -> Fragment | None:
    """Read the fragment for `node_id`, or `None` if it does not exist.

    Args:
        node_id: the namespaced node id to look up.
        sessions_dir: the directory holding per-session fragment files.

    Returns:
        Fragment | None: the validated fragment, or `None` if no fragment
        file exists for `node_id`. A genuinely absent file is not an error.

    Raises:
        FragmentCorrupt: if the fragment file EXISTS but cannot be read as
            a valid `Fragment` — undecodable bytes, unparseable JSON,
            schema-invalid content, or an unreadable file. The log
            (`events.jsonl`) remains the source of truth; callers that need
            to recover should call `core.projection.rebuild()`, not catch
            this and improvise a partial fragment.
    """
    path = fragment_path(node_id, sessions_dir)
    if not path.is_file():
        return None
    return _read_fragment_file(path)


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

    Raises:
        FragmentCorrupt: if `skip_corrupt` is False and a fragment file
            cannot be read as a valid `Fragment` (undecodable bytes,
            unparseable JSON, schema-invalid content, or an unreadable
            file).
    """
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return []

    fragments: list[Fragment] = []
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            fragments.append(_read_fragment_file(path))
        except FragmentCorrupt:
            if skip_corrupt:
                continue
            raise
    return fragments
