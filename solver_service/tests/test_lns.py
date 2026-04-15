"""Tests for LNS polish.

LNS must satisfy three invariants regardless of scenario:
  1. Never worsens: final_cost ≤ initial_cost
  2. Never drops visits: placed count unchanged
  3. Never produces invalid plans: validate_plan passes
  4. Opt-out via SOLVER_LNS_BUDGET_FRAC=0 disables it entirely
  5. Warm-start solves skip LNS (continuity > optimization)

Beyond invariants, LNS should actually IMPROVE some scenarios — the
regression test here pins that at least one scale scenario gets cheaper.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from solver.benders import solve, validate_plan  # noqa: E402
from tests.helpers import (  # noqa: E402
    WORKING_DAYS_WEEK,
    assert_valid,
    make_input,
    mk_instance,
    mk_patient,
    uniform_matrix,
)


# ── Helper: build a larger scenario that triggers LNS ───────────────


def _build_lns_triggerable_scenario():
    """Scenario with ≥15 placements AND multi-stop routes — meets the
    short-circuit thresholds that would otherwise skip LNS."""
    patients = [mk_patient(pid, f"P{pid}", dur=30, req=2, min_gap=1)
                for pid in range(1, 9)]  # 16 instances
    instances = []
    for pid in range(1, 9):
        for v in range(2):
            instances.append(mk_instance(f"p{pid}_v{v}", pid, dur=30))
    # Uniform matrix ensures multi-stop routes (low inter-travel)
    matrix = uniform_matrix(patients, n_clinicians=1, inter_travel=15, home_travel=15)
    return make_input(patients, instances, matrix=matrix)


# ── Invariant: LNS never worsens cost ──────────────────────────────


def test_lns_never_worsens_cost():
    """For any input, `drive` with LNS enabled is ≤ drive with LNS
    disabled.  Hill-climbing is strictly improvement-only."""
    inp = _build_lns_triggerable_scenario()

    os.environ["SOLVER_LNS_BUDGET_FRAC"] = "0"
    import importlib, solver.benders.lns, solver.benders.loop
    importlib.reload(solver.benders.lns)
    importlib.reload(solver.benders.loop)
    from solver.benders.loop import solve as solve_no_lns
    no_lns = solve_no_lns(inp, time_budget=15)

    os.environ["SOLVER_LNS_BUDGET_FRAC"] = "0.30"
    importlib.reload(solver.benders.lns)
    importlib.reload(solver.benders.loop)
    from solver.benders.loop import solve as solve_with_lns
    with_lns = solve_with_lns(inp, time_budget=15)

    assert with_lns.metadata["drive"] <= no_lns.metadata["drive"], (
        f"LNS worsened drive: no_lns={no_lns.metadata['drive']}, "
        f"with_lns={with_lns.metadata['drive']}"
    )


def test_lns_never_drops_visits():
    """LNS must preserve the number of placed visits.  Even if the
    repair solve finds a cheaper plan with fewer placements, reject it."""
    inp = _build_lns_triggerable_scenario()

    os.environ["SOLVER_LNS_BUDGET_FRAC"] = "0.30"
    import importlib, solver.benders.lns, solver.benders.loop
    importlib.reload(solver.benders.lns)
    importlib.reload(solver.benders.loop)
    from solver.benders.loop import solve as solve_fn

    out = solve_fn(inp, time_budget=15)
    assert out.metadata["placed"] == len(inp.instances)
    assert_valid(out, inp)


def test_lns_output_validates():
    """validate_plan must pass on LNS output — LNS shouldn't produce
    plans that violate hard constraints."""
    inp = _build_lns_triggerable_scenario()

    os.environ["SOLVER_LNS_BUDGET_FRAC"] = "0.30"
    import importlib, solver.benders.lns, solver.benders.loop
    importlib.reload(solver.benders.lns)
    importlib.reload(solver.benders.loop)
    from solver.benders.loop import solve as solve_fn

    out = solve_fn(inp, time_budget=15)
    validate_plan(out, inp)


def test_lns_opt_out_via_env_var():
    """Setting SOLVER_LNS_BUDGET_FRAC=0 disables LNS.  The output
    should have no LNS metadata keys."""
    inp = _build_lns_triggerable_scenario()

    os.environ["SOLVER_LNS_BUDGET_FRAC"] = "0"
    import importlib, solver.benders.lns, solver.benders.loop
    importlib.reload(solver.benders.lns)
    importlib.reload(solver.benders.loop)
    from solver.benders.loop import solve as solve_fn

    out = solve_fn(inp, time_budget=5)
    assert "lns_iterations" not in out.metadata
    assert "lns_improvements" not in out.metadata


def test_lns_skipped_on_warm_start():
    """When upper_bound is provided, LNS should be skipped so the
    warm-start's continuity isn't undone by destroy/repair."""
    inp = _build_lns_triggerable_scenario()

    os.environ["SOLVER_LNS_BUDGET_FRAC"] = "0.30"
    import importlib, solver.benders.lns, solver.benders.loop
    importlib.reload(solver.benders.lns)
    importlib.reload(solver.benders.loop)
    from solver.benders.loop import solve as solve_fn

    first = solve_fn(inp, time_budget=10)
    second = solve_fn(inp, time_budget=10, upper_bound=first)

    assert second.metadata.get("warm_start_used") is True
    assert "lns_iterations" not in second.metadata


# ── Impact: at least one scale scenario actually improves ──────────


@pytest.mark.slow
def test_lns_improves_scale_50_c5():
    """Regression anchor: LNS must find a strict improvement on
    scale_50_c5, which in the current solver state has ~11% drive
    headroom that LNS reliably captures.  If this stops improving,
    LNS has been silently broken or the solver has already closed
    the gap some other way.  Either case worth investigating."""
    import importlib, solver.benders.lns, solver.benders.loop
    from benchmark import _build_scale, WEEK

    inp = _build_scale(50, 5, WEEK, seed=5)

    os.environ["SOLVER_LNS_BUDGET_FRAC"] = "0"
    importlib.reload(solver.benders.lns)
    importlib.reload(solver.benders.loop)
    from solver.benders.loop import solve as solve_no_lns
    no_lns = solve_no_lns(inp, time_budget=15)

    os.environ["SOLVER_LNS_BUDGET_FRAC"] = "0.30"
    importlib.reload(solver.benders.lns)
    importlib.reload(solver.benders.loop)
    from solver.benders.loop import solve as solve_with_lns
    with_lns = solve_with_lns(inp, time_budget=15)

    assert with_lns.metadata["drive"] < no_lns.metadata["drive"], (
        f"LNS failed to improve scale_50_c5: "
        f"no_lns={no_lns.metadata['drive']}, with_lns={with_lns.metadata['drive']}"
    )
    assert with_lns.metadata.get("lns_improvements", 0) > 0, (
        "LNS reported zero improvements"
    )
