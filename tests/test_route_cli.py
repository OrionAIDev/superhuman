"""CLI-contract tests for ``scripts/route.py`` (entry-tiering Chunk 1).

Covers the router skeleton per ``docs/superhuman/entry-tiering/{DESIGN,PLAN,
TEST}.md`` Chunk 1: all five subcommands exist, the full ``resolve``
exit-code table (FR-6), the FR-4 resume short-circuit and its precedence over
signal evaluation, the stdlib-only import list (FR-7/NFR-2), determinism
(FR-2, partial), and the D-4 usage-error contract for ``--request``.

Signal evaluation (the request/repo/rung axes) is not implemented until
Chunk 2/3: a fixture that reaches scoring in this chunk raises
``NotImplementedError``, which the top-level handler maps to exit 3. That is
asserted here as correct Chunk-1 behaviour, not worked around -- see
``test_scoring_reached_hits_not_implemented_and_exits_internal``.

Per the PM's Chunk-1 ruling, rows 10/11/12 of the exit-code table cannot be
reached end-to-end via subprocess until Chunk 2 supplies real signals, so
they are asserted in-process by monkeypatching ``route.evaluate_signals`` and
calling ``route.main`` directly -- exercising the *real* dispatch path
(``cmd_resolve``'s tier -> exit-code mapping), not a test-only backdoor.
Subprocess-level assertion of these rows against a fixture that genuinely
scores to each tier lands in Chunk 2.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import route  # noqa: E402

ROUTE_PY = Path(__file__).resolve().parents[1] / "scripts" / "route.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "route"

GREENFIELD = FIXTURES / "greenfield"
WITH_PRIORS = FIXTURES / "with-priors"
OPEN_PROJECT = FIXTURES / "open-project"
STALE_PROJECT = FIXTURES / "stale-project"
CLOSED_PROJECT = FIXTURES / "closed-project"


def _cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``route.py`` as a subprocess and capture its output.

    Args:
        args: CLI arguments, excluding the interpreter and script path.

    Returns:
        The completed process (returncode, stdout, stderr).
    """
    return subprocess.run(
        [sys.executable, str(ROUTE_PY), *args],
        capture_output=True,
        text=True,
        check=False,
    )


# --------------------------------------------------------------------------- #
# CLI surface: subcommands, --help, --version
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sub", ["resolve", "explain", "doctor", "lint-ledger", "risk-scan"])
def test_every_subcommand_exists_and_has_help(sub: str) -> None:
    """Every documented subcommand exists and supports --help."""
    proc = _cli([sub, "--help"])
    assert proc.returncode == 0, proc.stderr
    assert sub.split("-")[0] in proc.stdout.lower() or sub in proc.stdout.lower()


def test_top_level_help() -> None:
    """`route.py --help` lists all five subcommands."""
    proc = _cli(["--help"])
    assert proc.returncode == 0, proc.stderr
    for sub in ("resolve", "explain", "doctor", "lint-ledger", "risk-scan"):
        assert sub in proc.stdout


def test_version_flag() -> None:
    """`route.py --version` exits 0 and prints something."""
    proc = _cli(["--version"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()


# --------------------------------------------------------------------------- #
# TC-1 / FR-6: exit-code table, one test per row
# --------------------------------------------------------------------------- #


def test_row_0_resume_exits_zero_no_tier_line() -> None:
    """Row 0: an open project short-circuits routing; no `tier:` line prints."""
    proc = _cli(["resolve", "--repo", str(OPEN_PROJECT), "--request", "do something unrelated"])
    assert proc.returncode == route.EXIT_RESUME
    assert "tier:" not in proc.stdout


def test_row_2_usage_error_missing_request() -> None:
    """Row 2: an absent --request exits 2 and names the flag."""
    proc = _cli(["resolve", "--repo", str(GREENFIELD)])
    assert proc.returncode == route.EXIT_USAGE
    assert "--request" in proc.stderr


def test_row_2_usage_error_empty_request() -> None:
    """Row 2 (D-4's other half): a whitespace-only --request exits 2 and names the flag."""
    proc = _cli(["resolve", "--repo", str(GREENFIELD), "--request", "   "])
    assert proc.returncode == route.EXIT_USAGE
    assert "--request" in proc.stderr


def test_row_2_usage_error_missing_repo() -> None:
    """Row 2: an absent --repo exits 2 and names the flag."""
    proc = _cli(["resolve", "--request", "add a widget"])
    assert proc.returncode == route.EXIT_USAGE
    assert "--repo" in proc.stderr


def test_row_2_usage_error_unreadable_repo() -> None:
    """Row 2: an unreadable --repo path exits 2 with the path in the message."""
    missing = FIXTURES / "does-not-exist"
    proc = _cli(["resolve", "--repo", str(missing), "--request", "add a widget"])
    assert proc.returncode == route.EXIT_USAGE
    assert str(missing) in proc.stderr


def test_row_2_usage_error_repo_is_a_file_not_a_directory() -> None:
    """--repo pointing at a file (not a directory) is unreadable per the contract."""
    proc = _cli(["resolve", "--repo", str(ROUTE_PY), "--request", "add a widget"])
    assert proc.returncode == route.EXIT_USAGE


def test_row_3_scoring_reached_hits_not_implemented_and_exits_internal() -> None:
    """Row 3: a repo with no resume state reaches scoring.

    In this chunk `evaluate_signals` is a stub that always raises
    NotImplementedError (Chunk 2/3 implement the axes); the top-level handler
    in `main` must map that to exit 3, never to a tier code and never to a
    silent success -- and must suppress the traceback from stdout. This is
    the documented, correct Chunk-1 behaviour, asserted end-to-end rather than
    worked around.
    """
    proc = _cli(["resolve", "--repo", str(GREENFIELD), "--request", "add a widget"])
    assert proc.returncode == route.EXIT_INTERNAL
    assert "tier:" not in proc.stdout
    assert proc.stdout == ""
    assert "Traceback" not in proc.stderr


def test_row_3_with_priors_also_reaches_scoring() -> None:
    """A repo with prior specs/plans but no project record also has no resume
    state, so it too reaches scoring and exits 3 in this chunk (row 3)."""
    proc = _cli(["resolve", "--repo", str(WITH_PRIORS), "--request", "add a widget"])
    assert proc.returncode == route.EXIT_INTERNAL


@pytest.mark.parametrize(
    ("tier", "expected_exit"),
    [
        (route.Tier.T0, route.EXIT_T0),
        (route.Tier.T1, route.EXIT_T1),
        (route.Tier.T2, route.EXIT_T2),
    ],
)
def test_rows_10_11_12_tier_exit_codes_via_real_dispatch(
    monkeypatch: pytest.MonkeyPatch, tier: "route.Tier", expected_exit: int
) -> None:
    """Rows 10/11/12, asserted in-process through the real dispatch path.

    Chunk 2 supplies the real signal evaluator; until then this monkeypatches
    ``evaluate_signals`` to return a stub ``Routing`` for each tier and
    asserts that ``main()`` maps it to the documented exit code via
    ``cmd_resolve``'s real tier -> exit-code table (``_TIER_EXIT_CODES``), not
    a test-only backdoor -- the sanctioned seam per TEST.md TC-3's note.
    Subprocess-level assertion of these rows against a fixture that genuinely
    scores to each tier lands in Chunk 2.
    """
    stub = route.Routing(tier=tier, axes=(), decided_by=(), ambiguity="none")
    monkeypatch.setattr(route, "evaluate_signals", lambda *a, **k: stub)
    code = route.main(["resolve", "--repo", str(GREENFIELD), "--request", "add a widget"])
    assert code == expected_exit


def test_exit_constants_values_and_disjointness() -> None:
    """The EXIT_* constants have the documented values and the tier codes
    (10/11/12) are disjoint from every other documented code (0/2/3)."""
    assert route.EXIT_RESUME == 0
    assert route.EXIT_T0 == 10
    assert route.EXIT_T1 == 11
    assert route.EXIT_T2 == 12
    assert route.EXIT_USAGE == 2
    assert route.EXIT_INTERNAL == 3
    tier_codes = {route.EXIT_T0, route.EXIT_T1, route.EXIT_T2}
    other_codes = {route.EXIT_RESUME, route.EXIT_USAGE, route.EXIT_INTERNAL}
    assert tier_codes.isdisjoint(other_codes)


def test_unknown_exit_code_rule_is_documented_in_module_docstring() -> None:
    """A test asserting the 'any other code -> treat as T2' rule is stated
    somewhere a caller would read it. README.md/SKILL.md wiring is a later
    chunk's job (chunks 7/8); this chunk's own authority is the module
    docstring, which every other doc quotes from."""
    doc = (route.__doc__ or "").lower()
    assert "any other" in doc
    assert "t2" in doc


# --------------------------------------------------------------------------- #
# risk-scan / lint-ledger stubs
# --------------------------------------------------------------------------- #


def test_risk_scan_stub_exits_twenty_not_zero() -> None:
    """risk-scan's stub must exit 20 (risk-found), never 0 (clean) -- the
    documented asymmetry with resolve's 0=resume. A partially-built scanner
    must never claim 'clean' for a question it cannot yet answer."""
    proc = _cli(["risk-scan", "--repo", str(GREENFIELD)])
    assert proc.returncode == route.EXIT_RISK_FOUND
    assert proc.returncode != route.EXIT_RESUME


def test_risk_scan_still_reports_usage_error_for_bad_repo() -> None:
    """Even as a stub, risk-scan validates --repo before claiming anything."""
    missing = FIXTURES / "does-not-exist"
    proc = _cli(["risk-scan", "--repo", str(missing)])
    assert proc.returncode == route.EXIT_USAGE


def test_lint_ledger_stub_exits_zero() -> None:
    """lint-ledger's stub always exits 0 in this chunk (real validation is Chunk 5)."""
    proc = _cli(["lint-ledger", "--repo", str(GREENFIELD)])
    assert proc.returncode == route.EXIT_LINT_OK


# --------------------------------------------------------------------------- #
# explain / doctor: always exit 0 (D-5); must not print a misleading trace
# --------------------------------------------------------------------------- #


def test_explain_always_exits_zero() -> None:
    proc = _cli(["explain", "--repo", str(GREENFIELD), "--request", "add a widget"])
    assert proc.returncode == route.EXIT_RESUME


def test_explain_states_signal_trace_unavailable() -> None:
    """explain must say plainly the signal trace isn't available yet, rather
    than printing an empty or misleading trace."""
    proc = _cli(["explain", "--repo", str(GREENFIELD), "--request", "add a widget"])
    assert "not yet available" in proc.stdout.lower()


def test_doctor_always_exits_zero() -> None:
    proc = _cli(["doctor", "--repo", str(GREENFIELD)])
    assert proc.returncode == route.EXIT_RESUME


def test_doctor_states_signal_trace_unavailable() -> None:
    proc = _cli(["doctor", "--repo", str(GREENFIELD)])
    assert "not yet available" in proc.stdout.lower()


def test_explain_reports_resume_state_for_open_project() -> None:
    proc = _cli(["explain", "--repo", str(OPEN_PROJECT), "--request", "anything"])
    assert proc.returncode == route.EXIT_RESUME
    assert "resume" in proc.stdout.lower()


# --------------------------------------------------------------------------- #
# TC-3 / FR-4: resume short-circuit precedes all scoring
# --------------------------------------------------------------------------- #


def test_open_project_short_circuits_before_signal_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-4: an open project must short-circuit and must never invoke the
    signal evaluator. Instrumented per TEST.md TC-3's sanctioned seam."""
    calls: list[object] = []

    def _tripwire(repo: Path, request: str) -> "route.Routing":
        calls.append((repo, request))
        raise AssertionError("signal evaluator must not be invoked")

    monkeypatch.setattr(route, "evaluate_signals", _tripwire)
    code = route.main(["resolve", "--repo", str(OPEN_PROJECT), "--request", "anything"])
    assert not calls, "signal evaluator was invoked despite an open project short-circuiting"
    assert code == route.EXIT_RESUME


def test_stale_project_short_circuits_before_signal_evaluation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-4: a stale (INVALID) SUPERHUMAN.md must also short-circuit -- it is
    not 'no file', but it is also not 'open', and must not fall through into
    scoring. Exit 0, `resume: stale(<path>)` on stdout, no signal evaluation.
    """
    calls: list[object] = []
    monkeypatch.setattr(route, "evaluate_signals", lambda *a, **k: calls.append(1))
    code = route.main(["resolve", "--repo", str(STALE_PROJECT), "--request", "anything"])
    out = capsys.readouterr().out
    assert not calls
    assert code == route.EXIT_RESUME
    assert "resume: stale(" in out
    assert "tier:" not in out


def test_closed_project_does_not_short_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-4: a closed project (G8 logged) must NOT short-circuit -- routing
    proceeds to scoring, which in this chunk means the signal evaluator IS
    invoked (and then raises its documented stub NotImplementedError)."""
    calls: list[object] = []

    def _spy(repo: Path, request: str) -> "route.Routing":
        calls.append((repo, request))
        raise NotImplementedError("chunk 2/3 stub")

    monkeypatch.setattr(route, "evaluate_signals", _spy)
    code = route.main(["resolve", "--repo", str(CLOSED_PROJECT), "--request", "anything"])
    assert calls, "closed project (G8 logged) incorrectly short-circuited resume"
    assert code == route.EXIT_INTERNAL


def test_open_project_via_subprocess_also_short_circuits() -> None:
    """Same as test_open_project_short_circuits_before_signal_evaluation, but
    end-to-end via subprocess (no monkeypatch available across a process
    boundary) -- the CLI-level half of the same property."""
    proc = _cli(["resolve", "--repo", str(OPEN_PROJECT), "--request", "anything"])
    assert proc.returncode == route.EXIT_RESUME
    assert "tier:" not in proc.stdout


def test_closed_project_via_subprocess_reaches_scoring_and_exits_internal() -> None:
    proc = _cli(["resolve", "--repo", str(CLOSED_PROJECT), "--request", "anything"])
    assert proc.returncode == route.EXIT_INTERNAL


# --------------------------------------------------------------------------- #
# check_resume unit tests: classification, exact-token matching, ambiguity
# --------------------------------------------------------------------------- #


def test_classify_open_project_fixture() -> None:
    path = OPEN_PROJECT / "docs" / "superhuman" / "sample-widget" / "SUPERHUMAN.md"
    assert route._classify_superhuman_file(path) == "open"


def test_classify_closed_project_fixture() -> None:
    path = CLOSED_PROJECT / "docs" / "superhuman" / "sample-widget" / "SUPERHUMAN.md"
    assert route._classify_superhuman_file(path) == "closed"


def test_classify_stale_project_fixture() -> None:
    path = STALE_PROJECT / "docs" / "superhuman" / "sample-widget" / "SUPERHUMAN.md"
    assert route._classify_superhuman_file(path) == "stale"


def test_classify_unreadable_file_is_stale(tmp_path: Path) -> None:
    """A path that doesn't exist at all is treated as INVALID/stale, per the
    error-handling table's 'SUPERHUMAN.md unreadable or malformed'."""
    missing = tmp_path / "does-not-exist" / "SUPERHUMAN.md"
    assert route._classify_superhuman_file(missing) == "stale"


@pytest.mark.parametrize(
    ("slug", "request_text", "expected"),
    [
        ("sample-widget", "resume the sample widget project", True),
        ("sample-widget", "resume the sample-widget project", True),
        ("sample-widget", "resume the SAMPLE_WIDGET project", True),
        ("sample-widget", "resume the   sample   widget   project", True),
        ("sample-widget", "resume the widget project", False),
        ("sample-widget", "samplewidget needs a look", False),
        ("key", "update the keymap module", False),
    ],
)
def test_slug_matches_is_exact_token_not_fuzzy(slug: str, request_text: str, expected: bool) -> None:
    """FR-4 step 3: slug matching is exact-token, never fuzzy/substring."""
    assert route._slug_matches(slug, request_text) is expected


def test_resume_outcome_render_forms() -> None:
    """The four documented resume-outcome renderings, pinned literally."""
    assert route.ResumeOutcome("resume", "sample-widget").render() == "resume(sample-widget)"
    assert (
        route.ResumeOutcome("ambiguous", "a,b").render() == "resume: ambiguous(slugs=a,b)"
    )
    assert route.ResumeOutcome("stale", "/x/SUPERHUMAN.md").render() == "resume: stale(/x/SUPERHUMAN.md)"
    assert (
        route.ResumeOutcome("unmatched", "a,b").render()
        == "resume: unmatched-open-projects(a,b)"
    )


def test_resume_outcome_render_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        route.ResumeOutcome("bogus", "x").render()


_OPEN_DECISIONS = (
    "## Decisions log\n"
    "<!-- Append-only. Format: [<ISO timestamp>] G<n>: <one-line summary>; "
    "user decision: <decision> -->\n"
    "[2026-01-01T10:00:00] G0: vision approved; user decision: approve\n"
    "[2026-01-01T10:05:00] G2: design approved; user decision: approve\n"
)
_CLOSED_DECISIONS = _OPEN_DECISIONS + (
    "[2026-01-02T10:00:00] G8: acceptance; user decision: approve\n"
)


def _write_project(root: Path, slug: str, *, closed: bool) -> Path:
    """Write a minimal synthetic SUPERHUMAN.md for ``slug`` under ``root``.

    Args:
        root: Fake repo root.
        slug: Project slug -- the ``docs/superhuman/<slug>/`` directory name.
        closed: Whether to append a G8 (closing) decision entry.

    Returns:
        The project's directory path.
    """
    proj_dir = root / "docs" / "superhuman" / slug
    proj_dir.mkdir(parents=True, exist_ok=True)
    body = _CLOSED_DECISIONS if closed else _OPEN_DECISIONS
    (proj_dir / "SUPERHUMAN.md").write_text(f"# Superhuman: {slug}\n\n{body}", encoding="utf-8")
    return proj_dir


def test_check_resume_returns_none_when_no_projects_exist(tmp_path: Path) -> None:
    assert route.check_resume(tmp_path, "anything") is None


def test_check_resume_returns_none_when_every_project_is_closed(tmp_path: Path) -> None:
    _write_project(tmp_path, "widget-one", closed=True)
    assert route.check_resume(tmp_path, "anything") is None


def test_check_resume_resumes_the_single_open_match(tmp_path: Path) -> None:
    _write_project(tmp_path, "widget-one", closed=True)
    _write_project(tmp_path, "widget-two", closed=False)
    outcome = route.check_resume(tmp_path, "keep working on widget-two")
    assert outcome is not None
    assert outcome.kind == "resume"
    assert outcome.detail == "widget-two"


def test_check_resume_is_ambiguous_when_multiple_open_projects_match(tmp_path: Path) -> None:
    _write_project(tmp_path, "widget-one", closed=False)
    _write_project(tmp_path, "widget-two", closed=False)
    outcome = route.check_resume(tmp_path, "work on widget-one and widget-two together")
    assert outcome is not None
    assert outcome.kind == "ambiguous"
    assert outcome.detail == "widget-one,widget-two"


def test_check_resume_is_unmatched_when_open_projects_exist_but_none_matches(tmp_path: Path) -> None:
    _write_project(tmp_path, "widget-one", closed=False)
    outcome = route.check_resume(tmp_path, "start something completely unrelated")
    assert outcome is not None
    assert outcome.kind == "unmatched"
    assert outcome.detail == "widget-one"


def test_check_resume_stale_file_takes_precedence_over_an_open_project(tmp_path: Path) -> None:
    """This repo's own ordering choice (documented in check_resume's
    docstring): when a stale file and an open project coexist, the stale
    outcome wins. Pinned here so any change to that ordering is a deliberate,
    reviewed edit rather than an accidental regression."""
    _write_project(tmp_path, "widget-one", closed=False)
    stale_dir = tmp_path / "docs" / "superhuman" / "widget-two"
    stale_dir.mkdir(parents=True)
    (stale_dir / "SUPERHUMAN.md").write_text("# Superhuman: widget-two\n\nno decisions log here\n")
    outcome = route.check_resume(tmp_path, "anything")
    assert outcome is not None
    assert outcome.kind == "stale"


def test_check_resume_slug_hint_disambiguates_directly(tmp_path: Path) -> None:
    """--slug lets a caller name the open project directly, bypassing text
    matching -- this implementation's reading of an otherwise-undocumented
    flag; flagged for confirmation in the dispatch report."""
    _write_project(tmp_path, "widget-one", closed=False)
    _write_project(tmp_path, "widget-two", closed=False)
    outcome = route.check_resume(tmp_path, "this text matches neither slug", slug_hint="widget-two")
    assert outcome is not None
    assert outcome.kind == "resume"
    assert outcome.detail == "widget-two"


def test_check_resume_unrecognised_slug_hint_falls_back_to_text_matching(tmp_path: Path) -> None:
    _write_project(tmp_path, "widget-one", closed=False)
    outcome = route.check_resume(tmp_path, "keep working on widget-one", slug_hint="does-not-exist")
    assert outcome is not None
    assert outcome.kind == "resume"
    assert outcome.detail == "widget-one"


# --------------------------------------------------------------------------- #
# Signal evaluation stub (Chunk 2/3 not yet implemented)
# --------------------------------------------------------------------------- #


def test_evaluate_signals_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        route.evaluate_signals(GREENFIELD, "add a widget")


# --------------------------------------------------------------------------- #
# Value objects: frozen + slots (per conventions/python.md)
# --------------------------------------------------------------------------- #


def test_signal_is_frozen_and_slotted() -> None:
    signal = route.Signal(name="x", evidence=(), floor=route.Tier.T0)
    with pytest.raises(AttributeError):
        signal.name = "y"  # type: ignore[misc]
    assert not hasattr(signal, "__dict__")


def test_axis_result_is_frozen_and_slotted() -> None:
    axis = route.AxisResult(axis="request", signals=(), floor=route.Tier.T1)
    with pytest.raises(AttributeError):
        axis.axis = "repo"  # type: ignore[misc]
    assert not hasattr(axis, "__dict__")


def test_routing_is_frozen_and_slotted() -> None:
    routing = route.Routing(tier=route.Tier.T2, axes=(), decided_by=(), ambiguity="none")
    with pytest.raises(AttributeError):
        routing.tier = route.Tier.T0  # type: ignore[misc]
    assert not hasattr(routing, "__dict__")


def test_resume_outcome_is_frozen_and_slotted() -> None:
    outcome = route.ResumeOutcome(kind="resume", detail="x")
    with pytest.raises(AttributeError):
        outcome.kind = "stale"  # type: ignore[misc]
    assert not hasattr(outcome, "__dict__")


# --------------------------------------------------------------------------- #
# FR-7 / NFR-2: stdlib-only import list
# --------------------------------------------------------------------------- #


def test_imports_are_stdlib_only() -> None:
    """route.py's import list must be stdlib-only, with no network module.

    Parsed with `ast` rather than by relying on the interpreter's own import
    machinery, per PLAN.md Chunk 1's acceptance criterion, so this test would
    still catch a violation that happened to be unreachable at import time
    (e.g. a network import guarded inside a rarely-taken branch).
    """
    tree = ast.parse(ROUTE_PY.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module.split(".")[0])

    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    non_stdlib = modules - stdlib
    assert not non_stdlib, f"non-stdlib imports found: {non_stdlib}"
    assert "socket" not in modules
    assert "urllib" not in modules
    assert "http" not in modules
    assert "requests" not in modules


# --------------------------------------------------------------------------- #
# FR-2 (partial): determinism
# --------------------------------------------------------------------------- #


def test_resolve_is_deterministic_over_100_runs() -> None:
    """The same (repo, request) pair produces identical stdout and exit code
    across 100 repeated invocations."""
    args = ["resolve", "--repo", str(OPEN_PROJECT), "--request", "touch the sample widget project"]
    first = _cli(args)
    for _ in range(99):
        proc = _cli(args)
        assert proc.returncode == first.returncode
        assert proc.stdout == first.stdout


# --------------------------------------------------------------------------- #
# --json (minimal, resume-case only in this chunk)
# --------------------------------------------------------------------------- #


def test_resolve_json_output_for_resume_case() -> None:
    proc = _cli(["resolve", "--repo", str(STALE_PROJECT), "--request", "anything", "--json"])
    assert proc.returncode == route.EXIT_RESUME
    payload = json.loads(proc.stdout)
    assert payload["resume"].startswith("resume: stale(")


# --------------------------------------------------------------------------- #
# Module docstring: resume-authority note + truthiness footgun (per the
# dispatch brief's required docstring content)
# --------------------------------------------------------------------------- #


def test_module_docstring_states_resume_authority_note() -> None:
    doc = (route.__doc__ or "").lower()
    assert "step 1" in doc
    assert "authority" in doc


def test_module_docstring_states_truthiness_footgun() -> None:
    doc = (route.__doc__ or "").lower()
    assert "truthiness" in doc
    assert "clean" in doc and "resume" in doc
