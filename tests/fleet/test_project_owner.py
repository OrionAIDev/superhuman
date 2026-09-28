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

from scripts.fleet.core.errors import OwnershipRefused, ValidationError  # noqa: E402
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
    resolve_legacy_owner_unrestricted,
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


class _AlwaysArchivedLiveness(dict):
    """A liveness mapping that resolves `"archived"` for ANY node id.

    Used by tests where the contended-owner identity changes on every
    re-evaluation (a fresh `racer-<n>` node each time) and the test's intent
    is "the prior owner is always archived, whoever it currently is" — not
    a fixed, enumerable set of node ids known in advance.
    """

    def get(self, key, default=None):  # noqa: ARG002 - default unused by design
        return "archived"


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

    def test_events_from_other_projects_are_ignored(self) -> None:
        # `_declared` hard-codes PROJECT_ID, so build a genuinely
        # different-project event by hand instead.
        from scripts.fleet.core.schema import validate_event

        other = validate_event(
            {
                "schema_version": 1,
                "event_id": "e-unrelated",
                "idempotency_key": "own:some-other-project:nodeZ:after:genesis",
                "ts": "2026-09-27T00:00:01Z",
                "type": "ownership_declared",
                "project_id": "some-other-project",
                "node_id": "nodeZ",
                "writer_role": "pm",
                "payload": {
                    "claim_id": "claim-other",
                    "anchor": "genesis",
                    "prior_owner": None,
                    "prior_owner_kind": "none",
                    "basis": "unowned",
                    "prior_owner_liveness": None,
                    "liveness_source": "not-supplied",
                    "attestation": None,
                },
            }
        )
        state = fold_owner([other], PROJECT_ID)
        assert state == fold_owner([], PROJECT_ID)

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

    def test_standdown_pairing_requires_claimant_matches_declaration_node_id(self) -> None:
        """M7: an on-behalf stand-down's `claim_key` matching SOME real
        `ownership_declared` event's idempotency_key is not enough — the
        stand-down's own `claimant` field must also equal THAT declaration's
        `node_id`. A stand-down naming a mismatched `claimant` is exactly as
        invisible as one naming a `claim_key` that matches nothing at all."""
        declared = _declared("nodeC", "e-declared-c")
        events = _events(
            declared,
            _stood_down(
                "nodeC",
                "e-standdown",
                stood_down_from="e-declared-c",
                written_by="claimant",
                basis="notified",
                claimant="nodeB",  # forged/mismatched: e-declared-c is nodeC's, not nodeB's
                claim_key=declared["idempotency_key"],
            ),
        )
        state = fold_owner(events, PROJECT_ID)
        # The mismatched-pairing stand-down must be invisible: nodeC's
        # declaration is the only valid ownership event, so the anchor must
        # still point at it, unmoved.
        assert state.owner == "nodeC"
        assert state.anchor == "e-declared-c"
        assert state.anchor_kind == "declared"


class TestResolveLegacyOwner:
    """Supporting coverage for OQ-1's legacy-owner resolver."""

    def test_no_registration_returns_none(self) -> None:
        assert resolve_legacy_owner([], PROJECT_ID, "nodeA") is None

    def test_relayed_pm_registration_is_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="pm", origination="relayed"))
        assert resolve_legacy_owner(events, PROJECT_ID, "someone-else") == (
            "nodeA",
            "eid-reg-nodeA",
        )

    def test_manual_pm_registration_is_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="pm", origination="manual"))
        assert resolve_legacy_owner(events, PROJECT_ID, "someone-else") == (
            "nodeA",
            "eid-reg-nodeA",
        )

    def test_observed_registration_is_not_a_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="pm", origination="observed"))
        assert resolve_legacy_owner(events, PROJECT_ID, "someone-else") is None

    def test_non_pm_writer_is_not_a_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="Developer", origination="relayed"))
        assert resolve_legacy_owner(events, PROJECT_ID, "someone-else") is None

    def test_a_later_standdown_naming_the_registration_clears_legacy_owner(self) -> None:
        events = _events(
            _register("nodeA", writer_role="pm", origination="relayed"),
            _stood_down("nodeA", "e2", stood_down_from="eid-reg-nodeA"),
        )
        assert resolve_legacy_owner(events, PROJECT_ID, "someone-else") is None

    def test_an_unrelated_standdown_naming_a_different_event_does_not_vacate(self) -> None:
        """A valid (non-orphan) self stand-down that names some OTHER
        event_id as `stood_down_from` must not affect the legacy owner at
        all — only a stand-down naming the registration's own `event_id`
        vacates it."""
        events = _events(
            _register("legacyPM", writer_role="pm", origination="relayed"),
            _stood_down("someone-else", "e2", stood_down_from="not-the-registration"),
        )
        assert resolve_legacy_owner(events, PROJECT_ID, "nodeA") == (
            "legacyPM",
            "eid-reg-legacyPM",
        )


class TestResolveLegacyOwnerExcludesClaimantsOwnNode:
    """C1 (PM ruling 2026-09-26): the claimant's own relayed/manual `pm`
    registration must never be returned as ITS OWN legacy prior owner — a
    successor must coordinate with a prior owner other than itself, never
    with itself."""

    def test_only_the_claimants_own_relayed_registration_is_not_a_legacy_owner(self) -> None:
        events = _events(_register("nodeA", writer_role="pm", origination="relayed"))
        assert resolve_legacy_owner(events, PROJECT_ID, "nodeA") is None

    def test_older_registration_by_another_node_is_still_the_legacy_owner(self) -> None:
        events = _events(
            _register("nodeB", writer_role="pm", origination="relayed"),
            _register("nodeA", writer_role="pm", origination="manual"),
        )
        # nodeA (the claimant) registered more recently, but its OWN
        # registration must never mask nodeB's older one.
        assert resolve_legacy_owner(events, PROJECT_ID, "nodeA") == ("nodeB", "eid-reg-nodeB")

    def test_revert_to_red_a_self_only_registrant_would_otherwise_coordinate_with_itself(
        self,
    ) -> None:
        """Documents the exact defect: without excluding the claimant's own
        node, this call would return the claimant as its own legacy owner."""
        events = _events(_register("nodeA", writer_role="pm", origination="relayed"))
        legacy = resolve_legacy_owner(events, PROJECT_ID, "nodeA")
        assert legacy is None, "a claimant must never be told its own registration is its owner"


class TestClaimExcludesClaimantsOwnRegistrationFromLegacyOwnerC1:
    """C1 at the `claim()` level: a claimant that is itself the newest
    relayed/manual `pm` registration must claim unowned, not be refused
    coordination with itself; an OLDER registration by a different node must
    still require coordination."""

    def test_claim_by_the_sole_relayed_registrant_lands_unowned(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA", writer_role="pm", origination="relayed"))

        event = claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        assert event is not None
        assert event.payload["basis"] == "unowned"
        assert event.payload["prior_owner_kind"] == "none"
        assert event.payload["prior_owner"] is None

    def test_claimants_own_newer_registration_never_masks_an_older_others_registration(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeB", writer_role="pm", origination="relayed"))
        append(log_path, _register("nodeA", writer_role="pm", origination="manual"))
        before = read_all(log_path)

        with pytest.raises(OwnershipRefused) as exc_info:
            claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        assert exc_info.value.code == "coordination_required"
        assert exc_info.value.current_owner == "nodeB"
        assert read_all(log_path) == before

        event = claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            attested_owner="nodeB",
            notified_via="slack",
        )
        assert event.payload["basis"] == "notified"
        assert event.payload["prior_owner_kind"] == "legacy"
        standdowns = [
            e
            for e in read_all(log_path)
            if e.type == "ownership_stood_down" and e.node_id == "nodeB"
        ]
        assert len(standdowns) == 1
        registration_b = next(
            e
            for e in read_all(log_path)
            if e.type == "session_registered" and e.node_id == "nodeB"
        )
        assert standdowns[0].payload["stood_down_from"] == registration_b.event_id


class TestDecideClaimOutcomeMatrix:
    """TC-O10: decide_claim's full outcome matrix (O-FR-3, O-FR-5)."""

    def test_unowned_is_claim_unowned_regardless_of_liveness_or_attestation(self) -> None:
        state = fold_owner([], PROJECT_ID)
        for liveness in ("active", "unknown", "archived", "deleted"):
            for attested in (None, "someone"):
                decision = decide_claim(state, "nodeA", {"nodeP": liveness}, attested)
                assert decision.outcome == CLAIM_UNOWNED
                assert decision.basis == "unowned"

    def test_owned_by_self_is_always_noop(self) -> None:
        events = _events(_declared("nodeA", "e1"))
        state = fold_owner(events, PROJECT_ID)
        for liveness in ("active", "unknown", "archived", "deleted"):
            for attested in (None, "nodeA", "someone-else"):
                decision = decide_claim(state, "nodeA", {"nodeA": liveness}, attested)
                assert decision.outcome == NOOP_ALREADY_OWNER

    @pytest.mark.parametrize("liveness", ["archived", "deleted"])
    def test_owned_by_other_archived_or_deleted_is_claim_over_regardless_of_attestation(
        self, liveness: str
    ) -> None:
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        for attested in (None, "nodeP", "someone-else"):
            decision = decide_claim(state, "nodeA", {"nodeP": liveness}, attested)
            assert decision.outcome == CLAIM_OVER
            assert decision.basis == liveness
            assert decision.prior_owner == "nodeP"

    @pytest.mark.parametrize("liveness", ["active", "unknown"])
    def test_owned_by_other_active_or_unknown_with_matching_attestation_is_claim_over_notified(
        self, liveness: str
    ) -> None:
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", {"nodeP": liveness}, "nodeP")
        assert decision.outcome == CLAIM_OVER
        assert decision.basis == "notified"

    @pytest.mark.parametrize("liveness", ["active", "unknown"])
    def test_owned_by_other_active_or_unknown_with_no_attestation_requires_coordination(
        self, liveness: str
    ) -> None:
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", {"nodeP": liveness}, None)
        assert decision.outcome == REFUSE_COORDINATION_REQUIRED
        assert decision.prior_owner == "nodeP"

    @pytest.mark.parametrize("liveness", ["active", "unknown"])
    def test_owned_by_other_with_mismatched_attestation_is_refused(self, liveness: str) -> None:
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", {"nodeP": liveness}, "some-other-node")
        assert decision.outcome == REFUSE_ATTESTATION_MISMATCH
        assert decision.prior_owner == "nodeP"

    def test_missing_map_entry_for_the_resolved_prior_owner_defaults_to_unknown(self) -> None:
        """C1: a mapping that simply omits the resolved prior owner (as
        opposed to naming it "unknown" explicitly) must fall back to
        `"unknown"`, not silently skip the coordination requirement."""
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", {}, None)
        assert decision.outcome == REFUSE_COORDINATION_REQUIRED
        assert decision.prior_owner == "nodeP"

    def test_revert_to_red_treating_unknown_like_archived_breaks_coordination(self) -> None:
        """Documents the revert-to-red mutation: if `unknown` liveness were
        treated the same as `archived` (skipping the attestation
        requirement), this "requires coordination" assertion would fail."""
        events = _events(_declared("nodeP", "e1"))
        state = fold_owner(events, PROJECT_ID)
        decision = decide_claim(state, "nodeA", {"nodeP": "unknown"}, None)
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
            liveness={"nodeA": "archived"},
        )
        # nodeA re-claims after the takeover — must be a NEW event, not a
        # dedupe against its own first claim.
        second = claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            liveness={"nodeB": "archived"},
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
            liveness={attested_owner: "active"},
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
            liveness={"nodeP": "archived"},
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


class TestOnBehalfStandDownR1:
    """R1: an on-behalf stand-down (`acting_on_behalf=True`, the CLI's
    `--node-id` path) is honestly recorded — never as `node`'s own voluntary
    self stand-down — while still clearing ownership exactly like a self
    stand-down does."""

    def test_on_behalf_standdown_is_not_written_by_self(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")

        event = stand_down(
            log_path,
            project_id=PROJECT_ID,
            node="nodeA",
            writer_role="cto",
            reason="nodeA is unreachable during a maintenance window",
            acting_on_behalf=True,
        )

        assert event is not None
        assert event.payload["written_by"] == "on_behalf"
        assert event.payload["basis"] == "on_behalf"
        assert event.payload["reason"] == "nodeA is unreachable during a maintenance window"
        assert event.payload["claimant"] is None
        assert event.payload["claim_key"] is None

    def test_on_behalf_standdown_clears_ownership(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")

        stand_down(
            log_path,
            project_id=PROJECT_ID,
            node="nodeA",
            writer_role="cto",
            reason="handing off",
            acting_on_behalf=True,
        )

        state = fold_owner(read_all(log_path), PROJECT_ID)
        assert state.owner is None
        assert state.has_ownership_events is True

    def test_on_behalf_standdown_is_never_an_orphan(self, tmp_path: Path) -> None:
        """`fold_owner`'s orphan check (`_is_orphaned_standdown`) only
        special-cases `written_by == "claimant"` — an on-behalf stand-down
        must clear ownership the same way a self stand-down does, with no
        `claim_key` pairing required."""
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        declared = claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        assert declared is not None

        stand_down(
            log_path,
            project_id=PROJECT_ID,
            node="nodeA",
            writer_role="cto",
            reason="handing off",
            acting_on_behalf=True,
        )

        events = read_all(log_path)
        standdown = next(e for e in events if e.type == "ownership_stood_down")
        assert standdown.payload["stood_down_from"] == declared.event_id
        state = fold_owner(events, PROJECT_ID)
        assert state.owner is None  # not treated as an orphan

    def test_on_behalf_standdown_missing_reason_raises_validation_error(
        self, tmp_path: Path
    ) -> None:
        """Core-level defense in depth: `core.schema` requires a non-null
        `reason` for `written_by='on_behalf'` even if a caller bypasses the
        CLI's own `--reason` usage check."""
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")

        with pytest.raises(ValidationError):
            stand_down(
                log_path,
                project_id=PROJECT_ID,
                node="nodeA",
                writer_role="cto",
                acting_on_behalf=True,
            )


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


class TestOrphanStanddownVacatesNothingI1:
    """I1: a torn legacy batch (only the on-behalf stand-down half landed)
    must not vacate the OQ-1 legacy owner — `resolve_legacy_owner` shares
    the same orphan predicate `fold_owner` uses."""

    def test_torn_legacy_batch_leaves_legacy_owner_resolved_then_completes_on_rerun(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))
        append(log_path, _register("nodeA"))
        append(log_path, _register("nodeB"))

        events_before = read_all(log_path)
        legacy = resolve_legacy_owner(events_before, PROJECT_ID, "nodeA")
        assert legacy == ("legacyPM", "eid-reg-legacyPM")
        state = fold_owner(events_before, PROJECT_ID)
        decision = decide_claim(state, "nodeA", {"legacyPM": "unknown"}, "legacyPM", legacy=legacy)
        assert decision.outcome == CLAIM_OVER
        assert decision.prior_owner_kind == "legacy"

        from scripts.fleet.core.project_owner import _build_claim_event, _build_standdown_event

        claim_event = _build_claim_event(
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            decision=decision,
            anchor=state.anchor,
            liveness="unknown",
            liveness_source="not-supplied",
            attested_owner="legacyPM",
            notified_via="slack",
        )
        standdown_event = _build_standdown_event(
            project_id=PROJECT_ID,
            node="legacyPM",
            writer_role="pm",
            stood_down_from=decision.prior_owner_registration_event_id,
            written_by="claimant",
            basis=decision.basis,
            claim_event=claim_event,
        )

        # Simulate the crash: only the on-behalf stand-down's line landed.
        log_path.write_text(
            log_path.read_text(encoding="utf-8") + json.dumps(standdown_event) + "\n",
            encoding="utf-8",
        )

        # The orphan must not vacate the legacy owner...
        events_torn = read_all(log_path)
        assert fold_owner(events_torn, PROJECT_ID).has_ownership_events is False
        assert resolve_legacy_owner(events_torn, PROJECT_ID, "nodeA") == legacy

        # ...and a DIFFERENT claimant, without attestation, is still refused
        # coordination (naming the legacy owner, not treating it as gone).
        with pytest.raises(OwnershipRefused) as exc_info:
            claim(log_path, project_id=PROJECT_ID, claimant="nodeB", writer_role="pm")
        assert exc_info.value.code == "coordination_required"
        assert exc_info.value.current_owner == "legacyPM"

        # The ORIGINAL claimant re-running the same call completes the pair:
        # the stand-down dedupes against the orphan already on disk, and
        # only the claim event is newly written.
        result = claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            attested_owner="legacyPM",
            notified_via="slack",
        )
        assert result is not None
        assert result.payload["basis"] == "notified"
        assert result.payload["prior_owner_kind"] == "legacy"
        assert fold_owner(read_all(log_path), PROJECT_ID).owner == "nodeA"
        standdowns = [
            e
            for e in read_all(log_path)
            if e.type == "ownership_stood_down" and e.node_id == "legacyPM"
        ]
        assert len(standdowns) == 1


class TestClaimTornBatchDeclaredVariantI3:
    """I3: claim-level torn-batch coverage, declared-owner variant — an
    orphan on-behalf stand-down (only half of a real takeover's batch)
    leaves the fold unchanged, and re-running `claim()` completes the pair."""

    def test_torn_batch_over_a_declared_owner_rerun_completes_the_pair(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeP"))
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeP", writer_role="pm")

        events_before = read_all(log_path)
        state = fold_owner(events_before, PROJECT_ID)
        decision = decide_claim(state, "nodeA", {"nodeP": "archived"}, None)
        assert decision.outcome == CLAIM_OVER

        from scripts.fleet.core.project_owner import _build_claim_event, _build_standdown_event

        claim_event = _build_claim_event(
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            decision=decision,
            anchor=state.anchor,
            liveness="archived",
            liveness_source="not-supplied",
            attested_owner=None,
            notified_via=None,
        )
        standdown_event = _build_standdown_event(
            project_id=PROJECT_ID,
            node=decision.prior_owner,
            writer_role="pm",
            stood_down_from=state.declaring_event_id,
            written_by="claimant",
            basis=decision.basis,
            claim_event=claim_event,
        )

        # Simulate the crash: only the on-behalf stand-down's line landed.
        log_path.write_text(
            log_path.read_text(encoding="utf-8") + json.dumps(standdown_event) + "\n",
            encoding="utf-8",
        )

        assert fold_owner(read_all(log_path), PROJECT_ID).owner == "nodeP"

        result = claim(
            log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm", liveness={"nodeP": "archived"}
        )
        assert result is not None
        assert fold_owner(read_all(log_path), PROJECT_ID).owner == "nodeA"
        standdowns = [
            e
            for e in read_all(log_path)
            if e.type == "ownership_stood_down" and e.node_id == "nodeP"
        ]
        assert len(standdowns) == 1


class TestClaimPreconditionAlsoCoversLegacyResolutionI2:
    """I2: when the project has no `ownership_declared` events, the claim's
    precondition must also pin the resolved legacy owner, not just
    `fold_owner` — otherwise a relayed/manual `pm` registration by another
    node landing between the unlocked read and the locked write is invisible
    to the precondition, and a stale `CLAIM_UNOWNED` decision could still be
    written."""

    def test_a_concurrent_legacy_registration_forces_a_reevaluation_not_a_stale_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.core import project_owner as po_mod

        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))

        real_read_all = po_mod.read_all
        calls = {"n": 0}

        def _racing_read_all(path):
            calls["n"] += 1
            result = real_read_all(path)
            if calls["n"] == 1:
                # A relayed `pm` registration by another node lands between
                # this unlocked read and claim()'s locked write.
                append(path, _register("nodeB", writer_role="pm", origination="relayed"))
            return result

        monkeypatch.setattr(po_mod, "read_all", _racing_read_all)

        with pytest.raises(OwnershipRefused) as exc_info:
            claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")

        assert exc_info.value.code == "coordination_required"
        assert exc_info.value.current_owner == "nodeB"
        assert calls["n"] == 2, "the precondition mismatch must trigger exactly one re-evaluation"
        assert not any(e.type == "ownership_declared" for e in read_all(log_path)), (
            "a stale CLAIM_UNOWNED decided before nodeB's registration existed "
            "must never be written"
        )


class TestClaimLivenessResolvedPerReevaluatedOwnerC1:
    """C1: liveness must be looked up for whichever prior owner THIS
    re-evaluation resolves, never a single value the caller resolved
    against a possibly-stale owner before the race.

    Probe (DESIGN O.4): owner A is archived, B is active. B claims over A
    between the caller's unlocked read and core's own read. A caller that
    resolves liveness once, for A, and hands `claim()` that single value
    would let core land a claim over B with `basis=archived` and no
    attestation -- exactly wrong, since B is a live session. A mapping
    covering every registered node, looked up fresh by whichever owner each
    re-evaluation actually resolves, must instead force coordination for B.
    """

    def test_a_late_race_forces_coordination_for_the_new_owner_not_the_stale_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.core import project_owner as po_mod

        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        append(log_path, _register("nodeB"))
        append(log_path, _register("nodeC"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")

        real_read_all = po_mod.read_all
        calls = {"n": 0}

        def _racing_read_all(path):
            calls["n"] += 1
            result = real_read_all(path)
            if calls["n"] == 1:
                # nodeB takes real ownership over nodeA between this
                # unlocked read and claim()'s locked write -- nodeB is a
                # live (active) session, unlike archived nodeA.
                append(path, _declared("nodeB", "e-nodeB-takeover"))
            return result

        monkeypatch.setattr(po_mod, "read_all", _racing_read_all)

        # nodeA (the owner this call's FIRST unlocked read sees) is
        # archived; nodeB (who actually wins the race) is active. The old,
        # single-string-liveness design could only ever hand `claim()` one
        # value, resolved for nodeA -- and that value would then get
        # blindly applied to whichever owner core re-resolved, including
        # nodeB.
        liveness_map = {"nodeA": "archived", "nodeB": "active"}

        with pytest.raises(OwnershipRefused) as exc_info:
            claim(
                log_path,
                project_id=PROJECT_ID,
                claimant="nodeC",
                writer_role="pm",
                liveness=liveness_map,
            )

        assert exc_info.value.code == "coordination_required"
        assert exc_info.value.current_owner == "nodeB"
        assert calls["n"] == 2, "the precondition mismatch must trigger exactly one re-evaluation"

        declared_after = [e for e in read_all(log_path) if e.type == "ownership_declared"]
        assert not any(e.node_id == "nodeC" for e in declared_after), (
            "nodeC's claim must never land with basis=archived over an owner "
            "(nodeB) this mapping marks active"
        )


class TestClaimRaisesOwnershipContendedI3:
    """I3: `claim()` must raise `OwnershipContended` (writing nothing) after
    exhausting its bounded re-evaluation budget against a state that keeps
    changing on every single attempt."""

    def test_four_failed_reevaluations_raise_and_write_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.core import project_owner as po_mod
        from scripts.fleet.core.errors import OwnershipContended

        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeP"))
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeP", writer_role="pm")

        real_read_all = po_mod.read_all
        calls = {"n": 0}

        def _always_racing_read_all(path):
            result = real_read_all(path)
            calls["n"] += 1
            # A concurrent takeover lands after every single unlocked read,
            # so the precondition captured against THIS read always
            # mismatches the fresh state append_batch reads under the lock.
            append(path, _declared(f"racer-{calls['n']}", f"e-racer-{calls['n']}"))
            return result

        monkeypatch.setattr(po_mod, "read_all", _always_racing_read_all)

        with pytest.raises(OwnershipContended):
            claim(
                log_path,
                project_id=PROJECT_ID,
                claimant="nodeA",
                writer_role="pm",
                liveness=_AlwaysArchivedLiveness(),
            )

        assert calls["n"] == 4  # _MAX_REEVALUATIONS + 1 attempts, all exhausted
        declared_after = [e for e in read_all(log_path) if e.type == "ownership_declared"]
        assert not any(e.node_id == "nodeA" for e in declared_after), (
            "the contended claim must never land, even partially"
        )


class TestClaimFindEventFallbackM4:
    """M4: the in-process-retry return path must use `_find_event` rather
    than a bare `next(...)` over `written` — a defensive path that must
    degrade gracefully (return `None`) instead of raising a bare
    `StopIteration` if `written` ever lacked the expected declared event."""

    def test_claim_does_not_raise_stopiteration_when_written_lacks_the_declared_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.core import project_owner as po_mod

        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeP"))
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeP", writer_role="pm")

        real_append_batch = po_mod.append_batch

        def _fake_append_batch(path, batch, **kwargs):
            written = real_append_batch(path, batch, **kwargs)
            # Simulate the defensive edge case this fix guards: `written`
            # reports events, but none of type "ownership_declared".
            return [e for e in written if e.type != "ownership_declared"]

        monkeypatch.setattr(po_mod, "append_batch", _fake_append_batch)

        result = claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            liveness={"nodeP": "archived"},
        )
        # Fix round 2: not a crash, and not an ambiguous `None` (which the
        # docstring reserves for the already-owner no-op) — the landed claim
        # is looked up from the log.
        assert result is not None
        assert result.type == "ownership_declared"
        assert result.node_id == "nodeA"
        standdowns = [e for e in read_all(log_path) if e.type == "ownership_stood_down"]
        assert len(standdowns) == 1


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


_CLAIMANT_SESSION = {
    "harness": "claude",
    "workspace": "/workspace/demo",
    "local_id": "sess-xyz",
    "branch": "main",
}


class TestRegisterIfAbsentO14a:
    """O14-a: `claim` registers an unregistered claimant in the SAME locked
    write as the claim itself, when `claimant_session` is supplied."""

    def test_claimant_session_none_preserves_not_registered_refusal(
        self, tmp_path: Path
    ) -> None:
        """Backward compatibility: omitting `claimant_session` (the default,
        `None`) must behave exactly as before O14-a -- `not_registered`,
        nothing written."""
        log_path = tmp_path / "events.jsonl"
        with pytest.raises(OwnershipRefused) as exc_info:
            claim(log_path, project_id=PROJECT_ID, claimant="ghost", writer_role="pm")
        assert exc_info.value.code == "not_registered"
        assert read_all(log_path) == []

    def test_unregistered_claimant_with_session_lands_registration_and_claim(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"

        event = claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="freshNode",
            writer_role="pm",
            claimant_session=_CLAIMANT_SESSION,
        )

        assert event is not None
        assert event.type == "ownership_declared"
        events = read_all(log_path)
        registrations = [e for e in events if e.type == "session_registered"]
        assert len(registrations) == 1
        reg = registrations[0]
        assert reg.node_id == "freshNode"
        assert reg.writer_role == "pm"
        assert reg.idempotency_key == "register:freshNode"
        assert reg.payload["harness"] == "claude"
        assert reg.payload["workspace"] == "/workspace/demo"
        assert reg.payload["local_id"] == "sess-xyz"
        assert reg.payload["branch"] == "main"
        assert reg.payload["origination"] == "manual"
        # the registration must land BEFORE the claim in the log.
        types_in_order = [e.type for e in events]
        assert types_in_order.index("session_registered") < types_in_order.index(
            "ownership_declared"
        )

    def test_registration_is_written_in_the_same_batch_as_a_coordinated_takeover(
        self, tmp_path: Path
    ) -> None:
        """The registration, the on-behalf stand-down, and the claim all land
        in ONE call -- three new events from one `claim()` invocation."""
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("owner-a"))
        claim(log_path, project_id=PROJECT_ID, claimant="owner-a", writer_role="pm")
        before = read_all(log_path)

        claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="freshNode",
            writer_role="pm",
            claimant_session=_CLAIMANT_SESSION,
            liveness={"owner-a": "archived"},
        )

        after = read_all(log_path)
        assert len(after) == len(before) + 3
        new_types = [e.type for e in after[len(before) :]]
        assert new_types == [
            "session_registered",
            "ownership_stood_down",
            "ownership_declared",
        ]

    def test_already_registered_claimant_never_gets_a_second_registration(
        self, tmp_path: Path
    ) -> None:
        """A claimant already registered must not get a spurious second
        `session_registered` event even when `claimant_session` is supplied
        (it is simply unused -- registration is already satisfied)."""
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA", origination="observed"))

        claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            claimant_session=_CLAIMANT_SESSION,
        )

        registrations = [e for e in read_all(log_path) if e.type == "session_registered"]
        assert len(registrations) == 1
        assert registrations[0].payload["origination"] == "observed"

    def test_registration_written_this_way_is_never_its_own_legacy_owner(
        self, tmp_path: Path
    ) -> None:
        """O14-a's requirement, verified directly: because the claim's own
        `ownership_declared` event lands in the SAME batch, `has_ownership_events`
        is True from that point on, so `resolve_legacy_owner` (gated on `not
        has_ownership_events`) can never resolve this registration as a
        legacy owner for a later claimant."""
        log_path = tmp_path / "events.jsonl"
        claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="freshNode",
            writer_role="pm",
            claimant_session=_CLAIMANT_SESSION,
        )
        events = read_all(log_path)
        state = fold_owner(events, PROJECT_ID)
        assert state.has_ownership_events is True
        assert state.owner == "freshNode"
        # `claim()` only ever consults `resolve_legacy_owner` when
        # `not state.has_ownership_events` -- and that is now permanently
        # False for this project (the claim's own `ownership_declared` event
        # is in the same batch as the registration). Proven behaviorally: a
        # second claimant is coordinated against "freshNode" as a real
        # DECLARED owner, never waved through as an uncoordinated legacy
        # takeover -- even though the registration's own shape (origination
        # "manual", writer_role "pm") would otherwise be eligible
        # `resolve_legacy_owner` input.
        append(log_path, _register("someone-else"))
        with pytest.raises(OwnershipRefused) as exc_info:
            claim(log_path, project_id=PROJECT_ID, claimant="someone-else", writer_role="pm")
        assert exc_info.value.code == "coordination_required"
        assert exc_info.value.current_owner == "freshNode"

    def test_coordination_required_refusal_writes_nothing_even_with_claimant_session(
        self, tmp_path: Path
    ) -> None:
        """A refused claim must write NOTHING -- not the claim, and not the
        O14-a register-if-absent registration either, even though
        `claimant_session` is supplied and the batch would otherwise be
        buildable. `claim` raises before the batch is ever built when the
        decision is a REFUSE_* outcome (see the `decision.outcome ==
        REFUSE_*` checks preceding the `batch = []` construction)."""
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("owner-a"))
        claim(log_path, project_id=PROJECT_ID, claimant="owner-a", writer_role="pm")
        before = read_all(log_path)

        with pytest.raises(OwnershipRefused) as exc_info:
            claim(
                log_path,
                project_id=PROJECT_ID,
                claimant="freshNode",
                writer_role="pm",
                claimant_session=_CLAIMANT_SESSION,
                # no liveness supplied -> "owner-a" resolves to "unknown",
                # which counts as active (O-FR-5) -- coordination required.
            )
        assert exc_info.value.code == "coordination_required"
        assert exc_info.value.current_owner == "owner-a"
        assert read_all(log_path) == before

    def test_attestation_mismatch_refusal_writes_nothing_even_with_claimant_session(
        self, tmp_path: Path
    ) -> None:
        """Same guarantee as the coordination_required case above, for the
        other REFUSE_* outcome: an attestation naming a node other than the
        real prior owner refuses before the batch (registration + claim) is
        ever built -- the log is byte-unchanged."""
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("owner-a"))
        claim(log_path, project_id=PROJECT_ID, claimant="owner-a", writer_role="pm")
        before = read_all(log_path)

        with pytest.raises(OwnershipRefused) as exc_info:
            claim(
                log_path,
                project_id=PROJECT_ID,
                claimant="freshNode",
                writer_role="pm",
                claimant_session=_CLAIMANT_SESSION,
                attested_owner="someone-not-the-owner",
                notified_via="x",
            )
        assert exc_info.value.code == "attestation_mismatch"
        assert exc_info.value.current_owner == "owner-a"
        assert read_all(log_path) == before


class TestLegacyStandDownO14c:
    """O14-c: a node that IS the OQ-1 legacy owner (a `relayed`/`manual` `pm`
    registration, with no `ownership_declared` event yet) can stand itself
    down, and the vacated registration then lets a successor claim uncontested.
    """

    def test_legacy_owner_can_stand_itself_down(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))

        event = stand_down(log_path, project_id=PROJECT_ID, node="legacyPM", writer_role="pm")

        assert event is not None
        assert event.type == "ownership_stood_down"
        assert event.payload["written_by"] == "self"
        assert event.payload["basis"] == "self"
        registration_event_id = _register("legacyPM", writer_role="pm", origination="relayed")[
            "event_id"
        ]
        assert event.payload["stood_down_from"] == registration_event_id

        state = fold_owner(read_all(log_path), PROJECT_ID)
        assert state.has_ownership_events is True
        assert state.owner is None

    def test_successor_claim_after_legacy_standdown_is_uncontested(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))
        append(log_path, _register("nodeA"))

        stand_down(log_path, project_id=PROJECT_ID, node="legacyPM", writer_role="pm")
        event = claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")

        assert event is not None
        assert event.payload["basis"] == "unowned"
        assert event.payload["prior_owner"] is None

    def test_standing_down_twice_as_legacy_owner_is_a_noop_the_second_time(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))
        first = stand_down(log_path, project_id=PROJECT_ID, node="legacyPM", writer_role="pm")
        assert first is not None
        second = stand_down(log_path, project_id=PROJECT_ID, node="legacyPM", writer_role="pm")
        assert second is None
        standdowns = [e for e in read_all(log_path) if e.type == "ownership_stood_down"]
        assert len(standdowns) == 1

    def test_a_node_that_is_neither_declared_nor_legacy_owner_is_still_refused(
        self, tmp_path: Path
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))
        append(log_path, _register("bystander"))
        before = read_all(log_path)

        with pytest.raises(OwnershipRefused) as exc_info:
            stand_down(log_path, project_id=PROJECT_ID, node="bystander", writer_role="pm")
        assert exc_info.value.code == "not_current_owner"
        assert read_all(log_path) == before

    def test_resolve_legacy_owner_unrestricted_does_not_exclude_anyone(
        self, tmp_path: Path
    ) -> None:
        events = _events(_register("legacyPM", writer_role="pm", origination="relayed"))
        # `resolve_legacy_owner` excludes the asking node (claim's semantics) --
        # a lone relayed pm registrant is never ITS OWN legacy owner there.
        assert resolve_legacy_owner(events, PROJECT_ID, "legacyPM") is None
        # `resolve_legacy_owner_unrestricted` excludes no one (O14-c) -- the
        # same node can be told it IS the legacy owner, for stand-down/show.
        assert resolve_legacy_owner_unrestricted(events, PROJECT_ID) == (
            "legacyPM",
            "eid-reg-legacyPM",
        )

    def test_declared_owner_takes_precedence_over_a_stale_legacy_standdown_path(
        self, tmp_path: Path
    ) -> None:
        """Once a real `ownership_declared` event exists, `has_ownership_events`
        is True, so the legacy stand-down branch is never consulted -- the
        ordinary declared-owner rules apply exactly as before O14-c."""
        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("legacyPM", writer_role="pm", origination="relayed"))
        append(log_path, _register("nodeA"))
        claim(
            log_path,
            project_id=PROJECT_ID,
            claimant="nodeA",
            writer_role="pm",
            attested_owner="legacyPM",
            notified_via="x",
        )
        before = read_all(log_path)

        with pytest.raises(OwnershipRefused) as exc_info:
            stand_down(log_path, project_id=PROJECT_ID, node="legacyPM", writer_role="pm")
        assert exc_info.value.code == "not_current_owner"
        assert read_all(log_path) == before


class TestExportedVocabularyF1:
    """F1: `core.project_owner` re-exports the anchor-kind and payload-enum
    vocabulary a consumer outside this package needs to interpret ownership
    events without hand-copying `core.schema`'s private sets."""

    def test_genesis_constant_matches_fold_owner_default_anchor(self) -> None:
        from scripts.fleet.core.project_owner import GENESIS

        assert GENESIS == "genesis"
        assert fold_owner([], PROJECT_ID).anchor == GENESIS

    def test_anchor_kind_constants_match_fold_owner_output(self, tmp_path: Path) -> None:
        from scripts.fleet.core.project_owner import (
            ANCHOR_DECLARED,
            ANCHOR_GENESIS,
            ANCHOR_STOOD_DOWN,
        )

        assert ANCHOR_GENESIS == "genesis"
        assert fold_owner([], PROJECT_ID).anchor_kind == ANCHOR_GENESIS

        log_path = tmp_path / "events.jsonl"
        append(log_path, _register("nodeA"))
        claim(log_path, project_id=PROJECT_ID, claimant="nodeA", writer_role="pm")
        state = fold_owner(read_all(log_path), PROJECT_ID)
        assert state.anchor_kind == ANCHOR_DECLARED

        stand_down(log_path, project_id=PROJECT_ID, node="nodeA", writer_role="pm")
        state = fold_owner(read_all(log_path), PROJECT_ID)
        assert state.anchor_kind == ANCHOR_STOOD_DOWN

    def test_enum_frozensets_are_exported_and_match_schema(self) -> None:
        from scripts.fleet.core import schema
        from scripts.fleet.core.project_owner import (
            CLAIM_BASES,
            PRIOR_OWNER_KINDS,
            STANDDOWN_BASES,
            STANDDOWN_WRITTEN_BY,
        )

        assert PRIOR_OWNER_KINDS == schema.PRIOR_OWNER_KINDS
        assert CLAIM_BASES == schema.CLAIM_BASES
        assert STANDDOWN_BASES == schema.STANDDOWN_BASES
        assert STANDDOWN_WRITTEN_BY == schema.STANDDOWN_WRITTEN_BY
        assert "on_behalf" in STANDDOWN_WRITTEN_BY
        assert "on_behalf" in STANDDOWN_BASES

    def test_dunder_all_names_the_public_surface(self) -> None:
        import scripts.fleet.core.project_owner as project_owner_module

        for name in (
            "GENESIS",
            "ANCHOR_GENESIS",
            "ANCHOR_DECLARED",
            "ANCHOR_STOOD_DOWN",
            "PRIOR_OWNER_KINDS",
            "CLAIM_BASES",
            "STANDDOWN_BASES",
            "STANDDOWN_WRITTEN_BY",
            "fold_owner",
            "claim",
            "stand_down",
        ):
            assert name in project_owner_module.__all__
