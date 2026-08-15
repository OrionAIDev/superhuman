"""Tests for ``scripts.fleet.core.done`` (PLAN.md Chunk 5, TEST.md TC-26..TC-32).

Covers the acceptance criteria in ``docs/superhuman/session-tracking/PLAN.md``
Chunk 5: ``D0-code -> D1-merged`` requires merge evidence (TC-26);
``D1-merged -> D2-test`` requires deploy+test evidence as a conjunction
(TC-27); ``D2-test -> D3-uat`` and ``D3-uat -> D4-prod`` both require a
recorded human approver (TC-28/TC-29); every skip-level and backward
transition (including same-level) is rejected regardless of evidence
(TC-30); the project's D-ceiling blocks advancement even with full
evidence + approver (TC-31); and ``core/done.py`` never imports or calls
anything LLM/model/adapter-shaped (TC-32, DP#5). Exhaustive per-rung
coverage (every adjacent-forward, every backward, skip-level, same-level,
both evidence gates, the approver gate, and the ceiling) is asserted
directly against the transition table so `--cov-branch --cov-fail-under=100`
on `core/done.py` is a real property, not merely "the file was imported."
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts.fleet.core.done import DONE_LEVELS, AdvanceResult, advance, event_for
from scripts.fleet.core.errors import DonePolicyError
from scripts.fleet.core.events import read_all

_PROJECT_ID = "proj-done"
_WRITER_ROLE = "Project Manager"
_NODE = "claude/repo/proj/session-1"
_TOP_CEILING = "D4-prod"  # no ceiling in practice — the top of the ladder


def _log(tmp_path: Path) -> Path:
    return tmp_path / "events.jsonl"


def _advance(log_path: Path, target: str, *, evidence=None, approver=None, ceiling=_TOP_CEILING):
    return advance(
        _NODE,
        target,
        evidence=evidence,
        approver=approver,
        ceiling=ceiling,
        project_id=_PROJECT_ID,
        writer_role=_WRITER_ROLE,
        log_path=log_path,
    )


def _full_evidence_for(target: str) -> dict[str, str]:
    """Return evidence that satisfies every gate `target` might have.

    Used by TC-30/TC-31 fixtures so a rejection can be attributed
    unambiguously to the adjacency/ceiling check under test, never to a
    starved evidence gate.
    """
    return {"commit": "abc123", "pr": "42", "deploy_id": "dep-1", "ci_run": "ci-1"}


def _full_approver() -> str:
    return "Jordan Rivera"


def _advance_to(log_path: Path, target: str) -> None:
    """Drive a node from D0-code straight up to `target` via legitimate calls."""
    for level in DONE_LEVELS[1 : DONE_LEVELS.index(target) + 1]:
        _advance(
            log_path,
            level,
            evidence=_full_evidence_for(level),
            approver=_full_approver() if level in ("D3-uat", "D4-prod") else None,
        )


class TestTC26MergeEvidenceGate:
    """TC-26: D0-code -> D1-merged requires merge evidence."""

    def test_advance_with_commit_evidence_succeeds(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        result = _advance(log_path, "D1-merged", evidence={"commit": "abc123"})

        assert result == AdvanceResult(status="advanced", node_id=_NODE, level="D1-merged")
        events = read_all(log_path)
        assert len(events) == 1
        assert events[0].type == "done_level_advanced"
        assert events[0].payload["done_level"] == "D1-merged"
        assert events[0].payload["evidence"] == {"commit": "abc123"}

    def test_advance_with_pr_evidence_succeeds(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        result = _advance(log_path, "D1-merged", evidence={"pr": "42"})
        assert result.status == "advanced"

    def test_advance_without_merge_evidence_raises_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        log_path = _log(tmp_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D1-merged", evidence={})

        assert read_all(log_path) == []

    def test_advance_with_none_evidence_raises_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        log_path = _log(tmp_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D1-merged", evidence=None)

        assert read_all(log_path) == []

    def test_whitespace_only_commit_evidence_raises_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        """G5 fix #3: `_has_merge_evidence` must not treat whitespace-only
        as present — `bool("   ")` is `True`, so the pre-fix gate silently
        accepted a blank-looking commit reference."""
        log_path = _log(tmp_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D1-merged", evidence={"commit": "   "})

        assert read_all(log_path) == []


class TestTC27DeployTestEvidenceConjunction:
    """TC-27: D1-merged -> D2-test requires deploy+test evidence (conjunction)."""

    def _at_d1(self, log_path: Path) -> None:
        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})

    def test_both_deploy_and_test_evidence_succeeds(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d1(log_path)

        result = _advance(
            log_path, "D2-test", evidence={"deploy_id": "dep-1", "ci_run": "ci-1"}
        )

        assert result.status == "advanced"

    def test_ci_run_only_raises_and_writes_nothing(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d1(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D2-test", evidence={"ci_run": "ci-1"})

        assert len(read_all(log_path)) == 1  # only the D1 event from setup

    def test_whitespace_only_deploy_and_test_evidence_raises_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        """G5 fix #3: a whitespace-only value for either D2-test evidence key
        must not satisfy the conjunction."""
        log_path = _log(tmp_path)
        self._at_d1(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D2-test", evidence={"deploy_id": "d", "ci_run": "\n"})

        assert len(read_all(log_path)) == 1

    def test_deploy_id_only_raises_and_writes_nothing(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d1(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D2-test", evidence={"deploy_id": "dep-1"})

        assert len(read_all(log_path)) == 1

    def test_neither_evidence_raises_and_writes_nothing(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d1(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D2-test", evidence={})

        assert len(read_all(log_path)) == 1


class TestTC28HumanApproverGateD3:
    """TC-28: D2-test -> D3-uat requires a recorded human approver."""

    def _at_d2(self, log_path: Path) -> None:
        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})
        _advance(log_path, "D2-test", evidence={"deploy_id": "dep-1", "ci_run": "ci-1"})

    def test_with_named_human_approver_succeeds(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d2(log_path)

        result = _advance(log_path, "D3-uat", evidence={"env": "uat"}, approver="Jordan Rivera")

        assert result.status == "advanced"
        events = read_all(log_path)
        assert events[-1].payload["approver"] == "Jordan Rivera"

    def test_missing_approver_raises_even_with_full_evidence(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d2(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D3-uat", evidence={"env": "uat"}, approver=None)

        assert len(read_all(log_path)) == 2  # only the D1/D2 setup events

    def test_blank_approver_raises(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d2(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D3-uat", evidence={"env": "uat"}, approver="   ")

    @pytest.mark.parametrize(
        "approver",
        [
            "jenkins",
            "Jenkins",
            "cron",
            "CI",
            "cd",
            "automation",
            "system",
            "robot",
            "bot",
            "daemon",
            "github-actions",
            "gitlab-ci",
            "circleci",
            "travis",
            "service-account",
            "noreply",
            "no-reply",
            "0",
            ".",
            "123",
            "--",
            "",
            "   ",
        ],
    )
    def test_bot_shaped_approver_is_rejected(self, tmp_path: Path, approver: str) -> None:
        """G5 fix #2: an automation-stem-shaped or trivial approver is not a
        human identity, and is rejected the same as a model/vendor-shaped
        one.

        `core/done.py`'s human-approver gate rejects three overlapping
        categories, matched at TOKEN boundaries (never raw substring, so a
        human "Cindy"/"Cicero" is never rejected for containing "ci" — see
        `test_plausible_human_names_are_accepted` below): (1)
        `core.schema.is_model_vendor_name` matches (claude, gpt, ...); (2)
        a whole token equal to a known automation stem (jenkins, cron, ci,
        cd, bot, robot, system, automation, daemon, actions, pipeline,
        runner, svc, noreply, github-actions, gitlab-ci, circleci, travis,
        service-account, agent); (3) a trivial string (< 2 chars, or no
        alphabetic character at all). None of this proves biological
        human-ness — the manifest records a *claimed* approver, an audit
        trail, not identity proof; a determined caller could still enter a
        fake human-shaped name. See the module/function docstrings.
        """
        log_path = _log(tmp_path)
        self._at_d2(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D3-uat", evidence={"env": "uat"}, approver=approver)

    def test_model_vendor_shaped_approver_is_rejected(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d2(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D3-uat", evidence={"env": "uat"}, approver="Claude Opus")

    @pytest.mark.parametrize(
        "approver",
        ["Jordan Rivera", "Cindy Lee", "Dana Fox", "Cicero Nash"],
    )
    def test_plausible_human_names_are_accepted(self, tmp_path: Path, approver: str) -> None:
        """Names that merely *contain* an automation-stem substring (e.g.
        "Cindy"/"Cicero" contain "ci") must NOT be rejected — the stem match
        is whole-token only, never a raw substring test."""
        log_path = _log(tmp_path)
        self._at_d2(log_path)

        result = _advance(log_path, "D3-uat", evidence={"env": "uat"}, approver=approver)

        assert result.status == "advanced"


class TestTC29HumanApproverGateD4:
    """TC-29: D3-uat -> D4-prod requires a recorded human approver."""

    def _at_d3(self, log_path: Path) -> None:
        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})
        _advance(log_path, "D2-test", evidence={"deploy_id": "dep-1", "ci_run": "ci-1"})
        _advance(log_path, "D3-uat", evidence={"env": "uat"}, approver="Jordan Rivera")

    def test_with_named_human_approver_succeeds(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d3(log_path)

        result = _advance(log_path, "D4-prod", evidence={"env": "prod"}, approver="Jordan Rivera")

        assert result.status == "advanced"

    def test_missing_approver_raises_even_with_full_evidence(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)
        self._at_d3(log_path)

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D4-prod", evidence={"env": "prod"}, approver=None)

        assert len(read_all(log_path)) == 3  # only the D1/D2/D3 setup events


class TestTC30SkipLevelAndBackwardTransitions:
    """TC-30: every non-adjacent forward and every backward transition rejected."""

    @pytest.mark.parametrize(
        "start,target",
        [
            ("D0-code", "D2-test"),
            ("D0-code", "D3-uat"),
            ("D0-code", "D4-prod"),
            ("D1-merged", "D3-uat"),
            ("D1-merged", "D4-prod"),
            ("D2-test", "D4-prod"),
        ],
    )
    def test_skip_level_forward_rejected_regardless_of_evidence(
        self, tmp_path: Path, start: str, target: str
    ) -> None:
        log_path = _log(tmp_path)
        _advance_to(log_path, start)
        before = read_all(log_path)

        with pytest.raises(DonePolicyError):
            _advance(
                log_path,
                target,
                evidence=_full_evidence_for(target),
                approver=_full_approver(),
            )

        assert read_all(log_path) == before  # nothing written

    @pytest.mark.parametrize(
        "start,target",
        [
            ("D4-prod", "D3-uat"),
            ("D3-uat", "D2-test"),
            ("D2-test", "D1-merged"),
            ("D1-merged", "D0-code"),
            ("D2-test", "D2-test"),  # same-level, grouped with backward per TC-30
        ],
    )
    def test_backward_and_same_level_rejected_regardless_of_evidence(
        self, tmp_path: Path, start: str, target: str
    ) -> None:
        log_path = _log(tmp_path)
        _advance_to(log_path, start)
        before = read_all(log_path)

        with pytest.raises(DonePolicyError):
            _advance(
                log_path,
                target,
                evidence=_full_evidence_for(target),
                approver=_full_approver(),
            )

        assert read_all(log_path) == before  # nothing written

    def test_sequential_repeat_of_an_already_completed_advance_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """See module docstring / core/done.py's "No retry-idempotency" note.

        A *sequential* repeat of the exact call that already succeeded is a
        same-level attempt under TC-30's own framing (the node is already at
        that level) — not the concurrent-race dedupe `AdvanceResult.status
        == "deduped"` documents.
        """
        log_path = _log(tmp_path)
        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})

        with pytest.raises(DonePolicyError):
            _advance(log_path, "D1-merged", evidence={"commit": "abc123"})

    @pytest.mark.parametrize("target", ["D0-code", "D1-merged", "D2-test", "D3-uat", "D4-prod"])
    def test_every_adjacent_forward_step_succeeds(self, tmp_path: Path, target: str) -> None:
        """Exhaustive positive coverage: every legitimate D(n) -> D(n+1) step."""
        log_path = _log(tmp_path)
        idx = DONE_LEVELS.index(target)
        if idx == 0:
            pytest.skip("D0-code is the default starting level, never an advance target")
        _advance_to(log_path, DONE_LEVELS[idx - 1])

        result = _advance(
            log_path,
            target,
            evidence=_full_evidence_for(target),
            approver=_full_approver(),
        )

        assert result.status == "advanced"


class TestTC31DCeilingBlocksAdvancement:
    """TC-31: D-ceiling blocks advancement regardless of evidence/approver."""

    def _at_d2_with_full_d3_evidence(self, tmp_path: Path) -> Path:
        log_path = _log(tmp_path)
        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})
        _advance(log_path, "D2-test", evidence={"deploy_id": "dep-1", "ci_run": "ci-1"})
        return log_path

    def test_ceiling_at_d2_test_rejects_the_d3_advance(self, tmp_path: Path) -> None:
        log_path = self._at_d2_with_full_d3_evidence(tmp_path)

        with pytest.raises(DonePolicyError):
            _advance(
                log_path,
                "D3-uat",
                evidence={"env": "uat"},
                approver="Jordan Rivera",
                ceiling="D2-test",
            )

        assert len(read_all(log_path)) == 2  # only the D1/D2 setup events

    def test_ceiling_at_d4_prod_allows_the_same_transition(self, tmp_path: Path) -> None:
        log_path = self._at_d2_with_full_d3_evidence(tmp_path)

        result = _advance(
            log_path,
            "D3-uat",
            evidence={"env": "uat"},
            approver="Jordan Rivera",
            ceiling="D4-prod",
        )

        assert result.status == "advanced"

    def test_unknown_ceiling_value_raises_value_error(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)

        with pytest.raises(ValueError):
            _advance(log_path, "D1-merged", evidence={"commit": "x"}, ceiling="D9-nonsense")

    def test_unknown_target_level_raises_value_error(self, tmp_path: Path) -> None:
        log_path = _log(tmp_path)

        with pytest.raises(ValueError):
            _advance(log_path, "D9-nonsense", evidence={"commit": "x"})


class TestTC32DoneIsDeterministicCodeNeverInferred:
    """TC-32: core/done.py has zero LLM/model/adapter imports or calls (DP#5)."""

    _DONE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "fleet" / "core" / "done.py"

    #: Substrings that mark an import as LLM/model/adapter-shaped. Matched
    #: case-insensitively against the full dotted module string of every
    #: import statement in `core/done.py`.
    _FORBIDDEN_SUBSTRINGS = (
        "anthropic",
        "openai",
        "adapter",
        "session_relay",
        "session-relay",
        "mcp__",
        "llm",
    )

    def _imported_modules(self) -> set[str]:
        tree = ast.parse(self._DONE_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.add(node.module)
                for alias in node.names:
                    modules.add(alias.name)
        return modules

    def test_file_exists_to_scan(self) -> None:
        # Guards against a vacuous pass if the file were ever renamed/moved.
        assert self._DONE_PATH.is_file(), f"{self._DONE_PATH} does not exist"

    def test_done_module_imports_nothing_llm_or_adapter_shaped(self) -> None:
        modules = self._imported_modules()
        offenders = {
            m for m in modules if any(tok in m.lower() for tok in self._FORBIDDEN_SUBSTRINGS)
        }
        assert not offenders, (
            f"core/done.py imports something LLM/model/adapter-shaped (DP#5 "
            f"violation): {offenders}"
        )

    def test_guard_can_actually_fail_on_a_broken_fixture(self) -> None:
        """Prove the guard is not vacuous: an injected anthropic import IS caught."""
        broken_source = "import anthropic\n" + self._DONE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(broken_source)
        modules = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        }
        offenders = {
            m for m in modules if any(tok in m.lower() for tok in self._FORBIDDEN_SUBSTRINGS)
        }
        assert offenders, "the import guard failed to detect a deliberately broken fixture"

    def test_advance_is_a_pure_function_of_its_arguments(self, tmp_path: Path) -> None:
        """Calling `advance()` with identical inputs against identical state
        is fully deterministic — no hidden randomness/model call could make
        two calls disagree (a lightweight runtime complement to the static
        import scan above)."""
        log_a = _log(tmp_path / "a")
        log_b = _log(tmp_path / "b")

        result_a = _advance(log_a, "D1-merged", evidence={"commit": "abc123"})
        result_b = _advance(log_b, "D1-merged", evidence={"commit": "abc123"})

        assert result_a == result_b


class TestCurrentLevelDefensiveReadTolerance:
    """`_current_level` skips a non-matching or malformed log entry (G5 F3).

    Never triggered by `advance()`'s own writes (every event it appends
    always carries a recognized `done_level` for the right `node_id`/type)
    — only a directly-forged log entry reaches these branches, matching the
    same read-time tolerance `core.edges` applies to its own event payloads.
    """

    def test_events_for_a_different_node_or_type_are_ignored(self, tmp_path: Path) -> None:
        from scripts.fleet.core.events import append

        log_path = _log(tmp_path)
        # A different node's advancement, and a same-node non-done event,
        # must not influence this node's computed current level.
        append(
            log_path,
            {
                "schema_version": 1,
                "event_id": "22222222-2222-2222-2222-222222222222",
                "idempotency_key": "done:other/node:D4-prod",
                "ts": "2026-08-15T00:00:00.000000Z",
                "type": "done_level_advanced",
                "project_id": _PROJECT_ID,
                "node_id": "other/node",
                "writer_role": _WRITER_ROLE,
                "payload": {"done_level": "D4-prod", "evidence": {}, "approver": "Jordan Rivera"},
            },
        )
        append(
            log_path,
            {
                "schema_version": 1,
                "event_id": "33333333-3333-3333-3333-333333333333",
                "idempotency_key": f"register:{_NODE}",
                "ts": "2026-08-15T00:00:00.000000Z",
                "type": "session_registered",
                "project_id": _PROJECT_ID,
                "node_id": _NODE,
                "writer_role": _WRITER_ROLE,
                "payload": {"harness": "claude", "workspace": "/w", "local_id": "1", "branch": ""},
            },
        )

        # This node is still at the default D0-code, so D1-merged is the
        # only legal next step — proves neither seeded event moved it.
        result = _advance(log_path, "D1-merged", evidence={"commit": "abc123"})
        assert result.status == "advanced"

    def test_malformed_done_level_value_is_skipped_not_trusted(self, tmp_path: Path) -> None:
        from scripts.fleet.core.events import append

        log_path = _log(tmp_path)
        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})
        # A forged event with an unrecognized done_level string (schema-valid
        # — a non-empty string — but not in DONE_LEVELS) must not become the
        # computed "current level".
        append(
            log_path,
            {
                "schema_version": 1,
                "event_id": "44444444-4444-4444-4444-444444444444",
                "idempotency_key": f"done:{_NODE}:D9-bogus",
                "ts": "2026-08-15T00:00:01.000000Z",
                "type": "done_level_advanced",
                "project_id": _PROJECT_ID,
                "node_id": _NODE,
                "writer_role": _WRITER_ROLE,
                "payload": {"done_level": "D9-bogus", "evidence": {}, "approver": None},
            },
        )

        # Current is still correctly D1-merged (the bogus entry was
        # skipped), so D2-test is the only legal next step.
        result = _advance(
            log_path, "D2-test", evidence={"deploy_id": "dep-1", "ci_run": "ci-1"}
        )
        assert result.status == "advanced"


class TestOwnershipAndDedupe:
    """Rounding out branch coverage: writer_role/ownership and the dedupe path."""

    def test_writer_role_must_not_be_model_shaped(self, tmp_path: Path) -> None:
        from scripts.fleet.core.errors import ValidationError

        log_path = _log(tmp_path)
        with pytest.raises(ValidationError):
            advance(
                _NODE,
                "D1-merged",
                evidence={"commit": "abc123"},
                approver=None,
                ceiling=_TOP_CEILING,
                project_id=_PROJECT_ID,
                writer_role="Claude Sonnet 5",
                log_path=log_path,
            )
        assert read_all(log_path) == []

    def test_dedupe_branch_when_append_reports_an_existing_idempotency_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exercise the `appended is None` ("deduped") branch directly.

        `core.events.append` returns `None` when two callers race to write
        the *same* legitimate next step concurrently — the loser's write
        collides on the shared idempotency key and is a safe no-op (see the
        module docstring's "No retry-idempotency" note: this is the
        concurrent-race case, distinct from TC-30's *sequential* same-level
        rejection). That race is not deterministically reproducible in a
        single-threaded test, so this monkeypatches `core.done.append`
        itself to return `None` — the exact contract `core.events.append`
        documents for a dedupe — on a call whose pre-lock adjacency check
        otherwise passes cleanly (node at the default D0-code, target
        D1-merged), and asserts `advance()` reports `status="deduped"`
        rather than treating it as a rejection.
        """
        import scripts.fleet.core.done as done_module

        log_path = _log(tmp_path)
        monkeypatch.setattr(done_module, "append", lambda *args, **kwargs: None)

        result = _advance(log_path, "D1-merged", evidence={"commit": "abc123"})

        assert result == AdvanceResult(status="deduped", node_id=_NODE, level="D1-merged")


class TestEventFor:
    """`event_for()` — the lookup helper `cli.py` uses to project a fragment."""

    def test_returns_the_persisted_event_after_a_successful_advance(
        self, tmp_path: Path
    ) -> None:
        log_path = _log(tmp_path)
        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})

        event = event_for(_NODE, "D1-merged", log_path)

        assert event is not None
        assert event.type == "done_level_advanced"
        assert event.node_id == _NODE
        assert event.payload["done_level"] == "D1-merged"

    def test_returns_none_when_no_such_transition_was_ever_recorded(
        self, tmp_path: Path
    ) -> None:
        log_path = _log(tmp_path)

        assert event_for(_NODE, "D1-merged", log_path) is None

    def test_skips_non_matching_events_before_returning_none(self, tmp_path: Path) -> None:
        # A recorded transition to a *different* level must not satisfy the
        # lookup — exercises the loop's "keep scanning" branch, not just
        # the "log is empty" shortcut above.
        log_path = _log(tmp_path)
        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})

        assert event_for(_NODE, "D2-test", log_path) is None

    def test_returns_none_against_a_log_that_does_not_exist_yet(self, tmp_path: Path) -> None:
        assert event_for(_NODE, "D1-merged", tmp_path / "nonexistent.jsonl") is None


class TestProjectionRoundTrip:
    """`advance()` -> `core.projection.project_event` round-trips `done_level`.

    `core/done.py` itself never imports `core/projection` (NFR-2); this
    proves the wiring `cli.py`'s `fleet done advance` performs (look up the
    event via `event_for`, then call `project_event`) actually produces a
    fragment whose `done_level` matches what was just recorded — the same
    "write -> project -> read" round-trip every other event type in this
    package already gets (`core.projection`'s own module docstring: any
    payload key matching `STATUS_FIELDS`, which includes `done_level`,
    projects generically with no special-casing needed here).
    """

    def test_advance_then_project_event_updates_the_fragment(self, tmp_path: Path) -> None:
        from scripts.fleet.core.projection import project_event
        from scripts.fleet.core.store import read_fragment

        log_path = _log(tmp_path)
        sessions_dir = tmp_path / "sessions"

        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})
        event = event_for(_NODE, "D1-merged", log_path)
        assert event is not None
        fragment = project_event(event, sessions_dir)

        assert fragment.done_level == "D1-merged"
        assert read_fragment(_NODE, sessions_dir).done_level == "D1-merged"

    def test_rebuild_from_scratch_also_reflects_the_latest_done_level(
        self, tmp_path: Path
    ) -> None:
        from scripts.fleet.core.projection import rebuild

        log_path = _log(tmp_path)
        sessions_dir = tmp_path / "sessions"

        _advance(log_path, "D1-merged", evidence={"commit": "abc123"})
        _advance(log_path, "D2-test", evidence={"deploy_id": "dep-1", "ci_run": "ci-1"})

        fragments = rebuild(log_path, sessions_dir, project_id=_PROJECT_ID)

        assert fragments[_NODE].done_level == "D2-test"


class TestCliDoneAdvanceSubcommand:
    """`fleet done advance` wiring, end-to-end (PLAN.md Chunk 5's CLI step)."""

    def _parser_args(self, workspace: Path, fleet_dir: Path, target: str, **extra: str):
        from scripts.fleet.cli import build_parser

        argv = [
            "done",
            "advance",
            "--node-id",
            _NODE,
            "--target-level",
            target,
            "--project-id",
            _PROJECT_ID,
            "--slug",
            "demo-slug",
            "--workspace",
            str(workspace),
            "--writer-role",
            _WRITER_ROLE,
            "--fleet-dir",
            str(fleet_dir),
        ]
        for flag, value in extra.items():
            argv.extend([f"--{flag.replace('_', '-')}", value])
        return build_parser().parse_args(argv)

    def test_advance_via_cli_writes_event_and_projects_fragment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.core.store import read_fragment

        monkeypatch.delenv("SUPERHUMAN_PROFILE", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fleet_dir = tmp_path / "fleet"
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text(json.dumps({"commit": "abc123"}), encoding="utf-8")

        args = self._parser_args(
            workspace, fleet_dir, "D1-merged", evidence_json=str(evidence_file)
        )
        exit_code = args.func(args)

        assert exit_code == 0
        events = read_all(fleet_dir / "events.jsonl")
        assert len(events) == 1
        assert events[0].payload["done_level"] == "D1-merged"
        fragment = read_fragment(_NODE, fleet_dir / "sessions")
        assert fragment is not None
        assert fragment.done_level == "D1-merged"

    def test_rejected_advance_via_cli_returns_nonzero_and_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUPERHUMAN_PROFILE", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fleet_dir = tmp_path / "fleet"

        # Skip-level from the default D0-code — rejected regardless of the
        # CLI plumbing, proving a policy rejection surfaces as a clean
        # nonzero exit with nothing written, not an uncaught traceback.
        args = self._parser_args(workspace, fleet_dir, "D2-test")
        exit_code = args.func(args)

        assert exit_code == 1
        assert read_all(fleet_dir / "events.jsonl") == []

    def test_explicit_ceiling_flag_blocks_advance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUPERHUMAN_PROFILE", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fleet_dir = tmp_path / "fleet"
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text(json.dumps({"commit": "abc123"}), encoding="utf-8")

        args = self._parser_args(
            workspace,
            fleet_dir,
            "D1-merged",
            evidence_json=str(evidence_file),
            ceiling="D0-code",
        )
        exit_code = args.func(args)

        assert exit_code == 1
        assert read_all(fleet_dir / "events.jsonl") == []

    def test_ceiling_resolved_from_profile_rung_label_blocks_advance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The D-ceiling, left unspecified on the CLI, comes from the
        operator's `.superhuman/profile.yaml` via a `d_ceiling` rung label
        (`cli._resolve_d_ceiling` — flagged as a design call in this
        chunk's report, since the profile schema has no dedicated field for
        it yet)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text(
            "version: 1\n"
            "ladder:\n"
            "  - name: work\n"
            "    detect:\n"
            "      default: true\n"
            "    labels:\n"
            "      d_ceiling: D0-code\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile_path))
        fleet_dir = tmp_path / "fleet"
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text(json.dumps({"commit": "abc123"}), encoding="utf-8")

        args = self._parser_args(
            workspace, fleet_dir, "D1-merged", evidence_json=str(evidence_file)
        )
        exit_code = args.func(args)

        assert exit_code == 1
        assert read_all(fleet_dir / "events.jsonl") == []

    def test_missing_evidence_json_file_returns_nonzero_not_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """G5 fix #4: `json.loads(path.read_text())` used to run OUTSIDE the
        try/except in `_cmd_done_advance`, so a missing/malformed evidence
        file crashed with an uncaught exception instead of a clean nonzero
        exit."""
        monkeypatch.delenv("SUPERHUMAN_PROFILE", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fleet_dir = tmp_path / "fleet"

        args = self._parser_args(
            workspace,
            fleet_dir,
            "D1-merged",
            evidence_json=str(tmp_path / "does-not-exist.json"),
        )
        exit_code = args.func(args)

        assert exit_code == 1
        assert read_all(fleet_dir / "events.jsonl") == []

    def test_evidence_json_top_level_array_returns_nonzero_not_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUPERHUMAN_PROFILE", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fleet_dir = tmp_path / "fleet"
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text("[1, 2]", encoding="utf-8")

        args = self._parser_args(
            workspace, fleet_dir, "D1-merged", evidence_json=str(evidence_file)
        )
        exit_code = args.func(args)

        assert exit_code == 1
        assert read_all(fleet_dir / "events.jsonl") == []

    def test_malformed_evidence_json_returns_nonzero_not_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUPERHUMAN_PROFILE", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fleet_dir = tmp_path / "fleet"
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text("{not valid json", encoding="utf-8")

        args = self._parser_args(
            workspace, fleet_dir, "D1-merged", evidence_json=str(evidence_file)
        )
        exit_code = args.func(args)

        assert exit_code == 1
        assert read_all(fleet_dir / "events.jsonl") == []

    def test_no_matching_profile_defaults_to_unrestricted_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUPERHUMAN_PROFILE", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fleet_dir = tmp_path / "fleet"
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text(json.dumps({"commit": "abc123"}), encoding="utf-8")

        args = self._parser_args(
            workspace, fleet_dir, "D1-merged", evidence_json=str(evidence_file)
        )
        exit_code = args.func(args)

        assert exit_code == 0  # D4-prod default ceiling never blocks D1-merged
