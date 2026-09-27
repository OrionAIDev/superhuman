"""Tests for ``scripts.fleet.core.project_owner`` — TC-O7..TC-O14 (increment O, Chunk O1).

Covers O-FR-1 (ownership is its own event, never suppressed by an earlier
registration), O-FR-2 (stand-down is its own event), O-FR-3 (a claim over an
active owner is coordinated), O-FR-4 (exactly one stand-down, written in the
same locked write as the claim), O-FR-5 (evidence-based liveness, conservative
when unknown), O-FR-6 (claims cannot race), and O-NFR-3 (`rebuild()`
byte-identity / "no declared owner" on an old manifest).
"""

from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.fleet.core.errors import OwnershipRefused  # noqa: E402
from scripts.fleet.core.events import append, read_all  # noqa: E402
from scripts.fleet.core.projection import rebuild  # noqa: E402
from scripts.fleet.core.project_owner import (  # noqa: E402
    CLAIM_OVER,
    CLAIM_UNOWNED,
    NOOP_ALREADY_OWNER,
    REFUSE_ATTESTATION_MISMATCH,
    REFUSE_COORDINATION_REQUIRED,
    OwnerState,
    claim,
    decide_claim,
    fold_owner,
    resolve_legacy_owner,
    stand_down,
)
from scripts.fleet.core.schema import Event

PROJECT_ID = "proj-own"


def _register(node_id: str, *, writer_role: str = "Developer", origination: str = "observed") -> dict:
    return {
        "schema_version": 1,
        "event_id": f"eid-reg-{node_id}",
        "idempotency_key": f"register:{node_id}",
        "ts": "2026-09-27T00:00:00Z",
        "type": "session_registered",
        "project_id": PROJECT_ID,
        "node_id": node_id,
        "writer_role": writer_role,
        "payload": {"origination": origination},
    }


def _declared(
    node_id: str,
    event_id: str,
    *,
    stood_down_from: str | None = None,
    writer_role: str = "pm",
) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "idempotency_key": f"own:{PROJECT_ID}:{node_id}:after:manual-{event_id}",
        "ts": "2026-09-27T00:00:01Z",
        "type": "ownership_declared",
        "project_id": PROJECT_ID,
        "node_id": node_id,
        "writer_role": writer_role,
        "payload": {
            "claim_id": f"claim-{event_id}",
            "anchor": "genesis",
            "prior_owner": None,
            "prior_owner_kind": "none",
            "basis": "unowned",
            "prior_owner_liveness": None,
            "liveness_source": "not-supplied",
            "attestation": None,
        },
    }


def _stood_down(
    node_id: str,
    event_id: str,
    *,
    stood_down_from: str,
    written_by: str = "self",
    basis: str = "self",
    claimant: str | None = None,
    claim_key: str | None = None,
    writer_role: str = "pm",
) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "idempotency_key": f"standdown:{PROJECT_ID}:{node_id}:from:{stood_down_from}:{event_id}",
        "ts": "2026-09-27T00:00:02Z",
        "type": "ownership_stood_down",
        "project_id": PROJECT_ID,
        "node_id": node_id,
        "writer_role": writer_role,
        "payload": {
            "stood_down_from": stood_down_from,
            "written_by": written_by,
            "basis": basis,
            "claimant": claimant,
            "claim_key": claim_key,
            "reason": None,
        },
    }


def _events(*raw: dict) -> list[Event]:
    """Round-trip raw dicts through validate_event for typed Events, in order."""
    from scripts.fleet.core.schema import validate_event

    return [validate_event(d) for d in raw]


class TestFoldOwnerTruthTable:
    """TC-O7: fold_owner's truth table (O-FR-2, O-NFR-3)."""

    def test_empty_log_has_no_owner(self) -> None:
        state = fold_owner([], PROJECT_ID)
        assert state == OwnerState(
            owner=None,
            declaring_event_id=None,
            anchor="genesis",
            anchor_kind="genesis",
            anchor_node=None,
            has_ownership_events=False,
        )

    def test_one_declaration_no_standdown(self) -> None:
        events = _events(_declared("nodeA", "e1"))
        state = fold_owner(events, PROJECT_ID)
        assert state.owner == "nodeA"
        assert state.declaring_event_id == "e1"
        assert state.anchor == "e1"
        assert state.has_ownership_events is True

    def test_declared_then_self_stood_down_matching_clears_owner(self) -> None:
        events = _events(
            _declared("nodeA", "e1"),
            _stood_down("nodeA", "e2", stood_down_from="e1"),
        )
        state = fold_owner(events, PROJECT_ID)
        assert state.owner is None
        assert state.declaring_event_id is None
        assert state.anchor == "e2"

    def test_standdown_naming_different_stood_down_from_does_not_clear(self) -> None:
        events = _events(
            _declared("nodeA", "e1"),
            _stood_down("nodeA", "e2", stood_down_from="some-other-event"),
        )
        state = fold_owner(events, PROJECT_ID)
        assert state.owner == "nodeA"  # unchanged
        assert state.anchor == "e2"  # anchor still moves

    def test_two_declarations_second_displaces_first_with_no_standdown(self) -> None:
        events = _events(_declared("nodeA", "e1"), _declared("nodeB", "e2"))
        state = fold_owner(events, PROJECT_ID)
        assert state.owner == "nodeB"
        assert state.declaring_event_id == "e2"

    def test_orphan_on_behalf_standdown_is_invisible(self) -> None:
        # claim_key references a claim that was never actually written.
        events = _events(
            _stood_down(
                "nodeA",
                "e1",
                stood_down_from="genesis-declaration",
                written_by="claimant",
                basis="notified",
                claimant="nodeB",
                claim_key="own:proj-own:nodeB:after:nonexistent",
            )
        )
        state = fold_owner(events, PROJECT_ID)
        assert state == fold_owner([], PROJECT_ID)

    def test_revert_to_red_counting_any_standdown_naming_owner_breaks_case_d(self) -> None:
        """Documents the revert-to-red mutation for the (d) case: if the fold
        cleared the owner on ANY stand-down naming `node_id == owner`
        (ignoring `stood_down_from`), this test's "unchanged" expectation
        would fail. Exercised directly here as the mutation-sensitive
        assertion this TC's docstring in TEST.md describes."""
        events = _events(
            _declared("nodeA", "e1"),
            _stood_down("nodeA", "e2", stood_down_from="not-e1"),
        )
        state = fold_owner(events, PROJECT_ID)
        assert state.owner == "nodeA"


class TestResolveLegacyOwner:
    """Supporting coverage for OQ-1's legacy-owner resolver."""

    def test_no_registration_returns_none(self) -> None:
        assert resolve_legacy_owner([], PROJECT_ID) is None

    def test_relayed_pm_registration_is_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="pm", origination="relayed"))
        assert resolve_legacy_owner(events, PROJECT_ID) == ("nodeA", "eid-reg-nodeA")

    def test_manual_pm_registration_is_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="pm", origination="manual"))
        assert resolve_legacy_owner(events, PROJECT_ID) == ("nodeA", "eid-reg-nodeA")

    def test_observed_registration_is_not_a_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="pm", origination="observed"))
        assert resolve_legacy_owner(events, PROJECT_ID) is None

    def test_non_pm_writer_is_not_a_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="Developer", origination="relayed"))
        assert resolve_legacy_owner(events, PROJECT_ID) is None

    def test_a_later_standdown_naming_the_registration_clears_legacy_owner(self) -> None:
        events = _events(
            _register("nodeA", writer_role="pm", origination="relayed"),
            _stood_down("nodeA", "e2", stood_down_from="eid-reg-nodeA"),
        )
        assert resolve_legacy_owner(events, PROJECT_ID) is None


class TestDecideClaimOutcomeMatrix:
    """TC-O10: decide_claim's full outcome matrix (O-FR-3, O-FR-5)."""

    def test_unowned_is_claim_unowned_regardless_of_liveness_or_attestation(self) -> None:
        state = fold_owner([], PROJECT_ID)
        for liveness in ("active", "unknown", "archived", "deleted"):
            for attested in (None, "someone"):
                decision = decide_claim(state, "nodeA", liveness, attested)
                assert decision.outcome == CLAIM_UNOWNED
                assert decision.basis == "unowned"

    def test_owned_by_self_is_always_noop(self) -> None:
        events = _events(_declared("nodeA", "e1"))
        state = fold_owner(events, PROJECT_ID)
        for liveness in ("active", "unknown", "archived", "deleted"):
            for attested in (None, "nodeA", "someone-else"):
                decision = decide_claim(state, "nodeA", liveness, attested)
                assert decision.outcome == NOOP_ALREADY_OWNER

    @pytest.mark.parametrize("liveness", ["archived", "deleted"])
    def test_owned_by_other_archived_or_deleted_is_claim_over_regardless_of_attestation(
        self, liveness: str
    ) -> None:
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        for attested in (None, "nodeP", "someone-else"):
            decision = decide_claim(state, "nodeA", liveness, attested)
            assert decision.outcome == CLAIM_OVER
            assert decision.basis == liveness
            assert decision.prior_owner == "nodeP"

    @pytest.mark.parametrize("liveness", ["active", "unknown"])
    def test_owned_by_other_active_or_unknown_with_matching_attestation_is_claim_over_notified(
        self, liveness: str
    ) -> None:
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", liveness, "nodeP")
        assert decision.outcome == CLAIM_OVER
        assert decision.basis == "notified"

    @pytest.mark.parametrize("liveness", ["active", "unknown"])
    def test_owned_by_other_active_or_unknown_with_no_attestation_requires_coordination(
        self, liveness: str
    ) -> None:
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", liveness, None)
        assert decision.outcome == REFUSE_COORDINATION_REQUIRED
        assert decision.prior_owner == "nodeP"

    @pytest.mark.parametrize("liveness", ["active", "unknown"])
    def test_owned_by_other_with_mismatched_attestation_is_refused(self, liveness: str) -> None:
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", liveness, "some-other-node")
        assert decision.outcome == REFUSE_ATTESTATION_MISMATCH
        assert decision.prior_owner == "nodeP"

    def test_revert_to_red_treating_unknown_like_archived_breaks_coordination(self) -> None:
        """Documents the revert-to-red mutation: if `unknown` liveness were
        treated the same as `archived` (skipping the attestation
        requirement), this "requires coordination" assertion would fail."""
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", "unknown", None)
        assert decision.outcome == REFUSE_COORDINATION_REQUIRED


class TestClaimOFR1Repro:
    """TC-O8: an `observed`-registered node claims; registration semantics unaffected."""

    def test_observed_registered_node_can_claim(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA", origination="observed"))

        event = claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        assert event is not None
        assert event.type == "ownership_declared"

    def test_later_relayed_registration_for_same_node_still_dedupes(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA", origination="observed"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")

        # A later `relayed` registration for the SAME node dedupes against
        # its own register:<node_id> key — registration semantics pinned
        # unchanged; this addendum never touches them.
        result = append(log_path, _register("nodeA", origination="relayed"))
        assert result is None
        registrations = [e for e in read_all(log_path) if e.type == "session_registered"]
        assert len(registrations) == 1
        assert registrations[0].payload["origination"] == "observed"

    def test_claim_by_unregistered_node_is_refused(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with pytest.raises(OwnershipRefused) as exc_info:
            claim(log_path, project_id=PROJECT_ID, claimant="ghost", writer_role="pm")
        assert exc_info.value.code == "not_registered"
        assert read_all(log_path) == []


class TestClaimRetryAndNoop:
    """TC-O9: re-claim after a takeover; retry writes once; CLI re-run is a no-op."""

    def test_reclaim_after_takeover_lands_as_a_new_event(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        append(log_path, _register("nodeB"))

        first = claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        # nodeB takes over (archived liveness needs no attestation).
        claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeB",
            writer_role="pm",
            liveness="archived",
        )
        # nodeA re-claims after the takeover — must be a NEW event, not a
        # dedupe against its own first claim.
        second = claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            liveness="archived",
        )
        assert second is not None
        assert second.event_id != first.event_id
        assert second.payload["anchor"] != first.payload["anchor"]

    def test_repeated_batch_dict_writes_exactly_one_event(self, tmp_path: Path) -> None:
        """In-process lock-timeout retry semantics: re-appending the exact
        same batch of dicts a second time must not double-write."""
        from scripts.fleet.core.events import append_batch

        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        event_dict = {
            "schema_version": 1,
            "event_id": "e-claim-1",
            "idempotency_key": f"own:{PROJECT_ID}:nodeA:after:genesis",
            "ts": "2026-09-27T00:00:03Z",
            "type": "ownership_declared",
            "project_id": PROJECT_ID,
            "node_id": "nodeA",
            "writer_role": "pm",
            "payload": {
                "claim_id": "claim-1",
                "anchor": "genesis",
                "prior_owner": None,
                "prior_owner_kind": "none",
                "basis": "unowned",
                "prior_owner_liveness": None,
                "liveness_source": "not-supplied",
                "attestation": None,
            },
        }
        first = append_batch(log_path, [event_dict])
        assert len(first) == 1
        # Simulated retry: the exact same dict, re-appended.
        second = append_batch(log_path, [dict(event_dict)])
        assert second == []
        assert len([e for e in read_all(log_path) if e.type == "ownership_declared"]) == 1

    def test_cli_style_reclaim_is_a_noop(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")

        result = claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        assert result is None
        assert len([e for e in read_all(log_path) if e.type == "ownership_declared"]) == 1


class TestClaimRaceOFR6:
    """TC-O11: two spawned claimants race over one prior owner."""

    def test_exactly_one_of_two_racing_claimants_lands(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeP"))
        append(log_path, _register("nodeA"))
        append(log_path, _register("nodeB"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeP", writer_role="pm")

        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        result_queue = ctx.Queue()

        procs = [
            ctx.Process(
                target=_claim_racer_worker,
                args=(str(log_path), claimant, "pm", "nodeP", barrier, result_queue),
            )
            for claimant in ("nodeA", "nodeB")
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=15)

        results = {result_queue.get(timeout=5) for _ in range(2)}
        landed = [r for r in results if r[1] == "landed"]
        refused = [r for r in results if r[1] == "refused:attestation_mismatch"]
        assert len(landed) == 1
        assert len(refused) == 1

        declared = [e for e in read_all(log_path) if e.type == "ownership_declared"]
        winners = [e for e in declared if e.node_id in ("nodeA", "nodeB")]
        assert len(winners) == 1
        assert winners[0].node_id == landed[0][0]


def _claim_racer_worker(
    log_path_str: str,
    claimant: str,
    writer_role: str,
    attested_owner: str,
    barrier,
    result_queue,
) -> None:  # pragma: no cover - runs in a subprocess
    """Wait at the barrier, then race to claim, attesting the same prior owner."""
    from scripts.fleet.core.errors import OwnershipRefused as _Refused
    from scripts.fleet.core.project_owner import claim as _claim

    barrier.wait(timeout=10)
    try:
        _claim(
            log_path_str,
            project_id=PROJECT_ID,
            claimant=claimant,
            writer_role=writer_role,
            liveness="active",
            attested_owner=attested_owner,
            notified_via="slack",
        )
        result_queue.put((claimant, "landed"))
    except _Refused as exc:
        result_queue.put((claimant, f"refused:{exc.code}"))


class TestExactlyOneStandDownOFR4:
    """TC-O12: exactly one stand-down for the prior owner, whichever party wrote it."""

    def test_takeover_without_prior_self_standdown_writes_one_claimant_standdown(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeP"))
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeP", writer_role="pm")

        claim_event = claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            liveness="archived",
        )

        standdowns = [
            e
            for e in read_all(log_path)
            if e.type == "ownership_stood_down" and e.node_id == "nodeP"
        ]
        assert len(standdowns) == 1
        assert standdowns[0].payload["written_by"] == "claimant"
        assert standdowns[0].payload["claim_key"] == claim_event.idempotency_key

    def test_self_standdown_then_claim_has_basis_unowned(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeP"))
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeP", writer_role="pm")
        stand_down(log_path, project_id=PROJECT_ID, node="nodeP", writer_role="pm")

        claim_event = claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        assert claim_event.payload["basis"] == "unowned"

        standdowns = [
            e
            for e in read_all(log_path)
            if e.type == "ownership_stood_down" and e.node_id == "nodeP"
        ]
        assert len(standdowns) == 1
        assert standdowns[0].payload["written_by"] == "self"


class TestStandDown:
    """O-FR-2: stand_down's own contract."""

    def test_non_owner_standdown_is_refused_and_writes_nothing(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        before = read_all(log_path)
        with pytest.raises(OwnershipRefused) as exc_info:
            stand_down(log_path, project_id=PROJECT_ID, node="nodeA", writer_role="pm")
        assert exc_info.value.code == "not_current_owner"
        assert read_all(log_path) == before

    def test_standing_down_twice_is_a_noop_the_second_time(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        first = stand_down(log_path, project_id=PROJECT_ID, node="nodeA", writer_role="pm")
        assert first is not None
        second = stand_down(log_path, project_id=PROJECT_ID, node="nodeA", writer_role="pm")
        assert second is None
        assert len([e for e in read_all(log_path) if e.type == "ownership_stood_down"]) == 1


class TestOQ1LegacyOwnerCoordination:
    """TC-O13: legacy-owner coordination at rollout."""

    def test_claim_over_legacy_owner_requires_coordination(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))
        append(log_path, _register("nodeA"))
        before = read_all(log_path)

        with pytest.raises(OwnershipRefused) as exc_info:
            claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        assert exc_info.value.code == "coordination_required"
        assert exc_info.value.current_owner == "legacyPM"
        assert read_all(log_path) == before

    def test_claim_over_legacy_owner_with_attestation_lands(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))
        append(log_path, _register("nodeA"))

        event = claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            attested_owner="legacyPM",
            notified_via="slack DM",
        )
        assert event.payload["basis"] == "notified"
        assert event.payload["prior_owner_kind"] == "legacy"

        standdowns = [
            e
            for e in read_all(log_path)
            if e.type == "ownership_stood_down" and e.node_id == "legacyPM"
        ]
        assert len(standdowns) == 1
        registration = next(
            e
            for e in read_all(log_path)
            if e.type == "session_registered" and e.node_id == "legacyPM"
        )
        assert standdowns[0].payload["stood_down_from"] == registration.event_id

    def test_third_claim_after_a_real_declaration_is_an_ordinary_takeover(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))
        append(log_path, _register("nodeA"))
        append(log_path, _register("nodeB"))
        claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            attested_owner="legacyPM",
            notified_via="slack",
        )

        # A takeover of nodeA (a real declared owner now) needs coordination
        # the ordinary way — the legacy special case does not recur.
        with pytest.raises(OwnershipRefused) as exc_info:
            claim(log_path, project_id=PROJECT_ID, claimant="nodeB", writer_role="pm")
        assert exc_info.value.code == "coordination_required"
        assert exc_info.value.current_owner == "nodeA"


class TestRebuildIdentityONFR3:
    """TC-O14: `rebuild()` fragments are byte-identical with/without ownership events."""

    def test_log_with_no_ownership_events_reads_as_no_declared_owner(self, tmp_path: Path) -> None:
        log_events = _events(_register("nodeA"))
        assert fold_owner(log_events, PROJECT_ID).has_ownership_events is False

    def test_rebuild_fragments_are_identical_with_and_without_ownership_events(
        self, tmp_path: Path
    ) -> None:
        log_path_without = tmp_path / "without" / "events.jsonl"
        log_path_with = tmp_path / "with" / "events.jsonl"
        sessions_without = tmp_path / "without" / "sessions"
        sessions_with = tmp_path / "with" / "sessions"

        for log_path in (log_path_without, log_path_with):
            append(log_path, _register("nodeA"))
            append(
                log_path,
                {
                    "schema_version": 1,
                    "event_id": "eid-lifecycle",
                    "idempotency_key": "lifecycle:nodeA:1",
                    "ts": "2026-09-27T00:00:00Z",
                    "type": "lifecycle_changed",
                    "project_id": PROJECT_ID,
                    "node_id": "nodeA",
                    "writer_role": "Developer",
                    "payload": {"lifecycle": "active"},
                },
            )

        claim(log_path_with, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        stand_down(log_path_with, project_id=PROJECT_ID, node="nodeA", writer_role="pm")

        rebuild(log_path_without, sessions_without)
        rebuild(log_path_with, sessions_with)

        from scripts.fleet.core.store import iter_fragments

        frags_without = {f.node_id: f for f in iter_fragments(sessions_without)}
        frags_with = {f.node_id: f for f in iter_fragments(sessions_with)}
        assert frags_without.keys() == frags_with.keys()
        for node_id in frags_without:
            assert frags_without[node_id] == frags_with[node_id]
