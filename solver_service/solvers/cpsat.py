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
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import datetime

from ortools.sat.python import cp_model
from pyvrp import Model as VRPModel
from pyvrp.stop import MaxRuntime

from models import SolverInput, SolverOutput, PlannedVisit, VisitInstanceData
from solvers.base import MAX_VISITS_PER_DAY, build_lunch_placements, compute_return_home, minutes_to_datetime

# Assignment penalties
PENALTY_UNSCHEDULED = 10_000
PENALTY_SPACING_MIN = 100
PENALTY_SPACING_MAX = 50
PENALTY_AVAILABILITY = 150
PENALTY_DRIVE_OVER = 50
PENALTY_DAY_OFFSET = 10

# Routing constants
SLOT_STEP = 15
TRANSIT_BUFFER = 5

# Pipeline
MAX_ITERATIONS = 3


def solve(
    input: SolverInput,
    time_budget: int = 30,
    upper_bound: SolverOutput | None = None,
) -> SolverOutput:
    """Decomposed solve: CP-SAT assignment → day routing → feedback loop."""
    if not input.instances:
        return _empty_output(input)

    ctx = _build_context(input)
    num_instances = len(input.instances)
    num_days = len(input.working_days)

    # Initial approximate costs: avg(home→patient, patient→home) per patient per day
    day_costs: dict[int, dict[int, int]] = {}  # day_idx → {instance_idx → marginal_cost}
    for d in range(num_days):
        day_costs[d] = {}
        for i, inst in enumerate(input.instances):
            t_out = ctx["travel"]("home", str(inst.patient_id))
            t_back = ctx["travel"](str(inst.patient_id), "home")
            day_costs[d][i] = (t_out + t_back) // 2

    best_output = None
    best_fitness = float("inf")

    for iteration in range(MAX_ITERATIONS):
        # Budget: split time between assignment and routing
        assign_budget = max(1, time_budget // (MAX_ITERATIONS * 2))

        # 1. CP-SAT: assign instances to days
        assignments = _assign_days(input, ctx, day_costs, assign_budget)
        if assignments is None:
            break

        # 2. Route each day
        all_planned = []
        all_lunch = {}
        actual_day_costs: dict[int, int] = {}

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

        # 3. Update costs for next iteration
        # Compute marginal cost: how much does adding instance i to day d cost?
        # Use actual route cost difference as feedback
        for d in range(num_days):
            assigned_on_d = assignments.get(d, [])
            base_cost = actual_day_costs.get(d, 0)

            for i in range(num_instances):
                if i in assigned_on_d:
                    # Marginal cost ≈ route cost with this instance / num_instances_on_day
                    count = max(len(assigned_on_d), 1)
                    day_costs[d][i] = base_cost // count
                else:
                    # Keep approximate cost for unassigned
                    inst = input.instances[i]
                    t_out = ctx["travel"]("home", str(inst.patient_id))
                    t_back = ctx["travel"](str(inst.patient_id), "home")
                    day_costs[d][i] = (t_out + t_back) // 2

        # Compute fitness
        total_drive = sum(actual_day_costs.values())
        total_penalty = PENALTY_UNSCHEDULED * (num_instances - len(all_planned))
        fitness = total_drive + total_penalty

        if fitness < best_fitness:
            best_fitness = fitness
            best_output = (all_planned, all_lunch, actual_day_costs)

    if best_output is None:
        return _empty_output(input)

    planned_visits, lunch_placements, day_drive_costs = best_output

    # Sort by date and time
    planned_visits.sort(key=lambda v: (v.date, v.starts_at))

    # Return home
    routes_by_day: dict[str, list[int]] = defaultdict(list)
    for v in planned_visits:
        routes_by_day[v.date].append(v.patient_id)
    return_home = compute_return_home(dict(routes_by_day), input.travel_matrix)

    # Fill lunch for days without visits
    for date in input.working_days:
        if date not in lunch_placements:
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
    patients_by_id = {p.id: p for p in input.patients}
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

    return SolverOutput(
        planned_visits=planned_visits,
        lunch_placements=lunch_placements,
        fitness=best_fitness,
        metadata={
            "optimizer_type": "cpsat",
            "proven_optimal": False,  # decomposed approach doesn't prove global optimality
            "status": "FEASIBLE",
            "iterations": min(MAX_ITERATIONS, MAX_ITERATIONS),
            "unschedulable": unschedulable,
            "drive_violations": drive_violations,
            "soft_constraint_overrides": sum(1 for v in planned_visits if v.soft_constraint_override),
            "return_home_by_day": return_home,
        },
    )


# ═══════════════════════════════════════════════════════════════════════
# CP-SAT Assignment Model (no routing variables)
# ═══════════════════════════════════════════════════════════════════════

def _assign_days(
    input: SolverInput,
    ctx: dict,
    day_costs: dict[int, dict[int, int]],
    time_budget: int,
) -> dict[int, list[int]] | None:
    """CP-SAT model for day assignment only. Returns {day_idx: [instance_indices]}."""
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
        inst_indices = [input.instances.index(inst) for inst in insts]
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
        inst_indices = [input.instances.index(inst) for inst in insts]
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

    # 7. Max drive per day (approximate)
    if clinician.max_drive_minutes_per_day:
        max_drive = clinician.max_drive_minutes_per_day
        for d in day_indices:
            drive_terms = []
            for i in range(num_instances):
                cost = day_costs[d].get(i, 0)
                if cost > 0:
                    c = model.new_int_var(0, cost, f"dc_{i}_{d}")
                    model.add(c == cost).only_enforce_if(assign[(i, d)])
                    model.add(c == 0).only_enforce_if(assign[(i, d)].negated())
                    drive_terms.append(c)
            if drive_terms:
                total = model.new_int_var(0, 10000, f"td_{d}")
                model.add(total == sum(drive_terms))
                over = model.new_int_var(0, 10000, f"do_{d}")
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

        inst_indices = sorted(input.instances.index(inst) for inst in insts)
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

    # ── Objective: approximate travel + penalties ──────────────────────

    # Travel cost from day_costs lookup
    travel_terms = []
    for d in day_indices:
        for i in range(num_instances):
            cost = day_costs[d].get(i, 0)
            if cost > 0:
                c = model.new_int_var(0, cost, f"tc_{i}_{d}")
                model.add(c == cost).only_enforce_if(assign[(i, d)])
                model.add(c == 0).only_enforce_if(assign[(i, d)].negated())
                travel_terms.append(c)

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
    solver.parameters.num_workers = 8

    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    # Extract assignments
    result: dict[int, list[int]] = defaultdict(list)
    for i in range(num_instances):
        if solver.value(scheduled[i]):
            d = solver.value(day_var[i])
            if d < num_days:
                result[d].append(i)

    return dict(result)


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
      1. HGS (PyVRP) finds the travel-optimal visit ordering
      2. For ≤5 visits, also try all permutations (exact)
      3. Retime the best ordering with lunch/breaks/blocks
      4. Pick the lowest-cost feasible result

    HGS handles the spatial optimization; retiming handles domain constraints.
    """
    clinician = input.clinician
    travel = ctx["travel"]
    blocked = ctx["blocked_ranges_by_day"]
    day_idx = _day_index(date, input.working_days)

    # Lunch config
    half_w = clinician.lunch_window_minutes // 2
    lunch_earliest = max(clinician.lunch_start_minute - half_w, clinician.workday_start_minute)
    lunch_latest = min(
        clinician.lunch_start_minute + half_w,
        clinician.workday_end_minute - clinician.lunch_duration_minutes,
    )
    lunch_dur = clinician.lunch_duration_minutes
    lunch_target = clinician.lunch_start_minute

    # Blocked ranges for this day
    day_blocks = blocked.get(day_idx, []) if day_idx is not None else []

    # Locked visits for this day
    day_locked_ranges = []
    for lv in input.locked_visits:
        if lv.date == date:
            s = _datetime_to_minute(lv.starts_at)
            e = s + lv.duration_minutes + clinician.charting_buffer_minutes
            day_locked_ranges.append((s, e))

    all_blocks = day_blocks + day_locked_ranges

    if not instances:
        lunch_start = _round_up(lunch_earliest, SLOT_STEP)
        return {
            "visits": [],
            "lunch": {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur},
            "drive_cost": 0,
        }

    # Collect candidate orderings
    candidate_orders: list[tuple[int, ...]] = []

    # 1. HGS ordering — travel-optimal route via PyVRP
    hgs_order = _hgs_route_order(instances, travel, clinician)
    if hgs_order is not None:
        candidate_orders.append(hgs_order)

    # 2. Nearest-neighbor ordering — fast fallback
    nn_order = _nearest_neighbor(instances, travel)
    candidate_orders.append(nn_order)

    # 3. Exhaustive permutations for small instance counts
    n = len(instances)
    if n <= 5:
        for perm in itertools.permutations(range(n)):
            candidate_orders.append(perm)

    # Evaluate all candidates with retiming
    best_result = None
    best_cost = float("inf")

    for order in candidate_orders:
        result = _evaluate_route(
            order, instances, date, clinician, travel, all_blocks,
            lunch_earliest, lunch_latest, lunch_dur, lunch_target,
        )
        if result is not None and result["total_cost"] < best_cost:
            best_cost = result["total_cost"]
            best_result = result

    # Fallback: try without lunch constraint
    if best_result is None:
        for order in candidate_orders:
            result = _evaluate_route(
                order, instances, date, clinician, travel, all_blocks,
                lunch_earliest, lunch_latest, lunch_dur, lunch_target,
                force_lunch=False,
            )
            if result is not None and result["total_cost"] < best_cost:
                best_cost = result["total_cost"]
                best_result = result

    # Absolute fallback: nearest-neighbor, no blocks
    if best_result is None:
        best_result = _evaluate_route(
            nn_order, instances, date, clinician, travel, [],
            lunch_earliest, lunch_latest, lunch_dur, lunch_target,
            force_lunch=False, skip_blocks=True,
        )
        if best_result is None:
            lunch_start = _round_up(lunch_earliest, SLOT_STEP)
            return {
                "visits": [],
                "lunch": {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur},
                "drive_cost": 0,
            }

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
        model.add_vehicle_type(
            num_available=1,
            capacity=[MAX_VISITS_PER_DAY],
            shift_duration=workday_dur,
            tw_early=clinician.workday_start_minute,
            tw_late=clinician.workday_end_minute,
        )

        # Very short budget — single-day TSP with ≤5 nodes solves in milliseconds
        result = model.solve(stop=MaxRuntime(0.05), seed=42, display=False)

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

    except Exception:
        return None  # HGS failed, fall through to other orderings


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
) -> dict | None:
    """Evaluate a specific visit ordering. Returns timing + cost, or None if infeasible."""
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

    for idx in perm:
        inst = instances[idx]
        pid_key = str(inst.patient_id)
        visit_dur = inst.duration - charting  # actual visit time
        footprint = inst.duration  # visit + charting buffer

        # Transit
        transit = travel(prev_key, pid_key)
        drive_cost += transit
        raw_start = current_time + transit + (TRANSIT_BUFFER if prev_key != "home" else 0)

        # Mandatory break check
        if max_cont and max_cont > 0 and accumulated_work + transit > max_cont:
            raw_start += break_dur
            accumulated_work = 0

        earliest_start = _round_up(raw_start, SLOT_STEP)

        # Availability window: try to place within preferred window
        if inst.availability_windows:
            dt = datetime.fromisoformat(date)
            wday = str((dt.weekday() + 1) % 7)
            windows = inst.availability_windows.get(wday, [])
            if windows:
                # Find earliest window that starts at or after earliest_start
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

        # Lunch insertion
        if not lunch_taken and earliest_start >= lunch_earliest:
            lunch_start = max(earliest_start, lunch_earliest)
            lunch_start = min(lunch_start, lunch_latest)
            lunch_start = _round_up(lunch_start, SLOT_STEP)
            lunch_end = lunch_start + lunch_dur
            lunch_placement = {"start_minute": lunch_start, "end_minute": lunch_end}
            if earliest_start < lunch_end:
                earliest_start = _round_up(lunch_end, SLOT_STEP)
            lunch_taken = True
            accumulated_work = 0

        # Skip past blocked ranges
        if not skip_blocks:
            for b_start, b_end in blocks:
                proposed_end = earliest_start + footprint
                if earliest_start < b_end and proposed_end > b_start:
                    earliest_start = _round_up(b_end + TRANSIT_BUFFER, SLOT_STEP)

        # Check workday bounds
        if earliest_start + visit_dur > workday_end:
            return None  # infeasible

        starts_at = minutes_to_datetime(date, earliest_start)
        ends_at = minutes_to_datetime(date, earliest_start + visit_dur)

        # Check availability window compliance
        soft_override = False
        if inst.availability_windows:
            dt = datetime.fromisoformat(date)
            wday = str((dt.weekday() + 1) % 7)
            windows = inst.availability_windows.get(wday, [])
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

        accumulated_work += footprint + transit
        current_time = earliest_start + footprint
        prev_key = pid_key

    # Return-home leg
    drive_cost += travel(prev_key, "home")

    # Lunch fallback
    if not lunch_taken:
        if force_lunch:
            lunch_start = _round_up(lunch_earliest, SLOT_STEP)
            lunch_placement = {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur}
        else:
            lunch_start = _round_up(lunch_earliest, SLOT_STEP)
            lunch_placement = {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur}

    # Lunch drift penalty (for cost comparison)
    lunch_drift = abs(lunch_placement["start_minute"] - lunch_target) if lunch_placement else 0

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
