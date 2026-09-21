#!/usr/bin/env python3
"""Audit Claude Code superhuman dispatches against the role-tier policy (roadmap#275).

superhuman dispatches role subagents through the ``Agent`` tool. Per
``adaptation/dispatch.md`` "Model-tier selection", each dispatch is supposed to
resolve to a *dispatch class* (``most_capable``, ``most_capable+raised``,
``standard``, ``cheap``) via ``adaptation/role-tiers.json``, and that class
pins a model + reasoning effort via the operator's ``~/.superhuman/profile.yaml``
``models:`` block and, on Claude Code, a generated tier-agent definition
(``templates/agents/claude-code/tier-agents.json``). This script reads real
Claude Code transcripts and checks what each dispatch *actually* ran at
against what it was *expected* to run at.

Transcript format found (read-only inspection of
``~/.claude/projects/<project>/...``, 2026-09-21):

- Each *project* is a directory under ``--projects-dir`` (default
  ``~/.claude/projects``), named after the working directory with path
  separators mangled to ``-``.
- Each top-level *session* is a single file directly inside a project
  directory: ``<project>/<session-id>.jsonl`` (JSON-Lines; one record per
  line). A "main" session's own dispatches live in this file as ``assistant``
  records whose ``message.content`` includes a block with
  ``"type": "tool_use", "name": "Agent"``. That block's ``id`` is the
  ``toolUseId`` a spawned subagent's own metadata file points back to; its
  ``input`` carries ``subagent_type``, ``prompt``, ``description`` and,
  optionally, an explicit ``model``.
- Every subagent dispatched from that session has its own transcript under
  ``<project>/<session-id>/subagents/agent-<hex>.jsonl``, with a sibling
  ``agent-<hex>.meta.json`` holding ``{"agentType", "description",
  "toolUseId", "spawnDepth", "model"}`` — ``toolUseId`` is the id of the
  dispatching ``Agent`` tool_use block (found either in the main session file
  or, for a nested dispatch with ``spawnDepth`` > 1, inside another
  subagent's own transcript in the same session). ``model`` here is the
  *alias* passed to the ``Agent`` tool (e.g. ``"sonnet"``, ``"haiku"``), not
  the concrete model id the subagent actually ran at.
- Within a subagent's own ``.jsonl``, each ``assistant`` record carries the
  *actual* model and effort the turn ran at as top-level/near-top-level
  fields on the record (sibling to ``"type": "assistant"``, not nested under
  ``message``): ``message.model`` (a concrete id, e.g.
  ``"claude-sonnet-5"``), a top-level ``"effort"`` string (e.g. ``"high"``),
  and a top-level ``"timestamp"`` (ISO-8601, ``Z``-suffixed). A model that
  ignores reasoning effort (observed: Haiku) omits the ``"effort"`` key
  entirely rather than writing ``null`` — this script treats a missing key
  and an explicit ``null`` identically, both as "no effort recorded" (which
  is the CORRECT actual value when the expected effort is ``n/a``, not a
  parse gap). Token usage lives at ``message.usage.input_tokens`` /
  ``message.usage.output_tokens`` on the same record.

This script never prints prompt text — only role names, agent types, models,
effort, counts, and timestamps (PROJECT-SPECIFIC CONSTRAINTS: real transcripts
under ``~/.claude/projects`` can carry PHI/PII).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPT_DIR.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts import superhuman_profile  # noqa: E402
from scripts.fleet.role_block import Verdict, check_role_block  # noqa: E402
from scripts.fleet.role_tiers import (  # noqa: E402
    RoleTierError,
    RoleTierPolicy,
    has_opt_in_reason,
    load_harness_class_map,
    load_policy,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISMATCH = 1

#: The Claude Code model aliases whose expected id is a family substring
#: match against the actual concrete model id, rather than an exact match
#: (adaptation/dispatch.md's alias-based tier table; DESIGN intent per the
#: dispatch brief: "alias family match when the profile holds an alias like
#: opus/sonnet/haiku").
_ALIAS_FAMILIES = ("opus", "sonnet", "haiku")

_DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_DEFAULT_PROFILE_PATH = Path.home() / ".superhuman" / "profile.yaml"
_DEFAULT_TIER_AGENTS_PATH = _SKILL_ROOT / "templates" / "agents" / "claude-code" / "tier-agents.json"
_DEFAULT_ROLE_TIERS_PATH = _SKILL_ROOT / "adaptation" / "role-tiers.json"
_DEFAULT_ROLES_DIR = _SKILL_ROOT / "roles"


# --------------------------------------------------------------------------
# Transcript reading
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolUse:
    """One ``Agent`` tool_use block found in some transcript.

    Attributes:
        tool_use_id: The block's own ``id`` — what a subagent's meta.json
            ``toolUseId`` points back to.
        subagent_type: The ``input.subagent_type`` value, or ``None``.
        model_param: The ``input.model`` value, if the dispatch passed one
            explicitly.
        prompt: The dispatch prompt, exactly as sent.
        description: The dispatch's one-line description.
        timestamp: The dispatching assistant turn's ISO-8601 timestamp, if
            present.
    """

    tool_use_id: str
    subagent_type: str | None
    model_param: str | None
    prompt: str
    description: str | None
    timestamp: str | None


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """One assistant turn inside a subagent's own transcript.

    Attributes:
        model: The concrete model id the turn ran at (``message.model``).
        effort: The reasoning effort the turn ran at, or ``None`` if the
            record carries no ``effort`` field (or an explicit ``null`` —
            both mean "not recorded", which is correct for an effort-less
            model such as Haiku).
        timestamp: The turn's ISO-8601 timestamp, or ``None``.
        input_tokens: ``message.usage.input_tokens``, or ``0`` if absent.
        output_tokens: ``message.usage.output_tokens``, or ``0`` if absent.
    """

    model: str | None
    effort: str | None
    timestamp: str | None
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class SubagentRun:
    """One dispatched subagent: its metadata plus its own transcript.

    Attributes:
        agent_id: The subagent's own id (from the transcript filename).
        agent_type: ``meta.json``'s ``agentType`` (the harness
            ``subagent_type`` actually used).
        tool_use_id: ``meta.json``'s ``toolUseId``.
        model_alias: ``meta.json``'s ``model`` (the alias passed to the
            dispatch, if any) — distinct from the actual model the subagent
            ran at.
        spawn_depth: ``meta.json``'s ``spawnDepth``.
        description: ``meta.json``'s ``description``.
        session_id: The owning top-level session's id.
        turns: Every assistant turn found in the subagent's own transcript,
            in file order.
    """

    agent_id: str
    agent_type: str | None
    tool_use_id: str | None
    model_alias: str | None
    spawn_depth: int | None
    description: str | None
    session_id: str
    turns: tuple[AssistantTurn, ...]


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield each parseable JSON object from a JSON-Lines file.

    Args:
        path: The ``.jsonl`` file to read.

    Yields:
        One decoded object per parseable line. Unparseable or non-object
        lines are silently skipped (mirrors this repo's existing tolerance
        of a torn final line — see ``role_block.read_role_gate_log``).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _extract_tool_uses(records: Iterator[dict[str, Any]]) -> Iterator[ToolUse]:
    """Yield every ``Agent`` tool_use block found in a transcript's records.

    Args:
        records: Decoded JSON-Lines records from one transcript file.

    Yields:
        One :class:`ToolUse` per ``Agent`` dispatch found.
    """
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        timestamp = record.get("timestamp")
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use" or block.get("name") != "Agent":
                continue
            tool_use_id = block.get("id")
            if not isinstance(tool_use_id, str):
                continue
            dispatch_input = block.get("input")
            if not isinstance(dispatch_input, dict):
                dispatch_input = {}
            prompt = dispatch_input.get("prompt")
            if not isinstance(prompt, str):
                continue
            subagent_type = dispatch_input.get("subagent_type")
            model_param = dispatch_input.get("model")
            description = dispatch_input.get("description")
            yield ToolUse(
                tool_use_id=tool_use_id,
                subagent_type=subagent_type if isinstance(subagent_type, str) else None,
                model_param=model_param if isinstance(model_param, str) else None,
                prompt=prompt,
                description=description if isinstance(description, str) else None,
                timestamp=timestamp if isinstance(timestamp, str) else None,
            )


def _assistant_turns(records: Iterator[dict[str, Any]]) -> Iterator[AssistantTurn]:
    """Yield every assistant turn's model/effort/timestamp/usage.

    Args:
        records: Decoded JSON-Lines records from a subagent's own
            transcript.

    Yields:
        One :class:`AssistantTurn` per assistant record, in file order.
    """
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        model = message.get("model")
        usage = message.get("usage")
        input_tokens = 0
        output_tokens = 0
        if isinstance(usage, dict):
            raw_in = usage.get("input_tokens")
            raw_out = usage.get("output_tokens")
            if isinstance(raw_in, int):
                input_tokens = raw_in
            if isinstance(raw_out, int):
                output_tokens = raw_out
        effort = record.get("effort")
        yield AssistantTurn(
            model=model if isinstance(model, str) else None,
            effort=effort if isinstance(effort, str) else None,
            timestamp=record.get("timestamp") if isinstance(record.get("timestamp"), str) else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


@dataclass(frozen=True, slots=True)
class Session:
    """One top-level session: its own transcript plus its subagents.

    Attributes:
        session_id: The session's id (its ``.jsonl`` filename stem).
        project_dir: The project directory it lives under.
        transcript_path: Path to ``<session_id>.jsonl``.
        subagents: Every dispatched subagent found under
            ``<session_id>/subagents/``.
    """

    session_id: str
    project_dir: Path
    transcript_path: Path
    subagents: tuple[SubagentRun, ...]


def _load_subagent(meta_path: Path, session_id: str) -> SubagentRun | None:
    """Load one subagent's metadata plus its own transcript.

    Args:
        meta_path: The ``agent-<hex>.meta.json`` file.
        session_id: The owning session's id.

    Returns:
        The loaded run, or ``None`` if the metadata file is unreadable/not
        a JSON object.
    """
    try:
        meta_raw = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta_raw, dict):
        return None
    agent_id = meta_path.name[len("agent-") : -len(".meta.json")]
    transcript_path = meta_path.with_name(f"agent-{agent_id}.jsonl")
    turns = tuple(_assistant_turns(_iter_jsonl(transcript_path))) if transcript_path.is_file() else ()
    spawn_depth = meta_raw.get("spawnDepth")
    return SubagentRun(
        agent_id=agent_id,
        agent_type=meta_raw.get("agentType") if isinstance(meta_raw.get("agentType"), str) else None,
        tool_use_id=meta_raw.get("toolUseId") if isinstance(meta_raw.get("toolUseId"), str) else None,
        model_alias=meta_raw.get("model") if isinstance(meta_raw.get("model"), str) else None,
        spawn_depth=spawn_depth if isinstance(spawn_depth, int) else None,
        description=meta_raw.get("description") if isinstance(meta_raw.get("description"), str) else None,
        session_id=session_id,
        turns=turns,
    )


def iter_sessions(projects_dir: Path) -> Iterator[Session]:
    """Walk ``projects_dir`` and yield every top-level session found.

    Args:
        projects_dir: The Claude Code projects root (default
            ``~/.claude/projects``).

    Yields:
        One :class:`Session` per top-level ``<project>/<session-id>.jsonl``
        file, whether or not it dispatched any subagents.
    """
    if not projects_dir.is_dir():
        return
    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        for transcript_path in sorted(project_dir.glob("*.jsonl")):
            session_id = transcript_path.stem
            subagents_dir = project_dir / session_id / "subagents"
            subagents: list[SubagentRun] = []
            if subagents_dir.is_dir():
                for meta_path in sorted(subagents_dir.glob("agent-*.meta.json")):
                    run = _load_subagent(meta_path, session_id)
                    if run is not None:
                        subagents.append(run)
            yield Session(
                session_id=session_id,
                project_dir=project_dir,
                transcript_path=transcript_path,
                subagents=tuple(subagents),
            )


def _tool_use_index(session: Session) -> dict[str, ToolUse]:
    """Build tool_use id -> :class:`ToolUse` across a session's own transcripts.

    A dispatch's ``Agent`` tool_use block can live in the session's main
    transcript (an ordinary, top-level dispatch) or inside another
    subagent's own transcript (a nested dispatch, ``spawnDepth`` > 1).

    Args:
        session: The session to index.

    Returns:
        Every ``Agent`` tool_use block found, keyed by its id. Later reads
        never overwrite earlier ones (tool_use ids are unique per API
        response; a collision would indicate corrupt input, not a real
        second dispatch).
    """
    index: dict[str, ToolUse] = {}
    for tool_use in _extract_tool_uses(_iter_jsonl(session.transcript_path)):
        index.setdefault(tool_use.tool_use_id, tool_use)
    for run in session.subagents:
        transcript_path = session.project_dir / session.session_id / "subagents" / f"agent-{run.agent_id}.jsonl"
        if not transcript_path.is_file():
            continue
        for tool_use in _extract_tool_uses(_iter_jsonl(transcript_path)):
            index.setdefault(tool_use.tool_use_id, tool_use)
    return index


# --------------------------------------------------------------------------
# Classification and comparison
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispatchAudit:
    """One audited superhuman dispatch (a ``ROLE`` or ``NON_ROLE`` verdict).

    Attributes:
        session_id: The owning session.
        agent_id: The dispatched subagent's id.
        classification: ``"role"`` or ``"non_role"``.
        role: The role name, for a role dispatch (``None`` for non-role).
        subagent_type: The harness ``subagent_type`` actually dispatched.
        expected_class: The dispatch class this dispatch was expected to
            run at, or ``None`` if it could not be determined (a non-role
            dispatch whose subagent_type names no tier agent).
        expected_model: The expected model alias, or ``None`` if unknown
            (no resolvable dispatch class, or the profile does not
            configure that tier at all).
        expected_effort: The expected effort (``"n/a"`` included), or
            ``None`` if unknown.
        actual_model: The first assistant turn's model, or ``None``.
        actual_model_changed: Whether later turns ran at a different model.
        actual_effort: The first assistant turn's effort, or ``None``.
        model_match: ``True``/``False``, or ``None`` if the expectation
            itself is unknown and so cannot be judged.
        effort_match: ``True``/``False``, or ``None`` likewise.
        model_param_present: Whether the dispatch passed an explicit
            ``model=`` input.
        subagent_type_is_tier_agent: Whether ``subagent_type`` names one of
            the four generated Claude Code tier agents.
        input_tokens: Total ``message.usage.input_tokens`` across the
            subagent's own turns.
        output_tokens: Total ``message.usage.output_tokens`` likewise.
        timestamps: Every assistant turn's timestamp (subagent's own
            transcript), for wall-clock aggregation.
        mismatches: Human-readable reasons this dispatch failed its
            expectation. Empty means clean.
    """

    session_id: str
    agent_id: str
    classification: str
    role: str | None
    subagent_type: str | None
    expected_class: str | None
    expected_model: str | None
    expected_effort: str | None
    actual_model: str | None
    actual_model_changed: bool
    actual_effort: str | None
    model_match: bool | None
    effort_match: bool | None
    model_param_present: bool
    subagent_type_is_tier_agent: bool
    input_tokens: int
    output_tokens: int
    timestamps: tuple[str, ...]
    mismatches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OtherDispatch:
    """A dispatch this audit does not classify as role/non-role.

    Attributes:
        subagent_type: The harness ``subagent_type`` dispatched, or
            ``None``.
        has_model: Whether the dispatch passed an explicit ``model=``.
    """

    subagent_type: str | None
    has_model: bool


def _model_matches(expected: str | None, actual: str | None) -> bool | None:
    """Compare an expected model alias/id against the actual model id.

    Args:
        expected: The expected model (a profile alias or concrete id), or
            ``None`` if unknown.
        actual: The concrete model id the subagent actually ran at.

    Returns:
        ``True``/``False`` on a resolvable comparison, ``None`` if
        ``expected`` is unknown and so nothing can be judged.
    """
    if expected is None:
        return None
    if actual == expected:
        return True
    if actual is not None and expected in _ALIAS_FAMILIES and expected in actual:
        return True
    return False


def _effort_matches(expected: str | None, actual: str | None) -> bool | None:
    """Compare an expected effort against the actual effort.

    Args:
        expected: The expected effort (``"n/a"`` included), or ``None`` if
            unknown.
        actual: The recorded effort, or ``None`` if the turn carried none.

    Returns:
        ``True``/``False`` on a resolvable comparison, ``None`` if
        ``expected`` is unknown. ``expected == "n/a"`` matches
        ``actual is None`` (a Haiku-class turn correctly recording no
        effort), not a missing-data gap.
    """
    if expected is None:
        return None
    if expected == "n/a":
        return actual is None
    return actual == expected


def _tier_for_class(dispatch_class: str) -> str:
    """Return the profile tier name a dispatch class's model/effort come from.

    Args:
        dispatch_class: e.g. ``"most_capable+raised"``.

    Returns:
        The base tier name (``"most_capable"``, ``"standard"``, ``"cheap"``).
    """
    return dispatch_class.split("+raised")[0]


def _expected_model_effort(
    dispatch_class: str, profile_models: dict[str, dict[str, Any]]
) -> tuple[str | None, str | None]:
    """Resolve a dispatch class to its expected model alias and effort.

    Tolerant of a profile whose ``models:`` tier entries do not (yet) carry
    ``effort``/``raised_effort`` keys — those are read with ``.get()`` and
    resolve to ``None`` ("unknown") rather than raising, since another
    concurrent change is adding them to the loader.

    Args:
        dispatch_class: e.g. ``"standard"`` or ``"most_capable+raised"``.
        profile_models: ``Profile.models`` — tier -> mapping.

    Returns:
        ``(expected_model, expected_effort)``, either possibly ``None``.
    """
    tier = _tier_for_class(dispatch_class)
    entry = profile_models.get(tier)
    if not isinstance(entry, dict):
        return None, None
    expected_model = entry.get("primary")
    if not isinstance(expected_model, str):
        expected_model = None
    if dispatch_class.endswith("+raised"):
        expected_effort = entry.get("raised_effort")
    else:
        expected_effort = entry.get("effort")
    if not isinstance(expected_effort, str):
        expected_effort = None
    return expected_model, expected_effort


def classify_dispatch(  # noqa: PLR0913 - a single audit decision; splitting would scatter its inputs
    *,
    session: Session,
    run: SubagentRun,
    tool_use: ToolUse,
    policy: RoleTierPolicy,
    class_to_agent: dict[str, str],
    profile_models: dict[str, dict[str, Any]],
    roles_dir: Path,
) -> DispatchAudit | OtherDispatch:
    """Classify and audit one dispatched subagent.

    Args:
        session: The owning session.
        run: The dispatched subagent (its metadata + own transcript).
        tool_use: The dispatching ``Agent`` tool_use block.
        policy: The parsed role-tier policy.
        class_to_agent: Dispatch class -> Claude Code ``subagent_type``.
        profile_models: The operator profile's ``models:`` block.
        roles_dir: Directory holding ``roles/*.md`` (for the role-block
            check).

    Returns:
        A :class:`DispatchAudit` for a ``ROLE``/``NON_ROLE`` dispatch, or an
        :class:`OtherDispatch` for anything else (a dispatch whose prompt
        does not open with a role block or the ``non-role`` marker).
    """
    agent_to_class = {agent: cls for cls, agent in class_to_agent.items()}
    verdict = check_role_block(tool_use.prompt, roles_dir)

    if verdict.verdict not in (Verdict.ROLE, Verdict.NON_ROLE):
        return OtherDispatch(
            subagent_type=tool_use.subagent_type, has_model=tool_use.model_param is not None
        )

    subagent_type = tool_use.subagent_type
    is_tier_agent = subagent_type in agent_to_class
    mismatches: list[str] = []

    if verdict.verdict is Verdict.ROLE:
        classification = "role"
        role = verdict.role
        rule = policy.roles.get(role) if role is not None else None
        if rule is None:
            mismatches.append(f"role {role!r} has no role-tiers.json policy row")
            expected_class: str | None = None
        else:
            expected_class = rule.default
            if has_opt_in_reason(tool_use.prompt) and is_tier_agent:
                actual_class = agent_to_class.get(subagent_type) if subagent_type is not None else None
                if actual_class in rule.opt_in:
                    expected_class = actual_class
        if tool_use.model_param is not None:
            mismatches.append("role dispatch passed an explicit model= param")
        if not is_tier_agent:
            mismatches.append(f"subagent_type {subagent_type!r} is not an allowed tier agent")
    else:
        classification = "non_role"
        role = None
        if is_tier_agent:
            expected_class = agent_to_class.get(subagent_type) if subagent_type is not None else None
        else:
            expected_class = None
            if tool_use.model_param is None:
                mismatches.append(
                    "non-role dispatch names no tier agent and passes no explicit model="
                )

    if expected_class is not None:
        expected_model, expected_effort = _expected_model_effort(expected_class, profile_models)
    else:
        expected_model, expected_effort = None, None

    turns = run.turns
    actual_model = turns[0].model if turns else None
    actual_model_changed = any(t.model != actual_model for t in turns[1:])
    actual_effort = turns[0].effort if turns else None

    model_match = _model_matches(expected_model, actual_model)
    effort_match = _effort_matches(expected_effort, actual_effort)

    if model_match is False:
        mismatches.append(f"model: expected {expected_model!r}, got {actual_model!r}")
    if effort_match is False:
        mismatches.append(f"effort: expected {expected_effort!r}, got {actual_effort!r}")
    if actual_model_changed:
        mismatches.append("model changed mid-run")

    input_tokens = sum(t.input_tokens for t in turns)
    output_tokens = sum(t.output_tokens for t in turns)
    timestamps = tuple(t.timestamp for t in turns if t.timestamp is not None)

    return DispatchAudit(
        session_id=session.session_id,
        agent_id=run.agent_id,
        classification=classification,
        role=role,
        subagent_type=subagent_type,
        expected_class=expected_class,
        expected_model=expected_model,
        expected_effort=expected_effort,
        actual_model=actual_model,
        actual_model_changed=actual_model_changed,
        actual_effort=actual_effort,
        model_match=model_match,
        effort_match=effort_match,
        model_param_present=tool_use.model_param is not None,
        subagent_type_is_tier_agent=is_tier_agent,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        timestamps=timestamps,
        mismatches=tuple(mismatches),
    )


# --------------------------------------------------------------------------
# Aggregation and reporting
# --------------------------------------------------------------------------


@dataclass
class RoleSummary:
    """Aggregate stats for one role (or non-role duty group).

    Attributes:
        dispatch_count: Number of dispatches audited under this key.
        model_ok: Dispatches whose model matched (``model_match is True``).
        model_unknown: Dispatches whose model could not be judged.
        effort_ok: Dispatches whose effort matched.
        effort_unknown: Dispatches whose effort could not be judged.
        mismatch_count: Dispatches with at least one mismatch reason.
        input_tokens: Summed input tokens.
        output_tokens: Summed output tokens.
        timestamps: Every timestamp seen, for the wall-clock span.
    """

    dispatch_count: int = 0
    model_ok: int = 0
    model_unknown: int = 0
    effort_ok: int = 0
    effort_unknown: int = 0
    mismatch_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    timestamps: list[str] = field(default_factory=list)

    def wall_clock_seconds(self) -> float | None:
        """Return the span from the earliest to the latest timestamp seen.

        Returns:
            Seconds between the first and last timestamp, or ``None`` if
            fewer than two timestamps (or none) were recorded.
        """
        non_null = [dt for dt in (_parse_ts(t) for t in self.timestamps) if dt is not None]
        if len(non_null) < 2:
            return None
        parsed = sorted(non_null)
        return (parsed[-1] - parsed[0]).total_seconds()


def _parse_ts(value: str) -> datetime | None:
    """Parse an ISO-8601, ``Z``-suffixed timestamp.

    Args:
        value: e.g. ``"2026-08-14T13:59:42.358Z"``.

    Returns:
        A timezone-aware :class:`datetime`, or ``None`` if unparseable.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _in_range(ts: str | None, since: datetime | None, until: datetime | None) -> bool:
    """Return whether a dispatch timestamp falls within ``[since, until]``.

    A dispatch with no timestamp always passes (never silently dropped by a
    date filter it cannot be judged against).

    Args:
        ts: The dispatch's own timestamp, if any.
        since: Inclusive lower bound, or ``None``.
        until: Inclusive upper bound, or ``None``.

    Returns:
        Whether the dispatch should be included.
    """
    if ts is None or (since is None and until is None):
        return True
    parsed = _parse_ts(ts)
    if parsed is None:
        return True
    if since is not None and parsed < since:
        return False
    if until is not None and parsed > until:
        return False
    return True


@dataclass
class AuditReport:
    """The full result of one audit run.

    Attributes:
        audits: Every audited ``ROLE``/``NON_ROLE`` dispatch, in discovery
            order.
        other_by_type: Non-audited dispatch counts, keyed by
            ``(subagent_type, has_model)``.
    """

    audits: list[DispatchAudit] = field(default_factory=list)
    other_by_type: Counter[tuple[str | None, bool]] = field(default_factory=Counter)


def run_audit(
    *,
    projects_dir: Path,
    profile_path: Path | None,
    tier_agents_path: Path,
    role_tiers_path: Path,
    roles_dir: Path,
    since: datetime | None,
    until: datetime | None,
) -> AuditReport:
    """Walk every session under ``projects_dir`` and audit its dispatches.

    Args:
        projects_dir: The Claude Code projects root.
        profile_path: The operator profile, or ``None`` for the built-in
            (empty ``models:``) profile.
        tier_agents_path: ``templates/agents/claude-code/tier-agents.json``.
        role_tiers_path: ``adaptation/role-tiers.json``.
        roles_dir: Directory holding ``roles/*.md``.
        since: Inclusive lower bound on a dispatch's own timestamp.
        until: Inclusive upper bound likewise.

    Returns:
        The full report.
    """
    policy = load_policy(role_tiers_path)
    class_to_agent = load_harness_class_map(tier_agents_path)
    profile = superhuman_profile.load_profile(profile_path)

    report = AuditReport()
    for session in iter_sessions(projects_dir):
        if not session.subagents:
            continue
        tool_use_index = _tool_use_index(session)
        for run in session.subagents:
            if run.tool_use_id is None:
                continue
            tool_use = tool_use_index.get(run.tool_use_id)
            if tool_use is None:
                continue
            if not _in_range(tool_use.timestamp, since, until):
                continue
            result = classify_dispatch(
                session=session,
                run=run,
                tool_use=tool_use,
                policy=policy,
                class_to_agent=class_to_agent,
                profile_models=profile.models,
                roles_dir=roles_dir,
            )
            if isinstance(result, DispatchAudit):
                report.audits.append(result)
            else:
                report.other_by_type[(result.subagent_type, result.has_model)] += 1
    return report


def _group_key(audit: DispatchAudit) -> str:
    """Return the reporting group key for one audit: role name or a duty label.

    Args:
        audit: The audited dispatch.

    Returns:
        The role name for a role dispatch; ``"non-role"`` for a non-role
        dispatch (non-role dispatches carry no duty name in the prompt, so
        they cannot be split further — see the module's classification
        notes).
    """
    if audit.role is not None:
        return audit.role
    return "non-role"


def build_summaries(audits: list[DispatchAudit]) -> dict[str, RoleSummary]:
    """Aggregate audited dispatches into a per-role/duty summary.

    Args:
        audits: Every audited dispatch.

    Returns:
        Group key (role name, or ``"non-role"``) -> aggregate stats.
    """
    summaries: dict[str, RoleSummary] = defaultdict(RoleSummary)
    for audit in audits:
        summary = summaries[_group_key(audit)]
        summary.dispatch_count += 1
        if audit.model_match is True:
            summary.model_ok += 1
        elif audit.model_match is None:
            summary.model_unknown += 1
        if audit.effort_match is True:
            summary.effort_ok += 1
        elif audit.effort_match is None:
            summary.effort_unknown += 1
        if audit.mismatches:
            summary.mismatch_count += 1
        summary.input_tokens += audit.input_tokens
        summary.output_tokens += audit.output_tokens
        summary.timestamps.extend(audit.timestamps)
    return dict(summaries)


def _audit_to_json(audit: DispatchAudit) -> dict[str, Any]:
    """Render one audit as a JSON-serialisable dict (no prompt text)."""
    return {
        "session_id": audit.session_id,
        "agent_id": audit.agent_id,
        "classification": audit.classification,
        "role": audit.role,
        "subagent_type": audit.subagent_type,
        "expected_class": audit.expected_class,
        "expected_model": audit.expected_model,
        "expected_effort": audit.expected_effort,
        "actual_model": audit.actual_model,
        "actual_model_changed": audit.actual_model_changed,
        "actual_effort": audit.actual_effort,
        "model_match": audit.model_match,
        "effort_match": audit.effort_match,
        "model_param_present": audit.model_param_present,
        "subagent_type_is_tier_agent": audit.subagent_type_is_tier_agent,
        "input_tokens": audit.input_tokens,
        "output_tokens": audit.output_tokens,
        "mismatches": list(audit.mismatches),
    }


def render_json(report: AuditReport, summaries: dict[str, RoleSummary]) -> str:
    """Render the full report as JSON.

    Args:
        report: The audit report.
        summaries: Per-role/duty aggregates.

    Returns:
        A JSON string (never containing prompt text).
    """
    payload = {
        "roles": {
            role: {
                "dispatch_count": summary.dispatch_count,
                "model_ok": summary.model_ok,
                "model_unknown": summary.model_unknown,
                "effort_ok": summary.effort_ok,
                "effort_unknown": summary.effort_unknown,
                "mismatch_count": summary.mismatch_count,
                "input_tokens": summary.input_tokens,
                "output_tokens": summary.output_tokens,
                "wall_clock_seconds": summary.wall_clock_seconds(),
            }
            for role, summary in sorted(summaries.items())
        },
        "other_dispatches": [
            {"subagent_type": subagent_type, "has_model": has_model, "count": count}
            for (subagent_type, has_model), count in sorted(
                report.other_by_type.items(), key=lambda kv: (-kv[1], str(kv[0][0]))
            )
        ],
        "dispatches": [_audit_to_json(a) for a in report.audits],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def render_text(report: AuditReport, summaries: dict[str, RoleSummary], *, mismatches_only: bool) -> str:
    """Render the full report as the default human-readable text.

    Args:
        report: The audit report.
        summaries: Per-role/duty aggregates.
        mismatches_only: If set, the per-dispatch mismatch list is the only
            section printed with detail; the summary table still prints.

    Returns:
        The report text.
    """
    lines: list[str] = []
    lines.append(
        f"{'role/duty':<20} {'dispatches':>10} {'model ok':>10} {'effort ok':>10} "
        f"{'mismatches':>11} {'in tok':>10} {'out tok':>10} {'wall clock':>12}"
    )
    for role, summary in sorted(summaries.items()):
        wall_clock = summary.wall_clock_seconds()
        wall_clock_str = f"{wall_clock:.0f}s" if wall_clock is not None else "n/a"
        model_ok_str = f"{summary.model_ok}/{summary.dispatch_count - summary.model_unknown}"
        effort_ok_str = f"{summary.effort_ok}/{summary.dispatch_count - summary.effort_unknown}"
        lines.append(
            f"{role:<20} {summary.dispatch_count:>10} {model_ok_str:>10} {effort_ok_str:>10} "
            f"{summary.mismatch_count:>11} {summary.input_tokens:>10} {summary.output_tokens:>10} "
            f"{wall_clock_str:>12}"
        )

    if report.other_by_type:
        lines.append("")
        lines.append("non-superhuman dispatches (not audited):")
        for (subagent_type, has_model), count in sorted(
            report.other_by_type.items(), key=lambda kv: (-kv[1], str(kv[0][0]))
        ):
            model_note = "with model=" if has_model else "no model="
            lines.append(f"  {subagent_type!r:<30} {model_note:<14} x{count}")

    lines.append("")
    lines.append("mismatches:")
    mismatch_audits = [a for a in report.audits if a.mismatches]
    if not mismatch_audits:
        lines.append("  (none)")
    for audit in mismatch_audits:
        lines.append(
            f"  session={audit.session_id} agent={audit.agent_id} "
            f"role={audit.role or 'non-role'} subagent_type={audit.subagent_type!r}: "
            f"{'; '.join(audit.mismatches)}"
        )

    if not mismatches_only:
        return "\n".join(lines)
    # mismatches_only still needs the summary table for context; only the
    # per-dispatch clean rows are the thing being suppressed, and this audit
    # never printed those in the first place, so mismatches_only changes
    # nothing further here beyond what was already built above.
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parse_date(value: str, *, end_of_day: bool) -> datetime:
    """Parse a ``YYYY-MM-DD`` CLI argument into a UTC ``datetime``.

    Args:
        value: The CLI-supplied date string.
        end_of_day: If set, resolves to the last instant of that day
            (for ``--until``); otherwise the first instant (for ``--since``).

    Returns:
        A timezone-aware UTC datetime.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is not ``YYYY-MM-DD``.
    """
    try:
        day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not YYYY-MM-DD") from exc
    if end_of_day:
        return day.replace(hour=23, minute=59, second=59, microsecond=999999)
    return day


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="superhuman-dispatch-tier-audit",
        description=(
            "Audit Claude Code superhuman dispatches: compare the model/effort each "
            "role dispatch was expected to run at (role-tiers.json + profile.yaml) "
            "against what it actually ran at (Claude Code transcripts)."
        ),
    )
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=_DEFAULT_PROJECTS_DIR,
        help="Claude Code projects root (default: ~/.claude/projects)",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="operator profile.yaml (default: ~/.superhuman/profile.yaml)",
    )
    parser.add_argument("--since", help="only dispatches on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", help="only dispatches on/before this date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--mismatches-only", action="store_true", help="print only the mismatch list (still shows the summary table)"
    )
    parser.add_argument(
        "--fail-on-mismatch",
        action="store_true",
        help="exit 1 if any dispatch has a mismatch (for CI use; default always exits 0)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    since = _parse_date(args.since, end_of_day=False) if args.since else None
    until = _parse_date(args.until, end_of_day=True) if args.until else None

    profile_path = args.profile if args.profile is not None else _DEFAULT_PROFILE_PATH
    if not profile_path.is_file():
        profile_path = None  # falls back to the built-in zero-config profile

    try:
        report = run_audit(
            projects_dir=args.projects_dir,
            profile_path=profile_path,
            tier_agents_path=_DEFAULT_TIER_AGENTS_PATH,
            role_tiers_path=_DEFAULT_ROLE_TIERS_PATH,
            roles_dir=_DEFAULT_ROLES_DIR,
            since=since,
            until=until,
        )
    except (RoleTierError, superhuman_profile.ProfileError) as exc:
        print(f"superhuman-dispatch-tier-audit: {exc}", file=sys.stderr)
        return EXIT_USAGE

    summaries = build_summaries(report.audits)

    if args.json:
        print(render_json(report, summaries))
    else:
        print(render_text(report, summaries, mismatches_only=args.mismatches_only))

    if args.fail_on_mismatch and any(a.mismatches for a in report.audits):
        return EXIT_MISMATCH
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
