"""Large Neighborhood Search with CP-SAT constraint repair.

Iteratively destroys and repairs portions of the schedule.
Destroy operators remove visit subsets; CP-SAT re-assigns them respecting
hard constraints (spacing, one-per-day, availability); then affected routes
are re-timed.
"""

from __future__ import annotations

import logging
import math
import random
import time
from collections import defaultdict
from enum import Enum, auto
from typing import TYPE_CHECKING

from ortools.sat.python import cp_model

from models import SolverInput, VisitInstanceData
from solver.context import (
    DESTROY_FRAC_MAX,
    DESTROY_FRAC_MIN,
    MAX_LNS_ITERATIONS,
    NUM_WORKERS,
    PENALTY_SPACING_MAX,
    PRIORITY_WEIGHT,
    SolverContext,
)
from solver.model import Solution
from solver.cpsat_timing import cpsat_time_vehicle_route
from solver.timing import TimedRoute, nearest_neighbor, time_vehicle_route

if TYPE_CHECKING:
    from solver.fitness import AdaptiveLambda

logger = logging.getLogger(__name__)


class DestroyOp(Enum):
    RANDOM = auto()
    WORST_COST = auto()
    PATIENT_CLUSTER = auto()
    RELATED = auto()


ALL_DESTROY_OPS = [
    DestroyOp.RANDOM,
    DestroyOp.WORST_COST,
    DestroyOp.PATIENT_CLUSTER,
    DestroyOp.RELATED,
]


# ── ALNS Operator Selection ─────────────────────────────────────────


class ALNSWeights:
    """Adaptive operator weights — tracks success rate per destroy operator."""

    REWARD_BEST = 3.0  # found new global best
    REWARD_IMPROVE = 2.0  # accepted improvement
    REWARD_ACCEPT = 0.5  # accepted (same quality)
    DECAY = 0.8  # exponential decay on old scores

    def __init__(self):
        self.scores: dict[DestroyOp, float] = {op: 1.0 for op in ALL_DESTROY_OPS}
        self.uses: dict[DestroyOp, int] = {op: 0 for op in ALL_DESTROY_OPS}

    def select(self, rng: random.Random) -> DestroyOp:
        """Roulette-wheel selection weighted by score / max(uses, 1)."""
        weights = [self.scores[op] / max(self.uses[op], 1) for op in ALL_DESTROY_OPS]
        total = sum(weights)
        if total <= 0:
            return rng.choice(ALL_DESTROY_OPS)
        r = rng.random() * total
        cumulative = 0.0
        for op, w in zip(ALL_DESTROY_OPS, weights):
            cumulative += w
            if r <= cumulative:
                return op
        return ALL_DESTROY_OPS[-1]

    def reward(self, op: DestroyOp, reward: float):
        self.scores[op] += reward
        self.uses[op] += 1

    def decay(self):
        """Apply exponential decay to all scores — keeps recent performance relevant."""
        for op in ALL_DESTROY_OPS:
            self.scores[op] *= self.DECAY
            self.uses[op] = max(1, int(self.uses[op] * self.DECAY))

    def to_dict(self) -> dict:
        return {
            op.name: {"score": round(self.scores[op], 2), "uses": self.uses[op]}
            for op in ALL_DESTROY_OPS
        }


# ── Destroy Operators ────────────────────────────────────────────────


def _destroy_random(
    solution: Solution,
    ctx: SolverContext,
    k: int,
) -> list[str]:
    """Remove k random visits from the solution."""
    all_placed = []
    for route in solution.routes.values():
        all_placed.extend(route)
    if not all_placed:
        return []
    k = min(k, len(all_placed))
    return random.sample(all_placed, k)


def _destroy_worst_cost(
    solution: Solution,
    ctx: SolverContext,
    timed_routes: dict[int, TimedRoute],
    k: int,
) -> list[str]:
    """Remove visits with highest marginal drive cost (detour cost)."""
    travel = ctx.travel
    costs: list[tuple[int, str]] = []

    for v_idx, route in solution.routes.items():
        if len(route) < 1:
            continue
        home_key = f"home_{ctx.vehicles[v_idx].clinician_idx}"
        pids = [str(ctx.instances_by_id[iid].patient_id) for iid in route]
        for i, iid in enumerate(route):
            prev = home_key if i == 0 else pids[i - 1]
            nxt = home_key if i == len(route) - 1 else pids[i + 1]
            with_visit = travel(prev, pids[i]) + travel(pids[i], nxt)
            without_visit = travel(prev, nxt)
            detour = max(0, with_visit - without_visit)
            costs.append((detour, iid))

    costs.sort(reverse=True)
    return [iid for _, iid in costs[:k]]


def _destroy_patient_cluster(
    solution: Solution,
    ctx: SolverContext,
    k: int,
) -> list[str]:
    """Remove all visits for a random subset of patients until k visits destroyed."""
    # Build patient → placed instance_ids
    patient_instances: dict[int, list[str]] = defaultdict(list)
    for route in solution.routes.values():
        for iid in route:
            inst = ctx.instances_by_id.get(iid)
            if inst:
                patient_instances[inst.patient_id].append(iid)

    pids = list(patient_instances.keys())
    random.shuffle(pids)
    destroyed: list[str] = []
    for pid in pids:
        if len(destroyed) >= k:
            break
        destroyed.extend(patient_instances[pid])
    return destroyed[:k] if len(destroyed) > k else destroyed


def _destroy_related(
    solution: Solution,
    ctx: SolverContext,
    k: int,
) -> list[str]:
    """Shaw removal: pick a random seed visit, remove k nearest visits."""
    travel = ctx.travel
    all_placed = []
    for route in solution.routes.values():
        all_placed.extend(route)
    if not all_placed:
        return []

    seed_id = random.choice(all_placed)
    seed_inst = ctx.instances_by_id[seed_id]
    seed_pid = str(seed_inst.patient_id)

    # Sort by travel distance to seed
    others = [
        (travel(seed_pid, str(ctx.instances_by_id[iid].patient_id)), iid)
        for iid in all_placed
        if iid != seed_id
    ]
    others.sort()
    result = [seed_id] + [iid for _, iid in others[: k - 1]]
    return result


def destroy(
    op: DestroyOp,
    solution: Solution,
    ctx: SolverContext,
    timed_routes: dict[int, TimedRoute],
    k: int,
) -> list[str]:
    """Apply a destroy operator, returning list of removed instance_ids."""
    if op == DestroyOp.RANDOM:
        return _destroy_random(solution, ctx, k)
    elif op == DestroyOp.WORST_COST:
        return _destroy_worst_cost(solution, ctx, timed_routes, k)
    elif op == DestroyOp.PATIENT_CLUSTER:
        return _destroy_patient_cluster(solution, ctx, k)
    elif op == DestroyOp.RELATED:
        return _destroy_related(solution, ctx, k)
    return _destroy_random(solution, ctx, k)


# ── CP-SAT Repair ───────────────────────────────────────────────────


def cpsat_repair(
    solution: Solution,
    destroyed_ids: list[str],
    ctx: SolverContext,
    input: SolverInput,
    budget: float = 2.0,
) -> Solution:
    """Re-assign destroyed visits to vehicles using CP-SAT, respecting hard constraints.

    Produces a new Solution with destroyed visits re-inserted (or left unassigned
    if infeasible).
    """
    destroyed_set = set(destroyed_ids)
    destroyed_instances = [
        ctx.instances_by_id[iid] for iid in destroyed_ids if iid in ctx.instances_by_id
    ]
    n_destroyed = len(destroyed_instances)
    vehicles = ctx.vehicles
    n_vehicles = len(vehicles)

    if n_destroyed == 0:
        return solution

    # Build the "kept" state: routes minus destroyed visits
    kept_routes: dict[int, list[str]] = {}
    for v_idx, route in solution.routes.items():
        kept = [iid for iid in route if iid not in destroyed_set]
        if kept:
            kept_routes[v_idx] = kept

    # Track which patients are already on which days (from kept visits)
    patient_on_day: dict[int, set[int]] = defaultdict(set)
    for v_idx, route in kept_routes.items():
        for iid in route:
            inst = ctx.instances_by_id.get(iid)
            if inst:
                patient_on_day[inst.patient_id].add(v_idx)

    # Also include locked visits
    for pid, day_set in ctx.locked_patient_days.items():
        patient_on_day[pid].update(day_set)

    # Count kept visits per vehicle for capacity
    kept_count = {v_idx: len(route) for v_idx, route in kept_routes.items()}

    # Compute which days each patient already has visits (kept + locked) — for spacing
    patient_placed_days: dict[int, list[int]] = defaultdict(list)
    for pid, days in patient_on_day.items():
        patient_placed_days[pid] = sorted(days)

    # ── CP-SAT model ─────────────────────────────────────────────────

    model = cp_model.CpModel()

    # Variables: assign[i, v] = 1 iff destroyed instance i goes to vehicle v
    assign: dict[tuple[int, int], object] = {}
    scheduled: list = []

    for i in range(n_destroyed):
        sv = model.new_bool_var(f"sched_{i}")
        scheduled.append(sv)
        for v_idx in range(n_vehicles):
            b = model.new_bool_var(f"a_{i}_{v_idx}")
            assign[(i, v_idx)] = b

        # Each instance on exactly one vehicle or unscheduled
        model.add(
            sum(assign[(i, v)] for v in range(n_vehicles)) + (1 - scheduled[i]) == 1
        )

    # ── Hard constraints ─────────────────────────────────────────────

    travel = ctx.travel

    # Pre-compute drive cost of kept routes for drive limit filtering
    any_drive_limit = any(v.max_drive < 999_999 for v in vehicles)
    kept_drive: dict[int, int] = {}
    if any_drive_limit:
        for v_idx in range(n_vehicles):
            route = kept_routes.get(v_idx, [])
            if not route:
                kept_drive[v_idx] = 0
                continue
            hk = f"home_{vehicles[v_idx].clinician_idx}"
            route_pids = [str(ctx.instances_by_id[iid].patient_id) for iid in route]
            cost = travel(hk, route_pids[0])
            for k in range(1, len(route_pids)):
                cost += travel(route_pids[k - 1], route_pids[k])
            cost += travel(route_pids[-1], hk)
            kept_drive[v_idx] = cost

    for i, inst in enumerate(destroyed_instances):
        pid = inst.patient_id
        patient = ctx.patients_by_id.get(pid)

        for v_idx in range(n_vehicles):
            vehicle = vehicles[v_idx]
            d_idx = vehicle.day_index

            # Clinician eligibility
            if (
                inst.eligible_clinician_indices
                and vehicle.clinician_idx not in inst.eligible_clinician_indices
            ):
                model.add(assign[(i, v_idx)] == 0)
                continue

            # Capacity check
            if kept_count.get(v_idx, 0) >= vehicle.capacity:
                model.add(assign[(i, v_idx)] == 0)
                continue

            # Drive limit: estimate if adding this visit would exceed max drive.
            # Uses kept route drive + cheapest insertion position.
            v_max_drive = vehicle.max_drive
            if v_max_drive < 999_999:
                home_key = f"home_{vehicle.clinician_idx}"
                pid_str = str(pid)
                route = kept_routes.get(v_idx, [])
                if not route:
                    add_cost = travel(home_key, pid_str) + travel(pid_str, home_key)
                else:
                    route_pids = [
                        str(ctx.instances_by_id[iid].patient_id) for iid in route
                    ]
                    # Find cheapest insertion position
                    best_insert = (
                        travel(home_key, pid_str)
                        + travel(pid_str, route_pids[0])
                        - travel(home_key, route_pids[0])
                    )
                    for k in range(1, len(route_pids)):
                        c = (
                            travel(route_pids[k - 1], pid_str)
                            + travel(pid_str, route_pids[k])
                            - travel(route_pids[k - 1], route_pids[k])
                        )
                        best_insert = min(best_insert, c)
                    c = (
                        travel(route_pids[-1], pid_str)
                        + travel(pid_str, home_key)
                        - travel(route_pids[-1], home_key)
                    )
                    best_insert = min(best_insert, c)
                    add_cost = max(0, best_insert)
                if kept_drive.get(v_idx, 0) + add_cost > v_max_drive:
                    model.add(assign[(i, v_idx)] == 0)
                    continue

            # One patient per day: if patient already on this day (kept or locked)
            if d_idx in patient_on_day.get(pid, set()):
                model.add(assign[(i, v_idx)] == 0)
                continue

            # Availability: must have windows on this weekday
            wday_str = str(ctx.day_wdays[d_idx])
            if inst.availability_windows:
                windows = inst.availability_windows.get(wday_str, [])
                if not windows:
                    model.add(assign[(i, v_idx)] == 0)
                    continue

            # Min spacing: check against kept + locked visits for same patient
            if patient and patient.min_days_between_visits >= 1:
                too_close = False
                for placed_d in patient_placed_days.get(pid, []):
                    gap = abs(d_idx - placed_d)
                    if gap > 0 and gap <= patient.min_days_between_visits:
                        too_close = True
                        break
                if too_close:
                    model.add(assign[(i, v_idx)] == 0)
                    continue

    # Min spacing between destroyed instances of the same patient
    patient_destroyed: dict[int, list[int]] = defaultdict(list)
    for i, inst in enumerate(destroyed_instances):
        patient_destroyed[inst.patient_id].append(i)

    for pid, indices in patient_destroyed.items():
        patient = ctx.patients_by_id.get(pid)
        if not patient or len(indices) < 2:
            continue
        min_gap = patient.min_days_between_visits
        max_gap = patient.max_days_between_visits

        # One per day between destroyed instances
        for v_idx in range(n_vehicles):
            model.add(sum(assign[(i, v_idx)] for i in indices) <= 1)

        # Spacing between destroyed instance pairs
        if min_gap >= 1:
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    ia, ib = indices[a], indices[b]
                    for v1 in range(n_vehicles):
                        for v2 in range(n_vehicles):
                            gap = abs(vehicles[v1].day_index - vehicles[v2].day_index)
                            if 0 < gap <= min_gap:
                                both = model.new_bool_var(f"sp_{ia}_{ib}_{v1}_{v2}")
                                model.add_min_equality(
                                    both, [assign[(ia, v1)], assign[(ib, v2)]]
                                )
                                model.add(both == 0)

    # ── Soft constraints / objective ─────────────────────────────────

    penalties: list[tuple] = []

    # Priority: prefer scheduling high-priority patients
    for i, inst in enumerate(destroyed_instances):
        patient = ctx.patients_by_id.get(inst.patient_id)
        priority_boost = (patient.priority + 1) if patient else 1
        penalties.append((scheduled[i].negated(), PRIORITY_WEIGHT * priority_boost))

    # Max spacing penalty (soft)
    for pid, indices in patient_destroyed.items():
        patient = ctx.patients_by_id.get(pid)
        if not patient or len(indices) < 2:
            continue
        max_gap = patient.max_days_between_visits
        if max_gap >= n_vehicles:
            continue
        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                ia, ib = indices[a], indices[b]
                # Approximate: penalize if assigned vehicles are far apart
                day_a = model.new_int_var(0, n_vehicles, f"da_{ia}")
                day_b = model.new_int_var(0, n_vehicles, f"db_{ib}")
                # Link day vars to assignments
                for v_idx in range(n_vehicles):
                    d = vehicles[v_idx].day_index
                    model.add(day_a == d).only_enforce_if(assign[(ia, v_idx)])
                    model.add(day_b == d).only_enforce_if(assign[(ib, v_idx)])
                model.add(day_a == 0).only_enforce_if(scheduled[ia].negated())
                model.add(day_b == 0).only_enforce_if(scheduled[ib].negated())

                both = model.new_bool_var(f"bs_{ia}_{ib}")
                model.add_min_equality(both, [scheduled[ia], scheduled[ib]])

                gap = model.new_int_var(0, n_vehicles, f"g_{ia}_{ib}")
                diff = model.new_int_var(-n_vehicles, n_vehicles, f"d_{ia}_{ib}")
                model.add(diff == day_a - day_b)
                model.add_abs_equality(gap, diff)

                if max_gap < n_vehicles:
                    viol = model.new_int_var(0, n_vehicles, f"xv_{ia}_{ib}")
                    model.add(viol >= gap - max_gap).only_enforce_if(both)
                    model.add(viol == 0).only_enforce_if(both.negated())
                    penalties.append((viol, PENALTY_SPACING_MAX))

    # Insertion cost (drive proximity)
    for i, inst in enumerate(destroyed_instances):
        pid_str = str(inst.patient_id)
        for v_idx in range(n_vehicles):
            hk = f"home_{vehicles[v_idx].clinician_idx}"
            route = kept_routes.get(v_idx, [])
            if not route:
                cost = travel(hk, pid_str) + travel(pid_str, hk)
            else:
                route_pids = [str(ctx.instances_by_id[iid].patient_id) for iid in route]
                # Best insertion cost
                best = (
                    travel(hk, pid_str)
                    + travel(pid_str, route_pids[0])
                    - travel(hk, route_pids[0])
                )
                for k in range(1, len(route_pids)):
                    c = (
                        travel(route_pids[k - 1], pid_str)
                        + travel(pid_str, route_pids[k])
                        - travel(route_pids[k - 1], route_pids[k])
                    )
                    best = min(best, c)
                c = (
                    travel(route_pids[-1], pid_str)
                    + travel(pid_str, hk)
                    - travel(route_pids[-1], hk)
                )
                best = min(best, c)
                cost = max(0, best)

            if cost > 0:
                penalties.append((assign[(i, v_idx)], cost))

    # ── Objective ────────────────────────────────────────────────────

    penalty_terms = []
    for var, weight in penalties:
        w = int(weight)
        if w > 0:
            penalty_terms.append(var * w)

    penalty_cap = sum(int(w) for _, w in penalties if int(w) > 0)
    penalty_cap = max(penalty_cap, 1)
    total_penalty = model.new_int_var(0, min(penalty_cap, 500_000_000), "tp")
    if penalty_terms:
        model.add(total_penalty == sum(penalty_terms))
    else:
        model.add(total_penalty == 0)

    visit_value = min(penalty_cap, 500_000_000) + 1
    model.minimize(
        total_penalty - visit_value * sum(scheduled[i] for i in range(n_destroyed))
    )

    # ── Solve ────────────────────────────────────────────────────────

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = budget
    solver.parameters.num_workers = min(NUM_WORKERS, 4)  # small model, fewer workers

    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Repair failed — return original solution
        return solution

    # ── Extract repaired solution ────────────────────────────────────

    new_routes: dict[int, list[str]] = {
        v_idx: list(route) for v_idx, route in kept_routes.items()
    }
    new_unassigned = list(solution.unassigned)

    for i, inst in enumerate(destroyed_instances):
        if solver.value(scheduled[i]):
            for v_idx in range(n_vehicles):
                if solver.value(assign[(i, v_idx)]):
                    if v_idx not in new_routes:
                        new_routes[v_idx] = []
                    new_routes[v_idx].append(inst.id)
                    break
        else:
            if inst.id not in new_unassigned:
                new_unassigned.append(inst.id)

    # Remove destroyed IDs that were in unassigned and got reassigned
    placed_set = set()
    for route in new_routes.values():
        placed_set.update(route)
    new_unassigned = [iid for iid in new_unassigned if iid not in placed_set]

    return Solution(routes=new_routes, unassigned=new_unassigned)


# ── Route Optimization after Repair ─────────────────────────────────


def _optimize_vehicle_order(
    vehicle: object,
    instance_ids: list[str],
    ctx: SolverContext,
    input: SolverInput,
) -> list[VisitInstanceData]:
    """Re-order visits on a vehicle using nearest-neighbor heuristic.

    For small routes (≤5 visits), tries all permutations and picks best.
    """
    import itertools

    instances = [ctx.instances_by_id[iid] for iid in instance_ids]
    travel = ctx.travel
    home_key = f"home_{vehicle.clinician_idx}"
    n = len(instances)

    if n <= 1:
        return instances

    def _route_cost(order: list[VisitInstanceData]) -> int:
        pids = [str(inst.patient_id) for inst in order]
        cost = travel(home_key, pids[0])
        for a, b in zip(pids, pids[1:]):
            cost += travel(a, b)
        cost += travel(pids[-1], home_key)
        return cost

    if n <= 5:
        best_order = instances
        best_cost = _route_cost(instances)
        for perm in itertools.permutations(range(n)):
            order = [instances[i] for i in perm]
            cost = _route_cost(order)
            if cost < best_cost:
                best_cost = cost
                best_order = order
        return best_order

    return nearest_neighbor(instances, travel, home_key=home_key)


# ── Main LNS Loop ───────────────────────────────────────────────────


def lns_improve(
    solution: Solution,
    timed_routes: dict[int, TimedRoute],
    ctx: SolverContext,
    input: SolverInput,
    time_budget: float,
    best_placed: int,
    best_cost: float,
    lambdas: AdaptiveLambda | None = None,
) -> tuple[Solution, dict[int, TimedRoute], int, float, list[dict]]:
    """Run LNS iterations with adaptive-λ 3-tier fitness.

    Returns (solution, timed_routes, placed, cost, iteration_log).
    """
    from solver.fitness import (
        AdaptiveLambda,
        compute_total_fitness,
        is_tier2_feasible,
        aggregate_tier2_violations,
    )

    if lambdas is None:
        lambdas = AdaptiveLambda()

    if not solution.routes:
        return solution, timed_routes, best_placed, best_cost, []

    total_placed = sum(len(r) for r in solution.routes.values())
    if total_placed == 0:
        return solution, timed_routes, best_placed, best_cost, []

    deadline = time.monotonic() + time_budget
    iteration_log: list[dict] = []
    rng = random.Random(42)
    random.seed(42)
    alns = ALNSWeights()

    # SA temperature calibrated to typical neighbor cost deltas, not
    # absolute cost (which includes 1M-per-unplaced and varies wildly).
    # Estimate: a typical LNS iteration changes drive cost by ~20-60 min
    # plus soft penalties.  Starting temp ~50 means ~60% acceptance of
    # delta=30, cooling to 0 linearly.
    sa_initial_temp = 50.0
    cur_placed = best_placed
    cur_cost = best_cost
    best_solution = solution
    best_timed = timed_routes

    # Track best feasible solution separately (feasible elite)
    best_feasible_placed = -1
    best_feasible_cost = float("inf")
    best_feasible_solution = None
    best_feasible_timed = None

    # Check if current solution is feasible
    cur_viols = aggregate_tier2_violations(timed_routes, ctx)
    if is_tier2_feasible(cur_viols):
        best_feasible_placed = best_placed
        best_feasible_cost = best_cost
        best_feasible_solution = solution
        best_feasible_timed = timed_routes

    for iteration in range(MAX_LNS_ITERATIONS):
        remaining = deadline - time.monotonic()
        if remaining < 1.0:
            break

        # Destroy size: 20-40% of placed visits
        total_placed = sum(len(r) for r in solution.routes.values())
        k = max(
            1,
            rng.randint(
                int(total_placed * DESTROY_FRAC_MIN),
                max(
                    int(total_placed * DESTROY_FRAC_MIN) + 1,
                    int(total_placed * DESTROY_FRAC_MAX),
                ),
            ),
        )

        op = alns.select(rng)

        # 1. Destroy — also include unassigned visits so repair can place them
        destroyed = destroy(op, solution, ctx, timed_routes, k)
        for uid in solution.unassigned:
            if uid not in destroyed:
                destroyed.append(uid)
        if not destroyed:
            continue

        # 2. Repair with CP-SAT
        repair_budget = min(remaining * 0.4, 3.0)
        repaired = cpsat_repair(solution, destroyed, ctx, input, budget=repair_budget)

        # 3. Re-order and re-time affected vehicles
        affected_vehicles = set()
        destroyed_set = set(destroyed)
        for v_idx, route in solution.routes.items():
            if any(iid in destroyed_set for iid in route):
                affected_vehicles.add(v_idx)
        for v_idx, route in repaired.routes.items():
            if any(iid in destroyed_set for iid in route):
                affected_vehicles.add(v_idx)

        new_timed = dict(timed_routes)
        for v_idx in affected_vehicles:
            vehicle = ctx.vehicles[v_idx]
            route_ids = repaired.routes.get(v_idx, [])
            if not route_ids:
                new_timed[v_idx] = TimedRoute(vehicle_idx=v_idx)
                continue
            ordered = _optimize_vehicle_order(vehicle, route_ids, ctx, input)
            try:
                timed = cpsat_time_vehicle_route(vehicle, ordered, input, ctx)
            except Exception:
                timed = time_vehicle_route(vehicle, ordered, input, ctx)
            new_timed[v_idx] = timed

            repaired.routes[v_idx] = [v.instance_id for v in timed.visits]

        # 4. Evaluate with 3-tier fitness
        all_visits = []
        total_drive = 0
        for v_idx in range(len(ctx.vehicles)):
            tr = new_timed.get(v_idx)
            if tr:
                all_visits.extend(tr.visits)
                total_drive += tr.drive_cost

        new_placed, new_cost, breakdown = compute_total_fitness(
            all_visits,
            total_drive,
            input,
            ctx,
            timed_routes=new_timed,
            lambdas=lambdas,
        )

        # Check Tier 2 feasibility for adaptive lambda
        new_viols = aggregate_tier2_violations(new_timed, ctx)
        new_feasible = is_tier2_feasible(new_viols)
        lambdas.record(new_feasible)

        log_entry = {
            "iteration": iteration,
            "operator": op.name,
            "destroyed": len(destroyed),
            "placed": new_placed,
            "cost": round(new_cost, 2),
            "tier2_feasible": new_feasible,
            **breakdown,
        }

        # 5. Accept/reject with SA acceptance criterion
        # New global best?
        is_new_best = new_placed > best_placed or (
            new_placed >= best_placed and new_cost < best_cost
        )
        # Improvement over current working solution?
        is_improvement = new_placed > cur_placed or (
            new_placed >= cur_placed and new_cost < cur_cost
        )

        # SA acceptance for worsening moves (same placement count only —
        # never accept fewer placed visits).
        sa_accept = False
        if not is_improvement and new_placed >= cur_placed and new_cost < float("inf"):
            progress = iteration / max(MAX_LNS_ITERATIONS, 1)
            temperature = sa_initial_temp * (1.0 - progress)
            if temperature > 0:
                delta = new_cost - cur_cost
                if delta > 0 and rng.random() < math.exp(-delta / temperature):
                    sa_accept = True

        accepted = is_improvement or sa_accept

        if accepted:
            solution = repaired
            timed_routes = new_timed
            cur_placed = new_placed
            cur_cost = new_cost
            log_entry["accepted"] = True

            if is_new_best:
                best_placed = new_placed
                best_cost = new_cost
                best_solution = repaired
                best_timed = new_timed
                alns.reward(op, ALNSWeights.REWARD_BEST)
            elif is_improvement:
                alns.reward(op, ALNSWeights.REWARD_IMPROVE)
            else:
                alns.reward(op, ALNSWeights.REWARD_ACCEPT)

            # Track feasible elite separately
            if new_feasible:
                if new_placed > best_feasible_placed or (
                    new_placed >= best_feasible_placed and new_cost < best_feasible_cost
                ):
                    best_feasible_placed = new_placed
                    best_feasible_cost = new_cost
                    best_feasible_solution = repaired
                    best_feasible_timed = new_timed
        else:
            log_entry["accepted"] = False

        iteration_log.append(log_entry)

        # 6. Adapt lambda and ALNS weights every 5 iterations
        if (iteration + 1) % 5 == 0:
            lambdas.adapt()
            alns.decay()

        # 7. Periodic revert: if SA has drifted far from global best,
        #    snap back every 10 iterations to prevent deep drift.
        if (iteration + 1) % 10 == 0:
            if cur_placed < best_placed or (
                cur_placed == best_placed and cur_cost > best_cost * 1.2
            ):
                solution = best_solution
                timed_routes = best_timed
                cur_placed = best_placed
                cur_cost = best_cost

    # Final lambda adaptation with whatever data we collected
    lambdas.adapt()

    # Return best feasible solution if available; otherwise best overall
    if best_feasible_solution is not None and best_feasible_timed is not None:
        return (
            best_feasible_solution,
            best_feasible_timed,
            best_feasible_placed,
            best_feasible_cost,
            iteration_log,
        )

    return solution, timed_routes, best_placed, best_cost, iteration_log
