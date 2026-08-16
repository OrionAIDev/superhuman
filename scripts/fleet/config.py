"""Reads the ``fleet:`` block directly from the profile YAML (Decision D, Q4).

This module answers exactly one question — "is fleet observation enabled for
this workspace, and why (not)?" — and never raises doing it. Every failure to
locate, read, or parse a profile (absent profile, unreadable file, invalid
YAML, non-UTF-8 bytes, a `fleet:` block that is not a mapping, `enabled` not
literally `true`) resolves to :class:`FleetConfig` with ``enabled=False`` and
a distinct, human-readable ``reason`` (W-FR-8) — never an exception and never
a silent default to enabled (W-FR-7 must be true *by construction*).

Reads the profile YAML directly, the same precedent
``handoff._resolve_handoff_expiry_seconds`` already set: the profile is
located via ``superhuman_profile.find_profile`` but read with `yaml.safe_load`
directly rather than through ``superhuman_profile.load_profile``, which
validates only the deployment-ladder schema and would reject the unrecognized
top-level ``fleet`` key. This keeps the (still-unextended, per
`REQUIREMENTS.md`'s out-of-scope clause) profile ladder schema untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Wall-clock ceiling for one `fleet observe` call (Decision A / W-NFR-7),
#: overridable via `fleet.observe_deadline_seconds` in the profile.
_DEFAULT_OBSERVE_DEADLINE_SECONDS = 5.0

#: Per-subprocess git timeout for the façade's own adapter calls (PLAN.md
#: Chunk 1's carried budget correction — NOT DESIGN's stale 0.4s figure).
#: `collect_git_facts` makes up to 7 calls in the worst case; 7 * 0.25 =
#: 1.75s, inside the 2.0s git-stage ceiling.
_DEFAULT_GIT_TIMEOUT_SECONDS = 0.25

#: Per-attempt manifest-lock timeout for the façade's own write calls.
_DEFAULT_LOCK_TIMEOUT_SECONDS = 0.8

#: Manifest-write retry attempts for the façade's own write calls.
_DEFAULT_LOCK_RETRY_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class FleetConfig:
    """The resolved fleet-observation configuration for one workspace.

    Attributes:
        enabled: whether `observe.py` may write anything at all. `False`
            means every `observe_*` call returns before any I/O (W-FR-7).
        reason: a human-readable, distinct-per-cause explanation (W-FR-8) —
            e.g. "no profile found", "profile has no `fleet:` block",
            "`fleet.enabled` is not true", or "enabled via <path>".
        manifest_dir: an operator override for the fleet manifest directory,
            or `None` to use the package default
            (`<workspace>/docs/superhuman/<slug>/fleet`).
        observe_deadline_seconds: the wall-clock ceiling for one observe
            call (W-NFR-7).
        git_timeout_seconds: the façade's own per-subprocess git timeout.
        lock_timeout_seconds: the façade's own per-attempt manifest-lock
            timeout.
    """

    enabled: bool
    reason: str
    manifest_dir: Path | None = None
    observe_deadline_seconds: float = _DEFAULT_OBSERVE_DEADLINE_SECONDS
    git_timeout_seconds: float = _DEFAULT_GIT_TIMEOUT_SECONDS
    lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS


def _disabled(reason: str) -> FleetConfig:
    """Build a disabled `FleetConfig` with the given reason.

    Args:
        reason: a human-readable, cause-specific explanation (W-FR-8).

    Returns:
        FleetConfig: `enabled=False` with every other field left at its
        package default (irrelevant while disabled).
    """
    return FleetConfig(enabled=False, reason=reason)


def _positive_float(value: Any, default: float) -> float:
    """Return `value` as a `float` if it is a positive, non-bool number.

    Args:
        value: the raw YAML value to interpret.
        default: returned unchanged if `value` is not a positive number.

    Returns:
        float: `value` coerced to `float`, or `default`.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default


def resolve_fleet_config(
    workspace: Path | str, *, profile_path: Path | None = None
) -> FleetConfig:
    """Resolve fleet-observation enablement + settings for `workspace` (Decision D).

    Never raises. Every failure mode below resolves to a disabled
    `FleetConfig` with a distinct reason string:

    - no profile found at or above `workspace`
    - the profile file cannot be read (missing, permissions, not UTF-8)
    - the profile is not valid YAML
    - the parsed profile is not a mapping
    - the profile has no `fleet:` block, or `fleet:` is not a mapping
    - `fleet.enabled` is not literally `true`

    Args:
        workspace: the working tree to resolve configuration for — passed to
            `superhuman_profile.find_profile` for the upward search.
        profile_path: override the profile file to read (for tests); `None`
            uses the normal `find_profile` search.

    Returns:
        FleetConfig: `enabled=True` with the resolved directory/deadline/
        timeout overrides only when every step above succeeds and
        `fleet.enabled` is literally `true`; disabled with a reason
        otherwise.
    """
    path = profile_path
    if path is None:
        try:
            from ..superhuman_profile import find_profile
        except ImportError:
            return _disabled("superhuman_profile module is unavailable")
        path = find_profile(Path(workspace))

    if path is None:
        return _disabled(f"no profile found at or above {workspace}")
    if not path.is_file():
        return _disabled(f"profile path {path} does not exist")

    try:
        import yaml
    except ImportError:
        return _disabled("PyYAML is unavailable")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # `ValueError` covers `UnicodeDecodeError` (raised by `read_text` on
        # invalid UTF-8 bytes) — matches `handoff._resolve_handoff_expiry_seconds`'s
        # precedent for the same failure class.
        return _disabled(f"profile at {path} could not be read/parsed: {exc}")

    if not isinstance(raw, dict):
        return _disabled(f"profile at {path} did not parse to a mapping")

    fleet_cfg = raw.get("fleet")
    if not isinstance(fleet_cfg, dict):
        return _disabled(f"profile at {path} has no `fleet:` block")

    if fleet_cfg.get("enabled") is not True:
        return _disabled(f"profile at {path} does not set `fleet.enabled: true`")

    manifest_dir_raw = fleet_cfg.get("manifest_dir")
    manifest_dir = Path(manifest_dir_raw) if isinstance(manifest_dir_raw, str) else None

    return FleetConfig(
        enabled=True,
        reason=f"enabled via {path}",
        manifest_dir=manifest_dir,
        observe_deadline_seconds=_positive_float(
            fleet_cfg.get("observe_deadline_seconds"), _DEFAULT_OBSERVE_DEADLINE_SECONDS
        ),
        git_timeout_seconds=_positive_float(
            fleet_cfg.get("git_timeout_seconds"), _DEFAULT_GIT_TIMEOUT_SECONDS
        ),
        lock_timeout_seconds=_positive_float(
            fleet_cfg.get("lock_timeout_seconds"), _DEFAULT_LOCK_TIMEOUT_SECONDS
        ),
    )
