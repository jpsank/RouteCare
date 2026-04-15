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
from solver.benders.envelope import (
    CutStore,
    Envelope,
    Slot,
    solve_envelope,
    solve_envelope_partial,
)
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
#
# Each operator signature: (env, sub, k, rng, ctx) -> list[str]
# Returns up to k instance IDs to remove from the current incumbent.


def _destroy_random(
    env: Envelope,
    sub: SubproblemResult,
    k: int,
    rng: random.Random,
    ctx: SolverContext,
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
    ctx: SolverContext,
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
        return _destroy_random(env, sub, k, rng, ctx)
    if len(costs) <= k:
        return [iid for _, _, iid in costs]
    costs.sort(key=lambda p: (-p[0], p[1]))
    return [iid for _, _, iid in costs[:k]]


def _destroy_shaw(
    env: Envelope,
    sub: SubproblemResult,
    k: int,
    rng: random.Random,
    ctx: SolverContext,
) -> list[str]:
    """Shaw / related removal: pick a random seed, remove the k-1
    spatially closest other placed instances.

    Concentrates the destroy on a spatial cluster so the repair has a
    real chance of finding a better local packing.  Much more likely
    to produce improvements than random removal on scenarios with real
    geographic clustering — which is most real-world scenarios.
    """
    placed = list(env.assignments.keys())
    if len(placed) <= k:
        return placed

    seed_id = rng.choice(placed)
    seed_inst = ctx.instances_by_id.get(seed_id)
    if seed_inst is None:
        return _destroy_random(env, sub, k, rng, ctx)
    seed_pid = str(seed_inst.patient_id)
    travel = ctx.travel

    distances: list[tuple[int, float, str]] = []
    for iid in placed:
        if iid == seed_id:
            continue
        inst = ctx.instances_by_id.get(iid)
        if inst is None:
            continue
        pid = str(inst.patient_id)
        if pid == seed_pid:
            continue
        d = travel(seed_pid, pid)
        distances.append((d, rng.random(), iid))

    distances.sort(key=lambda t: (t[0], t[1]))
    selected = [seed_id] + [iid for _, _, iid in distances[: k - 1]]
    return selected


def _destroy_worst_vehicle(
    env: Envelope,
    sub: SubproblemResult,
    k: int,
    rng: random.Random,
    ctx: SolverContext,
) -> list[str]:
    """Worst-vehicle removal: identify the vehicle with the highest
    drive-per-stop ratio and remove all its visits.

    Forces a full re-assignment of a poorly-routed vehicle's stops.
    Good for scenarios where one vehicle is carrying outlier work
    that should have been split or clustered differently.  The repair
    pass can reassign those instances across all eligible vehicles.
    """
    candidates: list[tuple[float, float, int, int]] = []
    for (c_idx, d_idx), route in sub.routes.items():
        n = len(route.ordered)
        if n < 2:
            continue
        ratio = route.drive_cost / n
        candidates.append((ratio, rng.random(), c_idx, d_idx))

    if not candidates:
        return _destroy_random(env, sub, k, rng, ctx)

    # Highest ratio first, random tiebreak
    candidates.sort(key=lambda t: (-t[0], t[1]))

    removed: list[str] = []
    for _, _, c_idx, d_idx in candidates:
        route = sub.routes[(c_idx, d_idx)]
        removed.extend(route.ordered)
        if len(removed) >= k:
            break

    return removed[:max(k, len(removed[:k]))] if removed else []


DESTROY_OPERATORS = [
    ("random", _destroy_random),
    ("worst_cost", _destroy_worst_cost),
    ("shaw", _destroy_shaw),
    ("worst_vehicle", _destroy_worst_vehicle),
]


# ── Adaptive operator weighting (ALNS) ─────────────────────────────


@dataclass
class AdaptiveWeights:
    """Per-operator weights that adapt based on recent performance.

    Classic ALNS: operators that produce improvements get more weight,
    operators that waste iterations get less.  Starts uniform and
    learns per-scenario which operators are productive.

    Score update on each iteration:
      - improvement found  → +SCORE_IMPROVE
      - no improvement    → +SCORE_NEUTRAL
    After each `update_interval` iterations, weights are updated:
      weight_new = (1 - REACTION) * weight_old + REACTION * (score / uses)
    """

    SCORE_IMPROVE = 10.0
    SCORE_NEUTRAL = 0.0
    REACTION = 0.5
    MIN_WEIGHT = 0.05
    UPDATE_INTERVAL = 5

    weights: dict[str, float] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    uses: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        for name, _ in DESTROY_OPERATORS:
            self.weights.setdefault(name, 1.0)
            self.scores.setdefault(name, 0.0)
            self.uses.setdefault(name, 0)

    def select(self, rng: random.Random) -> tuple[str, callable]:
        """Weighted random selection over DESTROY_OPERATORS."""
        names_and_ops = DESTROY_OPERATORS
        weights = [max(self.MIN_WEIGHT, self.weights[name]) for name, _ in names_and_ops]
        total = sum(weights)
        r = rng.random() * total
        cumulative = 0.0
        for (name, op), w in zip(names_and_ops, weights):
            cumulative += w
            if r <= cumulative:
                return name, op
        return names_and_ops[-1]

    def record(self, op_name: str, improved: bool) -> None:
        self.uses[op_name] += 1
        self.scores[op_name] += self.SCORE_IMPROVE if improved else self.SCORE_NEUTRAL

    def maybe_update(self, iteration: int) -> None:
        """Update weights every UPDATE_INTERVAL iterations from accumulated scores."""
        if iteration == 0 or iteration % self.UPDATE_INTERVAL != 0:
            return
        for name, _ in DESTROY_OPERATORS:
            if self.uses[name] == 0:
                continue
            perf = self.scores[name] / self.uses[name]
            self.weights[name] = (
                (1 - self.REACTION) * self.weights[name] + self.REACTION * perf
            )
            # Reset window counters so weights track recent performance
            self.scores[name] = 0.0
            self.uses[name] = 0


# ── Repair helpers ──────────────────────────────────────────────────


def _changed_route_keys(
    old: SubproblemResult,
    new: SubproblemResult,
) -> list[tuple[int, int]]:
    """Return the (clinician_idx, day_idx) keys of routes whose ordered
    instance sets differ between old and new — these are the ones that
    need concrete-timing re-verification after a repair iteration."""
    changed: list[tuple[int, int]] = []
    all_keys = set(old.routes.keys()) | set(new.routes.keys())
    for key in all_keys:
        old_route = old.routes.get(key)
        new_route = new.routes.get(key)
        old_ids = frozenset(old_route.ordered) if old_route else frozenset()
        new_ids = frozenset(new_route.ordered) if new_route else frozenset()
        if old_ids != new_ids:
            changed.append(key)
    return changed


def _timing_drops_any(
    sub: SubproblemResult,
    vehicle_keys: list[tuple[int, int]],
    input: SolverInput,
    ctx: SolverContext,
) -> bool:
    """Run concrete timing on the specified vehicles and return True if
    any drops a visit.  Used as an LNS-iteration acceptance gate: a
    route that the subproblem forward pass accepts but cpsat_timing
    would drop is worthless — we should reject it and try a different
    destroy/repair combination."""
    from solver.cpsat_timing import cpsat_time_vehicle_route  # late import

    for key in vehicle_keys:
        route = sub.routes.get(key)
        if not route or not route.ordered:
            continue
        vehicle = ctx.vehicle_by_key.get(key)
        if vehicle is None:
            continue
        instances = [ctx.instances_by_id[iid] for iid in route.ordered]
        try:
            tr = cpsat_time_vehicle_route(vehicle, instances, input, ctx)
        except Exception:
            return True
        if getattr(tr, "dropped", None):
            return True
    return False


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
    weights = AdaptiveWeights()

    logger.info(
        "lns.polish start placed=%d initial_cost=%d budget=%.1fs",
        placed_count, initial_cost, time_budget,
    )

    for iteration in range(MAX_LNS_ITERATIONS):
        weights.maybe_update(iteration)
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

        op_name, op_fn = weights.select(rng)
        destroyed_list = op_fn(best_env, best_sub, k, rng, ctx)
        destroyed_ids = set(destroyed_list)
        if not destroyed_ids:
            weights.record(op_name, improved=False)
            no_improve_streak += 1
            continue

        # Repair — partial envelope: CP-SAT variables only for the
        # destroyed instances, everything else hard-fixed.  Much
        # smaller model than full envelope re-solve, so iterations
        # run faster on large problems.
        locked_assignments = {
            iid: slot
            for iid, slot in best_env.assignments.items()
            if iid not in destroyed_ids
        }
        iter_budget = max(0.3, min(2.0, remaining * 0.25))

        try:
            new_env = solve_envelope_partial(
                destroyed_ids=destroyed_ids,
                locked_assignments=locked_assignments,
                input=input,
                ctx=ctx,
                cut_store=cut_store,
                precomputed_slots=precomputed_slots,
                precomputed_approx_costs=precomputed_approx_costs,
                time_budget=iter_budget,
            )
        except Exception as e:
            logger.warning("lns.iter %d partial solve raised %s", iteration, e)
            result.rejected += 1
            weights.record(op_name, improved=False)
            no_improve_streak += 1
            continue

        result.iterations = iteration + 1

        if new_env is None or new_env.status not in ("OPTIMAL", "FEASIBLE"):
            result.rejected += 1
            weights.record(op_name, improved=False)
            no_improve_streak += 1
            continue

        new_sub = solve_subproblems(new_env, input, ctx)

        # Feasibility gates for acceptance
        if not new_sub.all_feasible():
            result.rejected += 1
            weights.record(op_name, improved=False)
            no_improve_streak += 1
            continue
        if len(new_env.assignments) < len(best_env.assignments):
            # Lost placements — reject even if drive went down
            result.rejected += 1
            weights.record(op_name, improved=False)
            no_improve_streak += 1
            continue

        new_cost = new_sub.total_drive()
        if new_cost >= best_cost:
            result.rejected += 1
            weights.record(op_name, improved=False)
            no_improve_streak += 1
            continue

        # Concrete-timing acceptance gate.  The subproblem's forward
        # pass reserves lunch capacity but can't exactly predict what
        # cpsat_timing will do in edge cases (block + lunch + tight
        # windows).  Check that the new plan actually times cleanly
        # on the routes that changed vs the current best — if any
        # visit is dropped, reject and let LNS try a different path.
        changed_keys = _changed_route_keys(best_sub, new_sub)
        if changed_keys and _timing_drops_any(new_sub, changed_keys, input, ctx):
            result.rejected += 1
            weights.record(op_name, improved=False)
            no_improve_streak += 1
            continue

        delta = best_cost - new_cost
        logger.info(
            "lns.iter %d op=%s k=%d improved %d → %d (−%d)",
            iteration, op_name, k, best_cost, new_cost, delta,
        )
        best_env = new_env
        best_sub = new_sub
        best_cost = new_cost
        result.improvements += 1
        weights.record(op_name, improved=True)
        no_improve_streak = 0

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
