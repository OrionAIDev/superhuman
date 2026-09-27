"""Tests for `fleet owner claim|stand-down|show` -- TC-O17, TC-O18, TC-O19.

`fleet owner` is a deliberate-act verb group (O-NFR-1): unlike `observe`, it
never routes through the fail-soft façade and fails loudly with a stated,
non-zero exit. These tests drive it entirely through `cli.main(argv)`, per
DESIGN O.8's integration tier.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.fleet import cli as fleet_cli
from scripts.fleet.adapter.base import SessionInfo
from scripts.fleet.adapter.portable import PortableAdapter
from scripts.fleet.core.errors import LockTimeoutError, OwnershipRefused
from scripts.fleet.core.events import append, read_all
from scripts.fleet.core.nodes import make_node_id
from scripts.fleet.core.project_owner import claim as core_claim
from scripts.fleet.path_safety import default_fleet_dir

PROJECT_ID = "proj-own-cli"
_SKILL_ROOT = Path(__file__).resolve().parents[2]


def _log_path(workspace: Path, slug: str) -> Path:
    return default_fleet_dir(workspace, slug) / "events.jsonl"


def _register(
    workspace: Path,
    slug: str,
    local_id: str,
    *,
    writer_role: str = "developer",
    origination: str = "spawned",
) -> str:
    """Register `local_id` (a `PortableAdapter` session) and return its `node_id`.

    Deliberately registered `writer_role="developer"`/`origination="spawned"`
    by default -- neither is a `relayed`/`manual` `pm` registration, so a
    plain registration never accidentally becomes another claimant's OQ-1
    "legacy prior owner" (`resolve_legacy_owner` only considers `relayed`/
    `manual` registrations written by `pm`). Tests that specifically need a
    real declared owner use `_declare_owner` instead.
    """
    adapter = PortableAdapter(workspace, slug, local_id=local_id)
    session = adapter.current_session()
    event_dict = fleet_cli.build_session_registered_event(
        session, origination=origination, project_id=PROJECT_ID, writer_role=writer_role
    )
    append(_log_path(workspace, slug), event_dict)
    return session.node_id


def _declare_owner(workspace: Path, slug: str, node_id: str) -> None:
    """Make `node_id` (already registered) the project's real declared owner."""
    core_claim(_log_path(workspace, slug), project_id=PROJECT_ID, claimant=node_id, writer_role="pm")


def _register_claude(
    workspace: Path,
    slug: str,
    local_id: str,
    *,
    writer_role: str = "developer",
    origination: str = "spawned",
) -> str:
    """Register a `harness="claude"` session directly (no live ClaudeAdapter
    needed) and return its `node_id` -- so `--sessions-json`'s `sessionId`
    matching can resolve real liveness for it (`resolve_liveness` only knows
    how to read `"claude"`-harness records, DESIGN O.4). Defaults to a plain
    (non-legacy-owner-eligible) registration; pass `writer_role="pm"` and
    `origination="relayed"`/`"manual"` for an OQ-1 legacy prior owner."""
    node_id = make_node_id("claude", str(workspace), slug, local_id)
    session = SessionInfo(
        node_id=node_id, harness="claude", workspace=str(workspace), local_id=local_id
    )
    event_dict = fleet_cli.build_session_registered_event(
        session, origination=origination, project_id=PROJECT_ID, writer_role=writer_role
    )
    append(_log_path(workspace, slug), event_dict)
    return node_id


@pytest.fixture
def owner_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A workspace with fleet enabled and a resolvable `SUPERHUMAN.md` identity."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    slug = "demo-project"

    profile = tmp_path / "profile.yaml"
    profile.write_text("fleet:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(profile))

    project_dir = workspace / "docs" / "superhuman" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "SUPERHUMAN.md").write_text(
        f"**Slug:** {slug}\n**Project-id:** {PROJECT_ID}\n", encoding="utf-8"
    )
    return workspace, slug


@pytest.fixture
def disabled_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A workspace with no fleet configuration at all."""
    monkeypatch.setenv("SUPERHUMAN_PROFILE", str(tmp_path / "no-such-profile.yaml"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace, "demo-project"


class TestOwnerClaimExitCodes:
    def test_first_claim_by_a_registered_node_succeeds(self, owner_project: tuple[Path, str]) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
            ]
        )

        assert code == 0

    def test_reclaim_by_the_same_owner_is_a_noop(self, owner_project: tuple[Path, str]) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")
        argv = [
            "owner",
            "claim",
            "--workspace",
            str(workspace),
            "--slug",
            slug,
            "--harness",
            "portable",
            "--local-id",
            "claimant-1",
        ]
        assert fleet_cli.main(argv) == 0
        events_before = read_all(_log_path(workspace, slug))

        assert fleet_cli.main(argv) == 0

        events_after = read_all(_log_path(workspace, slug))
        assert len(events_after) == len(events_before)

    def test_forced_lock_timeout_exits_1(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")
        events_before = read_all(_log_path(workspace, slug))

        def _raise(*args: object, **kwargs: object) -> None:
            raise LockTimeoutError("forced for TC-O17")

        monkeypatch.setattr("scripts.fleet.core.project_owner.append_batch", _raise)

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
            ]
        )

        assert code == 1
        events_after = read_all(_log_path(workspace, slug))
        assert [e.event_id for e in events_after] == [e.event_id for e in events_before]

    def test_active_prior_owner_with_no_attestation_is_refused_exit_3(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        owner_node_id = _register(workspace, slug, "owner-a")
        _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node_id)
        events_before = read_all(_log_path(workspace, slug))

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
            ]
        )

        assert code == 3
        events_after = read_all(_log_path(workspace, slug))
        assert len(events_after) == len(events_before), "log must be byte-unchanged on refusal"
        stderr = capsys.readouterr().err
        assert owner_node_id in stderr

        # accepted with the matching attestation
        code_ok = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
                "--prior-owner-notified",
                owner_node_id,
                "--notified-via",
                "slack DM",
            ]
        )
        assert code_ok == 0

    def test_standdown_by_a_non_owner_is_refused_exit_4(self, owner_project: tuple[Path, str]) -> None:
        workspace, slug = owner_project
        owner_node_id = _register(workspace, slug, "owner-a")
        _register(workspace, slug, "bystander")
        _declare_owner(workspace, slug, owner_node_id)

        code = fleet_cli.main(
            [
                "owner",
                "stand-down",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "bystander",
            ]
        )

        assert code == 4

    def test_claim_by_an_unregistered_node_is_refused_exit_4(self, owner_project: tuple[Path, str]) -> None:
        workspace, slug = owner_project

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "never-registered",
            ]
        )

        assert code == 4

    def test_disabled_fleet_exits_5_with_one_stderr_line(
        self, disabled_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = disabled_project

        code = fleet_cli.main(
            ["owner", "claim", "--workspace", str(workspace), "--slug", slug, "--harness", "portable"]
        )

        assert code == 5
        captured = capsys.readouterr()
        assert captured.out == ""
        assert len(captured.err.rstrip("\n").splitlines()) == 1

    def test_unresolvable_project_identity_exits_5(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        (workspace / "docs" / "superhuman" / slug / "SUPERHUMAN.md").unlink()

        code = fleet_cli.main(
            ["owner", "show", "--workspace", str(workspace), "--slug", slug]
        )

        assert code == 5
        captured = capsys.readouterr()
        assert captured.out == ""
        assert len(captured.err.rstrip("\n").splitlines()) == 1


class TestOwnerClaimArgumentConstraints:
    def test_prior_owner_notified_without_notified_via_is_usage_error(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
                "--prior-owner-notified",
                "some/node/id",
            ]
        )

        assert code == 2

    def test_notified_via_without_prior_owner_notified_is_usage_error(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
                "--notified-via",
                "slack DM",
            ]
        )

        assert code == 2

    def test_node_id_without_writer_role_cto_is_refused_exit_4(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "target-1")

        code = fleet_cli.main(
            ["owner", "claim", "--workspace", str(workspace), "--slug", slug, "--node-id", node_id]
        )

        assert code == 4

    def test_node_id_with_writer_role_cto_proceeds_to_the_ownership_decision(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "target-1")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--node-id",
                node_id,
                "--writer-role",
                "cto",
            ]
        )

        assert code == 0


class TestNoBroadCatchInOwnerModules:
    """TC-O19 (O-NFR-1): the new owner-specific modules/handlers never add a
    broad `except Exception`/`except BaseException`/bare `except:` — that
    discipline belongs solely to `observe.py` (Decision A)."""

    @pytest.mark.parametrize(
        "relative_path",
        [
            "scripts/fleet/cli.py",
            "scripts/fleet/core/project_owner.py",
            "scripts/fleet/adapter/session_liveness.py",
            "scripts/fleet/path_safety.py",
        ],
    )
    def test_no_broad_except_clauses(self, relative_path: str) -> None:
        source = (_SKILL_ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)

        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                offenders.append(f"{relative_path}:{node.lineno}: bare except")
                continue
            names = (
                [n.id for n in node.type.elts if isinstance(n, ast.Name)]
                if isinstance(node.type, ast.Tuple)
                else [node.type.id]
                if isinstance(node.type, ast.Name)
                else []
            )
            if "Exception" in names or "BaseException" in names:
                offenders.append(f"{relative_path}:{node.lineno}: except {names}")

        assert offenders == []

    def test_observe_verb_still_exits_0_under_an_injected_fault(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decision A is unchanged by this increment: `observe` verbs
        remain fail-soft even though `owner` verbs now fail loudly."""
        workspace, slug = owner_project

        def _raise(*args: object, **kwargs: object) -> None:
            raise RuntimeError("injected for TC-O19")

        monkeypatch.setattr("scripts.fleet.cli.register_session", _raise)

        code = fleet_cli.main(
            [
                "observe",
                "dispatch",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--dispatch-id",
                "child-1",
                "--writer-role",
                "pm",
            ]
        )

        assert code == 0

    @pytest.mark.parametrize("bad", ["missing", "malformed"])
    def test_observe_verb_still_exits_0_with_an_unusable_sessions_json(
        self, owner_project: tuple[Path, str], tmp_path: Path, bad: str
    ) -> None:
        """Decision A regression pin: an unusable `--sessions-json` on an
        `observe` verb is journaled and swallowed, never a non-zero exit
        (before `_load_sessions_json`, a missing file escaped as an OSError)."""
        workspace, slug = owner_project
        sessions = tmp_path / f"sessions-{bad}.json"
        if bad == "malformed":
            sessions.write_text("{not json", encoding="utf-8")

        code = fleet_cli.main(
            [
                "observe",
                "session-start",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "claude",
                "--session-id",
                "obs-1",
                "--sessions-json",
                str(sessions),
            ]
        )

        assert code == 0


class TestOwnerLockRetryAttemptsMustBePositive:
    """Fix round 2: `--lock-retry-attempts` below 1 never calls core and used
    to crash on an `assert`; it is now an argparse usage error (exit 2)."""

    @pytest.mark.parametrize("verb", ["claim", "stand-down"])
    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_non_positive_lock_retry_attempts_is_a_usage_error(
        self, owner_project: tuple[Path, str], verb: str, value: str
    ) -> None:
        workspace, slug = owner_project
        with pytest.raises(SystemExit) as exc_info:
            fleet_cli.main(
                [
                    "owner",
                    verb,
                    "--workspace",
                    str(workspace),
                    "--slug",
                    slug,
                    "--writer-role",
                    "pm",
                    "--lock-retry-attempts",
                    value,
                ]
            )
        assert exc_info.value.code == 2


class TestOwnerClaimLivenessMappingC1:
    """C1: the CLI must build liveness for every node registered in the
    project, not just the one owner it happened to see on its own unlocked
    read. A race that changes the owner between the CLI's read and core's
    write must be decided using the NEW owner's own liveness (DESIGN O.4's
    probe), not a value the CLI resolved for whichever owner it saw a
    moment earlier -- in EITHER direction: landing without attestation over
    a real active owner (danger), or wrongly demanding coordination against
    a real archived owner (false refusal) because the map the CLI built
    never had an entry for the node core ended up resolving."""

    def test_takeover_by_an_archived_new_owner_lands_without_attestation(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = owner_project
        owner_a = _register_claude(workspace, slug, "owner-a")
        owner_b = _register_claude(workspace, slug, "owner-b")
        _register(workspace, slug, "claimant-c")
        _declare_owner(workspace, slug, owner_a)

        sessions_path = workspace / "sessions.json"
        # owner-a (the owner the CLI's OWN unlocked read will see) is not
        # mentioned at all -- unresolved. owner-b (who the race makes the
        # REAL current owner before core ever writes) is archived.
        sessions_path.write_text(
            json.dumps([{"sessionId": "owner-b", "isArchived": True}]), encoding="utf-8"
        )

        real_read_all = fleet_cli.read_all

        def _racing_read_all(path):
            result = real_read_all(path)
            # Race: owner-b takes real ownership over owner-a (with
            # attestation, so the race claim itself lands) right after the
            # CLI's OWN unlocked read builds its liveness map -- before
            # core's claim() ever reads or writes anything for THIS call.
            monkeypatch.setattr(fleet_cli, "read_all", real_read_all)
            core_claim(
                path,
                project_id=PROJECT_ID,
                claimant=owner_b,
                writer_role="pm",
                liveness={owner_a: "unknown"},
                attested_owner=owner_a,
                notified_via="test",
            )
            return result

        monkeypatch.setattr(fleet_cli, "read_all", _racing_read_all)

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-c",
                "--sessions-json",
                str(sessions_path),
            ]
        )

        # The CLI's own unlocked read only ever saw owner-a. The claim can
        # only correctly land here (over the REAL current owner, owner-b,
        # who IS archived) if the liveness mapping already covered owner-b
        # too -- a mapping built for owner-a alone has no entry for owner-b
        # and would wrongly default it to "unknown", forcing a spurious
        # coordination refusal instead of this clean takeover.
        assert code == 0

    def test_interleaved_competing_claim_by_an_active_owner_exits_3(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = owner_project
        owner_a = _register_claude(workspace, slug, "owner-a")
        owner_b = _register_claude(workspace, slug, "owner-b")
        _register(workspace, slug, "claimant-c")
        _declare_owner(workspace, slug, owner_a)

        sessions_path = workspace / "sessions.json"
        # owner-a is archived per the supplied records; owner-b is not
        # mentioned at all -- unresolved, so O-FR-5 treats it as active.
        sessions_path.write_text(
            json.dumps([{"sessionId": "owner-a", "isArchived": True}]), encoding="utf-8"
        )

        events_before = read_all(_log_path(workspace, slug))
        real_read_all = fleet_cli.read_all

        def _racing_read_all(path):
            result = real_read_all(path)
            # Race: owner-b takes real ownership over owner-a right after
            # the CLI's OWN unlocked read builds its liveness map -- before
            # core's claim() ever reads or writes anything for this call.
            monkeypatch.setattr(fleet_cli, "read_all", real_read_all)
            core_claim(
                path,
                project_id=PROJECT_ID,
                claimant=owner_b,
                writer_role="pm",
                liveness={owner_a: "archived"},
            )
            return result

        monkeypatch.setattr(fleet_cli, "read_all", _racing_read_all)

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-c",
                "--sessions-json",
                str(sessions_path),
            ]
        )

        assert code == 3
        events_after = read_all(_log_path(workspace, slug))
        # Only owner-b's takeover (injected by the race -- its own
        # stand-down-of-owner-a plus its own declaration) landed; nothing
        # from THIS claim call (for claimant-c) was ever written.
        assert len(events_after) == len(events_before) + 2
        assert not any(
            e.type == "ownership_declared" and e.node_id not in (owner_a, owner_b)
            for e in events_after
        )


class TestOwnerClaimBadSessionsJsonI1:
    """I1: a bad `--sessions-json` (missing file, malformed JSON, a JSON
    object instead of a list, a list of non-dict items, or undecodable
    bytes) must exit 1 with one stated reason -- never an uncaught
    traceback -- whether it is hit via self-identity resolution
    (`_build_adapter`) or the claim handler's own liveness-mapping read."""

    def test_missing_file_exits_1_with_one_stated_reason(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        missing = workspace / "does-not-exist.json"

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "claude",
                "--session-id",
                "claimant-1",
                "--sessions-json",
                str(missing),
            ]
        )

        assert code == 1
        stderr = capsys.readouterr().err
        lines = stderr.rstrip("\n").splitlines()
        assert len(lines) == 1
        assert "--sessions-json unusable" in lines[0]

    def test_malformed_json_exits_1(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        bad = workspace / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "claude",
                "--session-id",
                "claimant-1",
                "--sessions-json",
                str(bad),
            ]
        )

        assert code == 1
        assert "--sessions-json unusable" in capsys.readouterr().err

    def test_json_object_instead_of_list_exits_1(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        obj = workspace / "object.json"
        obj.write_text(json.dumps({"sessionId": "abc"}), encoding="utf-8")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "claude",
                "--session-id",
                "claimant-1",
                "--sessions-json",
                str(obj),
            ]
        )

        assert code == 1
        assert "--sessions-json unusable" in capsys.readouterr().err

    def test_list_of_non_dicts_exits_1(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        bad_list = workspace / "list.json"
        bad_list.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "claude",
                "--session-id",
                "claimant-1",
                "--sessions-json",
                str(bad_list),
            ]
        )

        assert code == 1
        assert "--sessions-json unusable" in capsys.readouterr().err

    def test_undecodable_bytes_exits_1(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        binary = workspace / "binary.json"
        binary.write_bytes(b"\xff\xfe\x00\x01not utf-8 \x80\x81")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "claude",
                "--session-id",
                "claimant-1",
                "--sessions-json",
                str(binary),
            ]
        )

        assert code == 1
        assert "--sessions-json unusable" in capsys.readouterr().err

    def test_bad_sessions_json_on_the_on_behalf_node_id_path_also_exits_1(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The `--node-id`/`--writer-role cto` path never calls
        `_build_adapter` -- this exercises the claim handler's OWN
        `_load_sessions_json` call site, not the one inside adapter
        construction."""
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "target-1")
        missing = workspace / "does-not-exist.json"

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--node-id",
                node_id,
                "--writer-role",
                "cto",
                "--sessions-json",
                str(missing),
            ]
        )

        assert code == 1
        assert "--sessions-json unusable" in capsys.readouterr().err


class TestOwnerClaimLegacyOwnerLivenessI2:
    """I2: a legacy (relayed/manual `pm`) prior owner's liveness must be
    resolved too -- fixed by C1's mapping, since it is built over every
    registered node (including a legacy registration), not just a
    `fold_owner`-declared owner."""

    def test_legacy_owner_archived_in_sessions_json_lands_without_attestation(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        log_path = _log_path(workspace, slug)
        # A `relayed` `pm` registration qualifies as the OQ-1 legacy prior
        # owner (resolve_legacy_owner requires writer_role == "pm" and
        # origination in {"relayed", "manual"}) -- no `ownership_declared`
        # event exists at all yet.
        _register_claude(workspace, slug, "legacy-pm", writer_role="pm", origination="relayed")
        node_a = _register(workspace, slug, "claimant-a")

        sessions_path = workspace / "sessions.json"
        sessions_path.write_text(
            json.dumps([{"sessionId": "legacy-pm", "isArchived": True}]), encoding="utf-8"
        )

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-a",
                "--sessions-json",
                str(sessions_path),
            ]
        )

        assert code == 0
        declared = [
            e for e in read_all(log_path) if e.type == "ownership_declared" and e.node_id == node_a
        ]
        assert len(declared) == 1
        assert declared[0].payload["basis"] == "archived"
        assert declared[0].payload["prior_owner_kind"] == "legacy"
        assert declared[0].payload["attestation"] is None


class TestOwnerClaimBoundedLockRetryI3:
    """I3: `owner claim`/`stand-down` must retry a bounded number of times
    on plain lock contention (`LockTimeoutError`), the same second-tier
    retry every other manifest-writing verb already gets via
    `_append_with_bounded_retry` -- distinct from `OwnershipContended`'s own
    bounded re-evaluation against a CHANGING ownership state."""

    def test_lock_timeout_once_then_success_lands_exactly_one_declaration(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")

        from scripts.fleet.core import project_owner as po_mod

        real_append_batch = po_mod.append_batch
        calls = {"n": 0}

        def _fail_once_then_succeed(*args: object, **kwargs: object):
            calls["n"] += 1
            if calls["n"] == 1:
                raise LockTimeoutError("forced for TC-O-I3")
            return real_append_batch(*args, **kwargs)

        monkeypatch.setattr(po_mod, "append_batch", _fail_once_then_succeed)

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
            ]
        )

        assert code == 0
        assert calls["n"] == 2
        declared = [e for e in read_all(_log_path(workspace, slug)) if e.type == "ownership_declared"]
        assert len(declared) == 1

    def test_lock_timeout_every_attempt_exits_1_after_bounded_attempts(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")
        events_before = read_all(_log_path(workspace, slug))

        calls = {"n": 0}

        def _always_raise(*args: object, **kwargs: object) -> None:
            calls["n"] += 1
            raise LockTimeoutError("forced for TC-O-I3")

        monkeypatch.setattr("scripts.fleet.core.project_owner.append_batch", _always_raise)

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
                "--lock-retry-attempts",
                "3",
            ]
        )

        assert code == 1
        assert calls["n"] == 3
        events_after = read_all(_log_path(workspace, slug))
        assert [e.event_id for e in events_after] == [e.event_id for e in events_before]

    def test_standdown_lock_timeout_once_then_success(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "owner-1")
        _declare_owner(workspace, slug, node_id)

        from scripts.fleet.core import project_owner as po_mod

        real_append_batch = po_mod.append_batch
        calls = {"n": 0}

        def _fail_once_then_succeed(*args: object, **kwargs: object):
            calls["n"] += 1
            if calls["n"] == 1:
                raise LockTimeoutError("forced for TC-O-I3")
            return real_append_batch(*args, **kwargs)

        monkeypatch.setattr(po_mod, "append_batch", _fail_once_then_succeed)

        code = fleet_cli.main(
            [
                "owner",
                "stand-down",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "owner-1",
            ]
        )

        assert code == 0
        assert calls["n"] == 2
        standdowns = [
            e for e in read_all(_log_path(workspace, slug)) if e.type == "ownership_stood_down"
        ]
        assert len(standdowns) == 1


class TestOwnerShowBasisAttestationPriorOwnerI4:
    """I4: `owner show` must print the declaring event's basis, attestation
    and prior_owner in both text and `--json` form (DESIGN O.3)."""

    def test_json_output_includes_basis_prior_owner_and_attestation(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register(workspace, slug, "owner-a")
        claimant_node = _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)
        core_claim(
            _log_path(workspace, slug),
            project_id=PROJECT_ID,
            claimant=claimant_node,
            writer_role="pm",
            attested_owner=owner_node,
            notified_via="slack DM",
        )

        code = fleet_cli.main(
            ["owner", "show", "--workspace", str(workspace), "--slug", slug, "--json"]
        )

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["owner"] == claimant_node
        assert payload["basis"] == "notified"
        assert payload["prior_owner"] == owner_node
        assert payload["attestation"] == {"notified_owner": owner_node, "notified_via": "slack DM"}

    def test_text_output_includes_basis_prior_owner_and_attestation(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register(workspace, slug, "owner-a")
        claimant_node = _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)
        core_claim(
            _log_path(workspace, slug),
            project_id=PROJECT_ID,
            claimant=claimant_node,
            writer_role="pm",
            attested_owner=owner_node,
            notified_via="slack DM",
        )

        code = fleet_cli.main(["owner", "show", "--workspace", str(workspace), "--slug", slug])

        assert code == 0
        stdout = capsys.readouterr().out
        assert f"owner: {claimant_node}" in stdout
        assert "basis: notified" in stdout
        assert f"prior_owner: {owner_node}" in stdout
        assert "notified_via" in stdout and "slack DM" in stdout


class TestOwnerVerbsI5:
    """I5: the CLI-level scenarios DESIGN O.8/TC-O17 call for explicitly --
    archived/active `--sessions-json` records, an attestation naming the
    wrong owner, a successful stand-down and its no-op, `show` in both
    forms, and the O-FR-1 observed-then-claim repro through `cli.main`."""

    def test_sessions_json_marking_the_owner_archived_exits_0_with_basis_archived(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register_claude(workspace, slug, "owner-a")
        _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)

        sessions_path = workspace / "sessions.json"
        sessions_path.write_text(
            json.dumps([{"sessionId": "owner-a", "isArchived": True}]), encoding="utf-8"
        )

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
                "--sessions-json",
                str(sessions_path),
            ]
        )

        assert code == 0
        declared = [
            e for e in read_all(_log_path(workspace, slug)) if e.type == "ownership_declared"
        ][-1]
        assert declared.payload["basis"] == "archived"
        assert declared.payload["attestation"] is None

    def test_sessions_json_marking_the_owner_active_still_exits_3(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register_claude(workspace, slug, "owner-a")
        _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)

        sessions_path = workspace / "sessions.json"
        sessions_path.write_text(
            json.dumps([{"sessionId": "owner-a", "isArchived": False}]), encoding="utf-8"
        )

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
                "--sessions-json",
                str(sessions_path),
            ]
        )

        assert code == 3

    def test_attestation_naming_the_wrong_owner_exits_3_attestation_mismatch(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register(workspace, slug, "owner-a")
        _register(workspace, slug, "someone-else")
        _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)
        events_before = read_all(_log_path(workspace, slug))

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
                "--prior-owner-notified",
                "someone-else",
                "--notified-via",
                "slack DM",
            ]
        )

        assert code == 3
        events_after = read_all(_log_path(workspace, slug))
        assert len(events_after) == len(events_before)
        assert "refused" in capsys.readouterr().err

    def test_exit_3_message_names_the_owner_and_the_rerun_flags(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register(workspace, slug, "owner-a")
        _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
            ]
        )

        assert code == 3
        stderr = capsys.readouterr().err
        assert owner_node in stderr
        assert f"--prior-owner-notified {owner_node} --notified-via" in stderr

    def test_successful_standdown_and_its_noop(self, owner_project: tuple[Path, str]) -> None:
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "owner-1")
        _declare_owner(workspace, slug, node_id)

        argv = [
            "owner",
            "stand-down",
            "--workspace",
            str(workspace),
            "--slug",
            slug,
            "--harness",
            "portable",
            "--local-id",
            "owner-1",
        ]

        first = fleet_cli.main(argv)
        assert first == 0
        standdowns = [
            e for e in read_all(_log_path(workspace, slug)) if e.type == "ownership_stood_down"
        ]
        assert len(standdowns) == 1

        second = fleet_cli.main(argv)
        assert second == 0
        standdowns_after = [
            e for e in read_all(_log_path(workspace, slug)) if e.type == "ownership_stood_down"
        ]
        assert len(standdowns_after) == 1

    def test_show_text_and_json_with_no_declared_owner(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "someone")

        code = fleet_cli.main(["owner", "show", "--workspace", str(workspace), "--slug", slug])
        assert code == 0
        assert "no declared owner" in capsys.readouterr().out

        code_json = fleet_cli.main(
            ["owner", "show", "--workspace", str(workspace), "--slug", slug, "--json"]
        )
        assert code_json == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["owner"] is None
        assert payload["has_ownership_events"] is False

    def test_observed_then_claim_repro_through_cli_main(
        self, owner_project: tuple[Path, str]
    ) -> None:
        """O-FR-1: a node registered `observed` (never `spawned`/`relayed`/
        `manual`) can still claim ownership through the real CLI entry
        point -- registration's origination never gates a claim."""
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "claimant-1", origination="observed")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
            ]
        )

        assert code == 0
        declared = [
            e for e in read_all(_log_path(workspace, slug)) if e.type == "ownership_declared"
        ]
        assert len(declared) == 1
        assert declared[0].node_id == node_id


class TestLivenessSourceUsesFlagPresenceM1:
    """M1: `liveness_source` must be recorded from `args.sessions_json is
    not None` -- whether `--sessions-json` was GIVEN -- never from the
    truthiness of the records it resolved to, which is falsy for a
    perfectly valid, deliberately empty `[]` file."""

    def test_an_empty_sessions_json_array_still_records_sessions_json_source(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")

        empty_sessions = workspace / "empty.json"
        empty_sessions.write_text("[]", encoding="utf-8")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
                "--sessions-json",
                str(empty_sessions),
            ]
        )

        assert code == 0
        declared = [
            e for e in read_all(_log_path(workspace, slug)) if e.type == "ownership_declared"
        ]
        assert declared[0].payload["liveness_source"] == "sessions-json"


class TestSessionsJsonHelpTextM2:
    """M2: `claim`'s `--sessions-json` help text must say it is used for
    prior-owner liveness regardless of the claimant's own harness;
    `stand-down`'s must say it is unused there -- neither should carry the
    generic "--harness claude only" text, which is wrong for `claim` and
    misleading for `stand-down` (it does nothing there at all)."""

    @staticmethod
    def _sessions_json_help(verb: str) -> str:
        parser = fleet_cli.build_parser()
        owner_parser = parser._subparsers._group_actions[0].choices["owner"]
        verb_parser = owner_parser._subparsers._group_actions[0].choices[verb]
        action = next(a for a in verb_parser._actions if a.option_strings == ["--sessions-json"])
        return action.help or ""

    def test_claim_help_text_says_any_harness_and_prior_owner_liveness(self) -> None:
        help_text = self._sessions_json_help("claim")
        assert "--harness claude only" not in help_text
        assert "prior-owner liveness" in help_text

    def test_stand_down_help_text_says_unused(self) -> None:
        help_text = self._sessions_json_help("stand-down")
        assert "--harness claude only" not in help_text
        assert "unused" in help_text


class TestNodeIdMutuallyExclusiveWithSelfIdentityM3:
    """M3: `--node-id` (on-behalf identity) and any self-identity flag
    (`--harness`/`--session-id`/`--local-id`/`--session-relay-script`) given
    together must exit 2 (a usage error), per DESIGN O.3's "mutually
    exclusive" identity groups -- not silently resolve as if `--node-id`
    alone had been given."""

    def test_node_id_with_explicit_harness_claude_is_usage_error(
        self, owner_project: tuple[Path, str]
    ) -> None:
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "target-1")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--writer-role",
                "cto",
                "--node-id",
                node_id,
                "--harness",
                "claude",
                "--session-id",
                "some-session",
            ]
        )

        assert code == 2

    def test_node_id_with_local_id_is_usage_error(self, owner_project: tuple[Path, str]) -> None:
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "target-1")

        code = fleet_cli.main(
            [
                "owner",
                "stand-down",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--writer-role",
                "cto",
                "--node-id",
                node_id,
                "--local-id",
                "some-local-id",
            ]
        )

        assert code == 2

    def test_node_id_alone_with_default_harness_is_not_a_usage_error(
        self, owner_project: tuple[Path, str]
    ) -> None:
        """`--harness` defaults to `"portable"` even when never typed --
        `--node-id` alone (the documented on-behalf shape) must NOT be
        flagged as a conflict."""
        workspace, slug = owner_project
        node_id = _register(workspace, slug, "target-1")

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--node-id",
                node_id,
                "--writer-role",
                "cto",
            ]
        )

        assert code == 0


class TestReadAllOSErrorWrappedM4:
    """M4: a bare `read_all(log_path)` in `_cmd_owner_claim` (building the
    liveness map) or `_format_coordination_refusal` (enriching the exit-3
    message) must map an `OSError` to exit 1, the same as `owner show`
    already does -- never an uncaught traceback."""

    def test_unreadable_manifest_during_claim_exits_1(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = owner_project
        _register(workspace, slug, "claimant-1")

        monkeypatch.setattr(
            fleet_cli,
            "read_all",
            lambda path: (_ for _ in ()).throw(OSError("forced for TC-O-M4")),
        )

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-1",
            ]
        )

        assert code == 1

    def test_unreadable_manifest_while_formatting_the_coordination_refusal_exits_1(
        self, owner_project: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register(workspace, slug, "owner-a")
        _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)

        real_read_all = fleet_cli.read_all
        calls = {"n": 0}

        def _raise_on_second_call(path):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_read_all(path)
            raise OSError("forced for TC-O-M4")

        monkeypatch.setattr(fleet_cli, "read_all", _raise_on_second_call)

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
            ]
        )

        assert code == 1


class TestFindSessionRecordTitleExactlyOneMatchM5:
    """M5: `_find_session_record_title` must apply the SAME "exactly one
    match" rule `resolve_liveness` uses -- two or more records matching the
    same local id is ambiguous, and printing either one's title could name
    the wrong session in the exit-3 message."""

    def test_two_matching_records_returns_none_not_the_first_titles(self) -> None:
        sessions = [
            {"sessionId": "abc123", "title": "First Session"},
            {"sessionId": "abc123", "title": "Second Session"},
        ]
        assert fleet_cli._find_session_record_title(sessions, "abc123") is None

    def test_exactly_one_matching_record_returns_its_title(self) -> None:
        sessions = [{"sessionId": "abc123", "title": "Only Session"}]
        assert fleet_cli._find_session_record_title(sessions, "abc123") == "Only Session"

    def test_zero_matching_records_returns_none(self) -> None:
        sessions = [{"sessionId": "someone-else", "title": "Other"}]
        assert fleet_cli._find_session_record_title(sessions, "abc123") is None

    def test_exit_3_message_omits_title_when_sessions_json_is_ambiguous(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register_claude(workspace, slug, "owner-a")
        _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)

        sessions_path = workspace / "sessions.json"
        sessions_path.write_text(
            json.dumps(
                [
                    {"sessionId": "owner-a", "title": "First Session"},
                    {"sessionId": "owner-a", "title": "Second Session"},
                ]
            ),
            encoding="utf-8",
        )

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
                "--sessions-json",
                str(sessions_path),
            ]
        )

        assert code == 3
        stderr = capsys.readouterr().err
        assert "title=" not in stderr


class TestOwnerVerbsValidateSlugWithManifestDirOverrideM6:
    """M6: the owner verbs must validate `--slug` (`slug_is_safe`) even when
    an operator `fleet.manifest_dir` override means `default_fleet_dir`
    (the usual validator) is never called -- `read_project_identity` builds
    a path from the raw slug regardless, so an unsafe slug must still be
    rejected before that call, not silently joined."""

    def test_path_traversal_slug_is_rejected_even_with_a_manifest_dir_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.fleet.config import FleetConfig

        workspace = tmp_path / "ws"
        workspace.mkdir()

        # A REAL project outside `workspace` entirely, reachable only by
        # traversal through the slug: `<workspace>/docs/superhuman/<slug>/
        # SUPERHUMAN.md` with `slug = "../../../secret-project/docs/
        # superhuman/real-slug"` resolves to this file. Without the M6 fix,
        # `read_project_identity` would happily read it and return its REAL
        # project id -- proving this isn't just "the file doesn't exist".
        secret_project_dir = tmp_path / "secret-project" / "docs" / "superhuman" / "real-slug"
        secret_project_dir.mkdir(parents=True)
        (secret_project_dir / "SUPERHUMAN.md").write_text(
            "**Slug:** real-slug\n**Project-id:** SECRET-PROJECT-ID\n", encoding="utf-8"
        )

        bad_slug = "../../../secret-project/docs/superhuman/real-slug"

        # An operator `fleet.manifest_dir` override, so `_resolve_owner_manifest`
        # never calls `default_fleet_dir` (the slug's usual validator) at all.
        override_cfg = FleetConfig(
            enabled=True, reason="test override", manifest_dir=workspace / "shared-fleet-dir"
        )
        monkeypatch.setattr(fleet_cli.fleet_config, "resolve_fleet_config", lambda workspace: override_cfg)

        args = argparse.Namespace(workspace=workspace, slug=bad_slug)
        result = fleet_cli._resolve_owner_manifest(args)

        assert result is None, (
            "an unsafe slug must be rejected even when an operator manifest_dir "
            "override skips default_fleet_dir's own validation"
        )


class TestExit3HeadingPlainHyphenM7:
    """M7: the exit-3 refusal heading must use a plain "-", not an em dash
    -- the em dash mis-renders as a replacement character on a cp1252
    Windows console (a real, observed symptom, not a style nit)."""

    def test_no_em_dash_in_the_refusal_heading(
        self, owner_project: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace, slug = owner_project
        owner_node = _register(workspace, slug, "owner-a")
        _register(workspace, slug, "claimant-b")
        _declare_owner(workspace, slug, owner_node)

        code = fleet_cli.main(
            [
                "owner",
                "claim",
                "--workspace",
                str(workspace),
                "--slug",
                slug,
                "--harness",
                "portable",
                "--local-id",
                "claimant-b",
            ]
        )

        assert code == 3
        stderr = capsys.readouterr().err
        assert "\u2014" not in stderr
        assert "refused - coordination required" in stderr
