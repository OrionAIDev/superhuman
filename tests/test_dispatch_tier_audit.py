"""Tests for scripts/superhuman_dispatch_tier_audit.py (roadmap#275).

All transcript fixtures built here are fully synthetic — no content from any
real ``~/.claude/projects`` transcript is copied into this file or into
``tests/fixtures/dispatch_tier_audit/``. Per PROJECT-SPECIFIC CONSTRAINTS,
real transcripts may carry PHI/PII and must never land in the repo.

The synthetic transcripts follow the real Claude Code format documented in
``scripts/superhuman_dispatch_tier_audit.py``'s module docstring:

- ``<project>/<session-id>.jsonl`` — the main session transcript. A dispatch
  is an ``assistant`` record whose ``message.content`` has a
  ``{"type": "tool_use", "name": "Agent", ...}`` block.
- ``<project>/<session-id>/subagents/agent-<id>.meta.json`` — the dispatched
  subagent's metadata (``agentType``, ``toolUseId``, ``model``,
  ``spawnDepth``, ``description``).
- ``<project>/<session-id>/subagents/agent-<id>.jsonl`` — the subagent's own
  transcript: ``assistant`` records with ``message.model``, a top-level
  ``effort`` (or no such key), a top-level ``timestamp``, and
  ``message.usage.{input_tokens,output_tokens}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from scripts import superhuman_dispatch_tier_audit as audit
from scripts import superhuman_profile

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dispatch_tier_audit"
ROLES_DIR = FIXTURES / "roles"
ROLE_TIERS_PATH = FIXTURES / "role-tiers.json"
TIER_AGENTS_PATH = FIXTURES / "tier-agents.json"

DEVELOPER_ROLE_TEXT = (ROLES_DIR / "developer.md").read_text(encoding="utf-8")
ARCHITECT_ROLE_TEXT = (ROLES_DIR / "architect.md").read_text(encoding="utf-8")

TEST_TIER_AGENTS = {
    "standard": "test-tier-standard-subagent",
    "most_capable": "test-tier-most-capable-subagent",
    "most_capable+raised": "test-tier-most-capable-high-effort-subagent",
    "cheap": "test-tier-cheap-subagent",
}


# --------------------------------------------------------------------------
# Synthetic transcript builder
# --------------------------------------------------------------------------


class TranscriptBuilder:
    """Builds a synthetic ``<projects_dir>/<project>/...`` tree for one session."""

    def __init__(self, projects_dir: Path, project: str, session_id: str) -> None:
        self.projects_dir = projects_dir
        self.project_dir = projects_dir / project
        self.session_id = session_id
        self.session_path = self.project_dir / f"{session_id}.jsonl"
        self.subagents_dir = self.project_dir / session_id / "subagents"
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.subagents_dir.mkdir(parents=True, exist_ok=True)
        if not self.session_path.exists():
            self.session_path.write_text("", encoding="utf-8")

    def _append(self, path: Path, record: Mapping[str, object]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def add_dispatch(
        self,
        *,
        tool_use_id: str,
        agent_id: str,
        prompt: str,
        subagent_type: str,
        agent_type: str,
        model_param: str | None = None,
        model_alias: str | None = None,
        description: str = "synthetic dispatch",
        dispatch_ts: str = "2026-09-10T10:00:00.000Z",
        spawn_depth: int = 1,
        turns: list[tuple[str, str | None, str, int, int]] | None = None,
        nested_in: str | None = None,
    ) -> None:
        """Record one synthetic dispatch: the parent tool_use plus the subagent run.

        Args:
            tool_use_id: The dispatching ``Agent`` tool_use block's id.
            agent_id: The dispatched subagent's id (transcript filename stem
                minus the ``agent-`` prefix).
            prompt: The dispatch prompt (role block or non-role marker,
                plus any task brief).
            subagent_type: The harness ``subagent_type`` dispatched.
            agent_type: ``meta.json``'s ``agentType`` (normally identical to
                ``subagent_type``).
            model_param: An explicit ``model=`` passed to the dispatch, if
                any.
            model_alias: ``meta.json``'s own ``model`` field (the alias
                passed, if any) — independent of ``model_param`` so a test
                can exercise the harness metadata without also claiming the
                dispatch passed a `model=` (a role-gate violation).
            description: The dispatch's one-line description.
            dispatch_ts: The dispatching assistant turn's timestamp.
            spawn_depth: ``meta.json``'s ``spawnDepth``.
            turns: ``(model, effort, timestamp, input_tokens,
                output_tokens)`` tuples for the subagent's own transcript.
                Defaults to a single turn matching ``dispatch_ts``.
            nested_in: If set, the parent tool_use block is written into
                this OTHER agent id's own transcript instead of the main
                session transcript (simulates a spawnDepth > 1 nested
                dispatch).
        """
        parent_record = {
            "type": "assistant",
            "timestamp": dispatch_ts,
            "message": {
                "model": "claude-opus-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "Agent",
                        "input": {
                            "description": description,
                            "subagent_type": subagent_type,
                            "prompt": prompt,
                            **({"model": model_param} if model_param is not None else {}),
                        },
                    }
                ],
            },
        }
        if nested_in is None:
            self._append(self.session_path, parent_record)
        else:
            self._append(self.subagents_dir / f"agent-{nested_in}.jsonl", parent_record)

        meta = {
            "agentType": agent_type,
            "description": description,
            "toolUseId": tool_use_id,
            "spawnDepth": spawn_depth,
            **({"model": model_alias} if model_alias is not None else {}),
        }
        (self.subagents_dir / f"agent-{agent_id}.meta.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )

        turn_specs = turns or [("claude-sonnet-5", "medium", dispatch_ts, 100, 50)]
        transcript_path = self.subagents_dir / f"agent-{agent_id}.jsonl"
        for model, effort, ts, in_tok, out_tok in turn_specs:
            record: dict[str, object] = {
                "type": "assistant",
                "timestamp": ts,
                "message": {
                    "model": model,
                    "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
                },
            }
            if effort is not None:
                record["effort"] = effort
            self._append(transcript_path, record)


def _profile(models: dict[str, dict[str, Any]]) -> superhuman_profile.Profile:
    """Build a :class:`superhuman_profile.Profile` directly (bypassing YAML).

    Constructed in-process rather than parsed from a written ``profile.yaml``
    so these tests exercise ``effort``/``raised_effort`` tier entries
    regardless of whether ``superhuman_profile.py``'s YAML loader has yet
    landed support for those keys (a concurrent, in-flight change per this
    chunk's brief) — this module only ever reads tier entries with
    ``.get()``.

    Args:
        models: Tier -> mapping (``primary``, ``effort``, etc.).

    Returns:
        A Profile with the given models block and an otherwise-empty ladder.
    """
    return superhuman_profile.Profile(
        version=1,
        citation=None,
        require_profile=False,
        ladder=(),
        conventions=(),
        models=models,
        path=None,
        digest="sha256:test",
    )


STANDARD_MODELS = {
    "standard": {"primary": "sonnet", "fallback": None, "effort": "medium"},
    "most_capable": {
        "primary": "opus",
        "fallback": None,
        "effort": "medium",
        "raised_effort": "high",
    },
    "cheap": {"primary": "haiku", "fallback": None, "effort": "n/a"},
}


@pytest.fixture
def patched_profile(monkeypatch: pytest.MonkeyPatch) -> superhuman_profile.Profile:
    """Patch ``load_profile`` to return :data:`STANDARD_MODELS`, and return it."""
    profile = _profile(STANDARD_MODELS)
    monkeypatch.setattr(superhuman_profile, "load_profile", lambda path: profile)
    return profile


def _run(projects_dir: Path) -> audit.AuditReport:
    return audit.run_audit(
        projects_dir=projects_dir,
        profile_path=None,
        tier_agents_path=TIER_AGENTS_PATH,
        role_tiers_path=ROLE_TIERS_PATH,
        roles_dir=ROLES_DIR,
        since=None,
        until=None,
    )


# --------------------------------------------------------------------------
# Correct role dispatch
# --------------------------------------------------------------------------


def test_role_dispatch_correct(tmp_path: Path, patched_profile: superhuman_profile.Profile) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nBuild the thing.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        turns=[("claude-sonnet-5", "medium", "2026-09-10T10:00:05.000Z", 100, 50)],
    )
    report = _run(tmp_path)
    assert len(report.audits) == 1
    a = report.audits[0]
    assert a.classification == "role"
    assert a.role == "developer"
    assert a.expected_class == "standard"
    assert a.expected_model == "sonnet"
    assert a.expected_effort == "medium"
    assert a.model_match is True
    assert a.effort_match is True
    assert a.mismatches == ()


def test_wrong_effort_is_flagged(tmp_path: Path, patched_profile: superhuman_profile.Profile) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nBuild the thing.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        turns=[("claude-sonnet-5", "high", "2026-09-10T10:00:05.000Z", 100, 50)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.model_match is True
    assert a.effort_match is False
    assert any("effort" in m for m in a.mismatches)


def test_haiku_null_effort_matches_expected_n_a(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    """A cheap-tier turn with NO effort key is the correct match for expected n/a."""
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt="superhuman-dispatch: non-role\n\nRun the docs sync.",
        subagent_type="test-tier-cheap-subagent",
        agent_type="test-tier-cheap-subagent",
        turns=[("claude-haiku-4-5", None, "2026-09-10T10:00:05.000Z", 20, 10)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.expected_effort == "n/a"
    assert a.actual_effort is None
    assert a.effort_match is True
    assert a.mismatches == ()


def test_effort_mismatch_when_expected_n_a_but_effort_present(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    """n/a is only correct when actual effort is genuinely absent."""
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt="superhuman-dispatch: non-role\n\nRun the docs sync.",
        subagent_type="test-tier-cheap-subagent",
        agent_type="test-tier-cheap-subagent",
        turns=[("claude-haiku-4-5", "low", "2026-09-10T10:00:05.000Z", 20, 10)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.expected_effort == "n/a"
    assert a.effort_match is False


# --------------------------------------------------------------------------
# model= param and non-tier-agent subagent_type
# --------------------------------------------------------------------------


def test_role_dispatch_with_model_param_is_flagged(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nBuild the thing.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        model_param="sonnet",
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.model_param_present is True
    assert any("model= param" in m for m in a.mismatches)


def test_role_dispatch_subagent_type_not_a_tier_agent(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    """Pre-#275 history: a role dispatch to an ordinary agent type, no tier agents exist yet."""
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nBuild the thing.",
        subagent_type="general-purpose",
        agent_type="general-purpose",
        turns=[("claude-sonnet-5", "medium", "2026-09-10T10:00:05.000Z", 100, 50)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.subagent_type_is_tier_agent is False
    assert any("not an allowed tier agent" in m for m in a.mismatches)
    # Expected class still resolves to the role's default (standard) even
    # though the actual subagent_type is not a tier agent.
    assert a.expected_class == "standard"


# --------------------------------------------------------------------------
# Opt-in tiers
# --------------------------------------------------------------------------


def test_opt_in_with_reason_and_matching_tier_agent(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    prompt = (
        DEVELOPER_ROLE_TEXT
        + "\n\nsuperhuman-tier-opt-in: prior sonnet attempt visibly failed\n\nRetry at most_capable."
    )
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt=prompt,
        subagent_type="test-tier-most-capable-subagent",
        agent_type="test-tier-most-capable-subagent",
        turns=[("claude-opus-5", "medium", "2026-09-10T10:00:05.000Z", 100, 50)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.expected_class == "most_capable"
    assert a.expected_model == "opus"
    assert a.model_match is True


def test_opt_in_without_reason_stays_at_default_tier(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    """No opt-in line: dispatching to the opt-in tier anyway is judged against default."""
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nRetry at most_capable, no reason given.",
        subagent_type="test-tier-most-capable-subagent",
        agent_type="test-tier-most-capable-subagent",
        turns=[("claude-opus-5", "medium", "2026-09-10T10:00:05.000Z", 100, 50)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.expected_class == "standard"
    assert a.expected_model == "sonnet"
    # actual ran opus, expected sonnet -> mismatch
    assert a.model_match is False


# --------------------------------------------------------------------------
# Non-role dispatches
# --------------------------------------------------------------------------


def test_non_role_with_tier_agent(tmp_path: Path, patched_profile: superhuman_profile.Profile) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt="superhuman-dispatch: non-role\n\nRun convention checks.",
        subagent_type="test-tier-cheap-subagent",
        agent_type="test-tier-cheap-subagent",
        turns=[("claude-haiku-4-5", None, "2026-09-10T10:00:05.000Z", 20, 10)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.classification == "non_role"
    assert a.expected_class == "cheap"
    assert a.mismatches == ()


def test_non_role_without_tier_agent_and_no_model_is_flagged(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt="superhuman-dispatch: non-role\n\nExplore the repo.",
        subagent_type="Explore",
        agent_type="Explore",
        turns=[("claude-sonnet-5", "high", "2026-09-10T10:00:05.000Z", 20, 10)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.expected_class is None
    assert any("explicit model=" in m for m in a.mismatches)


def test_non_role_without_tier_agent_but_with_model_is_not_flagged(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt="superhuman-dispatch: non-role\n\nExplore the repo.",
        subagent_type="Explore",
        agent_type="Explore",
        model_param="claude-sonnet-5",
        turns=[("claude-sonnet-5", "high", "2026-09-10T10:00:05.000Z", 20, 10)],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.expected_class is None
    assert a.mismatches == ()


# --------------------------------------------------------------------------
# Non-superhuman dispatches (not audited)
# --------------------------------------------------------------------------


def test_non_superhuman_dispatch_is_ignored(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt="Just a plain prose brief with no role block and no marker line.",
        subagent_type="general-purpose",
        agent_type="general-purpose",
        model_param="sonnet",
        turns=[("claude-sonnet-5", "medium", "2026-09-10T10:00:05.000Z", 20, 10)],
    )
    report = _run(tmp_path)
    assert report.audits == []
    assert report.other_by_type[("general-purpose", True)] == 1


# --------------------------------------------------------------------------
# toolUseId join, including nested (spawnDepth > 1) dispatch
# --------------------------------------------------------------------------


def test_join_via_tool_use_id_nested_dispatch(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    # Top-level dispatch: an architect subagent that itself dispatches a
    # nested developer subagent (spawnDepth 2). The nested tool_use block
    # lives inside the architect subagent's OWN transcript, not the main
    # session file.
    b.add_dispatch(
        tool_use_id="toolu_top",
        agent_id="architect1",
        prompt=ARCHITECT_ROLE_TEXT + "\n\nDesign it.",
        subagent_type="test-tier-most-capable-subagent",
        agent_type="test-tier-most-capable-subagent",
        turns=[("claude-opus-5", "medium", "2026-09-10T10:00:05.000Z", 200, 100)],
        spawn_depth=1,
    )
    b.add_dispatch(
        tool_use_id="toolu_nested",
        agent_id="dev1",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nImplement the design.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        turns=[("claude-sonnet-5", "medium", "2026-09-10T10:05:00.000Z", 100, 50)],
        spawn_depth=2,
        nested_in="architect1",
    )
    report = _run(tmp_path)
    assert len(report.audits) == 2
    roles = {a.role for a in report.audits}
    assert roles == {"architect", "developer"}
    nested = next(a for a in report.audits if a.role == "developer")
    assert nested.model_match is True


def test_missing_tool_use_join_is_skipped_not_crashed(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    """A subagent whose toolUseId matches nothing in this session is silently skipped."""
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    meta = {
        "agentType": "test-tier-standard-subagent",
        "description": "orphaned",
        "toolUseId": "toolu_does_not_exist",
        "spawnDepth": 1,
    }
    (b.subagents_dir / "agent-orphan.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (b.subagents_dir / "agent-orphan.jsonl").write_text("", encoding="utf-8")
    report = _run(tmp_path)
    assert report.audits == []
    assert sum(report.other_by_type.values()) == 0


# --------------------------------------------------------------------------
# Model mid-run change
# --------------------------------------------------------------------------


def test_model_change_mid_run_is_flagged(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nBuild the thing.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        turns=[
            ("claude-sonnet-5", "medium", "2026-09-10T10:00:05.000Z", 100, 50),
            ("claude-opus-5", "medium", "2026-09-10T10:01:05.000Z", 100, 50),
        ],
    )
    report = _run(tmp_path)
    a = report.audits[0]
    assert a.actual_model_changed is True
    assert any("changed mid-run" in m for m in a.mismatches)


# --------------------------------------------------------------------------
# Date filter
# --------------------------------------------------------------------------


def test_date_filter_excludes_out_of_range_dispatch(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_old",
        agent_id="old",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nOld dispatch.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        dispatch_ts="2026-01-01T00:00:00.000Z",
        turns=[("claude-sonnet-5", "medium", "2026-01-01T00:00:05.000Z", 100, 50)],
    )
    b.add_dispatch(
        tool_use_id="toolu_new",
        agent_id="new",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nNew dispatch.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        dispatch_ts="2026-09-15T00:00:00.000Z",
        turns=[("claude-sonnet-5", "medium", "2026-09-15T00:00:05.000Z", 100, 50)],
    )
    from datetime import datetime, timezone

    report = audit.run_audit(
        projects_dir=tmp_path,
        profile_path=None,
        tier_agents_path=TIER_AGENTS_PATH,
        role_tiers_path=ROLE_TIERS_PATH,
        roles_dir=ROLES_DIR,
        since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        until=None,
    )
    assert len(report.audits) == 1
    assert report.audits[0].agent_id == "new"


# --------------------------------------------------------------------------
# Summary aggregation
# --------------------------------------------------------------------------


def test_build_summaries_aggregates_per_role(
    tmp_path: Path, patched_profile: superhuman_profile.Profile
) -> None:
    b = TranscriptBuilder(tmp_path, "proj-a", "sess-1")
    b.add_dispatch(
        tool_use_id="toolu_1",
        agent_id="a1",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nOne.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        dispatch_ts="2026-09-10T10:00:00.000Z",
        turns=[("claude-sonnet-5", "medium", "2026-09-10T10:00:00.000Z", 100, 50)],
    )
    b.add_dispatch(
        tool_use_id="toolu_2",
        agent_id="a2",
        prompt=DEVELOPER_ROLE_TEXT + "\n\nTwo.",
        subagent_type="test-tier-standard-subagent",
        agent_type="test-tier-standard-subagent",
        dispatch_ts="2026-09-10T10:10:00.000Z",
        turns=[("claude-sonnet-5", "medium", "2026-09-10T10:10:30.000Z", 200, 75)],
    )
    report = _run(tmp_path)
    summaries = audit.build_summaries(report.audits)
    dev = summaries["developer"]
    assert dev.dispatch_count == 2
    assert dev.model_ok == 2
    assert dev.effort_ok == 2
    assert dev.input_tokens == 300
    assert dev.output_tokens == 125
    wall_clock = dev.wall_clock_seconds()
    assert wall_clock is not None
    assert wall_clock == pytest.approx(630.0)


# --------------------------------------------------------------------------
# Direct unit tests of the comparison primitives (belt-and-suspenders: these
# fail immediately, with no transcript scaffolding, if the comparison rules
# themselves regress).
# --------------------------------------------------------------------------


def test_model_matches_exact() -> None:
    assert audit._model_matches("claude-sonnet-5", "claude-sonnet-5") is True


def test_model_matches_alias_family() -> None:
    assert audit._model_matches("sonnet", "claude-sonnet-5") is True
    assert audit._model_matches("opus", "claude-sonnet-5") is False


def test_model_matches_unknown_expected() -> None:
    assert audit._model_matches(None, "claude-sonnet-5") is None


def test_effort_matches_n_a_and_none() -> None:
    assert audit._effort_matches("n/a", None) is True
    assert audit._effort_matches("n/a", "low") is False
    assert audit._effort_matches("high", "high") is True
    assert audit._effort_matches("high", "medium") is False
    assert audit._effort_matches(None, "high") is None
