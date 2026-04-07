"""Decomposed CP-SAT + routing solver for healthcare scheduling.

Architecture:
  CP-SAT handles day ASSIGNMENT (combinatorial):
    - Which visit instances go on which days
    - One patient per day, max visits per day
    - Min/max spacing, availability windows, priority
    - Schedule density, target day offsets
    - Uses approximate travel cost per day

  Day router handles ROUTING + TIMING (spatial):
    - Given fixed patients for a day, find optimal visit order
    - Concrete start/end times with 15-min granularity
    - Lunch break insertion within window
    - Max continuous work / mandatory breaks
    - Calendar blocks and locked visit avoidance
    - Charting buffer and transit buffer

  Pipeline iterates:
    1. CP-SAT assigns days using approximate cost
    2. Route each day → get actual costs
    3. Feed actual costs back, re-assign
    4. Converge in 2-3 iterations

  Env:
    CPSAT_NUM_WORKERS — OR-Tools worker threads (default: min(CPU, 8))
    CPSAT_HGS_MAX_SECONDS — max PyVRP time per day-route (default: 0.5)
    CPSAT_ROUTE_DAY_WORKERS — thread pool size for routing multiple days in parallel
      (default: min(CPSAT_NUM_WORKERS, num_working_days); set 1 to disable)
    CPSAT_SKIP_PERM_ENUM_FOR_N5 — if true, n=5 days use HGS+NN only (skip 5! permutations)
    CPSAT_PAIRWISE_PRUNE_MULT — iteration-0 pairwise pruning: drop pair if travel exceeds
      max(1, median_home_leg)×this multiplier (default 3; median 0 would otherwise prune everything)
    CPSAT_MAX_ITERATIONS — max assignment↔routing passes (default 3, minimum 1)
    CPSAT_ASSIGN_ITER0_FRAC — fraction of half-budget for CP-SAT on iteration 0 (default 0.42;
      later iterations split the remainder evenly). Clamped to [0.2, 0.85].
    CPSAT_USE_FEASIBILITY_JUMP — if 1 (default), set OR-Tools use_feasibility_jump when supported
    CPSAT_FULL_ASSIGN_HINTS — if true, hint every assign[(i,d)] on warm-start (slower); default false
      (day_var + scheduled only, sufficient to link channelling).
    CPSAT_ITER0_NN_MARGIN_FRAC — iter-0 marginal travel proxy += this × nearest other-patient leg
      (default 0.2; 0 = pure half round-trip home leg per instance, legacy behavior).
    solve(..., upper_bound=SolverOutput) — when prior plan is valid for this input, seeds CP-SAT hints on
      iter 0 and baseline incumbent (fitness/plan) so re-solves start from the prior plan.
"""

from __future__ import annotations

import itertools
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ortools.sat.python import cp_model
from pyvrp import Model as VRPModel
from pyvrp.stop import MaxRuntime

from models import SolverInput, SolverOutput, PlannedVisit, VisitInstanceData
from solvers.base import MAX_VISITS_PER_DAY, build_lunch_placements, compute_return_home, minutes_to_datetime

logger = logging.getLogger(__name__)

# Assignment penalties
PENALTY_UNSCHEDULED = 10_000
PENALTY_SPACING_MIN = 200
PENALTY_SPACING_MAX = 100
PENALTY_AVAILABILITY = 150
PENALTY_DRIVE_OVER = 120
PENALTY_DAY_OFFSET = 10
PENALTY_SOFT_OVERRIDE = 80

# Routing constants
SLOT_STEP = 15
TRANSIT_BUFFER = 5

# Pipeline (tests may monkeypatch MAX_ITERATIONS)
MAX_ITERATIONS = max(1, int(os.environ.get("CPSAT_MAX_ITERATIONS", "3")))
NUM_WORKERS = int(os.environ.get("CPSAT_NUM_WORKERS", min(os.cpu_count() or 4, 8)))
# Cap for PyVRP per-day routing (seconds); floor still scales with n inside _hgs_route_order.
HGS_MAX_SECONDS = float(os.environ.get("CPSAT_HGS_MAX_SECONDS", "0.5"))


def _cp_sat_assign_seconds(iteration: int, time_budget: int, max_iter: int) -> int:
    """Allocate wall time to one CP-SAT solve; ~half of time_budget across all passes, skew iter 0."""
    pool = max(max_iter, time_budget // 2)
    if max_iter <= 1:
        return max(1, pool)
    iter0_frac = float(os.environ.get("CPSAT_ASSIGN_ITER0_FRAC", "0.42"))
    iter0_frac = min(0.85, max(0.2, iter0_frac))
    if iteration == 0:
        return max(1, int(pool * iter0_frac))
    rem = pool - max(1, int(pool * iter0_frac))
    return max(1, rem // (max_iter - 1))


def upper_bound_valid_for_warm_start(
    input: SolverInput,
    ctx: dict,
    upper_bound: SolverOutput | None,
) -> bool:
    """True if prior output is safe to use for CP-SAT hints and incumbent seeding."""
    if upper_bound is None or not upper_bound.planned_visits:
        return False
    visits = upper_bound.planned_visits
    n = len(input.instances)
    if len(visits) != n:
        return False
    ids = [v.instance_id for v in visits]
    if len(set(ids)) != n:
        return False
    expected = {inst.id for inst in input.instances}
    if set(ids) != expected:
        return False
    date_to_idx = ctx["date_to_idx"]
    seen_patient_day: set[tuple[int, str]] = set()
    for v in visits:
        if v.date not in date_to_idx:
            return False
        key = (v.patient_id, v.date)
        if key in seen_patient_day:
            return False
        seen_patient_day.add(key)
    return True


def _normalized_assignment_signature(
    assignments: dict[int, list[int]],
    num_days: int,
) -> tuple[tuple[int, ...], ...]:
    """Per-day sorted instance indices including empty days — stable compare for convergence."""
    return tuple(tuple(sorted(assignments.get(d, []))) for d in range(num_days))


def _iter0_instance_marginals(input: SolverInput, ctx: dict, home_leg: dict[int, int]) -> list[int]:
    """Iter-0 CP-SAT travel marginal per instance: home_leg + NN margin toward next patient."""
    n = len(input.instances)
    travel = ctx["travel"]
    pids = [str(inst.patient_id) for inst in input.instances]
    nn = [0] * n
    for i in range(n):
        best = None
        for j in range(n):
            if i == j or pids[i] == pids[j]:
                continue
            t = travel(pids[i], pids[j])
            if best is None or t < best:
                best = t
        nn[i] = int(best) if best is not None else 0
    frac = float(os.environ.get("CPSAT_ITER0_NN_MARGIN_FRAC", "0.2"))
    frac = min(0.95, max(0.0, frac))
    return [home_leg[i] + int(frac * nn[i]) for i in range(n)]


def _prior_assignments_from_output(
    input: SolverInput,
    ctx: dict,
    upper_bound: SolverOutput | None,
) -> dict[int, list[int]] | None:
    """Map prior planned visits to {day_index: [instance_index,...]} for CP-SAT hints."""
    if not upper_bound_valid_for_warm_start(input, ctx, upper_bound):
        return None
    inst_idx_map = ctx["inst_idx_map"]
    date_to_idx = ctx["date_to_idx"]
    by_day: dict[int, list[int]] = defaultdict(list)
    for v in upper_bound.planned_visits:
        ii = inst_idx_map.get(v.instance_id)
        di = date_to_idx.get(v.date)
        if ii is None or di is None:
            continue
        by_day[di].append(ii)
    for di in by_day:
        by_day[di].sort()
    placed = set()
    for xs in by_day.values():
        placed.update(xs)
    if placed != set(range(len(input.instances))):
        return None
    return dict(by_day)


def _day_drive_costs_from_planned(
    planned: list[PlannedVisit],
    input: SolverInput,
    ctx: dict,
) -> dict[int, int]:
    """Round-trip drive minutes per working-day index from visit order (same metric as routing)."""
    by_date: dict[str, list[PlannedVisit]] = defaultdict(list)
    for v in planned:
        by_date[v.date].append(v)
    travel = ctx["travel"]
    costs: dict[int, int] = {}
    for d_idx, date in enumerate(input.working_days):
        visits = sorted(by_date.get(date, []), key=lambda x: x.starts_at)
        if not visits:
            costs[d_idx] = 0
            continue
        c = travel("home", str(visits[0].patient_id))
        for a, b in zip(visits, visits[1:]):
            c += travel(str(a.patient_id), str(b.patient_id))
        c += travel(str(visits[-1].patient_id), "home")
        costs[d_idx] = c
    return costs


def _warm_best_from_output(
    input: SolverInput,
    ctx: dict,
    upper_bound: SolverOutput | None,
) -> tuple[list[PlannedVisit], dict, dict[int, int], dict[str, str]] | None:
    """Incumbent (planned, lunch, day_drive_costs, route_winners) if upper_bound is valid."""
    if not upper_bound_valid_for_warm_start(input, ctx, upper_bound):
        return None
    assert upper_bound is not None
    planned = list(upper_bound.planned_visits)
    lunch = dict(upper_bound.lunch_placements)
    day_costs = _day_drive_costs_from_planned(planned, input, ctx)
    rw = dict(upper_bound.metadata.get("route_winners") or {})
    return planned, lunch, day_costs, rw


def solve(
    input: SolverInput,
    time_budget: int = 30,
    upper_bound: SolverOutput | None = None,
) -> SolverOutput:
    """Decomposed solve: CP-SAT assignment → day routing → feedback loop."""
    if not input.instances:
        meta = {
            "optimizer_type": "cpsat",
            "warm_start_used": False,
            "upper_bound_provided": upper_bound is not None,
            "upper_bound_accepted": False,
        }
        return _empty_output(input, metadata=meta)

    ctx = _build_context(input)
    patients_by_id = ctx["patients_by_id"]
    num_instances = len(input.instances)

    warm_ok = upper_bound is not None and upper_bound_valid_for_warm_start(input, ctx, upper_bound)

    warm_assignments = _prior_assignments_from_output(input, ctx, upper_bound)
    warm_best = _warm_best_from_output(input, ctx, upper_bound)

    # Static home-leg proxy for "if this instance were the only stop on a day"
    home_leg: dict[int, int] = {}
    for i, inst in enumerate(input.instances):
        t_out = ctx["travel"]("home", str(inst.patient_id))
        t_back = ctx["travel"](str(inst.patient_id), "home")
        home_leg[i] = (t_out + t_back) // 2

    # Per (day, instance) marginal cost for CP-SAT travel objective.
    # Iteration 0: all equal to home_leg[i]; later: routed share for placed days, else home_leg.
    num_days = len(input.working_days)
    iter0_row = _iter0_instance_marginals(input, ctx, home_leg)
    day_marginal_costs: dict[int, dict[int, int]] = {
        d: {i: iter0_row[i] for i in range(num_instances)} for d in range(num_days)
    }

    best_output: tuple | None = None
    best_fitness = float("inf")
    if warm_best is not None and upper_bound is not None:
        best_fitness = float(upper_bound.fitness)
        best_output = warm_best
    completed_iterations = 0
    cp_sat_status = "NOT_RUN"
    iteration_log = []
    prev_assignments: dict[int, list[int]] | None = None
    last_full_iteration_log: dict | None = None

    for iteration in range(MAX_ITERATIONS):
        assign_budget = _cp_sat_assign_seconds(iteration, time_budget, MAX_ITERATIONS)

        # 1. CP-SAT: assign instances to days
        # Iter 0: per-day marginals (== home leg) + pairwise; iter 1+: marginals from last route per (d,i)
        hint_prev = prev_assignments
        if hint_prev is None and iteration == 0:
            hint_prev = warm_assignments

        assignments, status = _assign_days(
            input, ctx, day_marginal_costs, home_leg, assign_budget, iteration,
            prev_assignments=hint_prev,
        )
        cp_sat_status = status
        if assignments is None:
            break

        # Early termination: if assignment didn't change, costs won't change either
        sig = _normalized_assignment_signature(assignments, num_days)
        if prev_assignments is not None and sig == _normalized_assignment_signature(prev_assignments, num_days):
            if last_full_iteration_log is not None:
                iteration_log.append({
                    **last_full_iteration_log,
                    "iteration": iteration,
                    "converged": True,
                })
            else:
                iteration_log.append({"iteration": iteration, "converged": True})
            completed_iterations = iteration + 1
            break

        # 2. Route each day (parallel across days when workers > 1)
        all_planned = []
        all_lunch = {}
        actual_day_costs: dict[int, int] = {}
        route_winners: dict[str, str] = {}

        route_workers = int(os.environ.get("CPSAT_ROUTE_DAY_WORKERS", str(min(NUM_WORKERS, num_days))))
        route_workers = max(1, route_workers)

        lv_by_date = ctx["locked_visits_by_date"]

        def _route_or_trivial(d: int) -> dict:
            date = input.working_days[d]
            day_instances = [input.instances[i] for i in assignments.get(d, [])]
            if not day_instances and not lv_by_date.get(date):
                return _trivial_empty_route_day(input, date)
            return _route_day(
                date=date, instances=day_instances, input=input, ctx=ctx,
            )

        if route_workers == 1:
            results_by_d: dict[int, dict] = {}
            for d in range(num_days):
                results_by_d[d] = _route_or_trivial(d)
        else:
            results_by_d = {}
            with ThreadPoolExecutor(max_workers=route_workers) as pool:
                future_to_d = {pool.submit(_route_or_trivial, d): d for d in range(num_days)}
                for fut in as_completed(future_to_d):
                    results_by_d[future_to_d[fut]] = fut.result()

        for d in range(num_days):
            date = input.working_days[d]
            route_result = results_by_d[d]
            all_planned.extend(route_result["visits"])
            all_lunch[date] = route_result["lunch"]
            actual_day_costs[d] = route_result["drive_cost"]
            if route_result.get("winner"):
                route_winners[date] = route_result["winner"]

        # 3. Update per-(day, instance) marginals from actual routing for next CP-SAT pass
        for d in range(num_days):
            assigned_on_d = set(assignments.get(d, []))
            base_cost = actual_day_costs.get(d, 0)
            count = max(len(assigned_on_d), 1)
            share = base_cost // count
            for i in range(num_instances):
                if i in assigned_on_d:
                    day_marginal_costs[d][i] = share
                else:
                    day_marginal_costs[d][i] = home_leg[i]

        # 4. Compute fitness: drive + weighted unscheduled + soft violations + CP-SAT-aligned proxies
        total_drive = sum(actual_day_costs.values())
        placed_ids = {v.instance_id for v in all_planned}
        unsched_penalty = 0
        for inst in input.instances:
            if inst.id not in placed_ids:
                patient = patients_by_id.get(inst.patient_id)
                priority_boost = (patient.priority + 1) if patient else 1
                unsched_penalty += PENALTY_UNSCHEDULED * priority_boost
        soft_penalty = sum(PENALTY_SOFT_OVERRIDE for v in all_planned if v.soft_constraint_override)

        # Spacing violations from routed result
        spacing_penalty = _compute_spacing_penalty(all_planned, patients_by_id, input)
        density_penalty = _compute_density_fitness_penalty(assignments, ctx, input)
        offset_penalty = _compute_day_offset_fitness_penalty(
            all_planned, patients_by_id, input, ctx
        )

        fitness = (
            total_drive
            + unsched_penalty
            + soft_penalty
            + spacing_penalty
            + density_penalty
            + offset_penalty
        )
        completed_iterations = iteration + 1

        last_full_iteration_log = {
            "drive": total_drive,
            "unsched_penalty": unsched_penalty,
            "soft_penalty": soft_penalty,
            "spacing_penalty": spacing_penalty,
            "density_penalty": density_penalty,
            "offset_penalty": offset_penalty,
            "fitness": fitness,
            "placed": len(all_planned),
        }
        iteration_log.append({"iteration": iteration, **last_full_iteration_log})

        if fitness < best_fitness:
            best_fitness = fitness
            best_output = (all_planned, all_lunch, actual_day_costs, route_winners)

        prev_assignments = assignments

    if best_output is None:
        return _empty_output(
            input,
            metadata={
                "optimizer_type": "cpsat",
                "warm_start_used": warm_ok,
                "upper_bound_provided": upper_bound is not None,
                "upper_bound_accepted": warm_ok,
                "cp_sat_status": cp_sat_status,
            },
        )

    planned_visits, lunch_placements, day_drive_costs, route_winners = best_output

    # Sort by date and time
    planned_visits.sort(key=lambda v: (v.date, v.starts_at))

    # Return home
    routes_by_day: dict[str, list[int]] = defaultdict(list)
    for v in planned_visits:
        routes_by_day[v.date].append(v.patient_id)
    return_home = compute_return_home(dict(routes_by_day), input.travel_matrix)

    # Fill lunch for days without visits or where lunch wasn't placed
    for date in input.working_days:
        if not lunch_placements.get(date):
            half_w = input.clinician.lunch_window_minutes // 2
            earliest = max(
                input.clinician.lunch_start_minute - half_w,
                input.clinician.workday_start_minute,
            )
            lunch_placements[date] = {
                "start_minute": earliest,
                "end_minute": earliest + input.clinician.lunch_duration_minutes,
            }

    # Unschedulable
    placed_ids = {v.instance_id for v in planned_visits}
    unschedulable = []
    for inst in input.instances:
        if inst.id not in placed_ids:
            patient = patients_by_id.get(inst.patient_id)
            unschedulable.append({
                "patient_name": patient.name if patient else "Unknown",
                "patient_id": inst.patient_id,
            })

    # Drive violations
    drive_violations = []
    if input.clinician.max_drive_minutes_per_day:
        for d, cost in day_drive_costs.items():
            if cost > input.clinician.max_drive_minutes_per_day:
                drive_violations.append({
                    "date": input.working_days[d],
                    "drive_minutes": cost,
                    "max_drive": input.clinician.max_drive_minutes_per_day,
                })

    schedule_status = "FEASIBLE" if not unschedulable else "PARTIAL"

    return SolverOutput(
        planned_visits=planned_visits,
        lunch_placements=lunch_placements,
        fitness=best_fitness,
        metadata={
            "optimizer_type": "cpsat",
            "proven_optimal": False,
            "cp_sat_status": cp_sat_status,
            "status": schedule_status,
            "iterations": completed_iterations,
            "iteration_log": iteration_log,
            "route_winners": route_winners,
            "unschedulable": unschedulable,
            "drive_violations": drive_violations,
            "soft_constraint_overrides": sum(1 for v in planned_visits if v.soft_constraint_override),
            "return_home_by_day": return_home,
            "warm_start_used": warm_ok,
            "upper_bound_provided": upper_bound is not None,
            "upper_bound_accepted": warm_ok,
        },
    )


def _compute_spacing_penalty(
    planned: list[PlannedVisit],
    patients_by_id: dict,
    input: SolverInput,
) -> int:
    """Compute spacing violation penalty from routed visits."""
    patient_dates: dict[int, list[str]] = defaultdict(list)
    for v in planned:
        patient_dates[v.patient_id].append(v.date)

    penalty = 0
    for pid, dates in patient_dates.items():
        # Unique calendar days only — duplicates would create bogus 0-day gaps
        unique_days = sorted(set(dates))
        if len(unique_days) < 2:
            continue
        patient = patients_by_id.get(pid)
        if not patient:
            continue
        sorted_dates = unique_days
        for i in range(len(sorted_dates) - 1):
            gap = (datetime.fromisoformat(sorted_dates[i + 1]) -
                   datetime.fromisoformat(sorted_dates[i])).days
            if gap < patient.min_days_between_visits:
                penalty += PENALTY_SPACING_MIN * (patient.min_days_between_visits - gap)
            if gap > patient.max_days_between_visits:
                penalty += PENALTY_SPACING_MAX * (gap - patient.max_days_between_visits)
    return penalty


def _compute_density_fitness_penalty(
    assignments: dict[int, list[int]],
    ctx: dict,
    input: SolverInput,
) -> int:
    """Mirror CP-SAT schedule_density penalties using realized assignment counts."""
    num_days = len(input.working_days)
    if num_days <= 1:
        return 0
    density = input.clinician.schedule_density
    locked_by_day = ctx["locked_count_by_day"]
    counts = [
        len(assignments.get(d, [])) + locked_by_day.get(d, 0) for d in range(num_days)
    ]
    if density < 0.5:
        w = int(2 * (1.0 - density))
        return w * (max(counts) - min(counts)) if w > 0 else 0
    w = int(2 * density)
    if w <= 0:
        return 0
    active = sum(1 for c in counts if c > 0)
    return w * active


def _compute_day_offset_fitness_penalty(
    planned: list[PlannedVisit],
    patients_by_id: dict,
    input: SolverInput,
    ctx: dict,
) -> int:
    """Approximate CP-SAT target day-offset penalty from placed visits."""
    if not planned:
        return 0
    date_to_idx = ctx["date_to_idx"]
    inst_day: dict[str, int] = {}
    for v in planned:
        di = date_to_idx.get(v.date)
        if di is not None:
            inst_day[v.instance_id] = di

    instances_by_patient = ctx["instances_by_patient"]
    inst_idx_map = ctx["inst_idx_map"]
    num_days = len(input.working_days)
    clinician = input.clinician
    penalty = 0

    for pid, insts in instances_by_patient.items():
        patient = patients_by_id.get(pid)
        if not patient or len(insts) <= 1:
            continue
        n_visits = len(insts)
        min_gap_p = patient.min_days_between_visits
        max_step = (num_days - 1) / max(n_visits - 1, 1)
        min_step = max(float(min_gap_p), 1.0)
        step = max_step - (clinician.schedule_density * (max_step - min_step))
        step = max(step, min_step)
        targets = [min(round(k * step), num_days - 1) for k in range(n_visits)]

        inst_indices = sorted(inst_idx_map[inst.id] for inst in insts)
        for k, gi in enumerate(inst_indices):
            inst = input.instances[gi]
            if inst.id not in inst_day:
                continue
            actual_d = inst_day[inst.id]
            target = targets[k] if k < len(targets) else targets[-1]
            penalty += PENALTY_DAY_OFFSET * abs(actual_d - target)

    return penalty


# ═══════════════════════════════════════════════════════════════════════
# CP-SAT Assignment Model (no routing variables)
# ═══════════════════════════════════════════════════════════════════════

def _assign_days(
    input: SolverInput,
    ctx: dict,
    day_marginal_costs: dict[int, dict[int, int]],
    home_leg: dict[int, int],
    time_budget: int,
    iteration: int = 0,
    prev_assignments: dict[int, list[int]] | None = None,
) -> tuple[dict[int, list[int]] | None, str]:
    """CP-SAT model for day assignment only.

    Returns (assignments, status_string).
    On iteration 0: per-(day, instance) marginals (initially home leg) + pairwise.
    On iteration 1+: marginals from last route per (day, instance); no pairwise.
    """
    clinician = input.clinician
    patients_by_id = ctx["patients_by_id"]
    inst_idx_map = ctx["inst_idx_map"]
    working_days = input.working_days
    num_days = len(working_days)
    num_instances = len(input.instances)
    day_indices = list(range(num_days))

    instances_by_patient = ctx["instances_by_patient"]
    locked_patient_days = ctx["locked_patient_days"]
    locked_count_by_day = ctx["locked_count_by_day"]
    day_wdays = ctx["day_wdays"]

    model = cp_model.CpModel()

    # ── Variables ───────────────────────────────────────────────────────

    day_var = []
    scheduled = []
    for i in range(num_instances):
        dv = model.new_int_var(0, num_days, f"day_{i}")
        day_var.append(dv)
        sv = model.new_bool_var(f"sched_{i}")
        scheduled.append(sv)
        model.add(dv < num_days).only_enforce_if(sv)
        model.add(dv == num_days).only_enforce_if(sv.negated())

    assign = {}
    for i in range(num_instances):
        for d in day_indices:
            b = model.new_bool_var(f"a_{i}_{d}")
            assign[(i, d)] = b
            model.add(day_var[i] == d).only_enforce_if(b)
            model.add(day_var[i] != d).only_enforce_if(b.negated())

    # ── Hard Constraints ───────────────────────────────────────────────

    # 1. One patient per day
    for pid, insts in instances_by_patient.items():
        inst_indices = [inst_idx_map[inst.id] for inst in insts]
        for d in day_indices:
            if d in locked_patient_days.get(pid, set()):
                for i in inst_indices:
                    model.add(assign[(i, d)] == 0)
            else:
                model.add(sum(assign[(i, d)] for i in inst_indices) <= 1)

    # 2. Max visits per day
    for d in day_indices:
        available = MAX_VISITS_PER_DAY - locked_count_by_day.get(d, 0)
        if available <= 0:
            for i in range(num_instances):
                model.add(assign[(i, d)] == 0)
        else:
            model.add(sum(assign[(i, d)] for i in range(num_instances)) <= available)

    # 3. Each instance on exactly one day or unscheduled
    for i in range(num_instances):
        model.add(sum(assign[(i, d)] for d in day_indices) + (1 - scheduled[i]) == 1)

    # ── Soft Constraints ───────────────────────────────────────────────

    penalties = []

    # 4. Unscheduled penalty
    for i, inst in enumerate(input.instances):
        patient = patients_by_id.get(inst.patient_id)
        priority_boost = (patient.priority + 1) if patient else 1
        penalties.append((scheduled[i].negated(), PENALTY_UNSCHEDULED * priority_boost))

    # 5. Spacing (min/max gap between visits of same patient)
    for pid, insts in instances_by_patient.items():
        patient = patients_by_id.get(pid)
        if not patient or len(insts) < 2:
            continue
        inst_indices = [inst_idx_map[inst.id] for inst in insts]
        min_gap = patient.min_days_between_visits
        max_gap = patient.max_days_between_visits
        locked_days = sorted(locked_patient_days.get(pid, set()))

        for a_idx in range(len(inst_indices)):
            i = inst_indices[a_idx]

            # vs locked visits
            for locked_d in locked_days:
                for d in day_indices:
                    if d == locked_d:
                        continue
                    gap = abs(d - locked_d)
                    if gap < min_gap:
                        pen = model.new_bool_var(f"spl_{i}_{d}_{locked_d}")
                        model.add_min_equality(pen, [assign[(i, d)], scheduled[i]])
                        penalties.append((pen, PENALTY_SPACING_MIN * (min_gap - gap)))
                    if gap > max_gap:
                        pen = model.new_bool_var(f"spx_{i}_{d}_{locked_d}")
                        model.add_min_equality(pen, [assign[(i, d)], scheduled[i]])
                        penalties.append((pen, PENALTY_SPACING_MAX * (gap - max_gap)))

            # vs other instances
            for b_idx in range(a_idx + 1, len(inst_indices)):
                j = inst_indices[b_idx]
                both = model.new_bool_var(f"bs_{i}_{j}")
                model.add_min_equality(both, [scheduled[i], scheduled[j]])

                gap = model.new_int_var(0, num_days, f"g_{i}_{j}")
                diff = model.new_int_var(-num_days, num_days, f"d_{i}_{j}")
                model.add(diff == day_var[i] - day_var[j])
                model.add_abs_equality(gap, diff)

                if min_gap > 1:
                    viol = model.new_int_var(0, min_gap, f"mv_{i}_{j}")
                    model.add(viol >= min_gap - gap).only_enforce_if(both)
                    model.add(viol == 0).only_enforce_if(both.negated())
                    penalties.append((viol, PENALTY_SPACING_MIN))

                if max_gap < num_days:
                    viol = model.new_int_var(0, num_days, f"xv_{i}_{j}")
                    model.add(viol >= gap - max_gap).only_enforce_if(both)
                    model.add(viol == 0).only_enforce_if(both.negated())
                    penalties.append((viol, PENALTY_SPACING_MAX))

    # 6. Availability windows
    for i, inst in enumerate(input.instances):
        if not inst.availability_windows:
            continue
        for d in day_indices:
            wday = str(day_wdays[d])
            windows = inst.availability_windows.get(wday, [])
            if not windows:
                # Patient has windows defined but none for this day → penalize assignment
                penalties.append((assign[(i, d)], PENALTY_AVAILABILITY))

    # 7. Pre-compute per-(day, instance) marginal cost vars (shared by drive constraint + objective)
    marginal_var: dict[tuple[int, int], tuple] = {}  # (d, i) → (cp_var, cost_value)
    max_marginal = 0
    for d in day_indices:
        for i in range(num_instances):
            cost = day_marginal_costs.get(d, {}).get(i, home_leg.get(i, 0))
            if cost > 0:
                c = model.new_int_var(0, cost, f"mc_{i}_{d}")
                model.add(c == cost).only_enforce_if(assign[(i, d)])
                model.add(c == 0).only_enforce_if(assign[(i, d)].negated())
                marginal_var[(d, i)] = (c, cost)
                max_marginal = max(max_marginal, cost)

    # Max drive per day (approximate using shared marginal cost vars)
    if clinician.max_drive_minutes_per_day:
        max_drive = clinician.max_drive_minutes_per_day
        drive_day_ub = max_marginal * MAX_VISITS_PER_DAY
        for d in day_indices:
            drive_terms = [mv[0] for i in range(num_instances) if (mv := marginal_var.get((d, i)))]
            if drive_terms:
                total = model.new_int_var(0, drive_day_ub, f"td_{d}")
                model.add(total == sum(drive_terms))
                over = model.new_int_var(0, drive_day_ub, f"do_{d}")
                model.add(over >= total - max_drive)
                model.add(over >= 0)
                penalties.append((over, PENALTY_DRIVE_OVER))

    # 8. Target day offsets (spread visits evenly)
    for pid, insts in instances_by_patient.items():
        patient = patients_by_id.get(pid)
        if not patient:
            continue
        n_visits = len(insts)
        if n_visits <= 1:
            continue

        inst_indices = sorted(inst_idx_map[inst.id] for inst in insts)
        min_gap_p = patient.min_days_between_visits

        max_step = (num_days - 1) / max(n_visits - 1, 1)
        min_step = max(float(min_gap_p), 1.0)
        step = max_step - (clinician.schedule_density * (max_step - min_step))
        step = max(step, min_step)

        targets = [min(round(k * step), num_days - 1) for k in range(n_visits)]

        for k, i in enumerate(inst_indices):
            target = targets[k] if k < len(targets) else targets[-1]
            dev = model.new_int_var(0, num_days, f"dd_{i}")
            raw = model.new_int_var(-num_days, num_days, f"dr_{i}")
            model.add(raw == day_var[i] - target)
            model.add_abs_equality(dev, raw)
            dev_s = model.new_int_var(0, num_days, f"ds_{i}")
            model.add(dev_s == dev).only_enforce_if(scheduled[i])
            model.add(dev_s == 0).only_enforce_if(scheduled[i].negated())
            penalties.append((dev_s, PENALTY_DAY_OFFSET))

    # 9. Schedule density
    day_counts = []
    for d in day_indices:
        cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, f"cnt_{d}")
        model.add(cnt == sum(assign[(i, d)] for i in range(num_instances)))
        day_counts.append(cnt)

    density = clinician.schedule_density
    if density < 0.5 and num_days > 1:
        max_cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, "mx")
        min_cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, "mn")
        model.add_max_equality(max_cnt, day_counts)
        model.add_min_equality(min_cnt, day_counts)
        spread = model.new_int_var(0, MAX_VISITS_PER_DAY, "sp")
        model.add(spread == max_cnt - min_cnt)
        w = int(2 * (1.0 - density))
        if w > 0:
            penalties.append((spread, w))
    elif density > 0.5:
        for d in day_indices:
            active = model.new_bool_var(f"act_{d}")
            model.add(day_counts[d] > 0).only_enforce_if(active)
            model.add(day_counts[d] == 0).only_enforce_if(active.negated())
            w = int(2 * density)
            if w > 0:
                penalties.append((active, w))

    # ── Objective: travel cost from distance matrix + penalties ────────

    travel_terms = []
    travel_fn = ctx["travel"]

    # Per-(day, instance) marginal routing cost — reuse shared vars from step 7
    for (d, i), (var, _cost) in marginal_var.items():
        travel_terms.append(var)

    # Pairwise inter-patient costs (iteration 0 only — teaches geographic clustering).
    # On later iterations, per-day marginals come from routing; pairwise would double-count.
    # Pruning: skip pairs where travel exceeds max(1, median home-leg) × mult (median 0 must
    # not yield threshold 0, or every t>0 pair would be dropped).
    travel_ub = sum(cost for (_, cost) in marginal_var.values())

    if iteration == 0:
        prune_mult = max(1, int(os.environ.get("CPSAT_PAIRWISE_PRUNE_MULT", "3")))
        home_vals = sorted(home_leg.values())
        median_home = home_vals[len(home_vals) // 2] if home_vals else 0
        pairwise_threshold = max(1, median_home) * prune_mult
        pids = [str(inst.patient_id) for inst in input.instances]
        for i in range(num_instances):
            for j in range(i + 1, num_instances):
                if pids[i] == pids[j]:
                    continue
                t = travel_fn(pids[i], pids[j])
                if t == 0 or t > pairwise_threshold:
                    continue
                travel_ub += t
                for d in day_indices:
                    both = model.new_bool_var(f"pw_{i}_{j}_{d}")
                    model.add_min_equality(both, [assign[(i, d)], assign[(j, d)]])
                    pw = model.new_int_var(0, t, f"pwc_{i}_{j}_{d}")
                    model.add(pw == t).only_enforce_if(both)
                    model.add(pw == 0).only_enforce_if(both.negated())
                    travel_terms.append(pw)

    travel_ub = max(travel_ub, 1)
    travel_cap = min(travel_ub, 50_000_000)
    total_travel = model.new_int_var(0, travel_cap, "tt")
    model.add(total_travel == sum(travel_terms)) if travel_terms else model.add(total_travel == 0)

    penalty_terms = []
    for var, weight in penalties:
        w = int(weight)
        if w > 0:
            penalty_terms.append(var * w)

    # Tight objective domain from penalty structure (OR-Tools var repr includes our name prefixes).
    drive_over_ub = 0
    if clinician.max_drive_minutes_per_day and marginal_var:
        max_marg = max((cost for (_, cost) in marginal_var.values()), default=0)
        drive_over_ub = max_marg * MAX_VISITS_PER_DAY

    penalty_cap = 0
    for var, wt in penalties:
        w = int(wt)
        if w <= 0:
            continue
        label = str(var)
        if "do_" in label:
            penalty_cap += w * drive_over_ub
        elif any(x in label for x in ("mv_", "xv_", "dd_", "ds_")):
            penalty_cap += w * num_days
        elif any(x in label for x in ("(mx(", "(mn(", "(sp(", " mx(", " mn(", " sp(")):
            penalty_cap += w * MAX_VISITS_PER_DAY
        else:
            penalty_cap += w

    penalty_cap = max(penalty_cap, 1)
    penalty_cap = min(penalty_cap, 500_000_000)
    total_penalty = model.new_int_var(0, penalty_cap, "tp")
    model.add(total_penalty == sum(penalty_terms)) if penalty_terms else model.add(total_penalty == 0)

    model.minimize(total_travel + total_penalty)

    # ── Warm-start from previous iteration's assignment (or prior upper_bound on iter 0) ──
    # Channelling links day_var to assign; hinting day_var + scheduled is enough unless
    # CPSAT_FULL_ASSIGN_HINTS forces every assign[(i,d)] hint (more hints, slower presolve).
    full_assign_hints = os.environ.get("CPSAT_FULL_ASSIGN_HINTS", "").lower() in ("1", "true", "yes")
    if prev_assignments is not None:
        prev_inst_day = {}
        for d, inst_list in prev_assignments.items():
            for i in inst_list:
                prev_inst_day[i] = d
        for i in range(num_instances):
            if i in prev_inst_day:
                model.add_hint(day_var[i], prev_inst_day[i])
                model.add_hint(scheduled[i], 1)
                if full_assign_hints:
                    for d in day_indices:
                        model.add_hint(assign[(i, d)], 1 if d == prev_inst_day[i] else 0)
            else:
                model.add_hint(scheduled[i], 0)
                model.add_hint(day_var[i], num_days)

    # ── Solve ──────────────────────────────────────────────────────────

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_budget
    solver.parameters.num_workers = NUM_WORKERS
    if os.environ.get("CPSAT_USE_FEASIBILITY_JUMP", "1").lower() not in ("0", "false", "no"):
        # Often speeds large LNS-style models; safe no-op if the field is absent in older OR-Tools.
        if hasattr(solver.parameters, "use_feasibility_jump"):
            solver.parameters.use_feasibility_jump = True

    status = solver.solve(model)
    status_name = solver.status_name(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, status_name

    # Extract assignments (sorted instance indices per day for stable routing I/O)
    result: dict[int, list[int]] = defaultdict(list)
    for i in range(num_instances):
        if solver.value(scheduled[i]):
            d = solver.value(day_var[i])
            if d < num_days:
                result[d].append(i)

    for d_key in result:
        result[d_key].sort()

    return dict(result), status_name


def _trivial_empty_route_day(input: SolverInput, date: str) -> dict:
    """No floating or locked visits on this date: placeholder lunch only (matches _route_day)."""
    c = input.clinician
    half_w = c.lunch_window_minutes // 2
    lunch_earliest = max(c.lunch_start_minute - half_w, c.workday_start_minute)
    lunch_latest = min(
        c.lunch_start_minute + half_w,
        c.workday_end_minute - c.lunch_duration_minutes,
    )
    if lunch_latest < lunch_earliest:
        lunch_latest = lunch_earliest
    lunch_dur = c.lunch_duration_minutes
    lunch_start = _round_up(lunch_earliest, SLOT_STEP)
    return {
        "visits": [],
        "lunch": {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur},
        "drive_cost": 0,
        "winner": None,
    }


# ═══════════════════════════════════════════════════════════════════════
# Day Router (optimal sequencing + timing for a single day)
# ═══════════════════════════════════════════════════════════════════════

def _route_day(
    date: str,
    instances: list[VisitInstanceData],
    input: SolverInput,
    ctx: dict,
) -> dict:
    """Find optimal route order and concrete times for a single day's visits.

    Strategy:
      1. Collect locked visits for this day as fixed-position route stops
      2. HGS (PyVRP) finds the travel-optimal ordering for floating visits
      3. For ≤5 floating visits, also try all permutations (exact)
      4. Retime each ordering with locked visits interleaved by time
      5. Pick the lowest-cost feasible result

    Locked visits are real stops: the route travels to/from them, and their
    travel cost is included in drive_cost.
    """
    clinician = input.clinician
    travel = ctx["travel"]

    # Lunch config (match ClinicianProfile#lunch_range; guard misconfig)
    half_w = clinician.lunch_window_minutes // 2
    lunch_earliest = max(clinician.lunch_start_minute - half_w, clinician.workday_start_minute)
    lunch_latest = min(
        clinician.lunch_start_minute + half_w,
        clinician.workday_end_minute - clinician.lunch_duration_minutes,
    )
    if lunch_latest < lunch_earliest:
        lunch_latest = lunch_earliest
    lunch_dur = clinician.lunch_duration_minutes
    lunch_target = clinician.lunch_start_minute

    # Blocked time ranges: calendar blocks + locked visit time slots
    # (floating visits must not overlap either; locked visits are also route stops
    # for travel purposes, handled separately in _evaluate_route)
    day_blocks = []
    for cb in ctx["calendar_blocks_by_date"].get(date, ()):
        day_blocks.append((_datetime_to_minute(cb.starts_at), _datetime_to_minute(cb.ends_at)))

    # Locked visits for this day — treated as fixed-time route stops AND blocked ranges
    locked_stops = []
    for lv in ctx["locked_visits_by_date"].get(date, ()):
        s = _datetime_to_minute(lv.starts_at)
        locked_stops.append({
            "patient_id": lv.patient_id,
            "start_min": s,
            "duration": lv.duration_minutes,
            "charting": clinician.charting_buffer_minutes,
        })
        day_blocks.append((s, s + lv.duration_minutes + clinician.charting_buffer_minutes))
    locked_stops.sort(key=lambda s: s["start_min"])

    if not instances and not locked_stops:
        lunch_start = _round_up(lunch_earliest, SLOT_STEP)
        return {
            "visits": [],
            "lunch": {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur},
            "drive_cost": 0,
            "winner": None,
        }

    if not instances:
        # Only locked visits — compute drive cost for locked route only
        drive_cost = 0
        prev = "home"
        for ls in locked_stops:
            drive_cost += travel(prev, str(ls["patient_id"]))
            prev = str(ls["patient_id"])
        drive_cost += travel(prev, "home")
        lunch_start = _round_up(lunch_earliest, SLOT_STEP)
        return {
            "visits": [],
            "lunch": {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur},
            "drive_cost": drive_cost,
            "winner": None,
        }

    # Collect candidate orderings (deduplicated)
    seen: set[tuple[int, ...]] = set()
    candidate_orders: list[tuple[str, tuple[int, ...]]] = []  # (source, order)

    def _add_candidate(source: str, order: tuple[int, ...]):
        if order not in seen:
            seen.add(order)
            candidate_orders.append((source, order))

    n = len(instances)
    # Full enumeration covers all orders for n≤5; HGS only adds PyVRP overhead then.
    # If CPSAT_SKIP_PERM_ENUM_FOR_N5, n=5 skips 5! evals and needs HGS for a strong order.
    skip_perm_n5 = os.environ.get("CPSAT_SKIP_PERM_ENUM_FOR_N5", "").lower() in ("1", "true", "yes")
    enumerate_all_orders = n <= 4 or (n == 5 and not skip_perm_n5)
    run_hgs = n >= 2 and not enumerate_all_orders

    # 1. HGS when n is large (or n=5 fast mode without full perm enumeration)
    if run_hgs:
        hgs_order = _hgs_route_order(instances, travel, clinician)
        if hgs_order is not None:
            _add_candidate("hgs", hgs_order)

    # 2. Nearest-neighbor ordering
    nn_order = _nearest_neighbor(instances, travel)
    _add_candidate("nn", nn_order)

    # 3. Exhaustive permutations for n≤5 (exact w.r.t. visit order); redundant with HGS when n≤5
    if enumerate_all_orders and n >= 2:
        for perm in itertools.permutations(range(n)):
            _add_candidate("perm", perm)

    # Evaluate all candidates with retiming
    best_result = None
    best_cost = float("inf")
    best_source = None

    for source, order in candidate_orders:
        result = _evaluate_route(
            order, instances, date, clinician, travel, day_blocks,
            lunch_earliest, lunch_latest, lunch_dur, lunch_target,
            locked_stops=locked_stops,
        )
        if result is not None and result["total_cost"] < best_cost:
            best_cost = result["total_cost"]
            best_result = result
            best_source = source

    # Fallback: try without lunch constraint
    if best_result is None:
        for source, order in candidate_orders:
            result = _evaluate_route(
                order, instances, date, clinician, travel, day_blocks,
                lunch_earliest, lunch_latest, lunch_dur, lunch_target,
                force_lunch=False, locked_stops=locked_stops,
            )
            if result is not None and result["total_cost"] < best_cost:
                best_cost = result["total_cost"]
                best_result = result
                best_source = source

    # Absolute fallback: nearest-neighbor, no blocks
    if best_result is None:
        best_result = _evaluate_route(
            nn_order, instances, date, clinician, travel, [],
            lunch_earliest, lunch_latest, lunch_dur, lunch_target,
            force_lunch=False, skip_blocks=True, locked_stops=locked_stops,
        )
        best_source = "nn_fallback"
        if best_result is None:
            lunch_start = _round_up(lunch_earliest, SLOT_STEP)
            return {
                "visits": [],
                "lunch": {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur},
                "drive_cost": 0,
                "winner": None,
            }

    best_result["winner"] = best_source
    return best_result


def _hgs_route_order(
    instances: list[VisitInstanceData],
    travel,
    clinician,
) -> tuple[int, ...] | None:
    """Use PyVRP HGS to find the travel-optimal visit ordering for a single day.

    Builds a single-vehicle TSP model: one depot (home), one client per visit.
    Returns the visit indices in HGS's optimal order, or None if infeasible.
    """
    n = len(instances)
    if n <= 1:
        return tuple(range(n))

    try:
        model = VRPModel()
        depot = model.add_depot(x=0, y=0)

        clients = []
        for inst in instances:
            client = model.add_client(
                x=0, y=0,
                delivery=[1],
                service_duration=inst.duration,
                tw_early=clinician.workday_start_minute,
                tw_late=clinician.workday_end_minute,
            )
            clients.append(client)

        # Edges: depot ↔ clients, client ↔ client
        for i, inst in enumerate(instances):
            pid = str(inst.patient_id)
            t_out = travel("home", pid)
            t_back = travel(pid, "home")
            model.add_edge(depot, clients[i], distance=t_out, duration=t_out)
            model.add_edge(clients[i], depot, distance=t_back, duration=t_back)

            for j, inst_j in enumerate(instances):
                if i == j:
                    continue
                t = travel(pid, str(inst_j.patient_id))
                model.add_edge(clients[i], clients[j], distance=t, duration=t)

        workday_dur = clinician.workday_end_minute - clinician.workday_start_minute
        max_dist = clinician.max_drive_minutes_per_day or 999_999
        model.add_vehicle_type(
            num_available=1,
            capacity=[MAX_VISITS_PER_DAY],
            shift_duration=workday_dur,
            max_distance=max_dist,
            tw_early=clinician.workday_start_minute,
            tw_late=clinician.workday_end_minute,
        )

        # Scale time budget with problem size; cap via CPSAT_HGS_MAX_SECONDS
        hgs_seconds = max(0.05, min(HGS_MAX_SECONDS, n * 0.02))
        result = model.solve(stop=MaxRuntime(hgs_seconds), seed=42, display=False)

        if not result.is_feasible():
            return None

        routes = list(result.best.routes())
        if not routes:
            return None

        # Extract visit order from HGS route (client indices are 1-based, 0=depot)
        order = []
        for visit_idx in routes[0].visits():
            node = visit_idx - 1  # convert to 0-based
            if 0 <= node < n:
                order.append(node)

        if len(order) != n:
            return None  # HGS didn't place all visits

        return tuple(order)

    except (ValueError, RuntimeError) as e:
        logger.warning("HGS routing failed for %d instances: %s", n, e)
        return None
    except Exception as e:
        logger.warning("HGS unexpected error for %d instances: %s", n, e)
        return None


def _evaluate_route(
    perm: tuple[int, ...],
    instances: list[VisitInstanceData],
    date: str,
    clinician,
    travel,
    blocks: list[tuple[int, int]],
    lunch_earliest: int,
    lunch_latest: int,
    lunch_dur: int,
    lunch_target: int,
    force_lunch: bool = True,
    skip_blocks: bool = False,
    locked_stops: list[dict] | None = None,
) -> dict | None:
    """Evaluate a specific visit ordering with locked visits interleaved.

    Locked visits are fixed-time stops inserted into the route at their
    scheduled positions. Travel to/from them is included in drive_cost.
    """
    max_cont = clinician.max_continuous_work_minutes
    break_dur = clinician.required_break_minutes
    charting = clinician.charting_buffer_minutes
    workday_end = clinician.workday_end_minute

    current_time = clinician.workday_start_minute
    prev_key = "home"
    accumulated_work = 0
    lunch_taken = False
    lunch_placement = None
    visits = []
    drive_cost = 0

    # Queue of locked stops to interleave (sorted by start_min); use index cursor (not pop(0))
    pending_locked = list(locked_stops or [])
    locked_cursor = 0

    # Pre-compute day-of-week for availability window checks
    dt_date = datetime.fromisoformat(date)
    wday_str = str((dt_date.weekday() + 1) % 7)

    for idx in perm:
        inst = instances[idx]
        pid_key = str(inst.patient_id)
        # Patient-facing duration on calendar; duration field includes charting in API
        visit_dur = max(0, inst.duration - charting)
        footprint = inst.duration  # visit + charting buffer (duration includes charting)

        # Transit to this floating visit
        transit = travel(prev_key, pid_key)
        transit_buf = TRANSIT_BUFFER if prev_key != "home" else 0
        raw_start = current_time + transit + transit_buf

        # Mandatory break: max continuous work (drive + on-site time) since last break.
        # If driving to this visit and completing it would exceed the legal limit, take a
        # break before starting the visit (footprint = visit + charting).
        if max_cont and max_cont > 0 and accumulated_work + transit + footprint > max_cont:
            raw_start += break_dur
            accumulated_work = 0

        earliest_start = _round_up(raw_start, SLOT_STEP)

        # Before placing this floating visit, handle any locked stops that
        # occur before earliest_start (travel to locked, wait, travel from)
        while locked_cursor < len(pending_locked) and pending_locked[locked_cursor]["start_min"] <= earliest_start:
            ls = pending_locked[locked_cursor]
            locked_cursor += 1
            ls_pid = str(ls["patient_id"])
            ls_end = ls["start_min"] + ls["duration"] + ls["charting"]

            # Travel to locked visit
            t_to_locked = travel(prev_key, ls_pid)
            drive_cost += t_to_locked
            prev_key = ls_pid

            # Update current_time to after locked visit
            current_time = max(current_time + t_to_locked, ls_end)
            accumulated_work += ls["duration"] + ls["charting"]

            # Recalculate earliest_start from new position
            transit = travel(prev_key, pid_key)
            transit_buf = TRANSIT_BUFFER
            raw_start = current_time + transit + transit_buf
            if max_cont and max_cont > 0 and accumulated_work + transit + footprint > max_cont:
                raw_start += break_dur
                accumulated_work = 0
            earliest_start = _round_up(raw_start, SLOT_STEP)

        drive_cost += transit

        # Availability window: try to place within preferred window
        if inst.availability_windows:
            windows = inst.availability_windows.get(wday_str, [])
            if windows:
                best_window_start = None
                for w in sorted(windows, key=lambda w: w.get("start_minute", 0)):
                    w_start = w.get("start_minute", 0)
                    w_end = w.get("end_minute", 1440)
                    candidate = max(earliest_start, w_start)
                    candidate = _round_up(candidate, SLOT_STEP)
                    if candidate + footprint <= w_end:
                        best_window_start = candidate
                        break
                if best_window_start is not None:
                    earliest_start = best_window_start

        # Lunch insertion (skip past any blocks that overlap the lunch window)
        if not lunch_taken and earliest_start >= lunch_earliest:
            lunch_start = max(earliest_start, lunch_earliest)
            lunch_start = min(lunch_start, lunch_latest)
            lunch_start = _round_up(lunch_start, SLOT_STEP)
            if not skip_blocks:
                for b_start, b_end in sorted(blocks):
                    if lunch_start < b_end and lunch_start + lunch_dur > b_start:
                        lunch_start = _round_up(b_end, SLOT_STEP)
            lunch_end = lunch_start + lunch_dur
            if lunch_start <= lunch_latest:
                lunch_placement = {"start_minute": lunch_start, "end_minute": lunch_end}
                if earliest_start < lunch_end:
                    earliest_start = _round_up(lunch_end, SLOT_STEP)
                lunch_taken = True
                accumulated_work = 0

        # Skip past blocked ranges (loop: pushing past one block may land in another)
        if not skip_blocks:
            moved = True
            while moved:
                moved = False
                for b_start, b_end in blocks:
                    proposed_end = earliest_start + footprint
                    if earliest_start < b_end and proposed_end > b_start:
                        earliest_start = _round_up(b_end + TRANSIT_BUFFER, SLOT_STEP)
                        moved = True

        # Check workday bounds
        if earliest_start + visit_dur > workday_end:
            return None  # infeasible

        starts_at = minutes_to_datetime(date, earliest_start)
        ends_at = minutes_to_datetime(date, earliest_start + visit_dur)

        # Check availability window compliance
        soft_override = False
        if inst.availability_windows:
            windows = inst.availability_windows.get(wday_str, [])
            if windows:
                in_any = any(
                    w.get("start_minute", 0) <= earliest_start < w.get("end_minute", 1440)
                    for w in windows
                )
                if not in_any:
                    soft_override = True

        visits.append(PlannedVisit(
            instance_id=inst.id,
            patient_id=inst.patient_id,
            date=date,
            starts_at=starts_at,
            ends_at=ends_at,
            soft_constraint_override=soft_override,
        ))

        accumulated_work += transit + footprint
        current_time = earliest_start + footprint
        prev_key = pid_key

    # Handle any remaining locked stops after all floating visits
    for ls in pending_locked[locked_cursor:]:
        ls_pid = str(ls["patient_id"])
        drive_cost += travel(prev_key, ls_pid)
        prev_key = ls_pid
        current_time = max(current_time, ls["start_min"] + ls["duration"] + ls["charting"])

    # Return-home leg
    drive_cost += travel(prev_key, "home")

    # Lunch fallback
    if not lunch_taken:
        if force_lunch:
            lunch_start = _round_up(lunch_earliest, SLOT_STEP)
            lunch_placement = {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur}
        else:
            lunch_placement = None

    # Lunch drift penalty (for cost comparison); missing lunch penalized heavily
    if lunch_placement:
        lunch_drift = abs(lunch_placement["start_minute"] - lunch_target)
    elif force_lunch:
        lunch_drift = 200  # failed to place mandatory lunch
    else:
        lunch_drift = 0

    return {
        "visits": visits,
        "lunch": lunch_placement,
        "drive_cost": drive_cost,
        "total_cost": drive_cost + lunch_drift,
    }


def _nearest_neighbor(
    instances: list[VisitInstanceData],
    travel,
) -> tuple[int, ...]:
    """Nearest-neighbor ordering as fallback."""
    remaining = set(range(len(instances)))
    order = []
    current = "home"
    while remaining:
        best_idx = min(remaining, key=lambda i: travel(current, str(instances[i].patient_id)))
        order.append(best_idx)
        remaining.discard(best_idx)
        current = str(instances[best_idx].patient_id)
    return tuple(order)


# ═══════════════════════════════════════════════════════════════════════
# Shared Helpers
# ═══════════════════════════════════════════════════════════════════════

def _build_context(input: SolverInput) -> dict:
    """Pre-compute shared data structures used by both assignment and routing."""
    clinician = input.clinician
    working_days = input.working_days
    matrix = input.travel_matrix

    day_wdays = []
    for d in working_days:
        dt = datetime.fromisoformat(d)
        day_wdays.append((dt.weekday() + 1) % 7)

    blocked_ranges_by_day: dict[int, list[tuple[int, int]]] = defaultdict(list)
    locked_patient_days: dict[int, set[int]] = defaultdict(set)
    locked_count_by_day: dict[int, int] = defaultdict(int)

    for lv in input.locked_visits:
        day_idx = _day_index(lv.date, working_days)
        if day_idx is None:
            continue
        locked_patient_days[lv.patient_id].add(day_idx)
        locked_count_by_day[day_idx] += 1
        # Locked visits are handled as route stops in _evaluate_route, but
        # also add as blocked ranges so floating visits don't overlap them
        start_min = _datetime_to_minute(lv.starts_at)
        end_min = start_min + lv.duration_minutes + clinician.charting_buffer_minutes
        blocked_ranges_by_day[day_idx].append((start_min, end_min))

    for cb in input.calendar_blocks:
        day_idx = _day_index(cb.date, working_days)
        if day_idx is None:
            continue
        start_min = _datetime_to_minute(cb.starts_at)
        end_min = _datetime_to_minute(cb.ends_at)
        blocked_ranges_by_day[day_idx].append((start_min, end_min))

    instances_by_patient: dict[int, list] = defaultdict(list)
    for inst in input.instances:
        instances_by_patient[inst.patient_id].append(inst)

    def travel_fn(from_key: str, to_key: str) -> int:
        return matrix.get(from_key, {}).get(to_key, 0)

    patients_by_id = {p.id: p for p in input.patients}
    inst_idx_map = {inst.id: i for i, inst in enumerate(input.instances)}
    date_to_idx = {d: i for i, d in enumerate(input.working_days)}

    calendar_blocks_by_date: dict[str, list] = defaultdict(list)
    for cb in input.calendar_blocks:
        calendar_blocks_by_date[cb.date].append(cb)
    locked_visits_by_date: dict[str, list] = defaultdict(list)
    for lv in input.locked_visits:
        locked_visits_by_date[lv.date].append(lv)

    return {
        "day_wdays": day_wdays,
        "blocked_ranges_by_day": dict(blocked_ranges_by_day),
        "locked_patient_days": dict(locked_patient_days),
        "locked_count_by_day": dict(locked_count_by_day),
        "instances_by_patient": dict(instances_by_patient),
        "calendar_blocks_by_date": dict(calendar_blocks_by_date),
        "locked_visits_by_date": dict(locked_visits_by_date),
        "travel": travel_fn,
        "patients_by_id": patients_by_id,
        "inst_idx_map": inst_idx_map,
        "date_to_idx": date_to_idx,
    }


def _day_index(date_str: str, working_days: list[str]) -> int | None:
    try:
        return working_days.index(date_str)
    except ValueError:
        return None


def _datetime_to_minute(dt_str: str) -> int:
    dt = datetime.fromisoformat(dt_str)
    return dt.hour * 60 + dt.minute


def _round_up(minute: int, step: int) -> int:
    remainder = minute % step
    return minute if remainder == 0 else minute + (step - remainder)


def _empty_output(input: SolverInput, metadata: dict | None = None) -> SolverOutput:
    return SolverOutput(
        planned_visits=[],
        lunch_placements=build_lunch_placements(input),
        fitness=0.0,
        metadata=metadata or {"optimizer_type": "cpsat"},
    )
