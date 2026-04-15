"""Tests for LNS polish.

LNS must satisfy these invariants regardless of scenario:
  1. Never worsens: final_cost ≤ initial_cost (hill-climbing)
  2. Never drops visits: placed count unchanged
  3. Never produces invalid plans: validate_plan passes
  4. Opt-out via SOLVER_LNS_BUDGET_FRAC=0 disables it entirely
  5. Warm-start solves skip LNS (continuity > optimization)

Beyond invariants, LNS should actually IMPROVE some scenarios — the
regression test here pins that at least one scale scenario gets cheaper.

All tests use monkeypatch for environment variables so they're isolated
from each other — direct os.environ mutation would leak state into
subsequent tests and cause order-dependent failures.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from solver.benders import validate_plan  # noqa: E402
from tests.helpers import (  # noqa: E402
    assert_valid,
    make_input,
    mk_instance,
    mk_patient,
    uniform_matrix,
)


# ── Fixtures ────────────────────────────────────────────────────────


def _reload_solver():
    """Reload the LNS and loop modules so they pick up env changes."""
    import solver.benders.lns as _lns_mod
    import solver.benders.loop as _loop_mod
    importlib.reload(_lns_mod)
    importlib.reload(_loop_mod)
    return _loop_mod.solve


@pytest.fixture
def solve_with_lns_budget(monkeypatch):
    """Factory fixture returning a solver function configured with a
    specific LNS budget fraction.  Monkeypatches the env var (so pytest
    automatically restores it after the test) and reloads the solver
    module so the new setting takes effect.

    Usage:
        def test_something(solve_with_lns_budget):
            solve_fn = solve_with_lns_budget("0.30")
            out = solve_fn(inp, time_budget=10)
    """
    def _make(budget: str):
        monkeypatch.setenv("SOLVER_LNS_BUDGET_FRAC", budget)
        return _reload_solver()

    yield _make

    # Teardown: restore solver modules to the env state monkeypatch
    # will revert to (which is the conftest default / SOLVER_NUM_WORKERS=1).
    _reload_solver()


# ── Helper: build a scenario that actually triggers LNS ────────────


def _build_lns_triggerable_scenario():
    """Scenario with ≥15 placements AND multi-stop routes — meets the
    short-circuit thresholds that would otherwise skip LNS."""
    patients = [mk_patient(pid, f"P{pid}", dur=30, req=2, min_gap=1)
                for pid in range(1, 9)]  # 16 instances
    instances = []
    for pid in range(1, 9):
        for v in range(2):
            instances.append(mk_instance(f"p{pid}_v{v}", pid, dur=30))
    matrix = uniform_matrix(patients, n_clinicians=1, inter_travel=15, home_travel=15)
    return make_input(patients, instances, matrix=matrix)


# ── Invariant tests ────────────────────────────────────────────────


def test_lns_never_worsens_cost(solve_with_lns_budget):
    """With LNS enabled, drive ≤ drive with LNS disabled."""
    inp = _build_lns_triggerable_scenario()

    solve_no_lns = solve_with_lns_budget("0")
    no_lns = solve_no_lns(inp, time_budget=15)

    solve_with_lns_ = solve_with_lns_budget("0.30")
    with_lns = solve_with_lns_(inp, time_budget=15)

    assert with_lns.metadata["drive"] <= no_lns.metadata["drive"], (
        f"LNS worsened drive: no_lns={no_lns.metadata['drive']}, "
        f"with_lns={with_lns.metadata['drive']}"
    )


def test_lns_never_drops_visits(solve_with_lns_budget):
    """LNS must preserve the number of placed visits."""
    inp = _build_lns_triggerable_scenario()
    solve_fn = solve_with_lns_budget("0.30")
    out = solve_fn(inp, time_budget=15)
    assert out.metadata["placed"] == len(inp.instances)
    assert_valid(out, inp)


def test_lns_output_validates(solve_with_lns_budget):
    """validate_plan passes on LNS output — LNS shouldn't violate hard constraints."""
    inp = _build_lns_triggerable_scenario()
    solve_fn = solve_with_lns_budget("0.30")
    out = solve_fn(inp, time_budget=15)
    validate_plan(out, inp)


def test_lns_opt_out_via_env_var(solve_with_lns_budget):
    """SOLVER_LNS_BUDGET_FRAC=0 disables LNS — no lns_* metadata keys."""
    inp = _build_lns_triggerable_scenario()
    solve_fn = solve_with_lns_budget("0")
    out = solve_fn(inp, time_budget=5)
    assert "lns_iterations" not in out.metadata
    assert "lns_improvements" not in out.metadata


def test_lns_skipped_on_warm_start(solve_with_lns_budget):
    """Warm-start re-solves skip LNS — continuity > optimization."""
    inp = _build_lns_triggerable_scenario()
    solve_fn = solve_with_lns_budget("0.30")

    first = solve_fn(inp, time_budget=10)
    second = solve_fn(inp, time_budget=10, upper_bound=first)

    assert second.metadata.get("warm_start_used") is True
    assert "lns_iterations" not in second.metadata


# ── Impact regression test ─────────────────────────────────────────


@pytest.mark.slow
def test_lns_improves_scale_50_c5(solve_with_lns_budget):
    """Regression anchor: LNS must find a strict improvement on
    scale_50_c5, which in the current solver state has real drive
    headroom that LNS reliably captures.  If this stops improving,
    LNS has been silently broken or the solver has already closed
    the gap some other way."""
    from benchmark import _build_scale, WEEK
    inp = _build_scale(50, 5, WEEK, seed=5)

    solve_no_lns = solve_with_lns_budget("0")
    no_lns = solve_no_lns(inp, time_budget=15)

    solve_lns = solve_with_lns_budget("0.30")
    with_lns = solve_lns(inp, time_budget=15)

    assert with_lns.metadata["drive"] < no_lns.metadata["drive"], (
        f"LNS failed to improve scale_50_c5: "
        f"no_lns={no_lns.metadata['drive']}, with_lns={with_lns.metadata['drive']}"
    )
    assert with_lns.metadata.get("lns_improvements", 0) > 0, (
        "LNS reported zero improvements"
    )
