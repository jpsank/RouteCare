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
    CPSAT_MAX_ITERATIONS — max assignment↔routing passes (default 5, minimum 1)
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

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from models import SolverInput, SolverOutput, PlannedVisit
from solvers.cpsat_context import (
    MAX_ITERATIONS,
    SolverContext,
    compute_return_home,
    NUM_WORKERS,
    PENALTY_SOFT_OVERRIDE,
    build_context,
    day_bounds,
    datetime_to_minute,
    empty_output,
)
from solvers.cpsat_assignment import assign_days
from solvers.cpsat_fitness import (
    compute_spacing_penalty,
    compute_density_fitness_penalty,
    compute_day_offset_fitness_penalty,
)
from solvers.cpsat_routing import route_day, trivial_empty_route_day

# Re-export for tests that monkeypatch or import internal names
_build_context = build_context
_assign_days = assign_days


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
    ctx: SolverContext,
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
    date_to_idx = ctx.date_to_idx
    seen_patient_day: set[tuple[int, str]] = set()
    for v in visits:
        if v.date not in date_to_idx:
            return False
        key = (v.patient_id, v.date)
        if key in seen_patient_day:
            return False
        seen_patient_day.add(key)
    return True


def _visits_within_day_bounds(
    visits: list[PlannedVisit],
    clinician,
) -> bool:
    """True if every visit's full footprint (visit + charting) fits within current per-day bounds."""
    charting = clinician.charting_buffer_minutes
    for v in visits:
        day_start, day_end = day_bounds(clinician, v.date)
        v_start = datetime_to_minute(v.starts_at)
        v_end = datetime_to_minute(v.ends_at) + charting
        if v_start < day_start or v_end > day_end:
            return False
    return True


def _visits_respect_min_spacing(
    visits: list[PlannedVisit],
    patients_by_id: dict,
) -> bool:
    """True if no two visits for the same patient are closer than min_days_between_visits."""
    patient_dates: dict[int, list[str]] = defaultdict(list)
    for v in visits:
        patient_dates[v.patient_id].append(v.date)
    for pid, dates in patient_dates.items():
        patient = patients_by_id.get(pid)
        if not patient or len(dates) < 2:
            continue
        ordinals = sorted(datetime.fromisoformat(d).toordinal() for d in set(dates))
        for i in range(len(ordinals) - 1):
            if ordinals[i + 1] - ordinals[i] <= patient.min_days_between_visits:
                return False
    return True


def _visits_within_drive_limit(
    planned: list[PlannedVisit],
    input: SolverInput,
    ctx: SolverContext,
) -> bool:
    """True if no day's total drive exceeds max_drive_minutes_per_day."""
    max_drive = input.clinician.max_drive_minutes_per_day
    if not max_drive:
        return True
    costs = _day_drive_costs_from_planned(planned, input, ctx)
    return all(c <= max_drive for c in costs.values())


def _visits_respect_calendar_blocks(
    visits: list[PlannedVisit],
    ctx: SolverContext,
    clinician,
) -> bool:
    """True if no visit overlaps a calendar block."""
    charting = clinician.charting_buffer_minutes
    for v in visits:
        v_start = datetime_to_minute(v.starts_at)
        v_end = datetime_to_minute(v.ends_at) + charting
        for cb in ctx.calendar_blocks_by_date.get(v.date, []):
            b_start = datetime_to_minute(cb.starts_at)
            b_end = datetime_to_minute(cb.ends_at)
            if v_start < b_end and v_end > b_start:
                return False
    return True


def _diagnose_unscheduled(inst, patient, input, ctx, placed_patient_dates) -> list[str]:
    """Identify which hard constraints prevented scheduling this instance."""
    clinician = input.clinician
    reasons = set()
    day_wdays = ctx.day_wdays
    blocked_ranges = ctx.blocked_ranges_by_day
    locked_patient_days = ctx.locked_patient_days

    for d, date in enumerate(input.working_days):
        wday = str(day_wdays[d])

        # Already has this patient on this day (one-per-day)
        if date in placed_patient_dates.get(inst.patient_id, set()):
            continue
        if d in locked_patient_days.get(inst.patient_id, set()):
            continue

        # Min spacing vs placed visits
        if patient and patient.min_days_between_visits >= 1:
            date_ord = datetime.fromisoformat(date).toordinal()
            too_close = False
            for pd in placed_patient_dates.get(inst.patient_id, set()):
                pd_ord = datetime.fromisoformat(pd).toordinal()
                if abs(date_ord - pd_ord) <= patient.min_days_between_visits:
                    too_close = True
                    break
            if too_close:
                reasons.add("min_days_between_visits")
                continue

        # Day capacity
        pdh = clinician.per_day_hours.get(wday, {})
        day_start = pdh.get("start", clinician.workday_start_minute)
        day_end = pdh.get("end", clinician.workday_end_minute)
        avail = max(0, day_end - day_start)
        for bs, be in blocked_ranges.get(d, []):
            avail -= max(0, min(be, day_end) - max(bs, day_start))
        if clinician.lunch_duration_minutes > 0:
            avail -= clinician.lunch_duration_minutes
        if inst.duration > avail:
            reasons.add("day_capacity")
            continue

        # Availability windows
        if inst.availability_windows:
            windows = inst.availability_windows.get(wday, [])
            if not windows:
                reasons.add("availability_windows")
                continue

        # If we get here, at least one day could have worked — likely a routing/drive issue
        reasons.add("routing_or_drive_limit")

    if not reasons:
        reasons.add("no_working_days_available")

    return sorted(reasons)


def _normalized_assignment_signature(
    assignments: dict[int, list[int]],
    num_days: int,
) -> tuple[tuple[int, ...], ...]:
    """Per-day sorted instance indices including empty days — stable compare for convergence."""
    return tuple(tuple(sorted(assignments.get(d, []))) for d in range(num_days))


def _best_insertion_cost(pid: str, route_pids: list[str], travel) -> int:
    """Cheapest drive-time increase to insert `pid` into an existing route.

    Tries every insertion position (before first, between each pair, after
    last) and returns the minimum incremental drive cost.  When the route is
    empty, returns the full round-trip home → pid → home.
    """
    if not route_pids:
        return travel("home", pid) + travel(pid, "home")

    # Before first stop: home → pid → route[0]  vs  home → route[0]
    best = (
        travel("home", pid)
        + travel(pid, route_pids[0])
        - travel("home", route_pids[0])
    )

    # Between consecutive stops
    for k in range(1, len(route_pids)):
        cost = (
            travel(route_pids[k - 1], pid)
            + travel(pid, route_pids[k])
            - travel(route_pids[k - 1], route_pids[k])
        )
        best = min(best, cost)

    # After last stop: route[-1] → pid → home  vs  route[-1] → home
    cost = (
        travel(route_pids[-1], pid)
        + travel(pid, "home")
        - travel(route_pids[-1], "home")
    )
    best = min(best, cost)

    return max(0, best)


def _iter0_instance_marginals(input: SolverInput, ctx: SolverContext, home_leg: dict[int, int]) -> list[int]:
    """Iter-0 CP-SAT travel marginal per instance: home_leg + NN margin toward next patient."""
    n = len(input.instances)
    travel = ctx.travel
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
    ctx: SolverContext,
    upper_bound: SolverOutput | None,
) -> dict[int, list[int]] | None:
    """Map prior planned visits to {day_index: [instance_index,...]} for CP-SAT hints."""
    if not upper_bound_valid_for_warm_start(input, ctx, upper_bound):
        return None
    inst_idx_map = ctx.inst_idx_map
    date_to_idx = ctx.date_to_idx
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
    ctx: SolverContext,
) -> dict[int, int]:
    """Round-trip drive minutes per working-day index from visit order.

    Includes travel to/from locked visits (they are real route stops).
    """
    by_date: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for v in planned:
        by_date[v.date].append((v.starts_at, str(v.patient_id)))
    for lv in input.locked_visits:
        by_date[lv.date].append((lv.starts_at, str(lv.patient_id)))

    travel = ctx.travel
    costs: dict[int, int] = {}
    for d_idx, date in enumerate(input.working_days):
        stops = sorted(by_date.get(date, []))  # sort by starts_at
        if not stops:
            costs[d_idx] = 0
            continue
        pids = [pid for _, pid in stops]
        c = travel("home", pids[0])
        for a, b in zip(pids, pids[1:]):
            c += travel(a, b)
        c += travel(pids[-1], "home")
        costs[d_idx] = c
    return costs


def _warm_best_from_output(
    input: SolverInput,
    ctx: SolverContext,
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
        return empty_output(input, metadata=meta)

    ctx = build_context(input)
    patients_by_id = ctx.patients_by_id
    num_instances = len(input.instances)

    warm_ok = upper_bound is not None and upper_bound_valid_for_warm_start(input, ctx, upper_bound)
    warm_incumbent_ok = warm_ok and all([
        _visits_within_day_bounds(upper_bound.planned_visits, input.clinician),
        _visits_respect_min_spacing(upper_bound.planned_visits, patients_by_id),
        _visits_within_drive_limit(upper_bound.planned_visits, input, ctx),
        _visits_respect_calendar_blocks(upper_bound.planned_visits, ctx, input.clinician),
    ])

    warm_assignments = _prior_assignments_from_output(input, ctx, upper_bound)
    warm_best = _warm_best_from_output(input, ctx, upper_bound)

    # Static home-leg proxy
    home_leg: dict[int, int] = {}
    for i, inst in enumerate(input.instances):
        t_out = ctx.travel("home", str(inst.patient_id))
        t_back = ctx.travel(str(inst.patient_id), "home")
        home_leg[i] = (t_out + t_back) // 2

    num_days = len(input.working_days)
    iter0_row = _iter0_instance_marginals(input, ctx, home_leg)

    # Build iter-0 marginals: for days with locked visits, compute insertion
    # cost into the locked-visit route (captures geographic proximity).
    # For days without locked visits, use the uniform home-leg + NN proxy.
    travel = ctx.travel
    lv_by_date = ctx.locked_visits_by_date
    day_marginal_costs: dict[int, dict[int, int]] = {}
    for d in range(num_days):
        date = input.working_days[d]
        locked_pids = [str(lv.patient_id) for lv in lv_by_date.get(date, [])]
        if locked_pids:
            day_marginal_costs[d] = {}
            for i in range(num_instances):
                pid_i = str(input.instances[i].patient_id)
                insertion = _best_insertion_cost(pid_i, locked_pids, travel)
                # Use the better of insertion cost and home-leg proxy —
                # insertion can be 0 for patients co-located with a locked visit.
                day_marginal_costs[d][i] = max(insertion, iter0_row[i] // 3)
        else:
            day_marginal_costs[d] = {i: iter0_row[i] for i in range(num_instances)}

    best_output: tuple | None = None
    best_placed = -1
    best_cost = float("inf")
    if warm_best is not None and upper_bound is not None and warm_incumbent_ok:
        planned_w, _, day_costs_w, _ = warm_best
        best_placed = len(planned_w)
        # Compute full cost matching iteration-loop components so comparison
        # is apples-to-apples (drive + soft + spacing + density + offset).
        warm_drive = sum(day_costs_w.values())
        warm_soft = sum(PENALTY_SOFT_OVERRIDE for v in planned_w if v.soft_constraint_override)
        warm_assign_map = warm_assignments or {}
        warm_spacing = compute_spacing_penalty(planned_w, patients_by_id, input)
        warm_density = compute_density_fitness_penalty(warm_assign_map, ctx, input)
        warm_offset = compute_day_offset_fitness_penalty(planned_w, patients_by_id, input, ctx)
        best_cost = float(warm_drive + warm_soft + warm_spacing + warm_density + warm_offset)
        best_output = warm_best
    completed_iterations = 0
    cp_sat_status = "NOT_RUN"
    iteration_log = []
    prev_assignments: dict[int, list[int]] | None = None
    prev_sig: tuple | None = None
    last_full_iteration_log: dict | None = None
    routing_dropped = False

    for iteration in range(MAX_ITERATIONS):
        assign_budget = _cp_sat_assign_seconds(iteration, time_budget, MAX_ITERATIONS)

        # 1. CP-SAT: assign instances to days
        hint_prev = prev_assignments
        if hint_prev is None and iteration == 0:
            hint_prev = warm_assignments

        assignments, status, assign_penalty = _assign_days(
            input, ctx, day_marginal_costs, home_leg, assign_budget, iteration,
            prev_assignments=hint_prev,
        )
        cp_sat_status = status
        if assignments is None:
            break

        # Early termination: if assignment didn't change AND routing placed
        # everything, costs won't change either.  Skip if routing dropped visits
        # — the updated marginals may steer the assignment model to a better plan.
        sig = _normalized_assignment_signature(assignments, num_days)
        if prev_sig is not None and sig == prev_sig and not routing_dropped:
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

        lv_by_date = ctx.locked_visits_by_date

        def _route_or_trivial(d: int) -> dict:
            date = input.working_days[d]
            day_instances = [input.instances[i] for i in assignments.get(d, [])]
            if not day_instances and not lv_by_date.get(date):
                return trivial_empty_route_day(input, date)
            return route_day(
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

        routed_instance_ids: set[str] = set()
        # Track (instance_id, patient_id) in route order for removal marginals
        routed_inst_order: dict[int, list[tuple[str, str]]] = {}
        routed_pid_order: dict[int, list[str]] = {}
        # Collect drop reasons from routing for targeted marginal penalties
        drop_reasons_by_day: dict[int, dict[str, str]] = {}  # day → {inst_id → reason}
        for d in range(num_days):
            date = input.working_days[d]
            route_result = results_by_d[d]
            all_planned.extend(route_result["visits"])
            all_lunch[date] = route_result["lunch"]
            actual_day_costs[d] = route_result["drive_cost"]
            if route_result.get("winner"):
                route_winners[date] = route_result["winner"]
            routed_inst_order[d] = [
                (v.instance_id, str(v.patient_id)) for v in route_result["visits"]
            ]
            routed_pid_order[d] = [str(v.patient_id) for v in route_result["visits"]]
            for v in route_result["visits"]:
                routed_instance_ids.add(v.instance_id)
            # Index drop reasons
            drop_reasons_by_day[d] = {
                entry["instance_id"]: entry["reason"]
                for entry in route_result.get("dropped", [])
            }

        # 3. Update per-(day, instance) marginals from actual routing.
        #    For routed visits, compute per-visit REMOVAL cost (detour cost
        #    of including the visit in its current position).  This is more
        #    informative than equal-share — it tells CP-SAT exactly how much
        #    each visit contributes to the day's drive cost.
        #    For dropped visits, tailor penalty to drop reason.
        #    For non-routed visits, compute cheapest insertion cost.
        travel = ctx.travel
        inst_idx_map = ctx.inst_idx_map
        max_drive = input.clinician.max_drive_minutes_per_day or 0
        routing_dropped = False
        for d in range(num_days):
            assigned_on_d = set(assignments.get(d, []))
            routed_on_d = {
                i for i in assigned_on_d
                if input.instances[i].id in routed_instance_ids
            }
            dropped_on_d = assigned_on_d - routed_on_d
            if dropped_on_d:
                routing_dropped = True

            base_cost = actual_day_costs.get(d, 0)
            route_pids = routed_pid_order.get(d, [])
            order = routed_inst_order.get(d, [])
            day_drops = drop_reasons_by_day.get(d, {})

            drive_floor = int(max_drive) if max_drive else 0

            # Per-visit removal costs for routed visits
            removal_costs: dict[int, int] = {}
            for k, (inst_id, pid) in enumerate(order):
                prev_pid = "home" if k == 0 else order[k - 1][1]
                next_pid = "home" if k == len(order) - 1 else order[k + 1][1]
                with_visit = travel(prev_pid, pid) + travel(pid, next_pid)
                without_visit = travel(prev_pid, next_pid)
                detour = max(0, with_visit - without_visit)
                i = inst_idx_map[inst_id]
                removal_costs[i] = detour

            for i in range(num_instances):
                if i in removal_costs:
                    day_marginal_costs[d][i] = removal_costs[i]
                elif i in dropped_on_d:
                    # Use drop reason to scale penalty appropriately.
                    inst_id = input.instances[i].id
                    reason = day_drops.get(inst_id, "unknown")
                    if reason == "drive_limit":
                        # Drive limit: heavily penalize THIS day (try other days).
                        day_marginal_costs[d][i] = max(drive_floor, home_leg[i] * 4, 200)
                    elif reason == "workday_overflow":
                        # Time overflow: moderate penalty (less-packed day might work).
                        day_marginal_costs[d][i] = max(home_leg[i] * 2, 90)
                    else:
                        day_marginal_costs[d][i] = max(home_leg[i] * 3, drive_floor, 120)
                else:
                    pid_i = str(input.instances[i].patient_id)
                    insertion = _best_insertion_cost(pid_i, route_pids, travel)
                    if max_drive and base_cost + insertion > max_drive:
                        day_marginal_costs[d][i] = max(home_leg[i] * 3, drive_floor, 120)
                    else:
                        day_marginal_costs[d][i] = insertion

        # 4. Compute cost (lexicographic: maximize placed, then minimize cost).
        #    Use CP-SAT's own assign_penalty (exact solver penalty for spacing,
        #    density, offsets, priority, availability) + actual drive from routing
        #    + routing-side soft overrides.  This is more accurate than
        #    re-approximating those penalties externally.
        total_drive = sum(actual_day_costs.values())
        placed_count = len(all_planned)
        soft_penalty = sum(PENALTY_SOFT_OVERRIDE for v in all_planned if v.soft_constraint_override)

        cost = total_drive + soft_penalty + assign_penalty
        completed_iterations = iteration + 1

        last_full_iteration_log = {
            "drive": total_drive,
            "soft_penalty": soft_penalty,
            "assign_penalty": assign_penalty,
            "cost": cost,
            "placed": placed_count,
        }
        iteration_log.append({"iteration": iteration, **last_full_iteration_log})

        # Lexicographic: more visits always wins; among equal counts, lower cost wins
        if placed_count > best_placed or (placed_count == best_placed and cost < best_cost):
            best_placed = placed_count
            best_cost = cost
            best_output = (all_planned, all_lunch, actual_day_costs, route_winners)

        prev_assignments = assignments
        prev_sig = sig

    if best_output is None:
        return empty_output(
            input,
            metadata={
                "optimizer_type": "cpsat",
                "warm_start_used": warm_ok,
                "warm_incumbent_used": warm_incumbent_ok,
                "upper_bound_provided": upper_bound is not None,
                "upper_bound_accepted": warm_ok,
                "cp_sat_status": cp_sat_status,
            },
        )

    planned_visits, lunch_placements, day_drive_costs, route_winners = best_output

    # Encode lexicographic (placed, cost) as single fitness number for output.
    # visit_level is set high enough that unscheduled visits always dominate cost.
    visit_level_fitness = max(int(best_cost) + 1, 1_000_000)
    best_fitness = (num_instances - best_placed) * visit_level_fitness + best_cost

    planned_visits.sort(key=lambda v: (v.date, v.starts_at))

    routes_by_day: dict[str, list[int]] = defaultdict(list)
    for v in planned_visits:
        routes_by_day[v.date].append(v.patient_id)
    return_home = compute_return_home(dict(routes_by_day), input.travel_matrix)

    for date in input.working_days:
        if not lunch_placements.get(date):
            day_start, _ = day_bounds(input.clinician, date)
            half_w = input.clinician.lunch_window_minutes // 2
            earliest = max(input.clinician.lunch_start_minute - half_w, day_start)
            lunch_placements[date] = {
                "start_minute": earliest,
                "end_minute": earliest + input.clinician.lunch_duration_minutes,
            }

    placed_ids = {v.instance_id for v in planned_visits}
    placed_patient_dates: dict[int, set[str]] = defaultdict(set)
    for v in planned_visits:
        placed_patient_dates[v.patient_id].add(v.date)

    unschedulable = []
    for inst in input.instances:
        if inst.id in placed_ids:
            continue
        patient = patients_by_id.get(inst.patient_id)
        reasons = _diagnose_unscheduled(inst, patient, input, ctx, placed_patient_dates)
        unschedulable.append({
            "patient_name": patient.name if patient else "Unknown",
            "patient_id": inst.patient_id,
            "reasons": reasons,
        })

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

    day_bounds_used: dict[str, dict[str, int]] = {}
    for date in input.working_days:
        ds, de = day_bounds(input.clinician, date)
        day_bounds_used[date] = {"start": ds, "end": de}

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
            "warm_incumbent_used": warm_incumbent_ok,
            "upper_bound_provided": upper_bound is not None,
            "upper_bound_accepted": warm_ok,
            "day_bounds": day_bounds_used,
        },
    )
