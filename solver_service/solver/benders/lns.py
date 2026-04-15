"""Large Neighborhood Search polish for the Benders solver.

Runs AFTER the Benders loop converges on a feasible incumbent.  Does
destroy/repair: pick k placed visits, remove them from the envelope,
re-solve the envelope with the rest locked via warm-start + continuity
penalty, re-route affected vehicles, accept if total drive decreased.

Hill-climbing acceptance (strict improvement only).  Never worsens the
incumbent — if the polish loop finds no improvement, the caller gets
back exactly what it passed in.

Two destroy operators for now:
  - random          : pick k placed instances uniformly at random
  - worst_cost      : pick k instances with the highest detour marginals

The loop is gated by:
  - `SOLVER_LNS_BUDGET_FRAC` : fraction of remaining time budget (default 0.30)
  - `SOLVER_LNS_MAX_ITERATIONS` : hard cap on iterations (default 30)
  - `SOLVER_LNS_NO_IMPROVE_LIMIT` : early-exit after N unproductive rounds (default 12)
  - `SOLVER_LNS_DESTROY_MIN` / `SOLVER_LNS_DESTROY_MAX` : destroy fraction bounds

Set `SOLVER_LNS_BUDGET_FRAC=0` to disable polish entirely.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field

from models import SolverInput
from solver.benders.envelope import CutStore, Envelope, Slot, solve_envelope
from solver.benders.subproblem import SubproblemResult, solve_subproblems
from solver.context import SolverContext

logger = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────────────

LNS_BUDGET_FRAC = float(os.environ.get("SOLVER_LNS_BUDGET_FRAC", "0.20"))
MAX_LNS_ITERATIONS = int(os.environ.get("SOLVER_LNS_MAX_ITERATIONS", "30"))
LNS_NO_IMPROVE_LIMIT = int(os.environ.get("SOLVER_LNS_NO_IMPROVE_LIMIT", "6"))
LNS_DESTROY_FRAC_MIN = float(os.environ.get("SOLVER_LNS_DESTROY_MIN", "0.15"))
LNS_DESTROY_FRAC_MAX = float(os.environ.get("SOLVER_LNS_DESTROY_MAX", "0.35"))


@dataclass
class LnsResult:
    """Summary of what LNS did."""

    envelope: Envelope
    subproblem: SubproblemResult
    iterations: int = 0
    improvements: int = 0
    rejected: int = 0
    initial_cost: int = 0
    final_cost: int = 0
    stopped_reason: str = ""


# ── Destroy operators ───────────────────────────────────────────────


def _destroy_random(
    env: Envelope,
    sub: SubproblemResult,
    k: int,
    rng: random.Random,
) -> list[str]:
    """Random removal: sample k placed instances uniformly."""
    placed = list(env.assignments.keys())
    if len(placed) <= k:
        return placed
    return rng.sample(placed, k)


def _destroy_worst_cost(
    env: Envelope,
    sub: SubproblemResult,
    k: int,
    rng: random.Random,
) -> list[str]:
    """Worst-cost removal: pick k instances with highest detour marginal.

    Uses the per-instance detour cost stored on each VehicleRoute.
    Ties broken randomly so repeated calls with the same incumbent
    don't keep picking the same subset.  Instances on vehicles where
    marginals weren't collected (single-stop routes) are excluded.
    """
    costs: list[tuple[int, float, str]] = []
    for route in sub.routes.values():
        for iid, detour in route.marginals.items():
            if iid in env.assignments:
                costs.append((detour, rng.random(), iid))
    if not costs:
        # Fall back to random if no marginals available
        return _destroy_random(env, sub, k, rng)
    if len(costs) <= k:
        return [iid for _, _, iid in costs]
    # Descending by detour cost, random tiebreak
    costs.sort(key=lambda p: (-p[0], p[1]))
    return [iid for _, _, iid in costs[:k]]


DESTROY_OPERATORS = [
    ("random", _destroy_random),
    ("worst_cost", _destroy_worst_cost),
]


# ── Repair helpers ──────────────────────────────────────────────────


def _remove_from_envelope(env: Envelope, destroyed_ids: set[str]) -> Envelope:
    """Return a new Envelope with the destroyed instances removed.

    The resulting partial envelope is passed as `warm_start` to the
    repair solve.  The envelope's continuity penalty will hold the
    non-destroyed instances in their prior (clinician, day) unless a
    meaningfully cheaper placement exists.
    """
    partial = Envelope(status="LNS_PARTIAL")
    for iid, slot in env.assignments.items():
        if iid not in destroyed_ids:
            partial.assignments[iid] = slot
    return partial


# ── Main polish loop ────────────────────────────────────────────────


def polish(
    initial_env: Envelope,
    initial_sub: SubproblemResult,
    input: SolverInput,
    ctx: SolverContext,
    cut_store: CutStore,
    precomputed_slots: dict,
    precomputed_approx_costs: dict,
    time_budget: float,
    seed: int = 42,
) -> LnsResult:
    """Hill-climbing LNS polish.  Takes a feasible incumbent, returns an
    incumbent with drive cost ≤ initial cost (never worse).

    The caller controls `time_budget` — pass 0 or negative to disable.
    """
    initial_cost = initial_sub.total_drive()
    result = LnsResult(
        envelope=initial_env,
        subproblem=initial_sub,
        initial_cost=initial_cost,
        final_cost=initial_cost,
    )

    if time_budget <= 0:
        result.stopped_reason = "budget_disabled"
        return result

    if not initial_sub.all_feasible():
        result.stopped_reason = "incumbent_not_feasible"
        return result

    placed_count = len(initial_env.assignments)
    if placed_count < 5:
        result.stopped_reason = "too_few_placements"
        return result

    # Skip if there are no multi-stop routes — LNS has nothing to
    # reorder, and the envelope already placed each instance on its
    # best-looking single-stop vehicle.  This catches the adversarial
    # "one patient per day" cases where LNS can't improve anything.
    multi_stop_routes = sum(
        1 for r in initial_sub.routes.values() if len(r.ordered) >= 2
    )
    if multi_stop_routes == 0:
        result.stopped_reason = "no_multi_stop_routes"
        return result

    rng = random.Random(seed)
    best_env = initial_env
    best_sub = initial_sub
    best_cost = initial_cost

    deadline = time.monotonic() + time_budget
    no_improve_streak = 0

    logger.info(
        "lns.polish start placed=%d initial_cost=%d budget=%.1fs",
        placed_count, initial_cost, time_budget,
    )

    for iteration in range(MAX_LNS_ITERATIONS):
        remaining = deadline - time.monotonic()
        if remaining <= 0.2:
            result.stopped_reason = "budget_exhausted"
            break
        if no_improve_streak >= LNS_NO_IMPROVE_LIMIT:
            result.stopped_reason = "no_improvement_streak"
            break

        # Destroy
        cur_placed = list(best_env.assignments.keys())
        k_min = max(2, int(LNS_DESTROY_FRAC_MIN * len(cur_placed)))
        k_max = max(k_min + 1, int(LNS_DESTROY_FRAC_MAX * len(cur_placed)))
        k = rng.randint(k_min, min(k_max, len(cur_placed)))

        op_name, op_fn = rng.choice(DESTROY_OPERATORS)
        destroyed_list = op_fn(best_env, best_sub, k, rng)
        destroyed_ids = set(destroyed_list)
        if not destroyed_ids:
            no_improve_streak += 1
            continue

        # Repair — partial envelope + continuity-weighted re-solve
        partial = _remove_from_envelope(best_env, destroyed_ids)
        iter_budget = max(0.3, min(2.0, remaining * 0.25))

        try:
            new_env = solve_envelope(
                input, ctx, cut_store,
                time_budget=iter_budget,
                warm_start=partial,
                precomputed_slots=precomputed_slots,
                precomputed_approx_costs=precomputed_approx_costs,
            )
        except Exception as e:
            logger.warning("lns.iter %d envelope solve raised %s", iteration, e)
            result.rejected += 1
            no_improve_streak += 1
            continue

        result.iterations = iteration + 1

        if new_env.status not in ("OPTIMAL", "FEASIBLE"):
            result.rejected += 1
            no_improve_streak += 1
            continue

        new_sub = solve_subproblems(new_env, input, ctx)

        # Feasibility gates for acceptance
        if not new_sub.all_feasible():
            result.rejected += 1
            no_improve_streak += 1
            continue
        if len(new_env.assignments) < len(best_env.assignments):
            # Lost placements — reject even if drive went down
            result.rejected += 1
            no_improve_streak += 1
            continue

        new_cost = new_sub.total_drive()
        if new_cost < best_cost:
            delta = best_cost - new_cost
            logger.info(
                "lns.iter %d op=%s k=%d improved %d → %d (−%d)",
                iteration, op_name, k, best_cost, new_cost, delta,
            )
            best_env = new_env
            best_sub = new_sub
            best_cost = new_cost
            result.improvements += 1
            no_improve_streak = 0
        else:
            result.rejected += 1
            no_improve_streak += 1

    if not result.stopped_reason:
        result.stopped_reason = "max_iterations"

    result.envelope = best_env
    result.subproblem = best_sub
    result.final_cost = best_cost

    logger.info(
        "lns.polish done iterations=%d improvements=%d %d → %d (%+d) reason=%s",
        result.iterations, result.improvements,
        initial_cost, best_cost, best_cost - initial_cost,
        result.stopped_reason,
    )
    return result
