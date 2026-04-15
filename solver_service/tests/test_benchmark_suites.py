"""Pytest integration of the benchmark suites.

Each scenario defined in benchmark.py is parametrized as an individual
pytest test.  The same Scenario objects and pass/fail logic that the CLI
benchmark uses are reused here, so the two stay in sync by construction.

Heavy scenarios (scale_100+, scale_200, warm_start, adversarial
tight_windows) are marked `slow` and skipped by default.  Run them with:

    pytest --run-slow
    pytest --run-slow -k warm_start    # filter by name
    pytest --run-slow -m slow          # only slow tests
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from benchmark import (  # noqa: E402
    Scenario,
    adversarial_scenarios,
    infeasibility_scenarios,
    quick_scenarios,
    realism_scenarios,
    run_scenario,
    scale_scenarios,
    warm_start_scenarios,
)


# ── Scenario → slow classification ──────────────────────────────────

# Scenarios listed here take >0.5s on a modern laptop.  Kept out of the
# default run so `pytest tests/` stays fast for TDD.
_SLOW_NAMES = {
    "scale_100_c5",
    "scale_100_c10",
    "scale_200_c10",
    "ws_add_patient",
    "ws_cancel_patient",
    "ws_shrink_day",
    "adv_tight_windows",
}


def _all_scenarios() -> list[Scenario]:
    """All scenarios from all suites.  Kept sorted for stable test IDs."""
    out: list[Scenario] = []
    for suite_fn in (
        quick_scenarios,
        scale_scenarios,
        infeasibility_scenarios,
        warm_start_scenarios,
        realism_scenarios,
        adversarial_scenarios,
    ):
        out.extend(suite_fn())
    return out


def _mark_for(scenario: Scenario) -> list:
    marks = []
    if scenario.name in _SLOW_NAMES:
        marks.append(pytest.mark.slow)
    return marks


def _scenario_params():
    params = []
    for sc in _all_scenarios():
        param_id = f"{sc.suite}/{sc.name}"
        params.append(pytest.param(sc, id=param_id, marks=_mark_for(sc)))
    return params


# ── The single parametrized test ────────────────────────────────────


@pytest.mark.parametrize("scenario", _scenario_params())
def test_benchmark_scenario(scenario: Scenario):
    """Run one benchmark scenario and assert its pass criterion.

    Uses samples=1 for speed — with SOLVER_NUM_WORKERS=1 (forced by
    conftest) the solver is deterministic so a single run is sufficient
    to detect regressions.  strict_placement=True means any worst-case
    placement under the threshold fails the test.
    """
    result = run_scenario(scenario, samples=1, strict_placement=True)
    if not result.passed:
        pytest.fail(
            f"scenario {scenario.suite}/{scenario.name} failed: {result.reason}\n"
            f"  placed={result.placed_min}/{result.instances} "
            f"drive={result.drive_min} rounds={result.rounds_max} "
            f"cuts={result.cuts_max} validated={result.validated}"
        )


# ── Sanity: make sure every suite has at least one scenario ─────────


@pytest.mark.parametrize("suite_fn,name", [
    (quick_scenarios, "quick"),
    (scale_scenarios, "scale"),
    (infeasibility_scenarios, "infeasibility"),
    (warm_start_scenarios, "warm_start"),
    (realism_scenarios, "realism"),
    (adversarial_scenarios, "adversarial"),
])
def test_suite_has_scenarios(suite_fn, name):
    scenarios = suite_fn()
    assert len(scenarios) > 0, f"suite {name} has no scenarios"
    for sc in scenarios:
        assert sc.suite == name, f"scenario {sc.name} has suite={sc.suite}, expected {name}"
