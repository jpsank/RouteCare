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
"""

from __future__ import annotations

import itertools
import logging
import os
from collections import defaultdict
from datetime import datetime

from ortools.sat.python import cp_model
from pyvrp import Model as VRPModel
from pyvrp.stop import MaxRuntime

from models import SolverInput, SolverOutput, PlannedVisit, VisitInstanceData
from solvers.base import MAX_VISITS_PER_DAY, build_lunch_placements, compute_return_home, minutes_to_datetime

logger = logging.getLogger(__name__)

# Assignment penalties
PENALTY_UNSCHEDULED = 10_000
PENALTY_SPACING_MIN = 100
PENALTY_SPACING_MAX = 50
PENALTY_AVAILABILITY = 150
PENALTY_DRIVE_OVER = 50
PENALTY_DAY_OFFSET = 10
PENALTY_SOFT_OVERRIDE = 80

# Routing constants
SLOT_STEP = 15
TRANSIT_BUFFER = 5

# Pipeline
MAX_ITERATIONS = 3
NUM_WORKERS = int(os.environ.get("CPSAT_NUM_WORKERS", min(os.cpu_count() or 4, 8)))
# Cap for PyVRP per-day routing (seconds); floor still scales with n inside _hgs_route_order.
HGS_MAX_SECONDS = float(os.environ.get("CPSAT_HGS_MAX_SECONDS", "0.5"))


def solve(
    input: SolverInput,
    time_budget: int = 30,
    upper_bound: SolverOutput | None = None,
) -> SolverOutput:
    """Decomposed solve: CP-SAT assignment → day routing → feedback loop."""
    _ = upper_bound  # API parity / future warm-start from a prior SolverOutput

    if not input.instances:
        return _empty_output(input)

    ctx = _build_context(input)
    patients_by_id = {p.id: p for p in input.patients}
    num_instances = len(input.instances)

    # Static home-leg proxy for "if this instance were the only stop on a day"
    home_leg: dict[int, int] = {}
    for i, inst in enumerate(input.instances):
        t_out = ctx["travel"]("home", str(inst.patient_id))
        t_back = ctx["travel"](str(inst.patient_id), "home")
        home_leg[i] = (t_out + t_back) // 2

    # Per (day, instance) marginal cost for CP-SAT travel objective.
    # Iteration 0: all equal to home_leg[i]; later: routed share for placed days, else home_leg.
    num_days = len(input.working_days)
    day_marginal_costs: dict[int, dict[int, int]] = {
        d: {i: home_leg[i] for i in range(num_instances)} for d in range(num_days)
    }

    best_output = None
    best_fitness = float("inf")
    completed_iterations = 0
    cp_sat_status = "NOT_RUN"
    iteration_log = []

    for iteration in range(MAX_ITERATIONS):
        assign_budget = max(1, time_budget // (MAX_ITERATIONS * 2))

        # 1. CP-SAT: assign instances to days
        # Iter 0: per-day marginals (== home leg) + pairwise; iter 1+: marginals from last route per (d,i)
        assignments, status = _assign_days(
            input, ctx, day_marginal_costs, home_leg, assign_budget, iteration
        )
        cp_sat_status = status
        if assignments is None:
            break

        # 2. Route each day
        all_planned = []
        all_lunch = {}
        actual_day_costs: dict[int, int] = {}
        route_winners: dict[str, str] = {}

        for d in range(num_days):
            date = input.working_days[d]
            day_instances = [input.instances[i] for i in assignments.get(d, [])]

            route_result = _route_day(
                date=date,
                instances=day_instances,
                input=input,
                ctx=ctx,
            )

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

        iteration_log.append({
            "iteration": iteration,
            "drive": total_drive,
            "unsched_penalty": unsched_penalty,
            "soft_penalty": soft_penalty,
            "spacing_penalty": spacing_penalty,
            "density_penalty": density_penalty,
            "offset_penalty": offset_penalty,
            "fitness": fitness,
            "placed": len(all_planned),
        })

        if fitness < best_fitness:
            best_fitness = fitness
            best_output = (all_planned, all_lunch, actual_day_costs, route_winners)

    if best_output is None:
        return _empty_output(input)

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
    date_to_idx = {d: i for i, d in enumerate(input.working_days)}
    inst_day: dict[str, int] = {}
    for v in planned:
        di = date_to_idx.get(v.date)
        if di is not None:
            inst_day[v.instance_id] = di

    instances_by_patient = ctx["instances_by_patient"]
    inst_idx_map = {inst.id: i for i, inst in enumerate(input.instances)}
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
) -> tuple[dict[int, list[int]] | None, str]:
    """CP-SAT model for day assignment only.

    Returns (assignments, status_string).
    On iteration 0: per-(day, instance) marginals (initially home leg) + pairwise.
    On iteration 1+: marginals from last route per (day, instance); no pairwise.
    """
    clinician = input.clinician
    patients_by_id = {p.id: p for p in input.patients}
    working_days = input.working_days
    num_days = len(working_days)
    num_instances = len(input.instances)
    day_indices = list(range(num_days))

    instances_by_patient = ctx["instances_by_patient"]
    locked_patient_days = ctx["locked_patient_days"]
    locked_count_by_day = ctx["locked_count_by_day"]
    day_wdays = ctx["day_wdays"]

    # Pre-compute instance index map: inst → index (avoid O(n) .index() calls)
    inst_idx_map: dict[str, int] = {inst.id: i for i, inst in enumerate(input.instances)}

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

    # 7. Max drive per day (approximate using per-(day, instance) marginals)
    if clinician.max_drive_minutes_per_day:
        max_drive = clinician.max_drive_minutes_per_day
        max_marginal = 0
        for d in day_indices:
            for i in range(num_instances):
                max_marginal = max(
                    max_marginal,
                    day_marginal_costs.get(d, {}).get(i, home_leg.get(i, 0)),
                )
        # Tight-enough UB: at most MAX_VISITS_PER_DAY routed legs contribute per day
        drive_day_ub = max_marginal * MAX_VISITS_PER_DAY
        for d in day_indices:
            drive_terms = []
            for i in range(num_instances):
                cost = day_marginal_costs.get(d, {}).get(i, home_leg.get(i, 0))
                if cost > 0:
                    c = model.new_int_var(0, cost, f"dc_{i}_{d}")
                    model.add(c == cost).only_enforce_if(assign[(i, d)])
                    model.add(c == 0).only_enforce_if(assign[(i, d)].negated())
                    drive_terms.append(c)
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

    # Per-(day, instance) marginal routing cost (equals home_leg[i] for every day on iter 0).
    for d in day_indices:
        for i in range(num_instances):
            cost = day_marginal_costs.get(d, {}).get(i, home_leg.get(i, 0))
            if cost > 0:
                c = model.new_int_var(0, cost, f"m_{d}_{i}")
                model.add(c == cost).only_enforce_if(assign[(i, d)])
                model.add(c == 0).only_enforce_if(assign[(i, d)].negated())
                travel_terms.append(c)

    # Pairwise inter-patient costs (iteration 0 only — teaches geographic clustering).
    # On later iterations, per-day marginals come from routing; pairwise would double-count.
    if iteration == 0:
        for i in range(num_instances):
            for j in range(i + 1, num_instances):
                pid_i = str(input.instances[i].patient_id)
                pid_j = str(input.instances[j].patient_id)
                if pid_i == pid_j:
                    continue
                t = travel_fn(pid_i, pid_j)
                if t == 0:
                    continue
                for d in day_indices:
                    both = model.new_bool_var(f"pw_{i}_{j}_{d}")
                    model.add_min_equality(both, [assign[(i, d)], assign[(j, d)]])
                    pw = model.new_int_var(0, t, f"pwc_{i}_{j}_{d}")
                    model.add(pw == t).only_enforce_if(both)
                    model.add(pw == 0).only_enforce_if(both.negated())
                    travel_terms.append(pw)

    total_travel = model.new_int_var(0, 1_000_000, "tt")
    model.add(total_travel == sum(travel_terms)) if travel_terms else model.add(total_travel == 0)

    penalty_terms = []
    for var, weight in penalties:
        w = int(weight)
        if w > 0:
            penalty_terms.append(var * w)

    total_penalty = model.new_int_var(0, 10_000_000, "tp")
    model.add(total_penalty == sum(penalty_terms)) if penalty_terms else model.add(total_penalty == 0)

    model.minimize(total_travel + total_penalty)

    # ── Solve ──────────────────────────────────────────────────────────

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_budget
    solver.parameters.num_workers = NUM_WORKERS

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
    for cb in input.calendar_blocks:
        if cb.date == date:
            day_blocks.append((_datetime_to_minute(cb.starts_at), _datetime_to_minute(cb.ends_at)))

    # Locked visits for this day — treated as fixed-time route stops AND blocked ranges
    locked_stops = []
    for lv in input.locked_visits:
        if lv.date == date:
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

    # 1. HGS ordering
    hgs_order = _hgs_route_order(instances, travel, clinician)
    if hgs_order is not None:
        _add_candidate("hgs", hgs_order)

    # 2. Nearest-neighbor ordering
    nn_order = _nearest_neighbor(instances, travel)
    _add_candidate("nn", nn_order)

    # 3. Exhaustive permutations for small instance counts
    n = len(instances)
    if n <= 5:
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

    # Queue of locked stops to interleave (sorted by start_min)
    pending_locked = list(locked_stops or [])

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
        while pending_locked and pending_locked[0]["start_min"] <= earliest_start:
            ls = pending_locked.pop(0)
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
    for ls in pending_locked:
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
    remaining = list(range(len(instances)))
    order = []
    current = "home"
    while remaining:
        best_idx = min(remaining, key=lambda i: travel(current, str(instances[i].patient_id)))
        order.append(best_idx)
        remaining.remove(best_idx)
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

    return {
        "day_wdays": day_wdays,
        "blocked_ranges_by_day": dict(blocked_ranges_by_day),
        "locked_patient_days": dict(locked_patient_days),
        "locked_count_by_day": dict(locked_count_by_day),
        "instances_by_patient": dict(instances_by_patient),
        "travel": travel_fn,
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
