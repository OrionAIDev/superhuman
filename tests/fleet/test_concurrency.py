"""TC-12: NFR-1 flagship — N appenders + a mid-write launch flip, zero lost rows,
zero duplicates, real OS processes.

Threads share the GIL and can mask lock-contention bugs that only manifest
across processes, so this test uses ``multiprocessing.Process`` — real OS
processes racing on one shared ``events.jsonl`` + lockfile in a temp dir.

Chunk 1 has no ``handoff.py`` yet (that lands in Chunk 3), so the "child
flipping awaiting-launch->active while the parent writes" scenario DESIGN
describes is exercised here at the mechanism it actually depends on: the
locked-append path in ``core/events.py`` that ``handoff.self_register()``
will call in Chunk 3. The event shape used (``type="handoff_launched"``,
``idempotency_key=f"launch:{handoff_id}"``) matches DESIGN's Decision F
formula exactly, so this proves the real mechanism, not a stand-in for it.

Worker functions are module-level (not closures) so they are picklable under
``multiprocessing``'s ``spawn`` start method, which Windows always uses.
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

import pytest

# Belt-and-suspenders: tests/fleet/conftest.py already puts the skill root on
# sys.path for the parent process, and multiprocessing's spawn start method
# snapshots the parent's sys.path for the child — but this module must also
# be independently importable-by-name in the freshly spawned child
# interpreter, so we do not rely solely on fixture-time side effects.
_SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.fleet.core.events import append, read_all  # noqa: E402
from scripts.fleet.core.projection import rebuild  # noqa: E402

N_PARENTS = 8
EVENTS_PER_PARENT = 20
EXPECTED_TOTAL = N_PARENTS * EVENTS_PER_PARENT + 1 + 1  # + child flip + deduped double-launch


def _parent_worker(log_path_str: str, project_id: str, worker_id: int, count: int) -> None:
    """Append `count` distinct session_registered events, as fast as possible."""
    for i in range(count):
        node_id = f"portable/ws/{project_id}/parent-{worker_id}-{i}"
        append(
            log_path_str,
            {
                "schema_version": 1,
                "event_id": f"eid-parent-{worker_id}-{i}",
                "idempotency_key": f"register:{node_id}",
                "ts": "2026-08-14T12:00:00Z",
                "type": "session_registered",
                "project_id": project_id,
                "node_id": node_id,
                "writer_role": "Developer",
                "payload": {"lifecycle": "active"},
            },
        )


def _child_flip_worker(log_path_str: str, project_id: str, node_id: str, handoff_id: str) -> None:
    """Flip one pre-seeded awaiting-launch node to active — the single flip."""
    append(
        log_path_str,
        {
            "schema_version": 1,
            "event_id": "eid-child-flip",
            "idempotency_key": f"launch:{handoff_id}",
            "ts": "2026-08-14T12:00:01Z",
            "type": "handoff_launched",
            "project_id": project_id,
            "node_id": node_id,
            "writer_role": "Developer",
            "payload": {"lifecycle": "active"},
        },
    )


def _double_launch_worker(
    log_path_str: str, project_id: str, node_id: str, handoff_id: str, attempt: int
) -> None:
    """Attempt the same launch flip twice (from two processes) — must dedupe to one."""
    append(
        log_path_str,
        {
            "schema_version": 1,
            "event_id": f"eid-double-launch-attempt-{attempt}",
            "idempotency_key": f"launch:{handoff_id}",
            "ts": "2026-08-14T12:00:02Z",
            "type": "handoff_launched",
            "project_id": project_id,
            "node_id": node_id,
            "writer_role": "Developer",
            "payload": {"lifecycle": "active"},
        },
    )


def _run_one_scenario(tmp_dir: Path, run_index: int) -> None:
    """Run the full N-appender + child-flip + double-launch scenario once."""
    project_id = f"proj-concurrency-{run_index}"
    log_path = tmp_dir / "events.jsonl"
    sessions_dir = tmp_dir / "sessions"

    handoff_id_single = f"handoff-single-{run_index}"
    node_id_single = f"portable/ws/{project_id}/handoff-single"
    handoff_id_double = f"handoff-double-{run_index}"
    node_id_double = f"portable/ws/{project_id}/handoff-double"

    # Pre-seed both handoff-target nodes as awaiting-launch, matching TC-12's
    # setup ("a pre-seeded handoff_id"), before any worker starts.
    for node_id in (node_id_single, node_id_double):
        append(
            log_path,
            {
                "schema_version": 1,
                "event_id": f"eid-seed-{node_id}",
                "idempotency_key": f"register:{node_id}",
                "ts": "2026-08-14T11:59:00Z",
                "type": "session_registered",
                "project_id": project_id,
                "node_id": node_id,
                "writer_role": "Developer",
                "payload": {"lifecycle": "awaiting-launch"},
            },
        )

    ctx = multiprocessing.get_context("spawn")
    processes: list[multiprocessing.process.BaseProcess] = []

    for worker_id in range(N_PARENTS):
        p = ctx.Process(
            target=_parent_worker,
            args=(str(log_path), project_id, worker_id, EVENTS_PER_PARENT),
        )
        processes.append(p)

    processes.append(
        ctx.Process(
            target=_child_flip_worker,
            args=(str(log_path), project_id, node_id_single, handoff_id_single),
        )
    )
    processes.append(
        ctx.Process(
            target=_double_launch_worker,
            args=(str(log_path), project_id, node_id_double, handoff_id_double, 1),
        )
    )
    processes.append(
        ctx.Process(
            target=_double_launch_worker,
            args=(str(log_path), project_id, node_id_double, handoff_id_double, 2),
        )
    )

    # Start all concurrently — not sequenced — to maximize lock contention.
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=60)
        assert p.exitcode == 0, f"worker {p.name} exited with code {p.exitcode}"

    raw_lines = [
        line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    events = read_all(log_path)

    # 1. Zero lost rows: exact count (2 seed events + the burst).
    assert len(events) == EXPECTED_TOTAL + 2, (
        f"expected {EXPECTED_TOTAL + 2} events (2 seeds + {EXPECTED_TOTAL}), got {len(events)}"
    )

    # 5. No torn/corrupted line under normal (non-crash) concurrent operation:
    # every raw line parsed as a valid event — none needed TC-9's skip path.
    assert len(raw_lines) == len(events), (
        "a raw log line did not parse as a valid event under normal "
        "concurrent operation — the lock did not serialize writers correctly"
    )

    # 2. Zero duplicates: no two events share an idempotency_key.
    keys = [e.idempotency_key for e in events]
    assert len(keys) == len(set(keys)), "duplicate idempotency_key found in the log"

    # The double-launch fixture contributes exactly one handoff_launched event.
    double_launch_events = [e for e in events if e.idempotency_key == f"launch:{handoff_id_double}"]
    assert len(double_launch_events) == 1

    # 3. Every parent's 20 events are present in full (per-worker completeness).
    for worker_id in range(N_PARENTS):
        worker_keys = {
            f"register:portable/ws/{project_id}/parent-{worker_id}-{i}"
            for i in range(EVENTS_PER_PARENT)
        }
        assert worker_keys.issubset(set(keys)), f"worker {worker_id} lost events"

    # 4. The child's flip lands correctly after rebuild.
    fragments = rebuild(log_path, sessions_dir, project_id=project_id)
    assert fragments[node_id_single].lifecycle == "active"
    assert fragments[node_id_double].lifecycle == "active"


@pytest.mark.parametrize("run_index", range(5))
def test_concurrent_appenders_lose_nothing_and_never_duplicate(
    tmp_path_factory: pytest.TempPathFactory, run_index: int
) -> None:
    """TC-12, repeated 5x for flake detection — each run is independently exact."""
    tmp_dir = tmp_path_factory.mktemp(f"fleet-concurrency-{run_index}")
    _run_one_scenario(tmp_dir, run_index)
