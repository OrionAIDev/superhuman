"""Regression test for `scripts.fleet.bounded_journal` (Phase 3.3 preflight
recommended fix, item 3): `observe.py`'s `_write_journal` and
`role_block.py`'s `record_role_gate_decision` each independently did a
whole-file read-modify-write with no lock, so under parallel dispatch (two
hook processes appending at once) one writer's rewrite silently discarded
the other's already-durable line -- reproduced under load, losing roughly
a quarter of the rows.

`N` concurrent writer PROCESSES (not threads -- threads share the GIL,
which can mask a true read-modify-write race; see
`tests/fleet/test_lock_os_native.py`'s own reasoning for the identical
choice) each append one uniquely-identified line to the SAME file via
`bounded_journal.append_bounded_line`; the test asserts every row
survives. Worker functions are module-level (not closures) so they are
picklable under `multiprocessing`'s `spawn` start method, which Windows
always uses (this machine's platform).
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

# Belt-and-suspenders, matching test_lock_os_native.py's own bootstrap:
# tests/fleet/conftest.py already puts the skill root on sys.path for the
# parent process, but this module must also be independently
# importable-by-name in the freshly spawned child interpreter.
_SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.fleet.bounded_journal import append_bounded_line  # noqa: E402

N_WRITERS = 20


def _append_one_line(path_str: str, index: int, max_lines: int) -> None:
    """Append one uniquely-identified line to `path_str`.

    Module-level (not a closure) so it is picklable under
    `multiprocessing`'s `spawn` start method.

    Args:
        path_str: the target journal path, as a string (`Path` objects
            pickle fine too, but a plain string keeps the child's argv
            unambiguous if this is ever driven via a real subprocess).
        index: this writer's unique row index.
        max_lines: forwarded to `append_bounded_line` -- set large enough
            by the caller that no legitimate trimming interferes with the
            row count under test.
    """
    append_bounded_line(Path(path_str), f"line-{index}", max_lines=max_lines)


def test_n_concurrent_writer_processes_lose_no_rows(tmp_path: Path) -> None:
    """TC-116: `N_WRITERS` processes race to append via
    `append_bounded_line` to the same journal file. Every row must land --
    the lock makes the read-modify-write critical section mutually
    exclusive, so no writer's whole-file rewrite can silently discard
    another writer's already-durable line (the defect this module exists
    to close)."""
    path = tmp_path / "journal.jsonl"
    ctx = multiprocessing.get_context("spawn")
    processes = [
        ctx.Process(target=_append_one_line, args=(str(path), i, N_WRITERS)) for i in range(N_WRITERS)
    ]
    for proc in processes:
        proc.start()
    for proc in processes:
        proc.join(timeout=30)
    for proc in processes:
        assert proc.exitcode == 0, f"writer process {proc.pid} exited with {proc.exitcode}"

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == N_WRITERS, (
        f"expected {N_WRITERS} rows, found {len(lines)} -- lost "
        f"{N_WRITERS - len(lines)} row(s) under concurrent dispatch"
    )
    assert sorted(lines) == sorted(f"line-{i}" for i in range(N_WRITERS))
